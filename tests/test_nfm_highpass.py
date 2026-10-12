"""Issue #84 / ADR 0015: NFM sub-audible high-pass."""

import time

import numpy as np
import pytest

from radiotui.audio.demod import DemodState, subaudible_highpass
from radiotui.config import HF_SAMPLE_RATE_HZ

NFM_RATES = [1_024_000.0 / 34, HF_SAMPLE_RATE_HZ / 8]


def gain_db(freq_hz: float, fs: float) -> float:
    t = np.arange(int(fs * 2.0)) / fs
    out = subaudible_highpass(np.sin(2 * np.pi * freq_hz * t), fs)
    settled = out[int(fs * 0.5) :]
    return 20 * np.log10(np.sqrt(2) * np.sqrt(np.mean(settled**2)))


@pytest.mark.parametrize("fs", NFM_RATES)
@pytest.mark.parametrize("freq_hz", [300.0, 1_000.0, 3_000.0])
def test_voice_band_passes(fs, freq_hz):
    assert abs(gain_db(freq_hz, fs)) <= 0.5


@pytest.mark.parametrize("fs", NFM_RATES)
@pytest.mark.parametrize("freq_hz", [67.0, 134.4, 254.1])
def test_subaudible_band_is_stopped(fs, freq_hz):
    assert gain_db(freq_hz, fs) <= -40.0


def test_blockwise_output_matches_whole_signal():
    fs = NFM_RATES[0]
    x = np.random.default_rng(7).normal(size=int(fs))
    whole = subaudible_highpass(x, fs, DemodState())
    state = DemodState()
    pieces = np.concatenate(
        [subaudible_highpass(x[i : i + 1_928], fs, state) for i in range(0, len(x), 1_928)]
    )
    np.testing.assert_allclose(pieces, whole, atol=1e-9)


def test_cost_per_block_fits_the_budget():
    """ADR 0015: under ~5 ms for one 64 ms block (1,928 samples at the NFM rate)."""
    fs = NFM_RATES[0]
    state = DemodState()
    block = np.random.default_rng(1).normal(size=1_928)
    subaudible_highpass(block, fs, state)  # build the cached taps
    start = time.perf_counter()
    for _ in range(20):
        subaudible_highpass(block, fs, state)
    assert (time.perf_counter() - start) / 20 < 0.005
