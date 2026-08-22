"""User channel file (~/.config/radiotui/channels.toml): bookmarks + ignore list.

Bookmarks give known frequencies a name (and optional demod); ignore entries
silence internal spurs ("birdies") so they never reach the table, the export,
or the autonomous hold gate. Optional: an absent file means no names, no
ignores. A malformed file degrades to empty with a single warning.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on py3.10
    import tomli as tomllib

from radiotui.core.models import DemodMode

#: Association window for matching live channels to bookmarks (mirrors the
#: tracker's 25 kHz peak-association tolerance).
BOOKMARK_WINDOW_HZ = 25_000.0

DEFAULT_IGNORE_WIDTH_HZ = 25_000.0

_SECTIONS = ("bookmark", "ignore")


def channels_file_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "radiotui" / "channels.toml"


class ChannelsFileError(Exception):
    """Invalid user channels file; message names the offending entry."""


@dataclass(frozen=True)
class Bookmark:
    freq_hz: float
    name: str
    demod: DemodMode | None = None


@dataclass(frozen=True)
class IgnoreEntry:
    freq_hz: float
    width_hz: float = DEFAULT_IGNORE_WIDTH_HZ
    note: str = ""

    def contains(self, freq_hz: float) -> bool:
        half = self.width_hz / 2
        return abs(freq_hz - self.freq_hz) <= half


@dataclass
class UserChannels:
    bookmarks: list[Bookmark] = field(default_factory=list)
    ignores: list[IgnoreEntry] = field(default_factory=list)

    def name_for(self, freq_hz: float) -> str:
        """Name of the closest bookmark within the association window."""
        best: Bookmark | None = None
        best_dist = BOOKMARK_WINDOW_HZ
        for bookmark in self.bookmarks:
            dist = abs(bookmark.freq_hz - freq_hz)
            if dist <= best_dist:
                best_dist = dist
                best = bookmark
        return best.name if best is not None else ""

    def ignored(self, freq_hz: float) -> bool:
        return any(entry.contains(freq_hz) for entry in self.ignores)


def _positive_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _parse_bookmark(entry: object, path: Path) -> Bookmark:
    if not isinstance(entry, dict):
        raise ChannelsFileError(f"[[bookmark]] entry must be a table in {path}")
    for required in ("freq_hz", "name"):
        if required not in entry:
            raise ChannelsFileError(f"[[bookmark]] entry missing '{required}' in {path}")
    if not _positive_number(entry["freq_hz"]):
        raise ChannelsFileError(f"[[bookmark]] 'freq_hz' must be a positive number in {path}")
    name = entry["name"]
    if not isinstance(name, str) or not name.strip():
        raise ChannelsFileError(f"[[bookmark]] 'name' must be a non-empty string in {path}")
    demod_value = entry.get("demod")
    demod: DemodMode | None = None
    if demod_value is not None:
        try:
            demod = DemodMode(str(demod_value).lower())
        except ValueError:
            valid = ", ".join(d.value for d in DemodMode)
            raise ChannelsFileError(
                f"[[bookmark]] has invalid demod '{demod_value}' (use: {valid}) in {path}"
            ) from None
    return Bookmark(float(entry["freq_hz"]), name.strip(), demod)


def _parse_ignore(entry: object, path: Path) -> IgnoreEntry:
    if not isinstance(entry, dict):
        raise ChannelsFileError(f"[[ignore]] entry must be a table in {path}")
    if "freq_hz" not in entry:
        raise ChannelsFileError(f"[[ignore]] entry missing 'freq_hz' in {path}")
    if not _positive_number(entry["freq_hz"]):
        raise ChannelsFileError(f"[[ignore]] 'freq_hz' must be a positive number in {path}")
    width = entry.get("width_hz", DEFAULT_IGNORE_WIDTH_HZ)
    if not _positive_number(width):
        raise ChannelsFileError(f"[[ignore]] 'width_hz' must be a positive number in {path}")
    note = entry.get("note", "")
    if not isinstance(note, str):
        raise ChannelsFileError(f"[[ignore]] 'note' must be a string in {path}")
    return IgnoreEntry(float(entry["freq_hz"]), float(width), note)


def parse_user_channels(text: str, path: Path | None = None) -> UserChannels:
    path = path or channels_file_path()
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ChannelsFileError(f"invalid TOML in {path}: {exc}") from None
    unknown = set(data) - set(_SECTIONS)
    if unknown:
        raise ChannelsFileError(
            f"unknown section(s) in {path}: {', '.join(sorted(unknown))}"
            f" (expected only: {', '.join(_SECTIONS)})"
        )
    entries = data.get("bookmark") or []
    if not isinstance(entries, list):
        raise ChannelsFileError(f"[[bookmark]] must be an array of tables in {path}")
    ignores = data.get("ignore") or []
    if not isinstance(ignores, list):
        raise ChannelsFileError(f"[[ignore]] must be an array of tables in {path}")
    return UserChannels(
        bookmarks=[_parse_bookmark(entry, path) for entry in entries],
        ignores=[_parse_ignore(entry, path) for entry in ignores],
    )


def load_user_channels(path: Path | None = None) -> tuple[UserChannels, str | None]:
    """Load the channels file; degrade to empty with one warning on problems.

    Returns ``(channels, warning)``; warning is None for an absent file.
    """
    path = path or channels_file_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return UserChannels(), None
    except OSError as exc:
        return UserChannels(), f"cannot read {path}: {exc}"
    try:
        return parse_user_channels(text, path), None
    except ChannelsFileError as exc:
        return UserChannels(), str(exc)


def _format_freq(freq_hz: float) -> str:
    return str(int(freq_hz)) if float(freq_hz).is_integer() else repr(float(freq_hz))


def _toml_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def serialize_user_channels(channels: UserChannels) -> str:
    lines = [
        "# radiotui user channels",
        "# [[bookmark]] freq_hz/name/demod - label known frequencies",
        "# [[ignore]]   freq_hz/width_hz/note - silence birdies and spurs",
    ]
    for bookmark in channels.bookmarks:
        lines += ["[[bookmark]]", f"freq_hz = {_format_freq(bookmark.freq_hz)}"]
        lines.append(f"name = {_toml_string(bookmark.name)}")
        if bookmark.demod is not None:
            lines.append(f'demod = "{bookmark.demod.value}"')
    for entry in channels.ignores:
        lines += ["[[ignore]]", f"freq_hz = {_format_freq(entry.freq_hz)}"]
        if entry.width_hz != DEFAULT_IGNORE_WIDTH_HZ:
            lines.append(f"width_hz = {_format_freq(entry.width_hz)}")
        if entry.note:
            lines.append(f"note = {_toml_string(entry.note)}")
    return "\n".join(lines) + "\n"


def save_user_channels(channels: UserChannels, path: Path | None = None) -> Path:
    path = path or channels_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize_user_channels(channels), encoding="utf-8")
    return path


def upsert_bookmark(channels: UserChannels, bookmark: Bookmark) -> bool:
    """Add or replace a bookmark; True when it replaces an existing entry.

    Replacement matches within the association window so re-naming a drifting
    carrier updates the existing row instead of piling up near-duplicates.
    """
    replaced = False
    kept: list[Bookmark] = []
    for existing in channels.bookmarks:
        if abs(existing.freq_hz - bookmark.freq_hz) <= BOOKMARK_WINDOW_HZ:
            replaced = True
            continue
        kept.append(existing)
    channels.bookmarks = [*kept, bookmark]
    return replaced


def remove_ignore(channels: UserChannels, freq_hz: float) -> bool:
    """Drop ignore windows covering ``freq_hz``; True when something was dropped."""
    kept = [entry for entry in channels.ignores if not entry.contains(freq_hz)]
    changed = len(kept) != len(channels.ignores)
    channels.ignores = kept
    return changed


def add_ignore(channels: UserChannels, entry: IgnoreEntry) -> bool:
    """Add an ignore window, replacing any entry already covering the frequency."""
    kept = [e for e in channels.ignores if not e.contains(entry.freq_hz)]
    replaced = len(kept) != len(channels.ignores)
    channels.ignores = [*kept, entry]
    return replaced
