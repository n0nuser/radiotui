"""Regression tests for TUI custom message dispatch (#7).

Textual namespaces messages defined inside an App subclass, so plain
``on_rssi_update``-style handlers never fire. The app must dispatch via
the ``@on`` decorator; these tests pin that behavior.
"""

import asyncio

from textual.widgets import Static

from radiotui.core.models import DemodMode
from radiotui.tui.app import RadioTuiApp


async def test_rssi_update_reaches_handler() -> None:
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        app.post_message(RadioTuiApp.RssiUpdate(-33))
        await pilot.pause(0.5)
        assert app.last_rssi == -33


async def test_rssi_update_renders_meter_bar() -> None:
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        app.post_message(RadioTuiApp.RssiUpdate(-20))
        await pilot.pause(0.5)
        meter = str(app.query_one("#meter", Static).render())
        assert "RSSI" in meter
        assert "-20.0" in meter


async def test_monitor_error_reaches_log() -> None:
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        count_before = len(app._events)
        app.post_message(RadioTuiApp.MonitorError("boom"))
        await pilot.pause(0.5)
        assert len(app._events) > count_before
        assert "boom" in app._events[-1]


async def test_live_monitor_thread_updates_meter() -> None:
    """End-to-end: monitor thread -> post_message -> handler -> meter."""
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        assert app.device is not None
        app.start_monitor(100_000_000, DemodMode.WFM, muted=True, enable_recorder=False)
        deadline = asyncio.get_running_loop().time() + 5.0
        while app.last_rssi is None:
            await pilot.pause(0.05)
            if asyncio.get_running_loop().time() > deadline:
                break
        rssi_seen = app.last_rssi
        meter = str(app.query_one("#meter", Static).render())
        app.stop_monitor()
        assert rssi_seen is not None
        assert "RSSI" in meter


async def test_autonomous_toggle_flips_setting_and_status() -> None:
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        assert not app.settings.scanner.autonomous
        await pilot.press("o")
        await pilot.pause(0.2)
        assert app.settings.scanner.autonomous
        meter = str(app.query_one("#meter", Static).render())
        assert "AUTO" in meter
        await pilot.press("o")
        await pilot.pause(0.2)
        assert not app.settings.scanner.autonomous
