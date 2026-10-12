"""Issue #85: change the demod mode and the SSB clarifier while listening."""

from radiotui.core.models import DemodMode
from radiotui.tui.app import RadioTuiApp
from tests.tui_waits import wait_listening, wait_until


def demod(app: RadioTuiApp) -> DemodMode | None:
    return app.session.status().listen_demod


def clarifier(app: RadioTuiApp) -> float | None:
    listening = app.session.status().listening
    return listening.clarifier_hz if listening is not None else None


async def test_d_cycles_the_demod_mode_on_the_same_frequency():
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.2)
        app.start_monitor(7.1e6, DemodMode.AM, muted=True, enable_recorder=False)
        assert await wait_listening(pilot, app, 7.1e6)
        await pilot.press("d")
        assert await wait_until(pilot, lambda: demod(app) is DemodMode.USB)
        await pilot.press("d")
        assert await wait_until(pilot, lambda: demod(app) is DemodMode.LSB)
        assert app.session.status().listen_freq_hz == 7.1e6


async def test_clarifier_keys_step_without_a_retune_and_reset_on_new_listen():
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.2)
        app.start_monitor(7.1e6, DemodMode.LSB, muted=True, enable_recorder=False)
        assert await wait_listening(pilot, app, 7.1e6)
        await pilot.press("right_parenthesis")
        await pilot.press("right_parenthesis")
        await pilot.press("left_parenthesis")
        assert await wait_until(pilot, lambda: clarifier(app) == 10.0)
        assert "RIT +10 Hz" in str(app.query_one("#meter").render())
        app.start_monitor(7.05e6, DemodMode.LSB, muted=True, enable_recorder=False)
        assert await wait_listening(pilot, app, 7.05e6)
        assert clarifier(app) == 0.0
        await pilot.press("right_parenthesis")
        assert await wait_until(pilot, lambda: clarifier(app) == 10.0)
