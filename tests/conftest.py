import os
from pathlib import Path

import pytest

# Captured at import, before any test patches the environment.
REAL_CONFIG_DIR = (
    Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "radiotui"
).resolve()


@pytest.fixture(autouse=True)
def isolate_user_files(tmp_path, monkeypatch):
    """Keep config.toml, channels.toml and recordings out of the real home (#60)."""
    for var in ("HOME", "USERPROFILE", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(var, str(tmp_path))
    # The default recordings_dir is relative, so cwd decides where clips land.
    monkeypatch.chdir(tmp_path)
