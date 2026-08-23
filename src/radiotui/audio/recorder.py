"""VOX-gated WAV recorder: captures transmissions, splits on silence.

Each kept clip gets a ``.json`` sidecar next to the WAV carrying the context
that produced it (frequency, demod, band, hardware settings, RSSI) so an
overnight run stays traceable (#20). Clips shorter than
``AudioSettings.min_clip_seconds`` are discarded instead of written.
"""

from __future__ import annotations

import json
import time
import wave
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from radiotui.config import AudioSettings

MAX_CLIP_SECONDS = 120.0


def pcm_rms_dbfs(pcm: bytes) -> float:
    samples = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float64)
    if len(samples) == 0:
        return -120.0
    rms = np.sqrt(np.mean((samples / 32768.0) ** 2))
    return 20.0 * np.log10(rms + 1e-12)


def _iso(epoch_s: float) -> str:
    return datetime.fromtimestamp(epoch_s, tz=timezone.utc).isoformat()


@dataclass
class ClipInfo:
    path: Path
    freq_hz: float
    seconds: float
    started_at: float = 0.0
    ended_at: float = 0.0
    peak_rssi_dbfs: float = -120.0
    mean_rssi_dbfs: float = -120.0
    demod: str = ""
    band: str = ""
    hardware: dict = field(default_factory=dict)


class VoxRecorder:
    def __init__(
        self,
        freq_hz: float,
        settings: AudioSettings,
        context: dict | None = None,
        squelch_rssi_dbfs: float | None = None,
    ) -> None:
        self._freq = freq_hz
        self._settings = settings
        self._squelch_dbfs = squelch_rssi_dbfs
        self._dir = Path(settings.recordings_dir)
        self._file: wave.Wave_write | None = None
        self._path: Path | None = None
        self._rate = settings.output_rate_hz
        self._frames_written = 0
        self._hang_remaining_ms = 0.0
        self.clips: list[ClipInfo] = []
        self.on_clip_end = None
        self.on_error = None
        self.enabled = False
        self.last_voice_ts: float | None = None
        extra = context or {}
        self._demod = str(extra.get("demod", ""))
        self._band = str(extra.get("band", ""))
        hw = extra.get("hardware")
        self._hardware = dict(hw) if isinstance(hw, dict) else {}
        self._clip_started_at = 0.0
        self._rssi_values: list[float] = []

    @property
    def directory(self) -> Path:
        return self._dir

    @property
    def recording(self) -> bool:
        return self._file is not None

    def seconds_since_voice(self, now: float | None = None) -> float:
        now = time.time() if now is None else now
        if self.last_voice_ts is None:
            return float("inf")
        return now - self.last_voice_ts

    def feed(self, pcm: bytes, rate_hz: int | None = None, rssi_dbfs: float | None = None) -> None:
        if rate_hz is not None:
            self._rate = rate_hz
        level_dbfs = pcm_rms_dbfs(pcm)
        block_ms = 1000.0 * (len(pcm) / 2) / self._rate
        voiced = level_dbfs > self._settings.vox_threshold_dbfs
        if self._squelch_dbfs is not None and (rssi_dbfs is None or rssi_dbfs < self._squelch_dbfs):
            voiced = False  # RF gate: no carrier on frequency, however hot the hiss
        if voiced:
            self.last_voice_ts = time.time()
        if self._file is not None and rssi_dbfs is not None:
            self._rssi_values.append(rssi_dbfs)
        if not self.enabled:
            return
        if voiced:
            if self._file is None:
                self._open_clip()
            self._write(pcm)
            self._hang_remaining_ms = self._settings.vox_hang_ms
        elif self._file is not None:
            self._hang_remaining_ms -= block_ms
            if self._hang_remaining_ms <= 0:
                self._close_clip()
            else:
                self._write(pcm)

    def stop(self) -> list[ClipInfo]:
        self._close_clip()
        return self.clips

    def _open_clip(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        mhz = self._freq / 1e6
        self._path = self._dir / f"{mhz:.4f}MHz_{stamp}.wav"
        self._file = wave.open(str(self._path), "wb")
        self._file.setnchannels(1)
        self._file.setsampwidth(2)
        self._file.setframerate(self._rate)
        self._frames_written = 0
        self._clip_started_at = time.time()
        self._rssi_values = []

    def _write(self, pcm: bytes) -> None:
        if self._file is None:
            return
        self._file.writeframes(pcm)
        self._frames_written += len(pcm) // 2
        if self._frames_written >= MAX_CLIP_SECONDS * self._rate:
            self._close_clip()

    def _close_clip(self) -> None:
        if self._file is None:
            return
        frames = self._frames_written
        path = self._path
        started_at = self._clip_started_at
        rssi_values = list(self._rssi_values)
        self._file.close()
        self._file = None
        self._path = None
        seconds = frames / self._rate
        if path is None or seconds < self._settings.min_clip_seconds:
            # Sub-second VOX blips are noise; leave nothing behind.
            if path is not None:
                path.unlink(missing_ok=True)
            return
        ended_at = time.time()
        info = ClipInfo(
            path=path,
            freq_hz=self._freq,
            seconds=seconds,
            started_at=started_at,
            ended_at=ended_at,
            peak_rssi_dbfs=max(rssi_values) if rssi_values else -120.0,
            mean_rssi_dbfs=sum(rssi_values) / len(rssi_values) if rssi_values else -120.0,
            demod=self._demod,
            band=self._band,
            hardware=dict(self._hardware),
        )
        self.clips.append(info)
        self._write_sidecar(info)
        if self.on_clip_end:
            self.on_clip_end(info)

    def _write_sidecar(self, clip: ClipInfo) -> None:
        payload = {
            "freq_hz": clip.freq_hz,
            "demod": clip.demod,
            "band": clip.band,
            "started_at": _iso(clip.started_at),
            "ended_at": _iso(clip.ended_at),
            "duration_s": round(clip.seconds, 3),
            "peak_rssi_dbfs": round(clip.peak_rssi_dbfs, 1),
            "mean_rssi_dbfs": round(clip.mean_rssi_dbfs, 1),
            "hardware": clip.hardware,
        }
        try:
            sidecar = clip.path.with_suffix(".json")
            sidecar.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            # The WAV itself is safe; surface the metadata failure if anyone listens.
            if self.on_error:
                self.on_error(f"sidecar write failed for {clip.path.name}: {exc}")
