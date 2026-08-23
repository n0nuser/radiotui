"""Issue #12: arbitrary frequency / range entry from the TUI (`f` key)."""

import time
from threading import Event

import pytest
from textual.widgets import Static

from radiotui.core.models import DemodMode
from radiotui.tui.app import RadioTuiApp, TuneModal, parse_tune_request


def test_parse_single_frequency_defaults_to_mhz():
    assert parse_tune_request("145.5") == (145.5e6, None, None)
    assert parse_tune_request("96.9 wfm") == (96.9e6, None, DemodMode.WFM)


def test_parse_accepts_explicit_units():
    freq, _, _ = parse_tune_request("433800k")
    assert freq == pytest.approx(433.8e6)
    freq, _, demod = parse_tune_request("121.5M am")
    assert freq == pytest.approx(121.5e6)
    assert demod == DemodMode.AM


def test_parse_range():
    start, end, demod = parse_tune_request("430-440")
    assert start == pytest.approx(430e6)
    assert end == pytest.approx(440e6)
    assert demod is None
    start, end, demod = parse_tune_request("88-108 nfm")
    assert start == pytest.approx(88e6)
    assert end == pytest.approx(108e6)
    assert demod == DemodMode.NFM


@pytest.mark.parametrize(
    "text",
    ["", "abc", "440-430", "10-", "-10", "145.5 xyz", "145.5 extra words"],
)
def test_parse_rejects_garbage(text):
    with pytest.raises(ValueError):
        parse_tune_request(text)


async def test_f_opens_modal_and_frequency_starts_listening():
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.2)
        await pilot.press("f")
        await pilot.pause(0.2)
        assert isinstance(app.screen, TuneModal)
        for key in "100":
            await pilot.press(key)
        await pilot.press("enter")
        await pilot.pause(0.5)
        assert not isinstance(app.screen, TuneModal)
        assert app.monitor is not None
        assert app.monitor.freq_hz == pytest.approx(100e6)
        assert app.monitor.demod == DemodMode.WFM


async def test_escape_cancels():
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("f")
        await pilot.pause(0.2)
        await pilot.press("escape")
        await pilot.pause(0.2)
        assert not isinstance(app.screen, TuneModal)
        assert app.monitor is None


async def test_bad_input_shows_inline_error_and_keeps_sweep():
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.2)
        sweeper_running = app.sweeper.running
        await pilot.press("f")
        await pilot.pause(0.2)
        for key in "abc":
            await pilot.press(key)
        await pilot.press("enter")
        await pilot.pause(0.2)
        error = str(app.screen.query_one("#tune-error", Static).render())
        assert error.strip()
        assert isinstance(app.screen, TuneModal)
        assert app.monitor is None
        assert app.sweeper.running == sweeper_running


async def test_range_sweeps_custom_band():
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.2)
        await pilot.press("f")
        await pilot.pause(0.2)
        for key in "430-440":
            await pilot.press(key)
        await pilot.press("enter")
        await pilot.pause(0.5)
        assert app.band_name == "custom"
        assert app.band_label == "430-440 MHz"
        assert all(430e6 <= hz <= 440e6 for hz in app.plan.hop_centers_hz)
        meter = str(app.query_one("#meter", Static).render())
        assert "430-440 MHz" in meter


async def test_hf_range_switches_direct_sampling_on():
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.2)
        await pilot.press("f")
        await pilot.pause(0.2)
        for key in "7.0-7.3":
            await pilot.press(key)
        await pilot.press("enter")
        await pilot.pause(0.5)
        assert app.settings.scanner.hf_mode
        assert app.plan.hop_centers_hz[0] >= 7.0e6
        assert app.plan.hop_centers_hz[-1] <= 7.3e6


async def test_monitor_handoff_waits_for_old_reader_before_starting_new_one():
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(140, 40)) as pilot:
        stopped = Event()
        started: list[tuple[float, DemodMode]] = []

        class SlowMonitor:
            def stop(self):
                time.sleep(0.05)
                stopped.set()

        app.monitor = SlowMonitor()
        app._start_monitor_now = lambda freq, demod, muted, record, pause: started.append(
            (freq, demod)
        )
        app.start_monitor(96.9e6, DemodMode.WFM, muted=True, enable_recorder=False)
        assert started == []
        await pilot.pause(0.01)
        assert started == []
        await pilot.pause(0.1)
        assert stopped.is_set()
        assert started == [(96.9e6, DemodMode.WFM)]
