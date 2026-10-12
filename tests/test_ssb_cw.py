"""Issue #85 / ADR 0016: USB, LSB and CW."""

import numpy as np
import pytest

from radiotui.audio.demod import DemodState, channel_audio
from radiotui.config import BANDS, HF_SAMPLE_RATE_HZ
from radiotui.core.models import Channel, DemodMode

FS = HF_SAMPLE_RATE_HZ
OUT = 48_000


def tone_iq(offset_hz: float, seconds: float = 1.0) -> np.ndarray:
    """A single tone ``offset_hz`` from the tuned frequency: SSB voice is just this."""
    t = np.arange(int(FS * seconds)) / FS
    return np.exp(2j * np.pi * offset_hz * t)


def demodulate(iq: np.ndarray, mode: DemodMode, clarifier=lambda i: 0.0) -> np.ndarray:
    state = DemodState()
    block = 16_384
    pieces = []
    for n, i in enumerate(range(0, len(iq), block)):
        pieces.append(
            channel_audio(iq[i : i + block], FS, mode, OUT, state=state, clarifier_hz=clarifier(n))
        )
    return np.concatenate(pieces)[OUT // 4 :]  # past filter and AGC transients


def level(audio: np.ndarray, freq_hz: float) -> float:
    t = np.arange(len(audio)) / OUT
    return 2 * abs(np.mean(audio * np.exp(-2j * np.pi * freq_hz * t)))


def purity(audio: np.ndarray, freq_hz: float) -> float:
    """Share of the output power that is the expected tone (1.0 = clean)."""
    return (level(audio, freq_hz) ** 2 / 2) / float(np.mean(audio**2))


@pytest.mark.parametrize(
    ("mode", "offset_hz"), [(DemodMode.USB, 1_000.0), (DemodMode.LSB, -1_000.0)]
)
def test_sideband_demodulates_at_its_pitch(mode, offset_hz):
    audio = demodulate(tone_iq(offset_hz), mode)
    assert purity(audio, 1_000.0) > 0.95


@pytest.mark.parametrize(
    ("mode", "offset_hz"), [(DemodMode.USB, -1_000.0), (DemodMode.LSB, 1_000.0)]
)
def test_opposite_sideband_is_rejected(mode, offset_hz):
    """The I±Q shortcut would pass this tone; a real sideband filter drops it 30 dB."""
    wanted = {DemodMode.USB: 1_000.0, DemodMode.LSB: -1_000.0}[mode]
    # AGC normalises each run, so compare both tones inside one signal.
    iq = tone_iq(wanted) + tone_iq(offset_hz * 1.2)
    audio = demodulate(iq, mode)
    assert 20 * np.log10(level(audio, 1_200.0) / level(audio, 1_000.0)) <= -30.0


def test_cw_carrier_becomes_a_700_hz_note():
    audio = demodulate(tone_iq(0.0), DemodMode.CW)
    assert purity(audio, 700.0) > 0.95


def test_clarifier_moves_the_pitch_without_phase_steps():
    """A shift that restarted every block would smear the tone; purity catches it."""
    audio = demodulate(tone_iq(1_000.0), DemodMode.USB, clarifier=lambda n: 300.0)
    assert purity(audio, 700.0) > 0.95


def test_clarifier_step_leaves_no_gap():
    audio = demodulate(
        tone_iq(1_000.0, 2.0), DemodMode.USB, clarifier=lambda n: 0.0 if n < 15 else 10.0
    )
    window = OUT // 100
    rms = [
        np.sqrt(np.mean(audio[i : i + window] ** 2)) for i in range(0, len(audio) - window, window)
    ]
    assert min(rms) > 0.5 * float(np.median(rms))


def test_ham_hf_bands_are_lsb_with_an_ssb_channel():
    for name in ("hf_ham_80m", "hf_ham_40m"):
        assert BANDS[name].demod is DemodMode.LSB
        assert BANDS[name].channel_bw_hz == 2_700.0


@pytest.mark.parametrize(
    ("mode", "expected"),
    [(DemodMode.USB, 7.0985e6), (DemodMode.LSB, 7.1015e6), (DemodMode.NFM, 7.1e6)],
)
def test_detected_ssb_peak_is_listened_to_at_its_carrier(mode, expected):
    channel = Channel(7.1e6, 2_400.0, -40.0, 20.0, mode, 0.0, 0.0)
    assert channel.listen_hz == pytest.approx(expected)
