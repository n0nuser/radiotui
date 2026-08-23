"""Discriminate voice/music from static and noise in demodulated audio.

Three cheap, dependency-free cues computed over short blocks:

- **Envelope modulation**: speech and music breathe (syllables, beats);
  band noise has a nearly constant envelope. Measured as the coefficient
  of variation of the smoothed envelope.
- **Spectral flatness**: noise spreads power evenly across bins (flatness
  near 1.0); voiced content concentrates it in formants/harmonics.
- **Speech-band share**: how much of the block's power sits inside
  300-3000 Hz relative to everything else.

``classify_block`` fuses them into a 0..1 score; ``SignalClass`` labels the
result (silence / noise / signal) against configurable thresholds.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

ENVELOPE_WINDOW_S = 0.02  # smoothing window for the amplitude envelope
SPEECH_LO_HZ = 300.0
SPEECH_HI_HZ = 3000.0


class SignalClass:
    SILENCE = "silence"
    NOISE = "noise"
    SIGNAL = "signal"


@dataclass
class AudioVerdict:
    rms_dbfs: float
    modulation: float
    flatness: float
    speech_share: float
    score: float
    klass: str


def _smooth_envelope(audio: np.ndarray, fs: float) -> np.ndarray:
    win = max(int(ENVELOPE_WINDOW_S * fs), 3)
    kernel = np.ones(win) / win
    return np.convolve(np.abs(audio), kernel, mode="same")


def _envelope_modulation(envelope: np.ndarray) -> float:
    """Coefficient of variation of the envelope: ~0 for static, high for speech."""
    mean = float(np.mean(envelope))
    if mean < 1e-9:
        return 0.0
    return float(np.std(envelope) / mean)


def _spectral_flatness(audio: np.ndarray) -> float:
    """Geometric/arithmetic mean ratio of the power spectrum (1.0 = white)."""
    spec = np.abs(np.fft.rfft(audio * np.hanning(len(audio)))) ** 2
    spec = spec[1:] + 1e-12  # skip DC, avoid log(0)
    log_mean = float(np.exp(np.mean(np.log(spec))))
    arith_mean = float(np.mean(spec))
    return log_mean / arith_mean


def _speech_band_share(audio: np.ndarray, fs: float) -> float:
    spec = np.abs(np.fft.rfft(audio * np.hanning(len(audio)))) ** 2
    freqs = np.fft.rfftfreq(len(audio), 1 / fs)
    total = float(spec.sum())
    if total <= 0:
        return 0.0
    in_band = float(spec[(freqs >= SPEECH_LO_HZ) & (freqs <= SPEECH_HI_HZ)].sum())
    return in_band / total


def classify_block(
    audio: np.ndarray,
    fs: float,
    silence_below_dbfs: float = -55.0,
    noise_max_score: float = 0.45,
) -> AudioVerdict:
    """Score one block of demodulated audio; label silence/noise/signal."""
    if len(audio) == 0:
        return AudioVerdict(-120.0, 0.0, 1.0, 0.0, 0.0, SignalClass.SILENCE)
    rms = float(np.sqrt(np.mean(audio**2)))
    rms_dbfs = 20.0 * np.log10(rms + 1e-9)
    if rms_dbfs < silence_below_dbfs:
        return AudioVerdict(rms_dbfs, 0.0, 1.0, 0.0, 0.0, SignalClass.SILENCE)

    envelope = _smooth_envelope(audio, fs)
    modulation = min(_envelope_modulation(envelope), 2.0) / 2.0
    flatness = _spectral_flatness(audio)
    speech_share = _speech_band_share(audio, fs)

    score = 0.4 * modulation + 0.35 * (1.0 - flatness) + 0.25 * min(speech_share * 2.0, 1.0)
    if score >= noise_max_score:
        klass = SignalClass.SIGNAL
    else:
        klass = SignalClass.NOISE
    return AudioVerdict(rms_dbfs, modulation, flatness, speech_share, score, klass)


class StreamClassifier:
    """Rolling verdicts over consecutive blocks, hysteresis on the label."""

    def __init__(
        self,
        fs: float,
        history_blocks: int = 6,
        silence_below_dbfs: float = -55.0,
        noise_max_score: float = 0.45,
    ) -> None:
        self._fs = fs
        self._history: deque[float] = deque(maxlen=history_blocks)
        self._silence_dbfs = silence_below_dbfs
        self._noise_max = noise_max_score

    def feed(self, audio: np.ndarray) -> AudioVerdict:
        """Classify one block; the label rides on a smoothed rolling score."""
        verdict = classify_block(audio, self._fs, self._silence_dbfs, self._noise_max)
        if verdict.rms_dbfs < self._silence_dbfs:
            self._history.clear()  # true silence resets any momentum
            return AudioVerdict(verdict.rms_dbfs, 0.0, 1.0, 0.0, 0.0, SignalClass.SILENCE)
        self._history.append(verdict.score)
        smoothed = float(np.mean(self._history))
        klass = SignalClass.SIGNAL if smoothed >= self._noise_max else SignalClass.NOISE
        return AudioVerdict(
            verdict.rms_dbfs,
            verdict.modulation,
            verdict.flatness,
            verdict.speech_share,
            smoothed,
            klass,
        )
