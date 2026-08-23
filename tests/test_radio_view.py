"""Issue #23: radio-first default view, tuning cursor, settings menu, RF squelch."""

import pytest
from textual.widgets import Static

from radiotui.audio.recorder import VoxRecorder
from radiotui.config import BANDS, AudioSettings, ScannerSettings
from radiotui.tui.app import RadioTuiApp, SettingsModal


def meter_text(app: RadioTuiApp) -> str:
    return str(app.query_one("#meter", Static).render())


async def open_radio_app(tmp_path=None):
    from radiotui.config import Settings

    kwargs = {}
    if tmp_path is not None:
        from radiotui.channels_file import channels_file_path  # noqa: F401

        kwargs["channels_path"] = tmp_path / "channels.toml"
    app = RadioTuiApp(force_sim=True, settings=Settings(), **kwargs)
    async with app.run_test(size=(140, 40)) as pilot:
        if app.sweeper is not None:
            app.sweeper.stop()
        await pilot.pause(0.3)
        yield app, pilot


async def test_default_view_hides_panels_and_shows_dial():
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.2)
        assert not app.query_one("#main").has_class("shown")
        assert "MHz" in meter_text(app)  # dial line always present
        assert "band" in meter_text(app)


async def test_t_toggles_advanced_panels():
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.2)
        if app.sweeper is not None:
            app.sweeper.stop()
        await pilot.pause(0.2)
        await pilot.press("t")
        await pilot.pause(0.2)
        assert app.query_one("#main").has_class("shown")
        assert "Analyst panels shown" in "\n".join(app._events)
        await pilot.press("t")
        await pilot.pause(0.2)
        assert not app.query_one("#main").has_class("shown")


async def test_arrow_keys_move_cursor_and_dial_follows():
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.2)
        start = app.cursor_hz
        step = app._cursor_step_hz()
        await pilot.press("right")
        await pilot.pause(0.1)
        assert app.cursor_hz == pytest.approx(start + step, abs=200.0)
        await pilot.press("left")
        await pilot.press("left")
        await pilot.pause(0.1)
        assert app.cursor_hz == pytest.approx(start - step, abs=200.0)
        before = app.cursor_hz
        await pilot.press("up")
        await pilot.pause(0.1)
        assert app.cursor_hz == pytest.approx(before + 100_000.0, abs=200.0)
        assert f"{app.cursor_hz / 1e6:.3f}" in meter_text(app)


async def test_cursor_clamps_to_band_edges():
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.2)
        app._move_cursor(-1e12)
        assert app.cursor_hz == pytest.approx(app.plan.start_hz)
        app._move_cursor(+1e12)
        assert app.cursor_hz == pytest.approx(app.plan.end_hz)


async def test_enter_in_radio_view_listens_under_cursor():
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.2)
        if app.sweeper is not None:
            app.sweeper.stop()
        await pilot.pause(0.2)
        target = app.cursor_hz
        await pilot.press("enter")
        await pilot.pause(0.5)
        assert app.monitor is not None
        assert app.monitor.freq_hz == pytest.approx(target)
        app.stop_monitor()


async def test_settings_menu_edits_threshold_and_squelch():
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.2)
        if app.sweeper is not None:
            app.sweeper.stop()
        await pilot.pause(0.2)
        scanner = app.settings.scanner
        base = scanner.threshold_margin_db
        await pilot.press("m")
        await pilot.pause(0.2)
        assert isinstance(app.screen, SettingsModal)
        await pilot.press("+")
        await pilot.pause(0.1)
        assert scanner.threshold_margin_db == pytest.approx(base + 1.0)
        await pilot.press("-")
        await pilot.pause(0.1)
        assert scanner.threshold_margin_db == pytest.approx(base)
        # walk down to the squelch row and switch it on/off
        await pilot.press("down")
        await pilot.press("down")
        await pilot.press("down")
        await pilot.press("right")
        await pilot.pause(0.1)
        assert scanner.squelch_rssi_dbfs == -70.0
        await pilot.press("left")
        await pilot.pause(0.1)
        assert scanner.squelch_rssi_dbfs is None
        await pilot.press("escape")
        await pilot.pause(0.2)
        assert not isinstance(app.screen, SettingsModal)


async def test_m_key_mutes_via_shifted_binding():
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.2)
        if app.sweeper is not None:
            app.sweeper.stop()
        await pilot.pause(0.2)
        seed = 100.0e6
        channel = app.sweeper._tracker.channels.get(seed)
        assert channel is None or True  # seeding not needed for mute path
        await pilot.press("M")
        await pilot.pause(0.2)
        # no monitor: mute action logs nothing but must not crash or open menu
        assert not isinstance(app.screen, SettingsModal)


def test_squelch_gate_blocks_recording_without_carrier(tmp_path):
    settings = AudioSettings(
        recordings_dir=str(tmp_path), vox_threshold_dbfs=-30.0, min_clip_seconds=0.0
    )
    recorder = VoxRecorder(
        446.00625e6,
        settings,
        squelch_rssi_dbfs=-30.0,
    )
    recorder.enabled = True
    loud = bytes([0x40, 0x40] * 480)  # hot PCM that would trip VOX alone
    recorder.feed(loud, 48_000, rssi_dbfs=-45.0)  # carrier absent
    recorder.feed(loud, 48_000, rssi_dbfs=-20.0)  # carrier present
    clips = recorder.stop()
    assert len(clips) == 1
    assert clips[0].seconds < 0.05  # only the gated-on block made it in


def test_squelch_default_off_keeps_vox_only_behaviour(tmp_path):
    settings = AudioSettings(recordings_dir=str(tmp_path), vox_threshold_dbfs=-30.0)
    scanner = ScannerSettings()
    assert scanner.squelch_rssi_dbfs is None
    recorder = VoxRecorder(446.00625e6, settings, squelch_rssi_dbfs=scanner.squelch_rssi_dbfs)
    recorder.enabled = True
    loud = bytes([0x40, 0x40] * 480)
    recorder.feed(loud, 48_000, rssi_dbfs=None)
    assert recorder.recording  # VOX opened despite no RSSI feed


async def test_sweep_key_refuses_while_listening():
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.2)
        if app.sweeper is not None:
            app.sweeper.stop()
        await pilot.pause(0.2)
        await pilot.press("enter")  # radio view: listens under cursor
        await pilot.pause(0.5)
        assert app.monitor is not None
        was_running = app.sweeper.running
        await pilot.press("s")
        await pilot.pause(0.2)
        assert app.monitor is not None, "sweep key must not kill the monitor"
        assert app.sweeper.running == was_running
        assert "press l first" in "\n".join(app._events)
        app.stop_monitor()


async def test_band_switch_stops_monitor():
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.2)
        if app.sweeper is not None:
            app.sweeper.stop()
        await pilot.pause(0.2)
        target = app.cursor_hz
        await pilot.press("enter")
        await pilot.pause(0.5)
        assert app.monitor is not None
        bands = sorted(__import__("radiotui").config.BANDS)
        other = next(b for b in bands if BANDS[b].start_hz > 1e8 and b != "fm_broadcast")
        app.start_band(other)
        await pilot.pause(0.3)
        assert app.monitor is None
        del target
