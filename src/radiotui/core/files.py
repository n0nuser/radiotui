"""Crash-safe writes for files the user cannot regenerate."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def write_text_atomic(path: Path, text: str) -> None:
    """Replace ``path`` so a killed process or full disk leaves the old or new file.

    The temporary file lives next to the target so ``os.replace`` stays a
    same-filesystem rename, atomic on POSIX and Windows. ``fsync`` before the
    rename also covers a power loss. The temporary file is created normally
    (umask applies) and inherits an existing target's mode, so permissions
    survive the replace.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    replaced = False
    try:
        with tmp.open("w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if path.exists():
            shutil.copymode(path, tmp)
        os.replace(tmp, path)
        replaced = True
    finally:
        if not replaced:
            tmp.unlink(missing_ok=True)
