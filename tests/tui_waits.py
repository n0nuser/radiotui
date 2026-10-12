"""Waits for TUI tests: the tuner session acts on its own thread, so a test
waits for the state it expects instead of reading it straight after a key."""

import time

from radiotui.tui.app import RadioTuiApp


async def wait_until(pilot, predicate, timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        await pilot.pause(0.05)
    return False


def listen_hz(app: RadioTuiApp) -> float | None:
    """The frequency being listened to, or None."""
    status = app.session.status()
    return status.listen_freq_hz if status.listening is not None else None


async def wait_listening(pilot, app: RadioTuiApp, freq_hz: float | None = None) -> bool:
    return await wait_until(
        pilot,
        lambda: listen_hz(app) is not None and (freq_hz is None or listen_hz(app) == freq_hz),
    )


async def wait_not_listening(pilot, app: RadioTuiApp) -> bool:
    return await wait_until(pilot, lambda: listen_hz(app) is None)


async def wait_sweeping(pilot, app: RadioTuiApp, sweeping: bool = True) -> bool:
    return await wait_until(pilot, lambda: app.session.status().sweeping == sweeping)
