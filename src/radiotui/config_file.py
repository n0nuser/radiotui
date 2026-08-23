"""User configuration file (~/.config/radiotui/config.toml).

Merged as defaults -> config file -> CLI flags. Optional: an absent file
means built-in behaviour exactly.
"""

from __future__ import annotations

import dataclasses
import os
import sys
import typing
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on py3.10
    import tomli as tomllib

from radiotui.config import (
    BANDS,
    DWELL_RANGE_S,
    MIN_SNR_RANGE,
    THRESHOLD_MARGIN_RANGE,
    Band,
    Settings,
    clamp,
    clamp_volume_db,
)
from radiotui.core.models import DemodMode

_HW_KEYS = {"ppm": int, "bias_tee": bool, "offset_tune": bool, "gain_db": float}

_SECTIONS = {"scanner", "audio"}


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "radiotui" / "config.toml"


class ConfigError(Exception):
    """Invalid user configuration; message names the offending key and file."""


def _line_of(text: str, needle: str) -> int | None:
    """Best-effort line number for a semantic error about ``needle``."""
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.split("#", 1)[0]
        if stripped.strip().startswith(needle) or f"{needle} =" in stripped:
            return lineno
    return None


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Parse the config file; {} when absent."""
    path = path or config_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from None
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from None
    unknown = set(data) - _SECTIONS - {"hardware", "band"}
    if unknown:
        raise ConfigError(f"unknown section(s) in {path}: {', '.join(sorted(unknown))}")
    return data


def _resolve_field_type(annotation: Any) -> Any:
    """Resolve string annotations (PEP 563) and unwrap Optional[X] to X."""
    if isinstance(annotation, str):
        annotation = {
            "float": float,
            "int": int,
            "bool": bool,
            "str": str,
        }.get(annotation, annotation)
    if typing.get_origin(annotation) is typing.Union:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        return args[0] if len(args) == 1 else object
    return {"float": float, "int": int, "bool": bool, "str": str}.get(annotation, annotation)


def _key_error(path: Path, message: str, key: str) -> ConfigError:
    """Build an error that names the offending key and its file line."""
    line = None
    try:
        for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if raw.split("#", 1)[0].strip().startswith(key):
                line = lineno
                break
    except OSError:
        pass
    where = f" ({path}, line {line})" if line else f" ({path})"
    return ConfigError(f"{message}: '{key}'{where}")


def apply_config_to_settings(
    settings: Settings, data: dict[str, Any], path: Path | None = None
) -> None:
    """Overlay [scanner]/[audio] values onto a Settings instance."""
    path = path or config_path()
    for section in sorted(_SECTIONS):
        overrides = data.get(section) or {}
        target = getattr(settings, section)
        fields = {f.name: f.type for f in dataclasses.fields(type(target))}
        for key, value in overrides.items():
            if key not in fields:
                raise _key_error(path, f"unknown key '[{section}]'", key)
            expected = _resolve_field_type(fields[key])
            if expected is float and isinstance(value, int) and not isinstance(value, bool):
                value = float(value)
            elif not isinstance(value, expected) or (
                isinstance(value, bool) and expected is not bool
            ):
                name = getattr(expected, "__name__", str(expected))
                raise _key_error(
                    path,
                    f"'[{section}] {key}' must be {name}, got {type(value).__name__} at",
                    key,
                )
            try:
                value = _validate_setting(section, key, value)
            except ValueError as exc:
                raise _key_error(path, str(exc), key) from None
            setattr(target, key, value)
    settings.audio.recordings_dir = os.path.expanduser(settings.audio.recordings_dir)


def _validate_setting(section: str, key: str, value: Any) -> Any:
    """Apply the same safety bounds used by interactive and CLI controls."""
    if section == "scanner":
        if key == "threshold_margin_db":
            return clamp(value, *THRESHOLD_MARGIN_RANGE)
        if key == "min_snr_db":
            return clamp(value, *MIN_SNR_RANGE)
        if key == "hop_dwell_s":
            return clamp(value, *DWELL_RANGE_S)
        if key == "fft_size" and value < 8:
            raise ValueError("fft_size must be at least 8")
        if key == "sample_rate_hz" and not 900_000 <= value <= 3_200_000:
            raise ValueError("sample_rate_hz is outside the RTL-SDR range")
        if key in {"min_persist_frames", "drop_after_misses", "history_size"} and value < 1:
            raise ValueError(f"{key} must be positive")
        if key == "peak_merge_gap_bins" and value < 0:
            raise ValueError("peak_merge_gap_bins cannot be negative")
        if key == "region" and value not in {"r1", "r2", "r3"}:
            raise ValueError("region must be one of r1, r2, or r3")
    elif section == "audio":
        if key in {"output_rate_hz", "block_size"} and value < 1:
            raise ValueError(f"{key} must be positive")
        if key == "vox_hang_ms" and value < 0:
            raise ValueError("vox_hang_ms cannot be negative")
        if key == "volume_db":
            return clamp_volume_db(value)
        if key == "min_clip_seconds" and value < 0:
            raise ValueError("min_clip_seconds cannot be negative")
        if key == "deemphasis_us" and value not in {50, 75}:
            raise ValueError("deemphasis_us must be 50 or 75")
    return value


def register_user_bands(data: dict[str, Any], path: Path | None = None) -> list[str]:
    """Add [[band]] entries to BANDS; returns the names registered."""
    path = path or config_path()
    entries = data.get("band") or []
    registered: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ConfigError(f"[[band]] entry must be a table in {path}")
        for required in ("name", "label", "start_hz", "end_hz", "demod"):
            if required not in entry:
                raise ConfigError(f"[[band]] entry missing '{required}' in {path}")
        name = str(entry["name"])
        if name in BANDS or name in registered:
            raise ConfigError(f"duplicate band name '{name}' in {path}")
        try:
            demod = DemodMode(str(entry["demod"]).lower())
        except ValueError:
            valid = ", ".join(d.value for d in DemodMode)
            raise ConfigError(
                f"band '{name}' has invalid demod '{entry['demod']}' (use: {valid})"
            ) from None
        start_hz, end_hz = entry["start_hz"], entry["end_hz"]
        for field_name in ("start_hz", "end_hz"):
            value = entry[field_name]
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ConfigError(f"band '{name}': '{field_name}' must be a number")
        if end_hz <= start_hz:
            raise ConfigError(f"band '{name}': end_hz must be above start_hz")
        bw = entry.get("channel_bw_hz", 12_500.0)
        if not isinstance(bw, (int, float)) or isinstance(bw, bool):
            raise ConfigError(f"band '{name}': 'channel_bw_hz' must be a number")
        BANDS[name] = Band(
            name, str(entry["label"]), float(start_hz), float(end_hz), demod, float(bw)
        )
        registered.append(name)
    return registered


def apply_hardware_defaults(args, data: dict[str, Any]) -> None:
    """Fill CLI-flag gaps from [hardware]; explicit flags keep winning."""
    hw = data.get("hardware") or {}
    for key in hw:
        if key not in _HW_KEYS:
            raise _key_error(config_path(), "unknown key '[hardware]'", key)
    ppm = hw.get("ppm")
    if isinstance(ppm, bool) or (ppm is not None and not isinstance(ppm, int)):
        raise ConfigError("'[hardware] ppm' must be an integer")
    gain = hw.get("gain_db")
    if gain is not None and (isinstance(gain, bool) or not isinstance(gain, (int, float))):
        raise ConfigError("'[hardware] gain_db' must be a number")

    args._hw_gain_db = None if gain is None else float(gain)
    if not hasattr(args, "ppm") and ppm is not None:
        args.ppm = int(ppm)
    if not hasattr(args, "bias_tee") and hw.get("bias_tee") is not None:
        args.bias_tee = bool(hw["bias_tee"])
    if not hasattr(args, "offset_tune") and hw.get("offset_tune") is not None:
        args.offset_tune = bool(hw["offset_tune"])


def runtime_settings(args, data: dict[str, Any]) -> Settings:
    """Settings for this run: defaults <- config file <- CLI scanner flags."""
    settings = Settings()
    apply_config_to_settings(settings, data)
    gain = args.gain if hasattr(args, "gain") else None
    if gain is None:
        gain = args._hw_gain_db
    if gain is not None:
        settings.scanner.gain_db = float(gain)
    return settings
