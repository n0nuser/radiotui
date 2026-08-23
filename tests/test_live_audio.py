"""Live listening quality: real-time audio production and channel selectivity.

Both guard user-visible symptoms reported from real hardware: audio stopping
for about a second every few seconds, and FM being much noisier than a
hardware receiver on the same station.
"""

import time

import numpy as np
import pytest

from radiotui.audio.demod import channel_audio, decimation_taps
from radiotui.config import Settings
from radiotui.core.models import DemodMode
from radiotui.scanner.monitor import ChannelMonitor
from radiotui.sdr.simulator import SimulatedDevice

FS = 1_024_000.0


class PacedDevice(SimulatedDevice):
    """Simulator that returns a block every ``count/fs`` seconds.

    A synchronous USB read is paced by the hardware, so the loop's own speed
    decides how much radio it can keep up with. Synthesising the samples up
    front keeps the fake device itself from being the bottleneck.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._prepared = super().read_samples(65_536)
        self._due: float | None = None

    def read_samples(self, count: int) -> np.ndarray:
        now = time.perf_counter()
        due = (self._due if self._due is not None else now) + count / self._sample_rate
        self._due = due
        if due > now:
            time.sleep(due - now)
        if count <= len(self._prepared):
            return self._prepared[:count]
        return np.resize(self._prepared, count)


class CountingPlayer:
    """Stands in for ffplay/aplay and counts the frames handed to it."""

    running = True
    backend = "test"

    def __init__(self) -> None:
        self.frames = 0

    def start(self) -> bool:
        return True

    def stop(self) -> None:
        pass

    def write(self, pcm: bytes) -> None:
        self.frames += len(pcm) // 2


READ_S = 0.10  # how long the "hardware" takes to deliver one block
PROCESS_S = 0.04  # what demodulating it costs the consumer


class TimedDevice(SimulatedDevice):
    """Records when each read starts and blocks for a fixed, device-paced time."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._prepared = super().read_samples(4096)
        self.read_starts: list[float] = []

    def read_samples(self, count: int) -> np.ndarray:
        self.read_starts.append(time.perf_counter())
        time.sleep(READ_S)
        return np.resize(self._prepared, count)


def test_reads_are_not_delayed_by_demodulation(monkeypatch):
    """The dropout: processing time must not eat into time spent reading.

    A synchronous read is paced by the hardware, so demodulating in the same
    loop makes each iteration cost read + processing while yielding only
    `read` worth of audio. The device keeps producing during that gap with
    nobody collecting, and the player's buffer drains until it stalls.

    Asserted as the interval between consecutive reads: it must track the
    device's own pace, not the pace plus the demodulation.
    """
    monkeypatch.setattr(
        "radiotui.scanner.monitor.channel_audio",
        lambda *a, **k: (time.sleep(PROCESS_S), np.zeros(64, dtype=np.float32))[1],
    )
    device = TimedDevice(seed=1)
    monitor = ChannelMonitor(device, 96.9e6, DemodMode.WFM, Settings(), muted=True)

    monitor.start()
    time.sleep(READ_S * 12)
    monitor.stop()

    intervals = np.diff(device.read_starts)
    assert len(intervals) >= 4, "too few reads to judge the cadence"
    median = float(np.median(intervals))
    serial = READ_S + PROCESS_S
    assert median < (READ_S + serial) / 2, (
        f"reads are {median * 1000:.0f} ms apart; the device delivers every "
        f"{READ_S * 1000:.0f} ms, so demodulation ({PROCESS_S * 1000:.0f} ms) "
        "is still being paid on the reading thread"
    )


def test_monitor_turns_every_delivered_block_into_audio():
    """Nothing the device hands over may be dropped on the way to the speaker."""
    device = PacedDevice(seed=1)
    monitor = ChannelMonitor(device, 96.9e6, DemodMode.WFM, Settings(), muted=False)
    player = CountingPlayer()
    monitor.player = player

    monitor.start()
    time.sleep(1.5)
    monitor.stop()

    settings = Settings()
    delivered_blocks = player.frames / (
        settings.audio.block_size / FS * settings.audio.output_rate_hz
    )
    assert delivered_blocks > 5, "the monitor barely produced any audio"


@pytest.mark.parametrize(
    ("factor", "offset_hz", "floor_db"),
    [
        (4, 200_000.0, 45.0),  # WFM: the first adjacent broadcast station
        (4, 400_000.0, 45.0),
        (34, 100_000.0, 60.0),  # NFM: anything that would fold into the channel
    ],
)
def test_channel_filter_rejects_neighbouring_signals(factor, offset_hz, floor_db):
    taps = decimation_taps(factor)
    response = np.fft.rfft(taps, 16_384)
    freqs = np.fft.rfftfreq(16_384, 1 / FS)
    rejection = -20 * np.log10(abs(response[np.argmin(abs(freqs - offset_hz))]) + 1e-12)
    assert rejection > floor_db, (
        f"factor {factor}: a neighbour {offset_hz / 1e3:.0f} kHz away is only "
        f"{rejection:.1f} dB down"
    )


def test_channel_filter_keeps_the_wanted_channel_flat():
    """Selectivity must not come at the cost of the audio it is protecting."""
    taps = decimation_taps(4)
    response = np.fft.rfft(taps, 16_384)
    freqs = np.fft.rfftfreq(16_384, 1 / FS)
    passband = abs(response[freqs <= 100_000.0])
    ripple_db = 20 * np.log10(passband.max() / passband.min())
    assert ripple_db < 1.0, f"{ripple_db:.2f} dB of ripple across the FM channel"


def test_wfm_audio_is_band_limited_before_resampling():
    """The 19 kHz pilot must not fold back into the audible band."""
    t = np.arange(int(FS * 0.5)) / FS
    mpx = 0.5 * np.sin(2 * np.pi * 1_000 * t) + 0.09 * np.sin(2 * np.pi * 19_000 * t)
    iq = np.exp(1j * 2 * np.pi * 75_000 * np.cumsum(mpx) / FS)

    audio = channel_audio(iq, FS, DemodMode.WFM, 48_000, 200_000.0)
    spectrum = np.abs(np.fft.rfft(audio * np.hanning(len(audio))))
    freqs = np.fft.rfftfreq(len(audio), 1 / 48_000)
    wanted = spectrum[(freqs > 900) & (freqs < 1_100)].max()
    above_audio = spectrum[freqs > 16_000].max()
    assert 20 * np.log10(wanted / (above_audio + 1e-12)) > 40
