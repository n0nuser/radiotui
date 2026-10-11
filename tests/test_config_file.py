"""Issue #16: optional TOML config file with defaults < file < CLI flags precedence."""

import dataclasses
import json
import re
from pathlib import Path

import pytest

from radiotui.cli import main, resolve_args
from radiotui.config import BANDS, Settings, band_by_name
from radiotui.config_file import (
    ConfigError,
    apply_config_to_settings,
    apply_hardware_defaults,
    config_path,
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


# ---- issue #59: optional fields, clean errors, --no-* flags, sidecars ----


def write_user_config(text: str) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def readme_config_snippets() -> list[str]:
    readme = (Path(__file__).parent.parent / "README.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```toml\n(.*?)```", readme, flags=re.S)
    # channels.toml snippets are covered by test_user_channels.py
    return [b for b in blocks if "[[bookmark]]" not in b and "[[ignore]]" not in b]


@pytest.mark.parametrize("snippet", readme_config_snippets())
def test_readme_config_snippets_parse(snippet, capsys):
    write_user_config(snippet)
    before = set(BANDS)
    try:
        assert main(["config"]) == 0
    finally:
        for name in set(BANDS) - before:
            BANDS.pop(name)
    assert "error" not in capsys.readouterr().out.lower()


def test_readme_has_a_config_snippet():
    assert readme_config_snippets()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("squelch_rssi_dbfs = -30", -30.0),
        ("squelch_rssi_dbfs = -30.5", -30.5),
        ("", None),  # omitting the key disables the gate
    ],
)
def test_optional_field_round_trips(cfg_path, text, expected):
    cfg_path.write_text(f"[scanner]\n{text}\ngain_db = 28\n")
    settings = Settings()
    apply_config_to_settings(settings, load_config(cfg_path), cfg_path)
    assert settings.scanner.squelch_rssi_dbfs == expected
    assert settings.scanner.gain_db == 28.0
    assert isinstance(settings.scanner.gain_db, float)


@pytest.mark.parametrize("value", ['"off"', "true"])
def test_optional_field_rejects_non_numbers(cfg_path, value):
    cfg_path.write_text(f"[scanner]\nsquelch_rssi_dbfs = {value}\n")
    with pytest.raises(ConfigError, match="squelch_rssi_dbfs"):
        apply_config_to_settings(Settings(), load_config(cfg_path), cfg_path)


@pytest.mark.parametrize(
    ("text", "argv", "needle"),
    [
        ("[scanner]\nfft_size = 2\n", ["scan", "--sim", "--band", "fm_broadcast"], "fft_size"),
        ("[scanner]\nfft_size = 2\n", ["config"], "fft_size"),
        ("[audio]\ndeemphasis_us = 60\n", ["scan", "--sim", "--band", "fm_broadcast"], "50"),
        (
            '[hardware]\ngain_db = "x"\n',
            ["scan", "--sim", "--band", "fm_broadcast"],
            "[hardware] gain_db",
        ),
    ],
)
def test_config_errors_exit_cleanly(text, argv, needle, capsys):
    write_user_config(text)
    assert main(argv) == 2
    out = capsys.readouterr().out
    assert "Traceback" not in out
    errors = [line for line in out.splitlines() if "Config error" in line]
    assert len(errors) == 1  # one unwrapped line, even for long paths
    assert needle in errors[0]


@pytest.mark.parametrize(
    "argv",
    [
        ["--no-bias-tee", "--no-offset-tune", "scan", "--band", "fm_broadcast"],
        ["scan", "--band", "fm_broadcast", "--no-bias-tee", "--no-offset-tune"],
    ],
)
def test_no_flags_override_file(argv):
    data = {"hardware": {"bias_tee": True, "offset_tune": True}}
    args = resolve_args(argv, data)
    assert args.bias_tee is False
    assert args.offset_tune is False
    assert resolve_args(["scan", "--band", "fm_broadcast"], data).bias_tee is True


def test_sidecar_reports_applied_hardware_settings():
    write_user_config("[hardware]\nbias_tee = true\n")
    assert (
        main(["record", "--sim", "--ppm", "-70", "--offset-tune", "--seconds", "2", "89.005M"]) == 0
    )
    sidecars = list(Path("recordings").glob("*.json"))
    assert sidecars, "no clip recorded"
    hardware = json.loads(sidecars[0].read_text(encoding="utf-8"))["hardware"]
    assert hardware["freq_correction_ppm"] == -70
    assert hardware["bias_tee"] is True
    assert hardware["offset_tune"] is True


def test_scan_export_reports_applied_hardware_settings():
    target = Path("found.json")
    argv = ["scan", "--sim", "--band", "fm_broadcast", "--ppm", "-70", "--no-bias-tee"]
    assert main([*argv, "--seconds", "2", "--export", str(target)]) == 0
    context = json.loads(target.read_text(encoding="utf-8"))["context"]
    assert context["freq_correction_ppm"] == -70
    assert context["bias_tee"] is False
