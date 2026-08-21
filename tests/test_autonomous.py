"""Autonomous scan-and-hold: sweeper hold requests, cooldown, release."""

import math
import queue
import time

import numpy as np

from radiotui.audio.recorder import VoxRecorder
from radiotui.config import AudioSettings, ScannerSettings
from radiotui.core.models import DemodMode, HoldRequest
from radiotui.dsp.spectrum import SweepPlan
from radiotui.scanner.sweeper import Sweeper
from radiotui.sdr.simulator import SimCarrier, SimulatedDevice


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
