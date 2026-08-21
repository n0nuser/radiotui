import numpy as np
import pytest

from radiotui.cli import parse_freq
from radiotui.config import BANDS, band_by_name
from radiotui.sdr.simulator import SimulatedDevice
from radiotui.dsp.spectrum import SweepPlan, compute_psd, frame_from_plan
from radiotui.dsp.detector import NoiseFloorEstimator, extract_peaks, ChannelTracker
from radiotui.config import ScannerSettings


def test_parse_freq_formats():
    assert parse_freq("145.5mhz") == 145.5e6
    assert parse_freq("145.5M") == 145.5e6
    assert parse_freq("446006k") == pytest.approx(446.006e6)
    assert parse_freq("145.5e6") == 145.5e6
    assert parse_freq("145500000") == 145.5e6
    with pytest.raises(Exception):
        parse_freq("not-a-freq")


def test_band_lookup():
    assert band_by_name("pmr446").end_hz == pytest.approx(446.09375e6)
    with pytest.raises(ValueError):
        band_by_name("nope")


def test_simulator_emits_expected_carriers():
    dev = SimulatedDevice(seed=3)
    settings = ScannerSettings()
    plan = SweepPlan.build(87.5e6, 108e6, settings.sample_rate_hz, settings.fft_size)
    est = NoiseFloorEstimator(settings)
    tracker = ChannelTracker(settings)
    for _ in range(10):
        hops = []
        for center in plan.hop_centers_hz:
            dev.set_center_freq_hz(center)
            iq = dev.read_samples(int(settings.sample_rate_hz * 0.12))
            hops.append((center, compute_psd(iq, settings.fft_size)))
        frame = frame_from_plan(plan, hops)
        floor = est.update(frame)
        peaks = extract_peaks(frame, floor, est.threshold_db, settings.min_snr_db, settings.peak_merge_gap_bins)
        tracker.update(peaks)

    found = {ch.center_hz for ch in tracker.active_channels()}
    for expected in (89.0e6, 93.2e6, 97.5e6, 101.3e6):
        assert any(abs(f - expected) < 60e3 for f in found), f"missing {expected/1e6} MHz"


def test_simulator_respects_sample_count():
    dev = SimulatedDevice(seed=1)
    dev.set_center_freq_hz(100e6)
    dev.set_sample_rate_hz(1_024_000)
    assert len(dev.read_samples(4096)) == 4096
