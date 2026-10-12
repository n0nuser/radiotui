"""CTCSS and DCS detection on the NFM discriminator output (ADR 0017).

Written from the published tone list and code structure; GNU Radio and SDR++
were read for the method only (both are GPL).

The detector takes the *unfiltered* discriminator (ADR 0015 removes these
tones from what is heard) and decimates it to about 1 kS/s with its own
``DemodState``. Two paths share one rolling 1.5 s buffer:

- **Identify** (display, ``any``): DCS first, then the listed tone nearest
  the strongest peak of a zero-padded FFT, kept only if it holds a real share
  of the power. That share test also rejects tones off the list.
- **Verify** a selected CTCSS tone over a window of 1.5 / spacing to its
  nearest neighbour. That puts each neighbour 1.5 Hann bins away (~16 dB of
  leakage), so the tone-share threshold alone rejects it. Most tones need
  well under a second; only 150.0/151.4 Hz needs about one.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from radiotui.audio.demod import DemodState, decimate

CTCSS_TONES_HZ: tuple[float, ...] = (
    67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5, 94.8, 97.4,
    100.0, 103.5, 107.2, 110.9, 114.8, 118.8, 123.0, 127.3, 131.8, 136.5,
    141.3, 146.2, 150.0, 151.4, 156.7, 159.8, 162.2, 165.5, 167.9, 171.3,
    173.8, 177.3, 179.9, 183.5, 186.2, 189.9, 192.8, 196.6, 199.5, 203.5,
    206.5, 210.7, 218.1, 225.7, 229.1, 233.6, 241.8, 250.3, 254.1,
)  # fmt: skip

#: The standard DCS code list (octal).
DCS_CODES: tuple[str, ...] = tuple(
    """023 025 026 031 032 036 043 047 051 053 054 065 071 072 073 074 114 115
    116 122 125 131 132 134 143 145 152 155 156 162 165 172 174 205 212 223 225
    226 243 244 245 246 251 252 255 261 263 265 266 271 274 306 311 315 325 331
    332 343 346 351 356 364 365 371 411 412 413 423 431 432 445 446 452 454 455
    462 464 465 466 503 506 516 523 526 532 546 565 606 612 624 627 631 632 654
    662 664 703 712 723 731 732 734 743 754""".split()
)

DCS_BAUD = 134.4
#: Golay (23,12) generator x^11+x^10+x^6+x^5+x^4+x^2+1. The other (23,12)
#: generator, 0xAE3, does not reproduce the published +023 = -047 alias.
GOLAY_POLY = 0xC75
_MASK = (1 << 23) - 1

WINDOW_S = 1.5
#: A tone belongs to a frequency within this: under half the 12.5 kHz
#: PMR/2 m channel spacing, so a neighbour's tone never applies.
TONE_WINDOW_HZ = 6_000.0
DETECT_RATE_HZ = 1_000.0
#: Share of the buffer's power the tone must hold: a smeared voice
#: fundamental never concentrates this much in one bin.
MIN_TONE_SHARE = 0.3
#: Consecutive matching blocks before a selected tone opens the squelch.
OPEN_AFTER = 2
#: DCS lock: the same corrected word repeats over this many 23-bit periods.
DCS_PERIODS = 4

# ---- DCS code book ----


def _remainder(value: int) -> int:
    for bit in range(value.bit_length() - 1, 10, -1):
        if value >> bit & 1:
            value ^= GOLAY_POLY << (bit - 11)
    return value


def dcs_word(code: str) -> int:
    """The 23-bit word as sent: bit 0 (C1) goes on air first.

    Layout P11..P1 | 100 | C9..C1 (yo3iiu.ro). It is a rotation of the
    systematic [data | check] codeword, so it is a Golay codeword too.
    """
    data = (0b100 << 9) | int(code, 8)
    return (_remainder(data << 11) << 12) | data


def _rotations(word: int) -> set[int]:
    return {((word << k) | (word >> (23 - k))) & _MASK for k in range(23)}


@lru_cache(maxsize=1)
def _dcs_table() -> dict[int, str]:
    """Every rotation of every code, both polarities; normal names win."""
    table: dict[int, str] = {}
    for code in DCS_CODES:
        for word in _rotations(dcs_word(code) ^ _MASK):
            table[word] = f"D{code}I"
    for code in DCS_CODES:
        for word in _rotations(dcs_word(code)):
            table[word] = f"D{code}N"
    return table


def dcs_aliases() -> list[tuple[str, str]]:
    """(shown name, hidden name) pairs that are the same bits on air."""
    table = _dcs_table()
    pairs = []
    for code in DCS_CODES:
        shown = table[dcs_word(code) ^ _MASK]
        if shown != f"D{code}I":
            pairs.append((shown, f"D{code}I"))
    return pairs


@lru_cache(maxsize=1)
def _syndrome_tables() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-bit syndromes, and the error pattern (and weight) per syndrome.

    The code is perfect: every syndrome has exactly one pattern of weight <= 3.
    """
    per_bit = np.array([_remainder(1 << i) for i in range(23)], dtype=np.int64)
    errors = np.zeros(2048, dtype=np.int64)
    weights = np.zeros(2048, dtype=np.int64)
    patterns = [0] + [1 << i for i in range(23)]
    patterns += [(1 << i) | (1 << j) for i in range(23) for j in range(i)]
    patterns += [
        (1 << i) | (1 << j) | (1 << k) for i in range(23) for j in range(i) for k in range(j)
    ]
    for pattern in reversed(patterns):  # lowest weight written last wins
        syndrome = _remainder(pattern)
        errors[syndrome] = pattern
        weights[syndrome] = bin(pattern).count("1")
    return per_bit, errors, weights


def _decode_dcs(samples: np.ndarray, fs: float) -> str | None:
    """Slice 134.4 bps NRZ, Golay-correct every window, look for a repeat."""
    spb = fs / DCS_BAUD
    length = round(spb)
    if len(samples) < spb * 23 * (DCS_PERIODS + 1):
        return None
    filtered = np.convolve(samples - samples.mean(), np.ones(length) / length, mode="same")
    per_bit, errors, weights = _syndrome_tables()
    powers = np.int64(1) << np.arange(23, dtype=np.int64)
    table = _dcs_table()
    for offset in np.arange(8) * spb / 8:
        count = int((len(filtered) - offset) // spb)
        centres = (offset + spb * (np.arange(count) + 0.5)).astype(int)
        bits = (filtered[centres] > 0).astype(np.int64)
        windows = sliding_window_view(bits, 23)
        words = windows @ powers
        syndromes = np.bitwise_xor.reduce(windows * per_bit, axis=1)
        corrected = words ^ errors[syndromes]
        good = weights[syndromes] <= 3
        span = 23 * (DCS_PERIODS - 1)
        if len(corrected) <= span:
            continue
        repeat = good[: len(corrected) - span].copy()
        for period in range(1, DCS_PERIODS):
            shift = 23 * period
            repeat &= corrected[: len(corrected) - span] == corrected[shift : shift + len(repeat)]
            repeat &= good[shift : shift + len(repeat)]
        for index in np.flatnonzero(repeat):
            name = table.get(int(corrected[index]))
            if name is not None:
                return name
    return None


# ---- tone names ----


def tone_near(tones: dict[float, str], freq_hz: float, window_hz: float = TONE_WINDOW_HZ) -> str:
    """The tone heard closest to ``freq_hz``, or "" (a channel's peak and the
    frequency it was listened on differ slightly)."""
    best, best_dist = "", window_hz
    for heard_hz, tone in tones.items():
        if abs(heard_hz - freq_hz) <= best_dist:
            best, best_dist = tone, abs(heard_hz - freq_hz)
    return best


def is_valid_tone(value: str) -> bool:
    if value.startswith("D"):
        return len(value) == 5 and value[1:4] in DCS_CODES and value[4] in "NI"
    try:
        return float(value) in CTCSS_TONES_HZ and value == f"{float(value):.1f}"
    except ValueError:
        return False


def canonical_tone(value: str | None) -> str | None:
    """The name shown for ``value``; aliased DCS codes share one name."""
    if value is None or not value.startswith("D"):
        return value
    word = dcs_word(value[1:4])
    return _dcs_table()[word ^ _MASK if value[4] == "I" else word]


def _spacing(tone: float) -> float:
    i = CTCSS_TONES_HZ.index(tone)
    near = [CTCSS_TONES_HZ[j] for j in (i - 1, i + 1) if 0 <= j < len(CTCSS_TONES_HZ)]
    return min(abs(tone - n) for n in near)


def _tone_share(x: np.ndarray, fs: float, freqs: list[float]) -> np.ndarray:
    """Power share of ``x`` in a sine at each frequency (Hann-windowed DFT)."""
    window = np.hanning(len(x))
    t = np.arange(len(x)) / fs
    amplitude = 2 * np.abs(np.exp(-2j * np.pi * np.outer(freqs, t)) @ (x * window)) / window.sum()
    return (amplitude**2 / 2) / (float(np.mean(x**2)) + 1e-12)


class ToneDetector:
    """Rolling CTCSS/DCS detector for one monitor; feed it every NFM block."""

    def __init__(self, channel_fs: float) -> None:
        self._factor = max(1, int(channel_fs // DETECT_RATE_HZ))
        self.fs = channel_fs / self._factor
        self._state = DemodState()  # its own: sharing would reset both decimators
        self._buffer = np.zeros(0)
        self._streak: dict[str, int] = {}
        self.detected: str | None = None

    def feed(self, discriminator: np.ndarray) -> None:
        low = decimate(np.asarray(discriminator, dtype=np.float64), self._factor, self._state)
        keep = int(WINDOW_S * self.fs)
        self._buffer = np.concatenate([self._buffer, low])[-keep:]
        self.detected = self._identify()
        self._streak = {k: v for k, v in self._streak.items() if self._check(k)}

    def matches(self, selected: str) -> bool:
        """``any`` or a tone/code; true after OPEN_AFTER matching blocks."""
        if not self._check(selected):
            self._streak[selected] = 0
            return False
        self._streak[selected] = self._streak.get(selected, 0) + 1
        return self._streak[selected] >= OPEN_AFTER

    def _check(self, selected: str) -> bool:
        if selected == "any":
            return self.detected is not None
        if selected.startswith("D"):
            return canonical_tone(self.detected) == canonical_tone(selected)
        return self._verify(float(selected))

    def _identify(self) -> str | None:
        x = self._buffer - self._buffer.mean() if len(self._buffer) else self._buffer
        if len(x) < int(WINDOW_S * self.fs * 0.9):
            return None
        dcs = _decode_dcs(x, self.fs)
        if dcs is not None:
            return dcs
        size = 16_384
        spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x)), size))
        freqs = np.fft.rfftfreq(size, 1.0 / self.fs)
        band = np.flatnonzero((freqs >= 60.0) & (freqs <= 260.0))
        peak_hz = freqs[band[np.argmax(spectrum[band])]]
        tone = min(CTCSS_TONES_HZ, key=lambda f: abs(f - peak_hz))
        if _tone_share(x, self.fs, [tone])[0] < MIN_TONE_SHARE:
            return None
        return f"{tone:.1f}"

    def _verify(self, tone: float) -> bool:
        window = int(min(max(1.5 / _spacing(tone), 0.4), WINDOW_S) * self.fs)
        if len(self._buffer) < window:
            return False
        x = self._buffer[-window:] - self._buffer[-window:].mean()
        return _tone_share(x, self.fs, [tone])[0] >= MIN_TONE_SHARE
