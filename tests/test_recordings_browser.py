"""Issue #20: clip sidecar metadata, minimum clip length, and the clips pane."""

import json
import wave
from pathlib import Path

import numpy as np
from textual.widgets import DataTable, RichLog, Static

from radiotui.audio.demod import audio_to_pcm16
from radiotui.audio.recorder import ClipInfo, VoxRecorder
from radiotui.config import AudioSettings, Settings
from radiotui.tui import app as tui_app_module
from radiotui.tui.app import RadioTuiApp

T0 = 1_000_000.0


def make_recorder(tmp_path, **audio_kwargs) -> VoxRecorder:
    settings = AudioSettings(
        recordings_dir=str(tmp_path),
        vox_threshold_dbfs=-30.0,
        vox_hang_ms=50,
        **audio_kwargs,
    )
    return VoxRecorder(
        446.00625e6,
        settings,
        context={
            "demod": "nfm",
            "band": "PMR446",
            "hardware": {"gain_db": 20.0, "freq_correction_ppm": 1},
        },
    )


def audio_block(samples: int) -> bytes:
    return audio_to_pcm16(np.full(samples, 0.5))


def feed_seconds(recorder: VoxRecorder, seconds: float, rssi_dbfs: float = -20.0) -> None:
    block = audio_block(int(48_000 * min(seconds, 0.05)))
    feeds = max(1, int(seconds * 48_000 / len(block) * 2))
    for _ in range(feeds):
        recorder.feed(block, 48_000, rssi_dbfs=rssi_dbfs)


def write_wav(path: Path, seconds: float = 0.3, rate: int = 48_000) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * int(seconds * rate))


# ---- sidecar metadata + minimum length --------------------------------------


def test_clip_gets_sidecar_metadata(tmp_path):
    recorder = make_recorder(tmp_path, min_clip_seconds=0.0)
    recorder.enabled = True
    feed_seconds(recorder, 0.2, rssi_dbfs=-18.0)
    clips = recorder.stop()
    assert len(clips) == 1
    sidecar = clips[0].path.with_suffix(".json")
    assert sidecar.exists()
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["freq_hz"] == 446.00625e6
    assert payload["demod"] == "nfm"
    assert payload["band"] == "PMR446"
    assert payload["duration_s"] > 0.15
    assert payload["peak_rssi_dbfs"] == -18.0
    assert payload["hardware"]["gain_db"] == 20.0


def test_clip_info_carries_rssi_stats(tmp_path):
    recorder = make_recorder(tmp_path, min_clip_seconds=0.0)
    recorder.enabled = True
    feed_seconds(recorder, 0.1, rssi_dbfs=-30.0)
    feed_seconds(recorder, 0.1, rssi_dbfs=-10.0)
    (clip,) = recorder.stop()
    assert clip.peak_rssi_dbfs == -10.0
    assert -30.0 <= clip.mean_rssi_dbfs <= -10.0
    assert clip.started_at > 0 and clip.ended_at >= clip.started_at
    assert clip.demod == "nfm" and clip.band == "PMR446"


def test_clips_below_minimum_are_discarded(tmp_path):
    recorder = make_recorder(tmp_path, min_clip_seconds=0.7)
    recorder.enabled = True
    feed_seconds(recorder, 0.3)
    clips = recorder.stop()
    assert clips == []
    assert list(tmp_path.glob("*.wav")) == []
    assert list(tmp_path.glob("*.json")) == []


def test_clips_at_or_above_minimum_survive(tmp_path):
    recorder = make_recorder(tmp_path, min_clip_seconds=0.7)
    recorder.enabled = True
    feed_seconds(recorder, 0.8)
    (clip,) = recorder.stop()
    assert clip.seconds >= 0.7
    assert clip.path.exists()


# ---- TUI clips pane ---------------------------------------------------------


class StubPlayer:
    started: list["StubPlayer"] = []

    def __init__(self, rate_hz: int = 48_000) -> None:
        self.rate_hz = rate_hz
        self.backend = "stub"
        self.written: list[bytes] = []
        self.stopped = False
        StubPlayer.started.append(self)

    def start(self) -> bool:
        return True

    def write(self, pcm: bytes) -> None:
        self.written.append(pcm)

    def stop(self) -> None:
        self.stopped = True

    @property
    def running(self) -> bool:
        return not self.stopped


def make_clip(tmp_path: Path, freq_hz: float = 145.5e6) -> ClipInfo:
    path = tmp_path / f"{freq_hz / 1e6:.4f}MHz_20260822_120000.wav"
    write_wav(path)
    return ClipInfo(
        path=path,
        freq_hz=freq_hz,
        seconds=0.3,
        started_at=T0,
        ended_at=T0 + 0.3,
        peak_rssi_dbfs=-14.0,
        mean_rssi_dbfs=-22.0,
        demod="nfm",
        band="2m Amateur",
    )


async def make_app_with_clip_player_stub(tmp_path, monkeypatch):
    monkeypatch.setattr(tui_app_module, "AudioPlayer", StubPlayer)
    StubPlayer.started.clear()
    return RadioTuiApp(force_sim=True, settings=Settings())


async def test_toggle_shows_populated_pane_and_hides_again(tmp_path, monkeypatch):
    monkeypatch.setattr(tui_app_module, "AudioPlayer", StubPlayer)
    StubPlayer.started.clear()
    app = RadioTuiApp(force_sim=True, settings=Settings())
    async with app.run_test(size=(140, 40)) as pilot:
        if app.sweeper is not None:
            app.sweeper.stop()
        await pilot.pause(0.3)
        app.session_clips.append(make_clip(tmp_path))
        await pilot.press("c")
        await pilot.pause(0.2)
        table = app.query_one("#clips", DataTable)
        assert table.has_class("shown")
        assert len(table.rows) == 1
        row = table.get_row_at(0)
        assert row[1] == "145.5000"
        assert row[3] == "-14"
        await pilot.press("c")
        await pilot.pause(0.2)
        assert not app.query_one("#clips", DataTable).has_class("shown")


async def test_enter_on_clip_row_replays_without_starting_monitor(tmp_path, monkeypatch):
    monkeypatch.setattr(tui_app_module, "AudioPlayer", StubPlayer)
    StubPlayer.started.clear()
    app = RadioTuiApp(force_sim=True, settings=Settings())
    async with app.run_test(size=(140, 40)) as pilot:
        if app.sweeper is not None:
            app.sweeper.stop()
        await pilot.pause(0.3)
        clip = make_clip(tmp_path)
        app.session_clips.append(clip)
        await pilot.press("c")
        await pilot.pause(0.2)
        await pilot.press("enter")
        await pilot.pause(0.3)
        assert StubPlayer.started, "replay must spin up an audio player"
        player = StubPlayer.started[-1]
        assert player.written, "replay must stream the wav content"
        assert app.monitor is None
        app._stop_replay()
        assert player.stopped


async def test_status_line_reports_session_clip_count(tmp_path):
    app = RadioTuiApp(force_sim=True, settings=Settings())
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.2)
        if app.sweeper is not None:
            app.sweeper.stop()
        await pilot.pause(0.2)
        app.session_clips.append(make_clip(tmp_path))
        app.refresh_status()
        meter = str(app.query_one("#meter", Static).render())
        assert "1 clip" in meter


async def test_startup_reports_resolved_recordings_directory(tmp_path):
    app = RadioTuiApp(force_sim=True, settings=Settings())
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.2)
        log_text = "\n".join(str(line) for line in app.query_one("#log", RichLog).lines)
        assert "Recordings:" in log_text
        assert str(Path("recordings").expanduser().resolve()) in log_text
