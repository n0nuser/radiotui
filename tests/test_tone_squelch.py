"""Issue #86 / ADR 0017: tone squelch through monitor, recorder, session and TUI."""

import json
import time

import numpy as np
import pytest

from radiotui.audio.recorder import VoxRecorder
from radiotui.audio.tones import WINDOW_S
from radiotui.channels_file import (
    Bookmark,
    ChannelsFileError,
    UserChannels,
    parse_user_channels,
    serialize_user_channels,
)
from radiotui.config import AudioSettings, Settings
from radiotui.core.models import Channel, DemodMode
from radiotui.export import export_channels
from radiotui.scanner.monitor import NEVER_VOICED, ChannelMonitor
from radiotui.scanner.session import ToneDetected, TunerSession
from radiotui.sdr.simulator import SimulatedDevice
from radiotui.tui.app import RadioTuiApp
from tests.tui_waits import wait_listening, wait_until

FS = 1_024_000.0


class ToneDevice(SimulatedDevice):
    """NFM voice plus a CTCSS tone, delivered in real time; 2 s loops cleanly."""

    def __init__(self, tone_hz: float = 88.5) -> None:
        super().__init__(carriers=[])
        t = np.arange(int(FS * 2.0)) / FS
        modulation = 2_500.0 * np.sin(2 * np.pi * 1_000 * t) + 750.0 * np.sin(
            2 * np.pi * tone_hz * t
        )
        rng = np.random.default_rng(2)
        noise = 0.03 * (rng.normal(size=len(t)) + 1j * rng.normal(size=len(t)))
        self._iq = np.exp(2j * np.pi * np.cumsum(modulation) / FS) + noise
        self._pos = 0

    def read_samples(self, count: int) -> np.ndarray:
        time.sleep(count / FS)
        idx = (self._pos + np.arange(count)) % len(self._iq)
        self._pos = (self._pos + count) % len(self._iq)
        return self._iq[idx]


class PeakPlayer:
    running = True
    backend = "test"

    def __init__(self) -> None:
        self.peak = 0
        self.frames = 0

    def start(self) -> bool:
        return True

    def stop(self) -> None:
        pass

    def write(self, pcm: bytes) -> None:
        samples = np.frombuffer(pcm, dtype="<i2")
        self.frames += len(samples)
        self.peak = max(self.peak, int(np.abs(samples).max(initial=0)))


def run_monitor(tone_squelch, seconds=3.0):
    settings = Settings()
    settings.audio.vox_threshold_dbfs = -60.0
    player = PeakPlayer()
    tones: list = []
    monitor = ChannelMonitor(
        ToneDevice(), 446.00625e6, DemodMode.NFM, settings, player=player, on_tone=tones.append
    )
    monitor.set_tone_squelch(tone_squelch)
    monitor.start()
    deadline = time.monotonic() + 15.0
    while player.frames < int(48_000 * seconds) and time.monotonic() < deadline:
        time.sleep(0.05)
    status = monitor.status()
    assert monitor.stop()
    return player, monitor, status, tones


def test_monitor_reports_the_detected_tone():
    _, _, status, tones = run_monitor(None)
    assert status.tone == "88.5"
    assert "88.5" in tones


def test_a_wrong_tone_keeps_the_speaker_silent_and_unvoiced():
    player, monitor, status, _ = run_monitor("100.0")
    assert status.tone_squelch == "100.0"
    assert player.frames > 0 and player.peak == 0
    assert monitor.silent_for() == NEVER_VOICED


@pytest.mark.parametrize("selected", ["88.5", "any"])
def test_the_matching_tone_opens(selected):
    player, monitor, _, _ = run_monitor(selected)
    assert player.peak > 1_000
    assert monitor.silent_for() < NEVER_VOICED


def tone_pcm(seconds: float) -> bytes:
    t = np.arange(int(48_000 * seconds)) / 48_000
    return (0.5 * np.sin(2 * np.pi * 700 * t) * 32767).astype("<i2").tobytes()


def test_pre_roll_keeps_the_start_of_a_transmission(tmp_path):
    settings = AudioSettings(recordings_dir=str(tmp_path), min_clip_seconds=0.0)
    recorder = VoxRecorder(446e6, settings)
    recorder.enabled = True
    recorder.preroll_s = WINDOW_S
    recorder.tone = "88.5"
    for _ in range(10):  # 1 s gated while the tone is being verified
        recorder.feed(tone_pcm(0.1), 48_000, squelch_open=False)
    for _ in range(10):
        recorder.feed(tone_pcm(0.1), 48_000)
    clip = recorder.stop()[0]
    assert clip.seconds >= 1.9
    sidecar = json.loads(clip.path.with_suffix(".json").read_text())
    assert sidecar["tone"] == "88.5"


def test_no_pre_roll_by_default(tmp_path):
    settings = AudioSettings(recordings_dir=str(tmp_path), min_clip_seconds=0.0)
    recorder = VoxRecorder(446e6, settings)
    recorder.enabled = True
    for _ in range(10):
        recorder.feed(tone_pcm(0.1), 48_000, squelch_open=False)
    for _ in range(10):
        recorder.feed(tone_pcm(0.1), 48_000)
    clip = recorder.stop()[0]
    assert clip.seconds < 1.5
    assert json.loads(clip.path.with_suffix(".json").read_text())["tone"] is None


def test_bookmark_tone_round_trips():
    book = UserChannels(bookmarks=[Bookmark(446.00625e6, "pmr 1", DemodMode.NFM, "D023N")])
    parsed = parse_user_channels(serialize_user_channels(book))
    assert parsed.bookmarks[0].tone == "D023N"
    assert parsed.tone_for(446.0063e6) == "D023N"
    assert parse_user_channels('[[bookmark]]\nfreq_hz = 1e6\nname = "x"').bookmarks[0].tone is None


@pytest.mark.parametrize("tone", ['"88.6"', '"D999N"', "88.5"])
def test_bad_bookmark_tone_is_rejected(tone):
    with pytest.raises(ChannelsFileError, match="tone"):
        parse_user_channels(f'[[bookmark]]\nfreq_hz = 1e6\nname = "x"\ntone = {tone}')


def test_session_applies_the_bookmark_tone_and_maps_detected_tones():
    settings = Settings()
    settings.audio.block_size = 16_384
    events = []
    session = TunerSession.start(ToneDevice(), settings, events.append)
    try:
        book = UserChannels(bookmarks=[Bookmark(446.00625e6, "pmr 1", DemodMode.NFM, "88.5")])
        session.set_user_channels(book)
        session.listen(446.00625e6, DemodMode.NFM, muted=True)
        assert wait(lambda: (st := session.status().listening) and st.tone_squelch == "88.5")
        assert wait(lambda: session.tones.get(446.00625e6) == "88.5")
        assert any(isinstance(e, ToneDetected) and e.tone == "88.5" for e in events)
    finally:
        session.close()


def wait(predicate, timeout_s: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_export_carries_the_tone(tmp_path):
    ch = Channel(446.00625e6, 12_500.0, -30.0, 20.0, DemodMode.NFM, 0.0, 0.0)
    path = export_channels([ch], tmp_path / "x.json", tones={446.0063e6: "88.5"})
    assert json.loads(path.read_text())["channels"][0]["tone"] == "88.5"


async def test_shift_t_cycles_off_any_detected(monkeypatch):
    monkeypatch.setattr("radiotui.sdr.manager.SimulatedDevice", ToneDevice)
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.2)
        app.start_monitor(446.00625e6, DemodMode.NFM, muted=True, enable_recorder=False)
        assert await wait_listening(pilot, app, 446.00625e6)

        def listening():
            return app.session.status().listening

        assert await wait_until(pilot, lambda: listening().tone == "88.5", 15.0)
        await pilot.press("T")
        assert await wait_until(pilot, lambda: listening().tone_squelch == "any")
        await pilot.press("T")
        assert await wait_until(pilot, lambda: listening().tone_squelch == "88.5")
        assert "TSQ 88.5" in str(app.query_one("#meter").render())
        await pilot.press("T")
        assert await wait_until(pilot, lambda: listening().tone_squelch is None)


def test_a_bookmark_tone_does_not_leak_to_the_next_channel():
    """PMR446 channels are 12.5 kHz apart; channel 1's tone must not mute channel 2."""
    from radiotui.audio.tones import tone_near

    book = UserChannels(bookmarks=[Bookmark(446.00625e6, "pmr 1", DemodMode.NFM, "88.5")])
    assert book.tone_for(446.01875e6) is None
    assert book.tone_for(446.0063e6) == "88.5"
    assert tone_near({446.00625e6: "88.5"}, 446.01875e6) == ""


def test_pre_roll_is_dropped_when_the_tone_squelch_is_turned_off(tmp_path):
    settings = AudioSettings(recordings_dir=str(tmp_path), min_clip_seconds=0.0)
    recorder = VoxRecorder(446e6, settings)
    recorder.enabled = True
    recorder.preroll_s = WINDOW_S
    for _ in range(10):
        recorder.feed(tone_pcm(0.1), 48_000, squelch_open=False)
    recorder.preroll_s = 0.0
    recorder.feed(tone_pcm(0.1), 48_000, squelch_open=False)
    for _ in range(10):
        recorder.feed(tone_pcm(0.1), 48_000)
    assert recorder.stop()[0].seconds < 1.5
