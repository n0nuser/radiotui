import numpy as np
import pytest

from radiotui.config import ScannerSettings
from radiotui.core.models import SpectrumFrame
from radiotui.dsp.detector import (
    ChannelTracker,
    NoiseFloorEstimator,
    extract_peaks,
)
from radiotui.dsp.spectrum import SweepPlan, compute_psd


def make_frame(peaks_db: dict[float, float], floor_db: float = -60.0) -> SpectrumFrame:
    freqs = np.arange(88e6, 89e6, 1e3)
    power = np.full(len(freqs), floor_db)
    rng = np.random.default_rng(0)
    power += rng.normal(0, 0.5, len(freqs))
    for center, db in peaks_db.items():
        idx = np.argmin(np.abs(freqs - center))
        power[idx - 2 : idx + 3] = db
    return SpectrumFrame(freqs_hz=freqs, power_db=power)


def test_noise_floor_tracks_median():
    settings = ScannerSettings()
    est = NoiseFloorEstimator(settings)
    for _ in range(5):
        floor = est.update(make_frame({}))
    assert floor == pytest.approx(-60.0, abs=2.0)
    assert est.threshold_db == pytest.approx(floor + settings.threshold_margin_db, abs=0.5)


def test_extract_peaks_finds_strong_signal():
    frame = make_frame({88.35e6: -30.0})
    peaks = extract_peaks(
        frame, floor_db=-60.0, threshold_db=-51.0, min_snr_db=4.0, merge_gap_bins=3
    )
    assert len(peaks) == 1
    assert peaks[0].center_hz == pytest.approx(88.35e6, abs=3e3)
    assert peaks[0].snr_db > 25


def test_extract_peaks_ignores_noise():
    frame = make_frame({})
    peaks = extract_peaks(
        frame, floor_db=-60.0, threshold_db=-51.0, min_snr_db=4.0, merge_gap_bins=3
    )
    assert peaks == []


def test_tracker_requires_persistence():
    settings = ScannerSettings(min_persist_frames=3)
    tracker = ChannelTracker(settings)
    now = 1000.0
    frame = make_frame({88.35e6: -30.0})
    active = tracker.update(extract_peaks(frame, -60.0, -51.0, 4.0, 3), now=now)
    assert active == []
    active = tracker.update([], now=now + 1)
    assert active == []


def test_tracker_activates_after_min_frames():
    settings = ScannerSettings(min_persist_frames=2, drop_after_misses=2)
    tracker = ChannelTracker(settings)
    frame = make_frame({88.35e6: -30.0})
    peaks = extract_peaks(frame, -60.0, -51.0, 4.0, 3)
    tracker.update(peaks, now=1000.0)
    active = tracker.update(peaks, now=1001.0)
    assert len(active) == 1
    assert active[0].center_hz == pytest.approx(88.35e6, abs=10e3)
    assert active[0].demod.value in ("nfm", "wfm", "am")


def test_tracker_drops_stale_channels():
    settings = ScannerSettings(min_persist_frames=1, drop_after_misses=1)
    tracker = ChannelTracker(settings)
    frame = make_frame({88.35e6: -30.0})
    peaks = extract_peaks(frame, -60.0, -51.0, 4.0, 3)
    tracker.update(peaks, now=1000.0)
    tracker.update([], now=1001.0)
    active = tracker.update([], now=1002.0)
    assert active == []


def test_sweep_plan_single_hop_for_narrow_range():
    plan = SweepPlan.build(88e6, 88.5e6, 1_024_000, 1024)
    assert len(plan.hop_centers_hz) == 1
    assert plan.bin_hz == pytest.approx(1000.0)


def test_sweep_plan_multi_hop_covers_range():
    plan = SweepPlan.build(87.5e6, 108e6, 1_024_000, 1024)
    assert len(plan.hop_centers_hz) > 20
    freqs = plan.freqs_for_hop(plan.hop_centers_hz[0])
    assert len(freqs) == 1024
    assert freqs[0] == pytest.approx(plan.hop_centers_hz[0] - 512_000)


def test_compute_psd_normalization():
    rng = np.random.default_rng(1)
    sigma = 10 ** (-50 / 20) / np.sqrt(2)
    iq = rng.normal(0, sigma, 1024 * 64) + 1j * rng.normal(0, sigma, 1024 * 64)
    psd = compute_psd(iq, 1024)
    assert np.median(psd) == pytest.approx(-50.0, abs=1.5)
