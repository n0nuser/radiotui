"""Issue #86 / ADR 0017: CTCSS and DCS detection on the NFM discriminator."""

import numpy as np
import pytest

from radiotui.audio.demod import DemodState, channel_audio
from radiotui.audio.tones import (
    CTCSS_TONES_HZ,
    DCS_BAUD,
    DCS_CODES,
    ToneDetector,
    canonical_tone,
    dcs_aliases,
    dcs_word,
    is_valid_tone,
)
from radiotui.core.models import DemodMode

FS = 1_024_000.0
BLOCK = 65_536
SUB_DEV_HZ = 750.0  # ~15 % of the 5 kHz NFM deviation


def fm_iq(modulation_hz: np.ndarray) -> np.ndarray:
    return np.exp(2j * np.pi * np.cumsum(modulation_hz) / FS)


def voice_like(t: np.ndarray, seed: int = 4) -> np.ndarray:
    """A harmonic series with a wandering pitch. Radios high-pass their mic
    audio at ~300 Hz, so harmonics start there, with a residual fundamental
    20 dB down that lands inside the CTCSS band."""
    rng = np.random.default_rng(seed)
    pitch = 130.0 + 20.0 * np.sin(2 * np.pi * 0.7 * t) + rng.normal(0, 2.0)
    phase = 2 * np.pi * np.cumsum(pitch) / FS
    voice = 0.1 * np.sin(phase)
    for k in range(3, 20):
        voice += np.sin(k * phase) / k
    return 2_500.0 * voice / np.max(np.abs(voice))


def dcs_waveform(t: np.ndarray, code: str, inverted: bool = False) -> np.ndarray:
    word = dcs_word(code)
    if inverted:
        word ^= (1 << 23) - 1
    bit_index = (t * DCS_BAUD).astype(int) % 23
    bits = (word >> bit_index) & 1
    return SUB_DEV_HZ * (2.0 * bits - 1.0)


def detect(modulation_hz: np.ndarray, selected: str | None = None) -> tuple[ToneDetector, bool]:
    """Run NFM IQ through the receive chain and the detector, block by block."""
    iq = fm_iq(modulation_hz)
    state = DemodState()
    detector = None
    matched = False
    for i in range(0, len(iq), BLOCK):
        channel_audio(iq[i : i + BLOCK], FS, DemodMode.NFM, state=state)
        if detector is None:
            detector = ToneDetector(FS / 34)
        detector.feed(state.discriminator)
        if selected is not None:
            matched = detector.matches(selected)
    return detector, matched


def seconds(n: float) -> np.ndarray:
    return np.arange(int(FS * n)) / FS


def test_tone_table_is_the_standard_list():
    assert len(CTCSS_TONES_HZ) == 51
    assert CTCSS_TONES_HZ[0] == 67.0 and CTCSS_TONES_HZ[-1] == 254.1
    assert list(CTCSS_TONES_HZ) == sorted(CTCSS_TONES_HZ)


@pytest.mark.parametrize("tone", CTCSS_TONES_HZ)
def test_every_ctcss_tone_is_identified(tone):
    t = seconds(2.0)
    detector, _ = detect(SUB_DEV_HZ * np.sin(2 * np.pi * tone * t))
    assert detector.detected == f"{tone:.1f}"


@pytest.mark.parametrize("tone", [150.0, 151.4])
def test_close_tones_are_told_apart(tone):
    t = seconds(2.0)
    detector, matched = detect(SUB_DEV_HZ * np.sin(2 * np.pi * tone * t), selected="151.4")
    assert detector.detected == f"{tone:.1f}"
    assert matched == (tone == 151.4)


def test_tone_is_identified_under_voice():
    t = seconds(2.5)
    detector, matched = detect(SUB_DEV_HZ * np.sin(2 * np.pi * 88.5 * t) + voice_like(t), "88.5")
    assert detector.detected == "88.5"
    assert matched


@pytest.mark.parametrize("selected", ["88.5", "any", "D023N"])
def test_voice_without_a_tone_does_not_open(selected):
    t = seconds(2.5)
    detector, matched = detect(voice_like(t), selected)
    assert detector.detected is None
    assert not matched


def test_a_different_tone_does_not_open():
    t = seconds(2.0)
    _, matched = detect(SUB_DEV_HZ * np.sin(2 * np.pi * 100.0 * t), "88.5")
    assert not matched


@pytest.mark.parametrize("code", ["023", "205", "754"])
@pytest.mark.parametrize("inverted", [False, True])
def test_dcs_code_is_identified_in_both_polarities(code, inverted):
    t = seconds(2.0)
    detector, _ = detect(dcs_waveform(t, code, inverted))
    assert canonical_tone(detector.detected) == canonical_tone(f"D{code}{'I' if inverted else 'N'}")


def test_dcs_does_not_false_report_its_neighbour_ctcss_tones():
    t = seconds(2.0)
    detector, _ = detect(dcs_waveform(t, "023"))
    assert detector.detected.startswith("D")


def test_aliased_dcs_codes_are_listed_and_open_on_either_name():
    """+023 is the same bit pattern as -047 (yo3iiu.ro DCS codec notes)."""
    aliases = dcs_aliases()
    assert ("D023N", "D047I") in aliases
    assert canonical_tone("D047I") == canonical_tone("D023N") == "D023N"
    t = seconds(2.0)
    _, matched = detect(dcs_waveform(t, "023"), "D047I")
    assert matched


def test_dcs_table_is_unambiguous_for_normal_codes():
    words = {}
    for code in DCS_CODES:
        w = dcs_word(code)
        rotations = {((w << k) | (w >> (23 - k))) & ((1 << 23) - 1) for k in range(23)}
        for r in rotations:
            assert words.setdefault(r, code) == code, f"D{code}N collides with D{words[r]}N"


def test_noise_reports_nothing_at_any_block():
    """Random peaks snap to some tone often; the tone-share test must reject them."""
    rng = np.random.default_rng(9)
    detector = ToneDetector(FS / 34)
    for _ in range(80):
        detector.feed(rng.uniform(-1.5, 1.5, size=1_928))
        assert detector.detected is None
        assert not detector.matches("any")


def test_a_non_standard_tone_is_not_snapped():
    t = seconds(2.0)
    detector, _ = detect(SUB_DEV_HZ * np.sin(2 * np.pi * 90.0 * t))
    assert detector.detected is None


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("88.5", True),
        ("88.6", False),
        ("D023N", True),
        ("D023X", False),
        ("D999N", False),
        ("any", False),
    ],
)
def test_tone_names_are_validated(value, valid):
    assert is_valid_tone(value) == valid
