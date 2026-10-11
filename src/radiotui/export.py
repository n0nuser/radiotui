"""Channel export: CSV for spreadsheets, self-describing JSON for scripts."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

from radiotui.core.files import write_text_atomic
from radiotui.core.models import Channel

_CSV_FIELDS = (
    "center_hz",
    "name",
    "bandwidth_hz",
    "peak_db",
    "snr_db",
    "demod",
    "hits",
    "first_seen",
    "last_seen",
)


def iso_ts(epoch_s: float) -> str:
    """ISO 8601 UTC timestamp so exported files are unambiguous."""
    return datetime.fromtimestamp(epoch_s, tz=timezone.utc).isoformat()


def export_path_for(band_name: str, directory: Path | str, fmt: str = "csv") -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path(directory) / f"radiotui-{band_name}-{stamp}.{fmt}"


def export_channels(
    channels: list[Channel],
    path: Path | str,
    context: dict | None = None,
) -> Path:
    """Write ``channels`` to ``path`` (.json -> JSON, anything else -> CSV)."""
    path = Path(path)
    ordered = sorted(channels, key=lambda ch: ch.center_hz)
    if path.suffix.lower() == ".json":
        payload = {
            "exported_at": iso_ts(datetime.now().timestamp()),
            "context": context or {},
            "channels": [_channel_dict(ch) for ch in ordered],
        }
        text = json.dumps(payload, indent=2)
    else:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        for ch in ordered:
            row = _channel_dict(ch)
            writer.writerow({k: row[k] for k in _CSV_FIELDS})
        text = buffer.getvalue()
    # Headless scans re-export to the same path every 30 s.
    write_text_atomic(path, text)
    return path


def _channel_dict(channel: Channel) -> dict:
    return {
        "center_hz": float(channel.center_hz),
        "name": channel.name,
        "bandwidth_hz": float(channel.bandwidth_hz),
        "peak_db": round(float(channel.peak_db), 1),
        "snr_db": round(float(channel.snr_db), 1),
        "demod": channel.demod.value,
        "hits": int(channel.hits),
        "first_seen": iso_ts(channel.first_seen),
        "last_seen": iso_ts(channel.last_seen),
    }
