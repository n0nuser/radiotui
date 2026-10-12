"""Live channel monitor: tune, demodulate, play and record.

Reading and processing run on separate threads. A synchronous device read is
paced by the hardware — a 65536-sample block at 1.024 MS/s takes 64 ms no
matter what — so doing the demodulation after it in the same loop makes each
iteration cost 64 ms + processing while yielding only 64 ms of audio. That
few-percent deficit drains the player's buffer and stalls it every few
seconds. The reader thread keeps the device drained while the consumer
demodulates the previous block, so audio is produced at real time.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from radiotui.audio.demod import (
    DemodState,
    audio_to_pcm16,
    channel_audio,
    rssi_dbfs,
    scale_pcm16,
)
from radiotui.audio.player import AudioPlayer
from radiotui.audio.recorder import ClipInfo, VoxRecorder
from radiotui.config import Settings, clamp_volume_db, effective_sample_rate
from radiotui.core.models import DemodMode
from radiotui.sdr.base import SdrDevice

NEVER_VOICED = float("inf")


def auto_hold_release_reason(
    seconds_since_voice: float, held_for: float, hold_release_s: float, max_hold_s: float
) -> str | None:
    """Why an autonomous hold should end now, or None to keep holding.

    Silence counts from the later of the hold start and the last voice, so a
    channel that has not voiced yet gets ``hold_release_s`` of grace before it
    is released as "no traffic" (``seconds_since_voice == inf``).
    """
    if min(seconds_since_voice, held_for) >= hold_release_s:
        return "no traffic" if seconds_since_voice == NEVER_VOICED else "silence"
    if held_for >= max_hold_s:
        return "max hold"
    return None


@dataclass(frozen=True)
class MonitorStatus:
    running: bool
    #: Audio backend in use ("ffplay", "aplay"), None if never started.
    backend: str | None
    #: Recording is armed: VOX opens a clip when voice arrives.
    recording: bool
    #: A clip file is open right now.
    capturing: bool


class ChannelMonitor:
    """Tune one frequency, then demodulate, play and VOX-record it.

    Only the monitor thread touches the recorder while it runs, so a WAV file
    is never closed under a write. Callbacks run on the monitor thread.
    """

    #: Per-thread join budget for stop(); a USB read can block this long.
    STOP_TIMEOUT_S = 5.0

    def __init__(
        self,
        device: SdrDevice,
        freq_hz: float,
        demod: DemodMode,
        settings: Settings,
        *,
        muted: bool = False,
        record: bool = False,
        band_label: str = "",
        channel_bw_hz: float | None = None,
        on_rssi: Callable[[float], None] | None = None,
        on_clip: Callable[[ClipInfo], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        player: AudioPlayer | None = None,
    ) -> None:
        self._device = device
        self._freq_hz = freq_hz
        self._demod = demod
        self._settings = settings
        self._channel_bw_hz = channel_bw_hz
        self._on_rssi = on_rssi
        self._on_error = on_error
        self._demod_state = DemodState()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._reader: threading.Thread | None = None
        self._blocks: queue.Queue = queue.Queue(maxsize=2)
        self._read_error: Exception | None = None
        self._player = player or AudioPlayer(settings.audio.output_rate_hz)
        scanner = settings.scanner
        self._recorder = VoxRecorder(
            freq_hz,
            settings.audio,
            context={
                "demod": demod.value,
                "band": band_label,
                "hardware": {
                    "gain_db": scanner.gain_db,
                    "freq_correction_ppm": scanner.freq_correction_ppm,
                    "offset_tune": scanner.offset_tune,
                    "bias_tee": scanner.bias_tee,
                    "sample_rate_hz": effective_sample_rate(scanner),
                },
            },
            squelch_rssi_dbfs=scanner.squelch_rssi_dbfs,
        )
        self._recorder.enabled = record
        self._recorder.on_clip_end = on_clip
        self._recorder.on_error = self._report
        # A pending set_recording() request, applied on the monitor thread.
        self._record_lock = threading.Lock()
        self._record_request: bool | None = None
        self.muted = muted
        self.rssi_dbfs: float = -120.0

    @property
    def freq_hz(self) -> float:
        return self._freq_hz

    @property
    def demod(self) -> DemodMode:
        return self._demod

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def clips(self) -> list[ClipInfo]:
        return list(self._recorder.clips)

    @property
    def recordings_dir(self) -> Path:
        return self._recorder.directory

    def status(self) -> MonitorStatus:
        with self._record_lock:
            pending = self._record_request
        return MonitorStatus(
            running=self.running,
            backend=self._player.backend,
            recording=self._recorder.enabled if pending is None else pending,
            capturing=self._recorder.recording,
        )

    def silent_for(self) -> float:
        """Seconds since the last voiced block; ``NEVER_VOICED`` before any."""
        return self._recorder.seconds_since_voice()

    def set_recording(self, enabled: bool) -> None:
        with self._record_lock:
            self._record_request = enabled
        if not self.running:
            self._apply_record_request()  # no monitor thread to hand it to

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        try:
            self._device.set_center_freq_hz(self._freq_hz)
            self._device.set_sample_rate_hz(effective_sample_rate(self._settings.scanner))
            self._device.set_gain_db(self._settings.scanner.gain_db)
        except (OSError, RuntimeError, ValueError) as exc:
            self._report(str(exc))
            return
        if not self.muted and not self._player.start():
            self._report("no audio backend found: install ffmpeg or alsa-utils")
        # Two blocks of slack: enough to ride out a slow demodulation without
        # letting stale radio pile up behind the speaker.
        self._blocks = queue.Queue(maxsize=2)
        self._read_error = None
        self._reader = threading.Thread(
            target=self._read_loop, name=f"reader-{self._freq_hz / 1e6:.3f}", daemon=True
        )
        self._thread = threading.Thread(
            target=self._run, name=f"monitor-{self._freq_hz / 1e6:.3f}", daemon=True
        )
        self._reader.start()
        self._thread.start()

    def stop(self) -> bool:
        """Stop both threads; False if one is still alive (a wedged USB read).

        A monitor that failed to stop keeps reporting ``running`` so nobody
        hands the tuner to a second reader (ADR 0004). Call again to retry.
        """
        self._stop.set()
        threads = [t for t in (self._reader, self._thread) if t is not None]
        for thread in threads:
            thread.join(timeout=self.STOP_TIMEOUT_S)
        if any(thread.is_alive() for thread in threads):
            return False
        self._reader = None
        self._thread = None
        self._recorder.stop()
        self._player.stop()
        return True

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        if not muted and not self._player.running:
            # Keep the player process warm while muted; only (re)start it if
            # it was never launched or has died.
            if not self._player.start():
                self._report("no audio backend found: install ffmpeg or alsa-utils")

    @property
    def volume_db(self) -> float:
        return self._settings.audio.volume_db

    def set_volume_db(self, volume_db: float) -> None:
        """Playback volume in dB; affects the speakers only, never recordings."""
        self._settings.audio.volume_db = clamp_volume_db(volume_db)

    def _report(self, message: str) -> None:
        if self._on_error:
            self._on_error(message)

    def _apply_record_request(self) -> None:
        with self._record_lock:
            request, self._record_request = self._record_request, None
        if request is None:
            return
        self._recorder.enabled = request
        if not request:
            self._recorder.stop()

    def _playback_pcm(self, pcm: bytes) -> bytes:
        gain = 10.0 ** (self._settings.audio.volume_db / 20.0)
        return scale_pcm16(pcm, gain)

    def _read_loop(self) -> None:
        """Keep the device drained; never do arithmetic on this thread."""
        block = self._settings.audio.block_size
        while not self._stop.is_set():
            try:
                iq = self._device.read_samples(block)
            except (OSError, RuntimeError, ValueError) as exc:
                # Record before handing over: if the queue is full because the
                # consumer is briefly wedged, the put times out and the only
                # evidence of the failure would otherwise be discarded, leaving
                # a live-looking monitor that is silent forever.
                self._read_error = exc
                try:
                    self._blocks.put(exc, timeout=1.0)
                except queue.Full:
                    pass
                return
            try:
                self._blocks.put(iq, timeout=1.0)
            except queue.Full:
                # The consumer is wedged; dropping is better than stalling the
                # reader, which would let the device's own buffer overflow.
                continue

    def _run(self) -> None:
        try:
            self._process_blocks()
        # The thread boundary is the one place a broad catch belongs: anything
        # escaping here would end listening silently while the UI still says
        # it is listening.
        except Exception as exc:
            self._report(f"monitor stopped: {exc}")
        finally:
            # Without a consumer the reader must not keep holding the tuner,
            # and an open clip is closed by the only thread that writes it.
            self._stop.set()
            self._recorder.stop()

    def _process_blocks(self) -> None:
        fs = effective_sample_rate(self._settings.scanner)
        rate = self._settings.audio.output_rate_hz
        while not self._stop.is_set():
            self._apply_record_request()
            try:
                iq = self._blocks.get(timeout=1.0)
            except queue.Empty:
                if self._reader is not None and not self._reader.is_alive():
                    # The reader died without managing to hand its error over.
                    # Staying in this loop would look like listening forever.
                    self._report(f"read failed: {self._read_error or 'reader stopped'}")
                    return
                continue
            if isinstance(iq, Exception):
                self._report(f"read failed: {iq}")
                return
            self.rssi_dbfs = rssi_dbfs(iq)
            if self._on_rssi:
                self._on_rssi(self.rssi_dbfs)
            audio = channel_audio(
                iq,
                fs,
                self._demod,
                rate,
                channel_bw_hz=self._channel_bw_hz,
                state=self._demod_state,
                deemphasis_us=self._settings.audio.deemphasis_us,
            )
            if len(audio):
                pcm = audio_to_pcm16(audio)
                if not self.muted:
                    self._player.write(self._playback_pcm(pcm))
                self._recorder.feed(pcm, rate, rssi_dbfs=self.rssi_dbfs)
            # No pacing here: the reader thread is already paced by the device,
            # and sleeping would only add to the deficit this split removes.
