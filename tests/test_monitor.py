"""ChannelMonitor through its interface: recording, failures and shutdown."""

import threading
import time
from pathlib import Path

import numpy as np
import pytest

from radiotui.audio.recorder import VoxRecorder
from radiotui.config import Settings
from radiotui.core.models import DemodMode
from radiotui.scanner.monitor import ChannelMonitor
from radiotui.sdr.simulator import SimCarrier, SimulatedDevice

FS = 1_024_000.0


class PacedDevice(SimulatedDevice):
    """Delivers blocks at the device's real-time pace, like hardware."""

    def read_samples(self, count: int) -> np.ndarray:
        time.sleep(count / FS)
        return super().read_samples(count)


class WedgedDevice(SimulatedDevice):
    """A USB read that never returns until released."""

    def __init__(self) -> None:
        super().__init__(carriers=[])
        self.release = threading.Event()

    def read_samples(self, count: int) -> np.ndarray:
        self.release.wait()
        return super().read_samples(count)


def loud_device() -> PacedDevice:
    carrier = SimCarrier(145.5e6, -10.0, "nfm", duty_cycle=1.0, phase=0.0)
    return PacedDevice(carriers=[carrier], seed=1)


def make_monitor(
    device=None, recordings_dir: Path | None = None, **kwargs
) -> tuple[ChannelMonitor, list[str]]:
    errors: list[str] = []
    settings = Settings()
    if recordings_dir is not None:
        settings.audio.recordings_dir = str(recordings_dir)
    settings.audio.vox_threshold_dbfs = -60.0
    settings.audio.min_clip_seconds = 0.0
    monitor = ChannelMonitor(
        device or loud_device(),
        145.5e6,
        DemodMode.NFM,
        settings,
        muted=True,
        on_error=errors.append,
        **kwargs,
    )
    return monitor, errors


def wait_for(predicate, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_rapid_recording_toggles_never_break_the_monitor():
    """Only the monitor thread touches the WAV file, however fast `r` is pressed."""
    monitor, errors = make_monitor(record=True)
    monitor.start()
    try:
        for i in range(60):
            monitor.set_recording(i % 2 == 0)
            time.sleep(0.01)
        monitor.set_recording(False)
        assert monitor.running
    finally:
        assert monitor.stop()
    assert errors == []
    for clip in monitor.clips:
        assert clip.path.exists()


def test_unexpected_failure_is_reported_and_stops_the_monitor(monkeypatch):
    def broken_feed(self, *args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(VoxRecorder, "feed", broken_feed)
    monitor, errors = make_monitor()
    monitor.start()
    assert wait_for(lambda: not monitor.running), "the monitor kept looking alive"
    monitor.stop()
    assert any("boom" in e for e in errors)


def test_unwritable_recordings_dir_reports_once_and_keeps_listening(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("", encoding="utf-8")
    monitor, errors = make_monitor(recordings_dir=blocker, record=True)
    monitor.start()
    try:
        assert wait_for(lambda: errors)
        time.sleep(0.3)
        assert monitor.running, "a recording failure must not end listening"
        assert not monitor.status().recording
    finally:
        monitor.stop()
    assert len(errors) == 1


def test_sidecar_write_failure_is_reported(monkeypatch):
    real_write_text = Path.write_text

    def failing_write_text(self, *args, **kwargs):
        if self.suffix == ".json":
            raise OSError("disk full")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", failing_write_text)
    monitor, errors = make_monitor(record=True)
    monitor.start()
    time.sleep(0.5)
    assert monitor.stop()
    assert monitor.clips, "no clip was recorded"
    assert any("sidecar" in e for e in errors)


def test_stop_reports_a_reader_that_will_not_stop(monkeypatch):
    monkeypatch.setattr(ChannelMonitor, "STOP_TIMEOUT_S", 0.2)
    device = WedgedDevice()
    monitor, _ = make_monitor(device)
    monitor.start()
    try:
        assert not monitor.stop()
        assert monitor.running, "a wedged monitor must not claim to be stopped"
    finally:
        device.release.set()
    assert wait_for(lambda: monitor.stop())
    assert not monitor.running


def test_clips_are_complete_once_stop_returns():
    """CLI record reads the clip list after stop; nothing may still be open."""
    monitor, errors = make_monitor(record=True)
    monitor.start()
    time.sleep(0.5)
    assert monitor.stop()
    assert monitor.clips
    assert not monitor.status().capturing
    assert errors == []


@pytest.mark.parametrize("muted", [True, False])
def test_status_reports_backend_and_recording(muted):
    class StubPlayer:
        backend = "stub"
        running = True

        def start(self) -> bool:
            return True

        def stop(self) -> None: ...

        def write(self, pcm: bytes) -> None: ...

    monitor, _ = make_monitor(record=True, player=StubPlayer())
    monitor.set_muted(muted)
    status = monitor.status()
    assert status.backend == "stub"
    assert status.recording
    assert not status.running


def test_stop_unblocks_a_write_to_a_stalled_audio_backend(monkeypatch):
    """A backend that stops reading must not keep the monitor from stopping."""
    writing = threading.Event()
    released = threading.Event()

    class StalledPlayer:
        backend = "stalled"
        running = True

        def start(self) -> bool:
            return True

        def write(self, pcm: bytes) -> None:
            writing.set()
            released.wait()  # the pipe is full and nobody drains it

        def stop(self) -> None:
            released.set()  # what killing the backend does to a blocked write

    monkeypatch.setattr(ChannelMonitor, "STOP_TIMEOUT_S", 0.5)
    monitor, _ = make_monitor(player=StalledPlayer())
    monitor.set_muted(False)
    monitor.start()
    assert writing.wait(5.0)
    assert monitor.stop()
