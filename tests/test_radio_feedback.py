"""The radio view must acknowledge what it is doing.

Two reports from real use: pressing the recordings key appeared to do nothing,
and a sweep gave no sign of progress or completion, so it was impossible to
tell whether the app was working or hung.
"""

import numpy as np
from textual.widgets import DataTable, Static

from radiotui.core.models import ScanState, SpectrumFrame
from radiotui.tui.app import RadioTuiApp


def meter_text(app: RadioTuiApp) -> str:
    return str(app.query_one("#meter", Static).render())


def log_text(app: RadioTuiApp) -> str:
    return "\n".join(app._events)


async def test_recordings_key_reveals_the_panel_from_the_radio_view():
    """`c` used to add a class to a table whose container was display:none."""
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.2)
        assert not app.query_one("#main").has_class("shown")

        await pilot.press("c")
        await pilot.pause(0.2)

        clips = app.query_one("#clips", DataTable)
        assert clips.has_class("shown")
        assert app.query_one("#main").has_class("shown"), "the container stayed hidden"
        assert clips.region.height > 0, "the panel occupies no space, so nothing is visible"
        assert "Recordings" in log_text(app)


async def test_recordings_key_explains_itself_when_there_is_nothing_yet():
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.2)
        await pilot.press("c")
        await pilot.pause(0.2)
        assert "none yet" in log_text(app)
        assert "WAV files" in log_text(app)


async def test_status_bar_shows_sweep_progress_and_paused_state():
    app = RadioTuiApp(force_sim=True, start_sweeper=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.5)
        assert "hop" in meter_text(app), "no hop counter while sweeping"

        app.sweeper.stop()
        app.refresh_status()
        await pilot.pause(0.1)
        assert "paused" in meter_text(app)


async def test_completed_sweep_is_announced():
    """A finished pass must say so, with what it found.

    The frame is delivered straight to the queue rather than waiting on a live
    sweep: what matters here is that a completed sweep is announced, and
    simulator throughput varies far too much across machines to gate on.
    """
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.2)
        freqs = np.linspace(87.5e6, 108e6, 512)
        app.queue.put(
            ScanState(
                frame=SpectrumFrame(freqs_hz=freqs, power_db=np.full_like(freqs, -55.0)),
                channels=[],
                noise_floor_db=-62.0,
                threshold_db=-53.0,
                sweeps_done=7,
                elapsed=3.2,
            )
        )
        await pilot.pause(0.4)

        log = log_text(app)
        assert "sweep #7 complete" in log, log
        assert "floor -62.0 dB" in log
        assert "3.2 s" in log
