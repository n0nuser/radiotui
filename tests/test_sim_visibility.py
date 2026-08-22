"""Issue #21: persistent simulated-mode marker and TUI meter peak hold."""

from types import SimpleNamespace

from textual.widgets import Static

from radiotui.cli import render_scan
from radiotui.core.models import DemodMode
from radiotui.tui.app import RadioTuiApp


def meter_text(app) -> str:
    return str(app.query_one("#meter", Static).render())


def test_cli_scan_title_flags_simulation():
    state = SimpleNamespace(sweeps_done=3, noise_floor_db=-80.0, threshold_db=-70.0, channels=[])
    assert "(SIMULATED)" in render_scan(state, simulated=True).title
    assert "(SIMULATED)" not in render_scan(state, simulated=False).title


async def test_sim_badge_shows_in_tui_status_line():
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        app.refresh_status()
        await pilot.pause(0.1)
        assert "SIM" in meter_text(app)


async def test_no_sim_badge_for_real_device():
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        app.is_real = True
        app.refresh_status()
        await pilot.pause(0.1)
        assert "SIM" not in meter_text(app)


async def test_meter_shows_peak_hold_marker_and_delta():
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        app.post_message(RadioTuiApp.RssiUpdate(-20))
        await pilot.pause(0.4)
        at_peak = meter_text(app)
        assert "at peak" in at_peak
        app.post_message(RadioTuiApp.RssiUpdate(-40))
        await pilot.pause(0.4)
        dropped = meter_text(app)
        assert "vs peak" in dropped
        assert "Δ-20." in dropped
        assert dropped.count("\n") == at_peak.count("\n"), "peak hold must not add rows"


async def test_meter_resets_peak_after_monitor_stops():
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        app.start_monitor(100e6, DemodMode.WFM, muted=True, enable_recorder=False)
        app.post_message(RadioTuiApp.RssiUpdate(-15))
        await pilot.pause(0.3)
        assert app._peak_rssi > -100
        app.stop_monitor()
        await pilot.pause(0.1)
        assert app._peak_rssi == -120.0
