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
        # Writes in flight. A write can block on a full pipe, so it runs
        # outside the lock; stop() reads this to know whether it must kill
        # the backend to unblock that write instead of draining it.
        self._writers = 0
        self.backend: str | None = None

    def start(self) -> bool:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return True
            for argv in (
                [
                    "ffplay",
                    "-autoexit",  # exit on stdin EOF, or a dead parent orphans it
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
                    "-autoexit",  # exit on stdin EOF, or a dead parent orphans it
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
                    "--buffer-size=65536",
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
            proc = self._proc
            if proc is None or proc.poll() is not None or proc.stdin is None:
                return
            self._writers += 1
        try:
            proc.stdin.write(pcm)
            proc.stdin.flush()
        except (OSError, ValueError):
            pass  # the backend died or stop() closed the pipe: drop the audio
        finally:
            with self._lock:
                self._writers -= 1

    def stop(self) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
            writing = self._writers > 0
        if proc is None:
            return
        if writing:
            # A write is (or may be) blocked on a full pipe, and closing stdin
            # would wait behind it; killing the reader turns it into EPIPE.
            proc.kill()
        try:
            if proc.stdin is not None:
                proc.stdin.close()
            proc.wait(timeout=2.0)
        except (OSError, ValueError, subprocess.SubprocessError):
            proc.kill()
            proc.wait()  # reap it, or it lingers as a zombie

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None
