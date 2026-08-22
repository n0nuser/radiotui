"""Issue #16: optional TOML config file with defaults < file < CLI flags precedence."""

import dataclasses

import pytest

from radiotui.config import BANDS, Settings
from radiotui.config_file import (
    ConfigError,
    apply_config_to_settings,
    apply_hardware_defaults,
    load_config,
    register_user_bands,
    runtime_settings,
)


@pytest.fixture
def cfg_path(tmp_path):
    return tmp_path / "config.toml"


def test_absent_file_means_defaults(cfg_path):
    assert load_config(cfg_path) == {}
    settings = Settings()
    apply_config_to_settings(settings, {}, cfg_path)
    default = Settings()
    for section in ("scanner", "audio"):
        for field in dataclasses.fields(getattr(default, section)):
            assert getattr(getattr(settings, section), field.name) == getattr(
                getattr(default, section), field.name
            )


def test_file_overrides_defaults(cfg_path):
    cfg_path.write_text(
        """
[scanner]
threshold_margin_db = 12.5
hop_dwell_s = 0.2

[audio]
recordings_dir = "~/radio/clips"
vox_threshold_dbfs = -40.0
"""
    )
    settings = Settings()
    apply_config_to_settings(settings, load_config(cfg_path), cfg_path)
    assert settings.scanner.threshold_margin_db == 12.5
    assert settings.scanner.hop_dwell_s == 0.2
    assert settings.audio.vox_threshold_dbfs == -40.0


def test_cli_flags_win_over_file():
    """Precedence: defaults -> file -> flags (flags applied after file)."""
    data = {"hardware": {"ppm": -70, "bias_tee": True}}
    args = type("NS", (), {})()  # no flags given: SUPPRESS left them absent
    apply_hardware_defaults(args, data)
    assert args.ppm == -70
    assert args.bias_tee is True

    args2 = type("NS", (), {"ppm": 5, "bias_tee": False})()  # flags present
    apply_hardware_defaults(args2, data)
    assert args2.ppm == 5
    assert args2.bias_tee is False


def test_runtime_settings_gain_precedence():
    args = type("NS", (), {"gain": 14.4, "_hw_gain_db": 28.0})()
    settings = runtime_settings(args, {})
    assert settings.scanner.gain_db == 14.4
    args2 = type("NS", (), {"gain": None, "_hw_gain_db": 28.0})()
    assert runtime_settings(args2, {}).scanner.gain_db == 28.0
    args3 = type("NS", (), {"gain": None, "_hw_gain_db": None})()
    assert runtime_settings(args3, {}).scanner.gain_db is None


def test_recordings_dir_expands_home(cfg_path):
    cfg_path.write_text('[audio]\nrecordings_dir = "~/radio"\n')
    settings = Settings()
    apply_config_to_settings(settings, load_config(cfg_path), cfg_path)
    assert "~" not in settings.audio.recordings_dir


def test_invalid_toml_reports_cleanly(cfg_path):
    cfg_path.write_text("[scanner\nthreshold= oops")
    with pytest.raises(ConfigError) as excinfo:
        load_config(cfg_path)
    assert str(cfg_path) in str(excinfo.value)


def test_unknown_key_is_reported_with_line(cfg_path):
    cfg_path.write_text("[scanner]\nthreshold_margin_db = 9.0\nnot_a_setting = 1\n")
    with pytest.raises(ConfigError) as excinfo:
        apply_config_to_settings(Settings(), load_config(cfg_path), cfg_path)
    assert "not_a_setting" in str(excinfo.value)
    assert "line 3" in str(excinfo.value)


def test_wrong_type_is_reported(cfg_path):
    cfg_path.write_text("[audio]\nvox_threshold_dbfs = 'loud'\n")
    with pytest.raises(ConfigError) as excinfo:
        apply_config_to_settings(Settings(), load_config(cfg_path), cfg_path)
    assert "vox_threshold_dbfs" in str(excinfo.value)


VALID_BAND = """
[[band]]
name = "local_repeaters"
label = "Local repeaters"
start_hz = 145_600_000
end_hz = 145_800_000
demod = "nfm"
channel_bw_hz = 12_500
"""


def test_user_band_registers_and_is_reachable(cfg_path):
    cfg_path.write_text(VALID_BAND)
    data = load_config(cfg_path)
    registered = register_user_bands(data, cfg_path)
    try:
        assert registered == ["local_repeaters"]
        from radiotui.config import band_by_name

        band = band_by_name("local_repeaters")
        assert band.start_hz == 145_600_000
        assert sorted(BANDS).index("local_repeaters") >= 0  # gets a number key slot
    finally:
        BANDS.pop("local_repeaters", None)


@pytest.mark.parametrize(
    "entry",
    [
        {"name": "x", "label": "X", "start_hz": 100e6},  # missing fields
        {"name": "fm_broadcast", "label": "dup", "start_hz": 88e6, "end_hz": 108e6, "demod": "wfm"},
        {"name": "bad", "label": "B", "start_hz": 200e6, "end_hz": 100e6, "demod": "am"},
        {"name": "bad", "label": "B", "start_hz": 100e6, "end_hz": 200e6, "demod": "usb"},
    ],
)
def test_bad_band_entries_raise(entry, cfg_path):
    with pytest.raises(ConfigError):
        register_user_bands({"band": [entry]}, cfg_path)


def test_unknown_section_rejected(cfg_path):
    cfg_path.write_text("[scannr]\nhop_dwell_s = 0.2\n")
    with pytest.raises(ConfigError) as excinfo:
        load_config(cfg_path)
    assert "scannr" in str(excinfo.value)
