"""Autonomous scan-and-hold: sweeper hold requests, cooldown, release."""

import math
import queue
import threading
import time

import numpy as np
import pytest

from radiotui.audio.recorder import VoxRecorder
from radiotui.config import AudioSettings, ScannerSettings, Settings
from radiotui.core.models import DemodMode, HoldRequest
from radiotui.dsp.spectrum import SweepPlan
from radiotui.scanner.monitor import auto_hold_release_reason
from radiotui.scanner.sweeper import Sweeper
from radiotui.sdr.simulator import SimCarrier, SimulatedDevice
from radiotui.tui.app import RadioTuiApp


def make_sweeper(queue_size=64, **overrides):
    device = SimulatedDevice(
        carriers=[SimCarrier(145.500e6, -18.0, "nfm", duty_cycle=1.0)],
        seed=7,
    )
    settings = ScannerSettings(
        hop_dwell_s=0.05,
        min_persist_frames=2,
        auto_hold_min_snr_db=10.0,
        channel_cooldown_s=2.0,
        hold_release_s=1.0,
        max_hold_s=5.0,
        autonomous=True,
    )
    for key, value in overrides.items():
        setattr(settings, key, value)
    plan = SweepPlan.build(144.0e6, 147.0e6, settings.sample_rate_hz, settings.fft_size)
    out: queue.Queue = queue.Queue(maxsize=queue_size)
    return Sweeper(device, plan, settings, out), out


def wait_for_state(out, predicate, timeout_s: float):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            state = out.get(timeout=0.25)
        except queue.Empty:
            continue
        if state is not None and predicate(state):
            return state
    return None


def test_hold_request_emitted_then_cooldown_then_reacquire():
    sweeper, out = make_sweeper()
    sweeper.start()
    try:
        held = wait_for_state(out, lambda s: s.hold_request is not None, 20.0)
        assert held is not None, "no HoldRequest emitted"
        req: HoldRequest = held.hold_request
        assert abs(req.freq_hz - 145.5e6) <= 25_000
        assert req.demod == DemodMode.NFM
        assert req.snr_db >= 10.0
        assert sweeper.holding_active()

        t_release = time.time()
        sweeper.cooldown_channel(145.5e6)
        sweeper.release_hold()
        while time.time() - t_release < 1.2:
            try:
                state = out.get(timeout=0.2)
            except queue.Empty:
                continue
            assert state.hold_request is None, "re-held during cooldown"

        reacquired = wait_for_state(out, lambda s: s.hold_request is not None, 12.0)
        assert reacquired is not None, "no re-acquire after cooldown expiry"
    finally:
        sweeper.stop()


def test_stop_unblocks_thread_while_holding():
    sweeper, out = make_sweeper(channel_cooldown_s=0.5)
    sweeper.start()
    try:
        held = wait_for_state(out, lambda s: s.hold_request is not None, 20.0)
        assert held is not None
        assert sweeper.holding_active()
        sweeper.stop()
        deadline = time.time() + 3
        while sweeper.running and time.time() < deadline:
            time.sleep(0.05)
        assert not sweeper.running
        assert not sweeper.holding_active()
    finally:
        sweeper.stop()


def test_autonomy_disabled_never_holds():
    sweeper, out = make_sweeper(autonomous=False)
    sweeper.start()
    try:
        any_state = wait_for_state(out, lambda s: s.sweeps_done >= 2, 10.0)
        assert any_state is not None
        deadline = time.time() + 2.0
        while time.time() < deadline:
            try:
                state = out.get(timeout=0.2)
            except queue.Empty:
                continue
            assert state.hold_request is None
        assert not sweeper.holding_active()
    finally:
        sweeper.stop()


def test_restart_does_not_inherit_stale_release():
    """#61: stop() sets the release event; the next hold must still park."""
    sweeper, out = make_sweeper()
    sweeper.start()
    sweeper.stop()
    sweeper.start()
    try:
        held = wait_for_state(out, lambda s: s.hold_request is not None, 20.0)
        assert held is not None
        sweeps = sweeper.sweeps_done
        time.sleep(0.5)
        assert sweeper.holding_active()
        assert sweeper.sweeps_done == sweeps
    finally:
        sweeper.stop()


def test_hold_state_is_not_dropped_on_a_full_queue():
    """#61: a hold the UI never sees would park the sweeper forever."""
    sweeper, out = make_sweeper(queue_size=1)
    out.put(None)  # the UI is behind
    sweeper.start()
    try:
        time.sleep(1.5)
        held = wait_for_state(out, lambda s: s.hold_request is not None, 10.0)
        assert held is not None
    finally:
        sweeper.stop()


def test_recorder_tracks_last_voice_even_when_disabled(tmp_path):
    audio = AudioSettings(recordings_dir=str(tmp_path))
    rec = VoxRecorder(145.5e6, audio)
    assert math.isinf(rec.seconds_since_voice())
    t_ramp = np.linspace(0, 2 * np.pi, 480)
    loud = (np.sin(t_ramp) * 12000).astype("<i2").tobytes()
    rec.feed(loud)
    assert rec.seconds_since_voice() < 1.0
    rec.last_voice_ts -= 30.0
    assert rec.seconds_since_voice() > 29.0


def test_hold_request_defaults_roundtrip():
    req = HoldRequest(freq_hz=145.5e6, demod=DemodMode.NFM, snr_db=17.5)
    assert req.freq_hz == 145.5e6
    assert req.demod == DemodMode.NFM


# ---- issue #22: the reported release reason must match what fired ----

HOLD_RELEASE_S = 4.0
MAX_HOLD_S = 120.0


@pytest.mark.parametrize(
    ("silent_for", "held_for", "expected"),
    [
        (float("inf"), 1.0, None),  # #61: grace period before any voice
        (float("inf"), HOLD_RELEASE_S, "no traffic"),
        (30.0, 30.0, "silence"),
        (1.5, 60.0, None),
        (1.5, MAX_HOLD_S + 1, "max hold"),
    ],
)
def test_auto_hold_release_reason_paths(silent_for, held_for, expected):
    assert auto_hold_release_reason(silent_for, held_for, HOLD_RELEASE_S, MAX_HOLD_S) == expected


# ---- issue #61: the TUI hold must engage, release and resume on its own ----


def tuner_consumers(app) -> int:
    """Threads reading the tuner: live monitors, plus the sweeper unless parked."""
    sweeper = app.sweeper
    count = 0
    for thread in threading.enumerate():
        if thread.name.startswith("monitor-"):
            count += 1
        elif thread.name == "sweeper" and not (sweeper is not None and sweeper.holding_active()):
            count += 1
    return count


async def test_tui_autonomous_hold_cycles_end_to_end() -> None:
    settings = Settings(
        scanner=ScannerSettings(
            hop_dwell_s=0.02,
            min_persist_frames=1,
            hold_release_s=0.5,
            max_hold_s=1.5,
            channel_cooldown_s=30.0,
        )
    )
    app = RadioTuiApp(force_sim=True, settings=settings, start_sweeper=True)
    peak_consumers = 0
    sampling = True

    def sample() -> None:
        nonlocal peak_consumers
        while sampling:
            peak_consumers = max(peak_consumers, tuner_consumers(app))
            time.sleep(0.005)

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    try:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.2)
            await pilot.press("o")
            deadline = time.time() + 30.0
            while time.time() < deadline:
                await pilot.pause(0.2)
                log = "\n".join(app._events)
                if log.count("releasing") >= 2:
                    break
            log = "\n".join(app._events)
            assert log.count("AUTO[/magenta] holding") >= 2, log
            assert log.count("releasing") >= 2, log
            # Released holds give the tuner back: the sweeper keeps sweeping.
            assert app.monitor is None or app.auto_hold_freq is not None
            sweeps = app.sweeper.sweeps_done
            await pilot.pause(1.0)
            assert app.sweeper.sweeps_done > sweeps or app.auto_hold_freq is not None
            app.sweeper.stop()
    finally:
        sampling = False
        sampler.join()
    assert peak_consumers <= 1
