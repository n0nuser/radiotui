"""Regression tests for TUI custom message dispatch (#7).

Textual namespaces messages defined inside an App subclass, so plain
``on_rssi_update``-style handlers never fire. The app must dispatch via
the ``@on`` decorator; these tests pin that behavior.
"""

import threading
import time

from textual.widgets import Static

from radiotui.core.models import DemodMode
from radiotui.scanner.monitor import ChannelMonitor
from radiotui.scanner.session import Rssi, SessionError, TunerSession
from radiotui.sdr.simulator import SimulatedDevice
from radiotui.tui.app import RadioTuiApp
from tests.tui_waits import listen_hz, wait_listening, wait_not_listening, wait_until


def update(event) -> RadioTuiApp.SessionUpdate:
    return RadioTuiApp.SessionUpdate(event)


async def test_rssi_update_reaches_handler() -> None:
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        app.post_message(update(Rssi(-33)))
        await pilot.pause(0.5)
        assert app.last_rssi == -33


async def test_rssi_update_renders_meter_bar() -> None:
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        app.post_message(update(Rssi(-20)))
        await pilot.pause(0.5)
        meter = str(app.query_one("#meter", Static).render())
        assert "RSSI" in meter
        assert "-20.0" in meter


async def test_session_error_reaches_log() -> None:
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        app.post_message(update(SessionError("boom")))
        await pilot.pause(0.5)
        assert any("boom" in line for line in app._events)


async def test_live_monitor_thread_updates_meter() -> None:
    """End-to-end: monitor thread -> session event -> handler -> meter."""
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        app.start_monitor(100_000_000, DemodMode.WFM, muted=True, enable_recorder=False)
        assert await wait_until(pilot, lambda: app.last_rssi is not None)
        meter = str(app.query_one("#meter", Static).render())
        app.stop_monitor()
        assert "RSSI" in meter


async def test_dead_monitor_stops_claiming_to_listen(monkeypatch) -> None:
    """A reader that dies must not leave the status bar saying "listening"."""
    unplugged = threading.Event()
    real_read = SimulatedDevice.read_samples

    def read_samples(self, count):
        if unplugged.is_set():
            raise OSError("device gone")
        return real_read(self, count)

    monkeypatch.setattr(SimulatedDevice, "read_samples", read_samples)
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        app.start_monitor(100_000_000, DemodMode.WFM, muted=True, enable_recorder=False)
        assert await wait_listening(pilot, app)
        unplugged.set()
        assert await wait_not_listening(pilot, app)
        await pilot.pause(0.2)
        meter = str(app.query_one("#meter", Static).render())
        assert "listening" not in meter
        assert any("device gone" in line for line in app._events)


async def test_session_error_while_alive_does_not_drop_monitor() -> None:
    """A non-fatal warning (e.g. no audio backend) must not tear down a live monitor."""
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        app.start_monitor(100_000_000, DemodMode.WFM, muted=True, enable_recorder=False)
        assert await wait_listening(pilot, app)
        app.post_message(update(SessionError("no audio backend found")))
        await pilot.pause(0.5)
        assert listen_hz(app) == 100_000_000
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


def record_close(monkeypatch) -> threading.Event:
    closed = threading.Event()
    monkeypatch.setattr(SimulatedDevice, "close", lambda self: closed.set())
    return closed


async def test_quit_while_listening_stops_monitor_and_closes_device(monkeypatch) -> None:
    closed = record_close(monkeypatch)
    app = RadioTuiApp(force_sim=True)
    before = set(threading.enumerate())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        app.start_monitor(100_000_000, DemodMode.WFM, muted=True, enable_recorder=False)
        assert await wait_listening(pilot, app)
    assert app.wait_for_teardown(timeout_s=TunerSession.CLOSE_WAIT_S)
    leaked = [
        t for t in set(threading.enumerate()) - before if t.name.startswith(("monitor-", "reader-"))
    ]
    assert not leaked
    assert closed.is_set()


async def test_quit_teardown_is_bounded_when_monitor_stop_hangs(monkeypatch) -> None:
    """A wedged reader (USB read, stalled audio pipe) must not hang the process on quit."""
    closed = record_close(monkeypatch)
    release = threading.Event()
    real_stop = ChannelMonitor.stop

    def wedged_stop(self) -> bool:
        release.wait()
        return real_stop(self)

    monkeypatch.setattr(ChannelMonitor, "stop", wedged_stop)
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        app.start_monitor(100_000_000, DemodMode.WFM, muted=True, enable_recorder=False)
        assert await wait_listening(pilot, app)
    t0 = time.monotonic()
    assert not app.wait_for_teardown(timeout_s=0.1)
    assert time.monotonic() - t0 < 3.0
    release.set()
    assert app.wait_for_teardown(timeout_s=TunerSession.CLOSE_WAIT_S)
    assert closed.is_set()


async def test_late_status_refresh_after_shutdown_is_harmless() -> None:
    """RSSI and clip messages can arrive after the main screen is gone."""
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.1)
    app.refresh_status()
