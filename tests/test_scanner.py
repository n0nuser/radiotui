import argparse
import queue

import pytest

from radiotui.cli import parse_freq
from radiotui.config import BANDS, ScannerSettings, band_by_name, effective_sample_rate, enable_hf
from radiotui.dsp.detector import ChannelTracker, NoiseFloorEstimator, extract_peaks
from radiotui.dsp.spectrum import SweepPlan, compute_psd, frame_from_plan
from radiotui.scanner.sweeper import Sweeper
from radiotui.sdr.simulator import SimulatedDevice


@pytest.mark.parametrize(
    "value,expected",
    [
        ("145.5mhz", 145.5e6),
        ("145.5M", 145.5e6),
        ("446006k", pytest.approx(446.006e6)),
        ("145.5e6", 145.5e6),
        ("145500000", 145.5e6),
    ],
)
def test_parse_freq_formats(value, expected):
    assert parse_freq(value) == expected


def test_parse_freq_rejects_garbage():
    with pytest.raises(argparse.ArgumentTypeError):
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
        peaks = extract_peaks(
            frame, floor, est.threshold_db, settings.min_snr_db, settings.peak_merge_gap_bins
        )
        tracker.update(peaks)

    found = {ch.center_hz for ch in tracker.active_channels()}
    for expected in (89.0e6, 93.2e6, 97.5e6, 101.3e6):
        assert any(abs(f - expected) < 60e3 for f in found), f"missing {expected / 1e6} MHz"


def test_simulator_respects_sample_count():
    dev = SimulatedDevice(seed=1)
    dev.set_center_freq_hz(100e6)
    dev.set_sample_rate_hz(1_024_000)
    assert len(dev.read_samples(4096)) == 4096


def test_sweeper_programs_plan_sample_rate_and_gain():
    settings = ScannerSettings(gain_db=18.0, min_persist_frames=1, hop_dwell_s=0.02)
    plan = SweepPlan.build(100e6, 101e6, settings.sample_rate_hz, settings.fft_size)
    device = SimulatedDevice(seed=4)
    calls = []
    original_rate = device.set_sample_rate_hz
    original_gain = device.set_gain_db
    device.set_sample_rate_hz = lambda rate: (calls.append(("rate", rate)), original_rate(rate))[1]
    device.set_gain_db = lambda gain: (calls.append(("gain", gain)), original_gain(gain))[1]
    out = queue.Queue(maxsize=2)
    sweeper = Sweeper(device, plan, settings, out)
    sweeper.start()
    try:
        state = out.get(timeout=5)
        assert not state.error
        assert calls[:2] == [("rate", plan.sample_rate_hz), ("gain", 18.0)]
    finally:
        sweeper.stop()


def test_hf_sweeper_reports_simulated_carrier_on_correct_axis():
    settings = ScannerSettings(min_persist_frames=1, hop_dwell_s=0.02)
    band = BANDS["hf_broadcast"]
    enable_hf(True, settings)
    plan = SweepPlan.build(
        band.start_hz,
        band.end_hz,
        effective_sample_rate(settings),
        settings.fft_size,
    )
    device = SimulatedDevice(seed=3)
    out = queue.Queue(maxsize=4)
    sweeper = Sweeper(device, plan, settings, out)
    sweeper.start()
    try:
        state = out.get(timeout=10)
        assert not state.error
        assert any(abs(channel.center_hz - 6.055e6) < 60e3 for channel in state.channels)
        assert device._sample_rate == pytest.approx(plan.sample_rate_hz)
    finally:
        sweeper.stop()
