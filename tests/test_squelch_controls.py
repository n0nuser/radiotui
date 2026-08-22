"""Issue #17: live squelch controls — threshold margin, dwell, scan tuning flags."""

import queue
import time

import numpy as np
from textual.widgets import Static

from radiotui.cli import apply_scan_tuning, build_parser
from radiotui.config import (
    DWELL_RANGE_S,
    THRESHOLD_MARGIN_RANGE,
    ScannerSettings,
    Settings,
    clamp,
)
from radiotui.dsp.spectrum import SweepPlan
from radiotui.scanner.sweeper import Sweeper
from radiotui.sdr.base import SdrDevice
from radiotui.tui.app import RadioTuiApp


class CountingDevice(SdrDevice):
    """Fake radio that records how many samples each hop requested."""

    name = "counting"

    def __init__(self) -> None:
        self.requested_counts: list[int] = []

    def open(self): ...

    def close(self): ...

    @property
    def center_freq_hz(self):
        return 0.0

    def set_center_freq_hz(self, freq_hz):
        self._freq = freq_hz

    def set_sample_rate_hz(self, rate_hz): ...

    def set_gain_db(self, gain_db): ...

    def read_samples(self, count):
        self.requested_counts.append(count)
        return np.zeros(count, dtype=np.complex128)


def make_sweeper(dwell_s: float):
    settings = ScannerSettings(hop_dwell_s=dwell_s)
    plan = SweepPlan.build(144.0e6, 144.25e6, settings.sample_rate_hz, settings.fft_size)
    device = CountingDevice()
    out: queue.Queue = queue.Queue(maxsize=32)
    return Sweeper(device, plan, settings, out), out, device, settings


def next_state(out: queue.Queue, min_sweeps: int, timeout_s: float = 8.0):
    deadline = time.time() + timeout_s
    latest = None
    while time.time() < deadline:
        try:
            latest = out.get(timeout=0.25)
        except queue.Empty:
            continue
        if latest is not None and not latest.error and latest.sweeps_done >= min_sweeps:
            return latest
    raise AssertionError("sweeper produced no state in time")


def test_dwell_change_applies_without_restart():
    """Mutating the shared ScannerSettings must reach the running sweep loop."""
    sweeper, out, device, settings = make_sweeper(dwell_s=0.05)
    sweeper.start()
    try:
        first = next_state(out, min_sweeps=1)
        assert first.sweeps_done >= 1
        expected_before = max(int(settings.sample_rate_hz * 0.05), settings.fft_size * 4)
        assert device.requested_counts
        assert set(device.requested_counts) == {expected_before}

        # Same mutation path the TUI uses: write the shared settings object.
        settings.hop_dwell_s = 0.2
        device.requested_counts.clear()

        done_marker = first.sweeps_done
        while True:
            state = next_state(out, min_sweeps=done_marker + 1)
            if device.requested_counts:
                break
            done_marker = state.sweeps_done
        expected_after = max(int(settings.sample_rate_hz * 0.2), settings.fft_size * 4)
        assert set(device.requested_counts) == {expected_after}
    finally:
        sweeper.stop()


def test_clamp_ranges_are_sane():
    lo_t, hi_t = THRESHOLD_MARGIN_RANGE
    lo_d, hi_d = DWELL_RANGE_S
    assert clamp(-5.0, lo_t, hi_t) == lo_t == 0.0
    assert clamp(99.0, lo_t, hi_t) == hi_t
    assert clamp(0.001, lo_d, hi_d) == lo_d
    assert clamp(9.9, lo_d, hi_d) == hi_d


def test_scan_tuning_flags_apply_and_clamp():
    args = build_parser().parse_args(
        ["scan", "--threshold-margin", "15", "--min-snr", "6", "--dwell", "0.25"]
    )
    settings = Settings()
    apply_scan_tuning(settings, args)
    assert settings.scanner.threshold_margin_db == 15.0
    assert settings.scanner.min_snr_db == 6.0
    assert settings.scanner.hop_dwell_s == 0.25

    crazy = build_parser().parse_args(
        ["scan", "--threshold-margin", "999", "--min-snr", "-3", "--dwell", "42"]
    )
    apply_scan_tuning(settings, crazy)
    assert settings.scanner.threshold_margin_db == THRESHOLD_MARGIN_RANGE[1]
    assert settings.scanner.min_snr_db == 0.0
    assert settings.scanner.hop_dwell_s == DWELL_RANGE_S[1]


async def test_bracket_keys_adjust_threshold_with_status_readout():
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.2)
        app.refresh_status()
        await pilot.press("]")
        await pilot.press("]")
        await pilot.pause(0.1)
        assert app.settings.scanner.threshold_margin_db == 11.0
        meter = str(app.query_one("#meter", Static).render())
        assert "thr +11.0 dB" in meter

        await pilot.press("[")
        await pilot.pause(0.1)
        assert app.settings.scanner.threshold_margin_db == 10.0

        for _ in range(30):
            await pilot.press("[")
        await pilot.pause(0.1)
        assert app.settings.scanner.threshold_margin_db == THRESHOLD_MARGIN_RANGE[0]


async def test_curly_keys_adjust_dwell_with_status_readout():
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.2)
        app.refresh_status()
        await pilot.press("}")
        await pilot.press("}")
        await pilot.pause(0.1)
        assert app.settings.scanner.hop_dwell_s == 0.16
        meter = str(app.query_one("#meter", Static).render())
        assert "dwell 160 ms" in meter

        await pilot.press("{")
        await pilot.press("{")
        await pilot.pause(0.1)
        assert app.settings.scanner.hop_dwell_s == 0.12

        for _ in range(50):
            await pilot.press("}")
        await pilot.pause(0.1)
        assert app.settings.scanner.hop_dwell_s == DWELL_RANGE_S[1]
