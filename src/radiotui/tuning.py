"""Frequency parsing and demod guessing shared by the CLI and the TUI."""

from __future__ import annotations

import argparse

from radiotui.config import BANDS
from radiotui.core.models import DemodMode


def parse_freq(value: str) -> float:
    v = value.strip().lower().replace(" ", "")
    multipliers = {"ghz": 1e9, "mhz": 1e6, "m": 1e6, "khz": 1e3, "k": 1e3, "hz": 1.0}
    for suffix, mult in sorted(multipliers.items(), key=lambda kv: -len(kv[0])):
        if v.endswith(suffix):
            return float(v[: -len(suffix)]) * mult
    try:
        return float(v)
    except ValueError:
        raise argparse.ArgumentTypeError(f"cannot parse frequency '{value}'") from None


def guess_demod(freq_hz: float) -> DemodMode:
    for band in BANDS.values():
        if band.start_hz <= freq_hz <= band.end_hz:
            return band.demod
    return DemodMode.NFM


def token_to_hz(token: str) -> float:
    """Bare numbers mean MHz; anything else goes through parse_freq."""
    try:
        return float(token) * 1e6
    except ValueError:
        pass
    try:
        return parse_freq(token)
    except argparse.ArgumentTypeError as exc:
        raise ValueError(str(exc)) from None


def parse_tune_request(text: str) -> tuple[float, float | None, DemodMode]:
    """Parse a tune entry: '145.5', '430-440', optional ' nfm|wfm|am' demod suffix.

    Returns ``(freq_hz, None, demod)`` for a single frequency or
    ``(start_hz, end_hz, demod)`` for a range.
    """
    parts = text.strip().lower().split()
    if len(parts) == 2 and parts[1] in {d.value for d in DemodMode}:
        demod = DemodMode(parts[1])
        spec = parts[0]
    elif len(parts) == 1:
        demod = None
        spec = parts[0] if parts else ""
    else:
        raise ValueError(f"cannot parse '{text.strip()}'")
    if not spec:
        raise ValueError("enter a frequency or a start-end range")
    if "-" in spec:
        lo, _, hi = spec.partition("-")
        try:
            start_hz, end_hz = token_to_hz(lo), token_to_hz(hi)
        except ValueError:
            raise ValueError(f"cannot parse range '{spec}'") from None
        if end_hz <= start_hz:
            raise ValueError(f"end of range must be above start in '{spec}'")
        return start_hz, end_hz, demod
    return token_to_hz(spec), None, demod
