"""TunerSession through its interface, without Textual, on a paced fake device."""

import queue
import threading
import time

import numpy as np
import pytest

from radiotui.config import BANDS, ScannerSettings, Settings
from radiotui.core.models import DemodMode
from radiotui.scanner.monitor import ChannelMonitor
from radiotui.scanner.session import (
    HoldEngaged,
    HoldReleased,
    ListenStarted,
    ScanUpdate,
    SessionError,
    SweepStarted,
    TunerSession,
)
from radiotui.sdr.simulator import SimulatedDevice


class PacedDevice(SimulatedDevice):
    """Reads take real time, like hardware; records what overlaps a read."""

    def __init__(self) -> None:
        super().__init__(seed=1)
        self.in_read = threading.Event()
        self.gain_calls = 0
        self.gain_overlaps = 0
        self.hf_calls: list[bool] = []
        self.closed = False

    def read_samples(self, count: int) -> np.ndarray:
        self.in_read.set()
        try:
            time.sleep(count / self._sample_rate)
            return super().read_samples(count)
        finally:
            self.in_read.clear()

    def set_gain_db(self, gain_db) -> None:
        self.gain_calls += 1
        if self.in_read.is_set():
            self.gain_overlaps += 1

    def set_hf_mode(self, enabled: bool) -> bool:
        self.hf_calls.append(enabled)
        return False

    def close(self) -> None:
        self.closed = True


class ReaderSampler:
    """Peak count of threads reading the tuner, sampled in the background."""

    def __init__(self, session: TunerSession) -> None:
        self.peak = 0
        self._session = session
        self._ignore = set(threading.enumerate())
        self._running = True
        self._thread = threading.Thread(target=self._sample, daemon=True)
        self._thread.start()

    def _count(self) -> int:
        parked = self._session.status().sweep_parked
        count = 0
        for thread in threading.enumerate():
            if thread in self._ignore:
                continue
            if thread.name.startswith("reader-") or (thread.name == "sweeper" and not parked):
                count += 1
        return count

    def _sample(self) -> None:
        while self._running:
            self.peak = max(self.peak, self._count())
            time.sleep(0.002)

    def stop(self) -> int:
        self._running = False
        self._thread.join()
        return self.peak


def make_session(**scanner) -> tuple[TunerSession, PacedDevice, queue.Queue]:
    device = PacedDevice()
    settings = Settings(scanner=ScannerSettings(hop_dwell_s=0.02, min_persist_frames=1, **scanner))
    settings.audio.block_size = 16_384
    events: queue.Queue = queue.Queue()
    return TunerSession.start(device, settings, events.put), device, events


def wait_for_event(events: queue.Queue, kind, timeout_s: float = 20.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            event = events.get(timeout=0.1)
        except queue.Empty:
            continue
        if isinstance(event, kind):
            return event
    return None


def wait_until(predicate, timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_rapid_listen_and_band_switches_never_share_the_tuner():
    session, device, _ = make_session()
    sampler = ReaderSampler(session)
    try:
        session.sweep(BANDS["fm_broadcast"])
        time.sleep(0.2)
        for _ in range(3):
            session.listen(101.3e6, DemodMode.WFM)
            session.listen(145.5e6, DemodMode.NFM)
            session.sweep(BANDS["vhf_ham"])
            session.listen(446.00625e6, DemodMode.NFM)
            time.sleep(0.05)
        assert wait_until(lambda: session.status().listen_freq_hz == 446.00625e6)
    finally:
        session.close()
        assert session.wait_closed(10.0)
        peak = sampler.stop()
    assert peak <= 1
    assert device.closed


def test_latest_tuner_request_wins():
    """Requests queued behind a slow stop collapse to the newest one (#49)."""
    session, _, events = make_session()
    try:
        session.listen(101.3e6, DemodMode.WFM)
        for freq in (145.5e6, 145.6e6, 145.7e6):
            session.listen(freq, DemodMode.NFM)
        assert wait_until(lambda: session.status().listen_freq_hz == 145.7e6)
        time.sleep(0.3)
        assert session.status().listen_freq_hz == 145.7e6
    finally:
        session.close()
        assert session.wait_closed(10.0)
    started = []
    while not events.empty():
        event = events.get()
        if isinstance(event, ListenStarted):
            started.append(event.freq_hz)
    assert started[-1] == 145.7e6
    assert len(started) < 4


def test_gain_changes_never_overlap_a_read():
    session, device, _ = make_session()
    try:
        session.sweep(BANDS["fm_broadcast"])
        for i in range(20):
            session.set_gain(10.0 + i)
            time.sleep(0.01)
        session.listen(101.3e6, DemodMode.WFM)
        for i in range(20):
            session.set_gain(30.0 - i)
            time.sleep(0.01)
        time.sleep(0.3)
    finally:
        session.close()
        assert session.wait_closed(10.0)
    assert device.gain_calls > 0
    assert device.gain_overlaps == 0


def test_gain_applies_when_nothing_is_reading():
    session, device, _ = make_session()
    try:
        session.set_gain(20.0)
        assert wait_until(lambda: device.gain_calls == 1)
    finally:
        session.close()
        assert session.wait_closed(10.0)


def test_listen_switches_direct_sampling_by_frequency():
    session, device, _ = make_session()
    try:
        session.listen(7.1e6, DemodMode.AM)
        assert wait_until(lambda: session.status().listen_freq_hz == 7.1e6)
        session.listen(145.5e6, DemodMode.NFM)
        assert wait_until(lambda: session.status().listen_freq_hz == 145.5e6)
    finally:
        session.close()
        assert session.wait_closed(10.0)
    assert device.hf_calls[:2] == [True, False]


def test_close_closes_the_device_even_when_a_stop_raises(monkeypatch):
    real_stop = ChannelMonitor.stop

    def failing_stop(self) -> bool:
        real_stop(self)
        raise RuntimeError("stop blew up")

    monkeypatch.setattr(ChannelMonitor, "stop", failing_stop)
    session, device, events = make_session()
    session.listen(145.5e6, DemodMode.NFM)
    assert wait_for_event(events, ListenStarted)
    session.close()
    assert session.wait_closed(10.0)
    assert device.closed
    assert wait_for_event(events, SessionError, timeout_s=1.0)


def test_autonomous_hold_engages_releases_and_sweeping_resumes():
    session, _, events = make_session(
        autonomous=True, hold_release_s=0.3, max_hold_s=1.0, channel_cooldown_s=30.0
    )
    sampler = ReaderSampler(session)
    try:
        session.sweep(BANDS["fm_broadcast"])
        engaged = wait_for_event(events, HoldEngaged)
        assert engaged is not None
        released = wait_for_event(events, HoldReleased)
        assert released is not None
        assert released.freq_hz == engaged.request.freq_hz
        sweeps = session.status().sweeps_done
        assert wait_until(
            lambda: (
                session.status().sweeps_done > sweeps or session.status().hold_freq_hz is not None
            )
        )
    finally:
        session.close()
        assert session.wait_closed(10.0)
        peak = sampler.stop()
    assert peak <= 1


@pytest.mark.parametrize("start", [True, False])
def test_sweep_emits_scan_updates_only_when_started(start):
    session, _, events = make_session()
    try:
        session.sweep(BANDS["fm_broadcast"], start=start)
        update = wait_for_event(events, ScanUpdate, timeout_s=3.0 if not start else 20.0)
    finally:
        session.close()
        assert session.wait_closed(10.0)
    assert (update is not None) == start


def test_an_unexpected_failure_does_not_kill_the_session():
    """The session thread must outlive a bad step, or later requests are lost."""

    class BrokenGainDevice(PacedDevice):
        def set_gain_db(self, gain_db) -> None:
            if self.gain_calls == 0:
                self.gain_calls += 1
                raise TypeError("broken device command")

    events: queue.Queue = queue.Queue()
    session = TunerSession.start(BrokenGainDevice(), Settings(), events.put)
    try:
        session.set_gain(10.0)  # nothing is reading, so the session thread runs it
        assert wait_for_event(events, SessionError, timeout_s=5.0)
        session.listen(145.5e6, DemodMode.NFM)
        assert wait_until(lambda: session.status().listen_freq_hz == 145.5e6)
    finally:
        session.close()
        assert session.wait_closed(10.0)


def test_band_channel_width_applies_only_in_the_bands_own_mode():
    """FM broadcast's 200 kHz must not reach a USB listen on the same band (#85)."""
    session, _, events = make_session()
    try:
        session.sweep(BANDS["fm_broadcast"], start=False)
        assert wait_for_event(events, SweepStarted) is not None
        session.listen(101.3e6, DemodMode.WFM)
        assert wait_until(lambda: session.status().listen_demod is DemodMode.WFM)
        assert session._monitor._channel_bw_hz == 200_000.0
        session.listen(101.3e6, DemodMode.USB)
        assert wait_until(lambda: session.status().listen_demod is DemodMode.USB)
        assert session._monitor._channel_bw_hz is None
    finally:
        session.close()
