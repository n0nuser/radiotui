"""The radio view must acknowledge what it is doing.

Two reports from real use: pressing the recordings key appeared to do nothing,
and a sweep gave no sign of progress or completion, so it was impossible to
tell whether the app was working or hung.
"""

from textual.widgets import DataTable, Static

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
    app = RadioTuiApp(force_sim=True, start_sweeper=True)
    async with app.run_test(size=(140, 40)) as pilot:
        for _ in range(60):
            await pilot.pause(0.1)
            if "sweep #1 complete" in log_text(app):
                break
        assert "sweep #1 complete" in log_text(app)
        app.sweeper.stop()
