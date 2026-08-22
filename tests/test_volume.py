"""Issue #19: live volume control, warm mute, visible audio-backend failure."""

import numpy as np

from radiotui.audio.demod import scale_pcm16
from radiotui.config import Settings, clamp_volume_db
from radiotui.core.models import DemodMode
from radiotui.scanner.monitor import ChannelMonitor
from radiotui.sdr.simulator import SimulatedDevice
from radiotui.tui.app import RadioTuiApp


def test_scale_pcm16_identity_at_unity():
    pcm = (np.arange(-100, 100, dtype="<i2")).tobytes()
    assert scale_pcm16(pcm, 1.0) is pcm


def test_scale_pcm16_applies_linear_gain():
    pcm = np.full(64, 5_000, dtype="<i2").tobytes()
    out = np.frombuffer(scale_pcm16(pcm, 2.0), dtype="<i2")
    assert np.all(out == 10_000)


def test_scale_pcm16_clips_to_full_scale():
    pcm = np.full(64, 20_000, dtype="<i2").tobytes()
    out = np.frombuffer(scale_pcm16(pcm, 4.0), dtype="<i2")
    assert out.max() == 32_767


def test_clamp_volume_bounds():
    assert clamp_volume_db(100.0) == 12.0
    assert clamp_volume_db(-100.0) == -60.0
    assert clamp_volume_db(-3.04) == -3.0


class StubPlayer:
    """Records lifecycle calls; mimics a healthy long-lived process."""

    backend = "stub"

    def __init__(self, ok: bool = True):
        self.ok = ok
        self.starts = 0
        self.stops = 0
        self._alive = False

    def start(self) -> bool:
        self.starts += 1
        self._alive = self.ok
        return self.ok

    def stop(self) -> None:
        self.stops += 1
        self._alive = False

    def write(self, pcm: bytes) -> None: ...

    @property
    def running(self) -> bool:
        return self._alive


def make_monitor(muted: bool = False) -> ChannelMonitor:
    device = SimulatedDevice(carriers=[])
    return ChannelMonitor(device, 145.5e6, DemodMode.NFM, Settings(), muted=muted)


def test_mute_cycles_do_not_respawn_player():
    monitor = make_monitor()
    monitor.player = StubPlayer()
    for muted in (True, False, True, False, True):
        monitor.set_muted(muted)
    assert monitor.player.starts == 1
    assert monitor.player.stops == 0


def test_missing_backend_surfaces_error():
    monitor = make_monitor()
    monitor.player = StubPlayer(ok=False)
    errors: list[str] = []
    monitor.on_error = errors.append
    monitor.start()
    try:
        assert errors
        assert "no audio backend" in errors[-1]
    finally:
        monitor.stop()


def test_playback_volume_applies_only_to_player_path():
    settings = Settings()
    settings.audio.volume_db = -6.0
    monitor = ChannelMonitor(SimulatedDevice(carriers=[]), 145.5e6, DemodMode.NFM, settings)
    pcm = np.full(64, 10_000, dtype="<i2").tobytes()

    playback = np.frombuffer(monitor._playback_pcm(pcm), dtype="<i2")
    assert abs(playback[0] - 5_012) < 20  # 10^(-6/20)

    settings.audio.volume_db = 0.0
    assert monitor._playback_pcm(pcm) == pcm


async def test_volume_keys_adjust_level_and_status_shows_it():
    from textual.widgets import Static

    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.2)
        app.start_monitor(145.5e6, DemodMode.NFM, muted=True, enable_recorder=False)
        await pilot.pause(0.2)
        await pilot.press(">")
        await pilot.pause(0.1)
        assert app.monitor.volume_db == 3.0
        meter = str(app.query_one("#meter", Static).render())
        assert "+3dB" in meter
        await pilot.press("<")
        await pilot.press("<")
        await pilot.pause(0.1)
        assert app.monitor.volume_db == -3.0
