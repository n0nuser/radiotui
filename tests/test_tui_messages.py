"""Regression tests for TUI custom message dispatch (#7).

Textual namespaces messages defined inside an App subclass, so plain
``on_rssi_update``-style handlers never fire. The app must dispatch via
the ``@on`` decorator; these tests pin that behavior.
"""

import asyncio
import threading
import time

from textual.widgets import Static

from radiotui.core.models import DemodMode
from radiotui.scanner.monitor import ChannelMonitor
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


async def test_monitor_error_on_dead_thread_drops_monitor_and_notifies() -> None:
    """A fatal reader-thread death must not leave the UI claiming to listen.

    Regression test: previously ``self.monitor`` stayed set after the reader
    thread died, so the status bar kept showing "listening" with a frozen
    meter and the only trace of the failure was a log line that is easy to
    miss when the log pane is hidden.
    """
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        app.start_monitor(100_000_000, DemodMode.WFM, muted=True, enable_recorder=False)
        await pilot.pause(0.2)
        assert app.monitor is not None
        app.monitor._thread = None  # simulate the reader/consumer thread having died
        app.post_message(RadioTuiApp.MonitorError("read failed: device gone"))
        await pilot.pause(0.5)
        assert app.monitor is None
        meter = str(app.query_one("#meter", Static).render())
        assert "listening" not in meter


async def test_monitor_error_while_alive_does_not_drop_monitor() -> None:
    """A non-fatal warning (e.g. no audio backend) must not tear down a live monitor."""
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        app.start_monitor(100_000_000, DemodMode.WFM, muted=True, enable_recorder=False)
        await pilot.pause(0.2)
        assert app.monitor is not None
        app.post_message(RadioTuiApp.MonitorError("no audio backend found"))
        await pilot.pause(0.5)
        assert app.monitor is not None
        app.stop_monitor()


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


async def test_quit_while_listening_stops_monitor_and_closes_device() -> None:
    """Workers no longer run once the app unmounts; teardown must still happen."""
    app = RadioTuiApp(force_sim=True)
    before = set(threading.enumerate())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        app.start_monitor(100_000_000, DemodMode.WFM, muted=True, enable_recorder=False)
        await pilot.pause(0.3)
    deadline = time.time() + 6.0
    while time.time() < deadline:
        leaked = [
            t
            for t in set(threading.enumerate()) - before
            if t.name.startswith(("monitor-", "reader-"))
        ]
        if not leaked and app.device is None:
            break
        time.sleep(0.05)
    assert not leaked
    assert app.device is None


async def test_quit_teardown_is_bounded_when_monitor_stop_hangs(monkeypatch) -> None:
    """A wedged reader (USB read, stalled audio pipe) must not hang the process on quit."""
    release = threading.Event()
    real_stop = ChannelMonitor.stop

    def wedged_stop(self) -> None:
        release.wait()
        real_stop(self)

    monkeypatch.setattr(ChannelMonitor, "stop", wedged_stop)
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        app.start_monitor(100_000_000, DemodMode.WFM, muted=True, enable_recorder=False)
        await pilot.pause(0.3)
    t0 = time.monotonic()
    assert not app.wait_for_teardown(timeout_s=0.1)
    assert time.monotonic() - t0 < 3.0
    release.set()
    assert app.wait_for_teardown(timeout_s=6.0)
    assert app.device is None
