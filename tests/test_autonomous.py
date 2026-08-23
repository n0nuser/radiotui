"""Autonomous scan-and-hold: sweeper hold requests, cooldown, release."""

import math
import queue
import time

import numpy as np
import pytest

from radiotui.audio.recorder import VoxRecorder
from radiotui.config import AudioSettings, ScannerSettings
from radiotui.core.models import DemodMode, HoldRequest
from radiotui.dsp.spectrum import SweepPlan
from radiotui.scanner.monitor import auto_hold_release_reason
from radiotui.scanner.sweeper import Sweeper
from radiotui.sdr.simulator import SimCarrier, SimulatedDevice
from radiotui.tui.app import RadioTuiApp


def make_sweeper(**overrides):
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
    out: queue.Queue = queue.Queue(maxsize=64)
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
    ("silent_for", "expected"),
    [
        (float("inf"), "no traffic"),
        (30.0, "silence"),
        (1.5, "max hold"),
    ],
)
def test_auto_hold_release_reason_paths(silent_for, expected):
    assert auto_hold_release_reason(silent_for, HOLD_RELEASE_S) == expected


async def _hold_log_line(app) -> str:
    for event in reversed(app._events):
        if "releasing" in event:
            return event
    return ""


async def test_auto_hold_release_reports_no_traffic() -> None:
    """A channel that never produced voice is labeled 'no traffic', not 'max hold'."""
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        app.settings.scanner.autonomous = True
        app._engage_auto_hold(HoldRequest(freq_hz=100e6, demod=DemodMode.WFM, snr_db=15))
        await pilot.pause(0.3)
        assert app.auto_hold_freq is not None
        app._pause_sweeper_for_monitor()
        app._auto_hold_started_at = time.time() - MAX_HOLD_S / 2
        app.monitor.recorder.last_voice_ts = None  # never any voice
        app._auto_release_check()
        await pilot.pause(0.1)
        assert "no traffic" in await _hold_log_line(app)
        assert app.auto_hold_freq is None


async def test_auto_hold_release_reports_max_hold() -> None:
    """Released while still talking (held past max_hold_s) is labeled 'max hold'."""
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        app.settings.scanner.autonomous = True
        app._engage_auto_hold(HoldRequest(freq_hz=100e6, demod=DemodMode.WFM, snr_db=15))
        await pilot.pause(0.3)
        assert app.auto_hold_freq is not None
        app._pause_sweeper_for_monitor()
        app._auto_hold_started_at = time.time() - (MAX_HOLD_S + 10)
        app.monitor.recorder.last_voice_ts = time.time()  # voice right now
        app._auto_release_check()
        await pilot.pause(0.1)
        assert "max hold" in await _hold_log_line(app)


async def test_auto_hold_release_reports_silence() -> None:
    """Voice that stopped long enough ago is labeled 'silence'."""
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        app.settings.scanner.autonomous = True
        app._engage_auto_hold(HoldRequest(freq_hz=100e6, demod=DemodMode.WFM, snr_db=15))
        await pilot.pause(0.3)
        assert app.auto_hold_freq is not None
        app._pause_sweeper_for_monitor()
        app._auto_hold_started_at = time.time() - 1.0
        app.monitor.recorder.last_voice_ts = time.time() - (HOLD_RELEASE_S + 10)
        app._auto_release_check()
        await pilot.pause(0.1)
        assert "silence" in await _hold_log_line(app)
