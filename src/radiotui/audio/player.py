"""PCM playback through ffplay or aplay."""

from __future__ import annotations

import shutil
import subprocess
import threading


class AudioPlayer:
    def __init__(self, rate_hz: int = 48_000) -> None:
        self.rate_hz = rate_hz
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self.backend: str | None = None

    def start(self) -> bool:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return True
            for argv in (
                [
                    "ffplay",
                    "-loglevel",
                    "quiet",
                    "-nostats",
                    "-nodisp",
                    "-f",
                    "s16le",
                    "-ar",
                    str(self.rate_hz),
                    "-ch_layout",
                    "mono",
                    "-i",
                    "-",
                ],
                [
                    "ffplay",
                    "-loglevel",
                    "quiet",
                    "-nostats",
                    "-nodisp",
                    "-f",
                    "s16le",
                    "-ar",
                    str(self.rate_hz),
                    "-ac",
                    "1",
                    "-i",
                    "-",
                ],
                [
                    "aplay",
                    "-q",
                    "-f",
                    "S16_LE",
                    "-r",
                    str(self.rate_hz),
                    "-c",
                    "1",
                    "-t",
                    "raw",
                    "-",
                ],
            ):
                if shutil.which(argv[0]) is None:
                    continue
                try:
                    self._proc = subprocess.Popen(
                        argv,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    self.backend = argv[0]
                    return True
                except OSError:
                    continue
            self.backend = None
            return False

    def write(self, pcm: bytes) -> None:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                return
            try:
                assert self._proc.stdin is not None
                self._proc.stdin.write(pcm)
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError):
                pass

    def stop(self) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
        if proc is not None:
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
                proc.wait(timeout=2.0)
            except (OSError, subprocess.SubprocessError):
                proc.kill()

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None
