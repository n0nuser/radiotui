"""VOX-gated WAV recorder: captures transmissions, splits on silence."""

from __future__ import annotations

import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from radiotui.config import AudioSettings

MAX_CLIP_SECONDS = 120.0
MIN_CLIP_SECONDS = 0.5


def pcm_rms_dbfs(pcm: bytes) -> float:
    samples = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float64)
    if len(samples) == 0:
        return -120.0
    rms = np.sqrt(np.mean((samples / 32768.0) ** 2))
    return 20.0 * np.log10(rms + 1e-12)


@dataclass
class ClipInfo:
    path: Path
    freq_hz: float
    seconds: float


class VoxRecorder:
    def __init__(self, freq_hz: float, settings: AudioSettings) -> None:
        self._freq = freq_hz
        self._settings = settings
        self._dir = Path(settings.recordings_dir)
        self._file: wave.Wave_write | None = None
        self._path: Path | None = None
        self._rate = settings.output_rate_hz
        self._frames_written = 0
        self._hang_remaining_ms = 0.0
        self.clips: list[ClipInfo] = []
        self.on_clip_end = None
        self.enabled = False

    @property
    def directory(self) -> Path:
        return self._dir

    @property
    def recording(self) -> bool:
        return self._file is not None

    def feed(self, pcm: bytes, rate_hz: int | None = None) -> None:
        if not self.enabled:
            return
        if rate_hz is not None:
            self._rate = rate_hz
        level_dbfs = pcm_rms_dbfs(pcm)
        block_ms = 1000.0 * (len(pcm) / 2) / self._rate
        voiced = level_dbfs > self._settings.vox_threshold_dbfs
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
        self._file.close()
        self._file = None
        self._path = None
        if path is not None and frames / self._rate < MIN_CLIP_SECONDS:
            path.unlink(missing_ok=True)
            return
        if path is not None and frames > 0:
            info = ClipInfo(path=path, freq_hz=self._freq, seconds=frames / self._rate)
            self.clips.append(info)
            if self.on_clip_end:
                self.on_clip_end(info)
