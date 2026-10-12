"""Issue #87 / ADR 0018: NFM noise squelch."""

import time

import numpy as np
import pytest

from radiotui.audio.demod import DemodState, channel_audio
from radiotui.config import Settings
from radiotui.config_file import apply_config_to_settings, load_config
from radiotui.core.models import DemodMode
from radiotui.scanner.monitor import NEVER_VOICED, ChannelMonitor, NoiseSquelch
from radiotui.sdr.simulator import SimCarrier, SimulatedDevice

FS = 1_024_000.0
BLOCK = 65_536


def quieting_db(iq: np.ndarray) -> float:
    """Noise-squelch reading after the stream has settled."""
    state = DemodState()
    for i in range(0, len(iq), BLOCK):
        channel_audio(iq[i : i + BLOCK], FS, DemodMode.NFM, state=state)
    return state.noise_quieting_db


def noise(seconds: float = 1.0, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(FS * seconds)
    return (rng.normal(size=n) + 1j * rng.normal(size=n)) / np.sqrt(2)


def carrier(seconds: float = 1.0, snr_db: float = 30.0) -> np.ndarray:
    t = np.arange(int(FS * seconds)) / FS
    phase = 2 * np.pi * 2_500.0 * np.cumsum(np.sin(2 * np.pi * 1_000 * t)) / FS
    return 10 ** (snr_db / 20) * np.exp(1j * phase) + noise(seconds, seed=1)


def test_noise_alone_reads_as_unquieted():
    assert abs(quieting_db(noise())) < 2.0


def test_a_carrier_quiets_the_noise_band():
    assert quieting_db(carrier()) < -15.0


def test_tuner_dc_offset_does_not_fake_a_carrier():
    """The RTL-SDR DC spike sits in the channel; it must not open the squelch."""
    assert quieting_db(noise() + 4.0) > -3.0


def test_gate_opens_holds_through_gaps_and_closes_with_hysteresis():
    gate = NoiseSquelch()
    assert gate.update(-20.0, -10.0, 0.064)
    assert gate.update(-8.0, -10.0, 0.064)  # inside the hysteresis band: stays open
    assert gate.update(0.0, -10.0, 0.064)  # above it, but the hang has not run out
    for _ in range(10):
        is_open = gate.update(0.0, -10.0, 0.064)
    assert not is_open
    assert not gate.update(-8.0, -10.0, 0.064)  # inside the band: stays closed


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


class PacedDevice(SimulatedDevice):
    def read_samples(self, count: int) -> np.ndarray:
        time.sleep(count / FS)
        return super().read_samples(count)


def run_monitor(carriers, noise_squelch_db) -> tuple[PeakPlayer, ChannelMonitor]:
    settings = Settings()
    settings.scanner.noise_squelch_db = noise_squelch_db
    settings.audio.vox_threshold_dbfs = -60.0
    player = PeakPlayer()
    monitor = ChannelMonitor(
        PacedDevice(carriers=carriers, seed=3), 145.5e6, DemodMode.NFM, settings, player=player
    )
    monitor.start()
    deadline = time.monotonic() + 10.0
    while player.frames < 48_000 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert monitor.stop()
    return player, monitor


def test_closed_squelch_silences_the_speaker_and_never_voices():
    player, monitor = run_monitor([], noise_squelch_db=10.0)
    assert player.frames >= 48_000  # the player is kept fed, with silence
    assert player.peak == 0
    assert monitor.silent_for() == NEVER_VOICED


def test_open_squelch_passes_a_carrier():
    carrier = SimCarrier(145.5e6, -10.0, "nfm", duty_cycle=1.0, phase=0.0)
    player, _ = run_monitor([carrier], noise_squelch_db=10.0)
    assert player.peak > 1_000


def test_squelch_off_by_default_plays_noise():
    assert Settings().scanner.noise_squelch_db is None
    player, _ = run_monitor([], noise_squelch_db=None)
    assert player.peak > 1_000


@pytest.mark.parametrize(("text", "expected"), [("noise_squelch_db = 12", 12.0), ("", None)])
def test_noise_squelch_round_trips_through_config(tmp_path, text, expected):
    path = tmp_path / "config.toml"
    path.write_text(f"[scanner]\n{text}\n")
    settings = Settings()
    apply_config_to_settings(settings, load_config(path), path)
    assert settings.scanner.noise_squelch_db == expected
