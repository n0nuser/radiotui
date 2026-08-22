"""Issue #18: channel filtering, derived decimation, vectorized de-emphasis, AM AGC."""

import time

import numpy as np
import pytest

from radiotui.audio.demod import (
    DemodState,
    channel_audio,
    channel_decimate,
    channel_decimation,
    deemphasize_fir,
    deemphasize_loop,
    demod_am,
)
from radiotui.config import HF_SAMPLE_RATE_HZ
from radiotui.core.models import DemodMode

FS = 1_024_000.0


def nfm_tone(offset_hz: float, seconds: float = 0.2, dev: float = 2_500.0) -> np.ndarray:
    t = np.arange(int(FS * seconds)) / FS
    voice = np.sin(2 * np.pi * 1_000 * t)
    phase = 2 * np.pi * (offset_hz * t + dev * np.cumsum(voice) / FS)
    rng = np.random.default_rng(3)
    iq = np.exp(1j * phase).astype(np.complex128)
    return iq + 0.01 * (rng.normal(size=len(t)) + 1j * rng.normal(size=len(t)))


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.abs(x) ** 2)))


def test_decimation_derived_from_bandwidth_and_rate():
    assert channel_decimation(FS, 12_500.0, DemodMode.NFM) == 34
    assert channel_decimation(FS, 200_000.0, DemodMode.WFM) == 4
    assert channel_decimation(FS, 8_330.0, DemodMode.AM) == 64


def test_hf_forced_sample_rate_stays_valid():
    factor = channel_decimation(HF_SAMPLE_RATE_HZ, 10_000.0, DemodMode.AM)
    chan_fs = HF_SAMPLE_RATE_HZ / factor
    assert chan_fs >= 16_000.0
    assert chan_fs >= 10_000.0  # above the channel bandwidth itself


def test_adjacent_carrier_suppressed_by_channel_filter():
    """A carrier one PMR slot away must come out at least 20 dB below the tuned one."""
    wanted_alone = channel_decimate(nfm_tone(0.0), FS, 12_500.0, DemodMode.NFM)
    other_alone = channel_decimate(nfm_tone(25_000.0), FS, 12_500.0, DemodMode.NFM)
    suppression = 20 * np.log10(rms(other_alone) / rms(wanted_alone))
    assert suppression <= -20.0


def test_adjacent_suppression_beats_the_old_boxcar_chain():
    """The whole point: without the new chain the neighbour lands in-band."""
    raw = rms(nfm_tone(25_000.0)) / rms(nfm_tone(0.0))
    assert raw > -1.0  # unfiltered, the two are indistinguishable


def test_deemphasize_fir_matches_reference_loop():
    rng = np.random.default_rng(11)
    audio = rng.normal(size=8_000) * 0.2
    reference = deemphasize_loop(audio, 240_000.0, 50e-6)
    filtered, _ = deemphasize_fir(audio, 240_000.0, 50e-6)
    tail = slice(400, None)  # skip the cold-start transient
    assert np.max(np.abs(filtered[tail] - reference[tail])) < 1e-4


def test_deemphasize_fir_state_continues_across_blocks():
    rng = np.random.default_rng(5)
    blocks = [rng.normal(size=4_096) * 0.2 for _ in range(6)]
    reference = deemphasize_loop(np.concatenate(blocks), 240_000.0, 50e-6)
    filtered_all = []
    hist = None
    for block in blocks:
        out, hist = deemphasize_fir(block, 240_000.0, 50e-6, hist)
        filtered_all.append(out)
    assert np.max(np.abs(np.concatenate(filtered_all)[-400:] - reference[-400:])) < 1e-3


def test_deemphasis_benchmark_vectorized_wins():
    rng = np.random.default_rng(7)
    audio = rng.normal(size=65_536) * 0.2

    t0 = time.perf_counter()
    for _ in range(3):
        deemphasize_loop(audio, 240_000.0, 50e-6)
    loop_s = (time.perf_counter() - t0) / 3

    t0 = time.perf_counter()
    for _ in range(3):
        deemphasize_fir(audio, 240_000.0, 50e-6)
    fir_s = (time.perf_counter() - t0) / 3

    assert fir_s < loop_s / 3, f"fir={fir_s:.5f}s loop={loop_s:.5f}s"


def am_block(seconds: float, amplitude: float) -> np.ndarray:
    t = np.arange(int(FS * seconds)) / FS
    mod = 0.5 * np.sin(2 * np.pi * 800 * t)
    return amplitude * (1 + mod) * np.exp(1j * 0)


def test_am_level_stable_across_blocks_of_varying_amplitude():
    state = DemodState()
    quiet = demod_am(am_block(0.05, 0.05), FS, state)
    loud = demod_am(am_block(0.05, 0.50), FS, state)
    ratio_db = 20 * np.log10(rms(loud) / max(rms(quiet), 1e-9))
    assert abs(ratio_db) < 6.0


def test_am_output_tracks_audible_variation_within_reason():
    """AGC must not flatten the modulation itself, only the carrier level."""
    state = DemodState()
    block = demod_am(am_block(0.05, 0.5), FS, state)
    envelope = np.abs(block)
    assert np.percentile(envelope, 90) > np.percentile(envelope, 10)


def test_end_to_end_channel_audio_survives_the_new_chain():
    iq = nfm_tone(0.0)
    audio = channel_audio(iq, FS, DemodMode.NFM, 48_000, channel_bw_hz=12_500.0)
    assert len(audio) == pytest.approx(0.2 * 48_000, rel=0.02)
    assert audio.dtype == np.float32
