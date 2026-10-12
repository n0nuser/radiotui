"""Issue #60: no test may resolve user files under the real ~/.config/radiotui."""

from pathlib import Path

from radiotui.channels_file import channels_file_path
from radiotui.config_file import config_path
from tests.conftest import REAL_CONFIG_DIR


def test_user_files_resolve_under_tmp_path(tmp_path):
    for path in (config_path(), channels_file_path(), Path.home(), Path.cwd()):
        assert path.resolve().is_relative_to(tmp_path.resolve())
        assert not path.resolve().is_relative_to(REAL_CONFIG_DIR)
