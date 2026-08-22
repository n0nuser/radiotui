"""Issue #13: stable channel table — frequency sort default, `,` toggles, in-place updates."""

from textual.widgets import DataTable

from radiotui.core.models import Channel, DemodMode
from radiotui.tui.app import RadioTuiApp

T0 = 1_000_000.0


def make_channel(freq_hz: float, peak_db: float) -> Channel:
    return Channel(
        center_hz=freq_hz,
        bandwidth_hz=12_500.0,
        peak_db=peak_db,
        snr_db=peak_db - 10,
        demod=DemodMode.WFM,
        first_seen=T0,
        last_seen=T0 + 5,
    )


def row_keys(app: RadioTuiApp) -> list[str]:
    table = app.query_one("#channels", DataTable)
    return [str(row_key.value) for row_key in table.rows]


async def test_default_sort_is_by_frequency_and_stable():
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.2)
        channels = [
            make_channel(101.0e6, -20),  # loudest but not first
            make_channel(98.0e6, -40),
            make_channel(106.0e6, -30),
        ]
        app.refresh_table(channels)
        await pilot.pause(0.1)
        assert row_keys(app) == ["98000000.0", "101000000.0", "106000000.0"]
        # peak values shuffle between refreshes; order must stay put
        channels[1].peak_db = -15
        channels[0].peak_db = -45
        app.refresh_table(channels)
        await pilot.pause(0.1)
        assert row_keys(app) == ["98000000.0", "101000000.0", "106000000.0"]


async def test_comma_toggles_peak_sort_with_header_marker():
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.2)
        if app.sweeper is not None:
            app.sweeper.stop()
        await pilot.pause(0.3)
        table = app.query_one("#channels", DataTable)
        app.refresh_table([make_channel(101.0e6, -20), make_channel(98.0e6, -40)])
        assert "▾" in str(table.columns["freq"].label)
        await pilot.press(",")
        await pilot.pause(0.1)
        assert app.sort_by_peak
        assert row_keys(app) == ["101000000.0", "98000000.0"]  # loudest first
        assert "▾" in str(table.columns["peak"].label)
        assert "▾" not in str(table.columns["freq"].label)
        await pilot.press(",")
        await pilot.pause(0.1)
        assert row_keys(app) == ["98000000.0", "101000000.0"]
        assert "▾" in str(table.columns["freq"].label)


async def test_rows_update_in_place_without_rebuild(monkeypatch):
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.2)
        table = app.query_one("#channels", DataTable)
        rebuilds = []
        original_clear = DataTable.clear

        def counting_clear(*args, **kwargs):
            rebuilds.append(1)
            return original_clear(*args, **kwargs)

        monkeypatch.setattr(DataTable, "clear", counting_clear)
        channels = {
            101.0e6: make_channel(101.0e6, -20),
            98.0e6: make_channel(98.0e6, -40),
        }
        app.refresh_table(list(channels.values()))
        assert len(rebuilds) == 1  # initial population

        # same membership, only a value changes: no structural touch
        channels[98.0e6].peak_db = -35.5
        app.refresh_table(list(channels.values()))
        assert len(rebuilds) == 1
        rendered = table.get_row("98000000.0")
        assert "-35.5" in rendered

        # a new channel appears: structure changes once
        channels[105.0e6] = make_channel(105.0e6, -25)
        app.refresh_table(list(channels.values()))
        assert len(rebuilds) == 2


async def test_cursor_follows_same_channel_across_refreshes():
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.2)
        if app.sweeper is not None:
            app.sweeper.stop()  # keep the live sweeper from rewriting the table
        await pilot.pause(0.3)
        table = app.query_one("#channels", DataTable)
        channels = [
            make_channel(98.0e6, -40),
            make_channel(101.0e6, -20),
            make_channel(106.0e6, -30),
        ]
        app.refresh_table(channels)
        await pilot.pause(0.1)
        table.move_cursor(row=1, column=0)
        await pilot.pause(0.05)
        selected = app.selected_key()
        assert selected == 101.0e6

        # new channel slots in below the selection; selection must not move
        channels.append(make_channel(103.0e6, -50))
        app.refresh_table(channels)
        await pilot.pause(0.1)
        assert app.selected_key() == selected

        # toggling to peak sort moves the cursor with its channel
        app.action_toggle_sort()
        await pilot.pause(0.1)
        assert app.selected_key() == selected
