"""Live channel monitor: tune, demodulate, play and record."""

from __future__ import annotations

import threading
import time

from radiotui.audio.demod import audio_to_pcm16, channel_audio, rssi_dbfs, scale_pcm16
from radiotui.audio.player import AudioPlayer
from radiotui.audio.recorder import VoxRecorder
from radiotui.config import Settings, clamp_volume_db, effective_sample_rate
from radiotui.core.models import DemodMode
from radiotui.sdr.base import SdrDevice

NEVER_VOICED = float("inf")


def auto_hold_release_reason(seconds_since_voice: float, hold_release_s: float) -> str:
    """Label matching whichever autonomous-hold release condition fired.

    ``seconds_since_voice == inf`` means no voice was ever detected on the
    channel, which is worth diagnosing differently from a transmission that
    simply ended.
    """
    if seconds_since_voice == NEVER_VOICED:
        return "no traffic"
    if seconds_since_voice >= hold_release_s:
        return "silence"
    return "max hold"


class ChannelMonitor:
    def __init__(
        self,
        device: SdrDevice,
        freq_hz: float,
        demod: DemodMode,
        settings: Settings,
        muted: bool = False,
    ) -> None:
        self._device = device
        self._freq_hz = freq_hz
        self._demod = demod
        self._settings = settings
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.player = AudioPlayer(settings.audio.output_rate_hz)
        self.recorder = VoxRecorder(freq_hz, settings.audio)
        self.muted = muted
        self.rssi_dbfs: float = -120.0
        self.on_rssi = None
        self.on_error = None

    @property
    def freq_hz(self) -> float:
        return self._freq_hz

    @property
    def demod(self) -> DemodMode:
        return self._demod

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        try:
            self._device.set_center_freq_hz(self._freq_hz)
            self._device.set_sample_rate_hz(effective_sample_rate(self._settings.scanner))
            self._device.set_gain_db(self._settings.scanner.gain_db)
        except (OSError, RuntimeError, ValueError) as exc:
            if self.on_error:
                self.on_error(str(exc))
            return
        if not self.muted:
            if not self.player.start():
                if self.on_error:
                    self.on_error("no audio backend found: install ffmpeg or alsa-utils")
        self._thread = threading.Thread(
            target=self._run, name=f"monitor-{self._freq_hz / 1e6:.3f}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        self.recorder.stop()
        self.player.stop()

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        if not muted and not self.player.running:
            # Keep the player process warm while muted; only (re)start it if
            # it was never launched or has died.
            if not self.player.start() and self.on_error:
                self.on_error("no audio backend found: install ffmpeg or alsa-utils")

    @property
    def volume_db(self) -> float:
        return self._settings.audio.volume_db

    def set_volume_db(self, volume_db: float) -> None:
        """Playback volume in dB; affects the speakers only, never recordings."""
        self._settings.audio.volume_db = clamp_volume_db(volume_db)

    def _playback_pcm(self, pcm: bytes) -> bytes:
        gain = 10.0 ** (self._settings.audio.volume_db / 20.0)
        return scale_pcm16(pcm, gain)

    def _run(self) -> None:
        block = self._settings.audio.block_size
        fs = effective_sample_rate(self._settings.scanner)
        rate = self._settings.audio.output_rate_hz
        while not self._stop.is_set():
            t0 = time.time()
            try:
                iq = self._device.read_samples(block)
            except (OSError, RuntimeError, ValueError) as exc:
                if self.on_error:
                    self.on_error(f"read failed: {exc}")
                break
            self.rssi_dbfs = rssi_dbfs(iq)
            if self.on_rssi:
                self.on_rssi(self.rssi_dbfs)
            audio = channel_audio(iq, fs, self._demod, rate)
            if len(audio):
                pcm = audio_to_pcm16(audio)
                if not self.muted:
                    self.player.write(self._playback_pcm(pcm))
                self.recorder.feed(pcm, rate)
            elapsed = time.time() - t0
            budget = block / fs
            if elapsed < budget:
                time.sleep(budget - elapsed)
