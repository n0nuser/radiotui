"""Issue #15: bookmarks + ignore list in ~/.config/radiotui/channels.toml."""

import csv
import json

import pytest
from textual.widgets import DataTable, RichLog

from radiotui.channels_file import (
    DEFAULT_IGNORE_WIDTH_HZ,
    Bookmark,
    ChannelsFileError,
    IgnoreEntry,
    UserChannels,
    add_ignore,
    load_user_channels,
    parse_user_channels,
    remove_ignore,
    save_user_channels,
    upsert_bookmark,
)
from radiotui.config import ScannerSettings, Settings
from radiotui.core.models import Channel, DemodMode, Peak
from radiotui.dsp.detector import ChannelTracker
from radiotui.export import export_channels
from radiotui.tui.app import RadioTuiApp

T0 = 1_000_000.0


def book_path(tmp_path):
    return tmp_path / "channels.toml"


def make_channel(
    freq_hz: float, peak_db: float = -20.0, name: str = "", active: bool = False
) -> Channel:
    return Channel(
        center_hz=freq_hz,
        bandwidth_hz=12_500.0,
        peak_db=peak_db,
        snr_db=peak_db - 10,
        demod=DemodMode.WFM,
        first_seen=T0,
        last_seen=T0 + 5,
        active=active,
        name=name,
    )


def make_peak(freq_hz: float, peak_db: float = -20.0) -> Peak:
    return Peak(center_hz=freq_hz, bandwidth_hz=12_500.0, peak_db=peak_db, snr_db=peak_db - 10)


VALID_TOML = """
[[bookmark]]
freq_hz = 145_500_000
name = "2m calling"
demod = "nfm"

[[bookmark]]
freq_hz = 97.5e6
name = "local FM"

[[ignore]]
freq_hz = 96_000_000
width_hz = 20_000
note = "birdie"
"""


# ---- parsing / loading ------------------------------------------------------


def test_missing_file_degrades_silently(tmp_path):
    loaded, warning = load_user_channels(book_path(tmp_path))
    assert warning is None
    assert loaded == UserChannels()


def test_parse_valid_file():
    parsed = parse_user_channels(VALID_TOML)
    assert parsed.bookmarks == [
        Bookmark(145.5e6, "2m calling", DemodMode.NFM),
        Bookmark(97.5e6, "local FM", None),
    ]
    assert parsed.ignores == [IgnoreEntry(96.0e6, 20_000.0, "birdie")]


@pytest.mark.parametrize(
    "text",
    [
        "not toml [",
        "[[unknown]]",
        '[[bookmark]]\nname = "no freq"',
        '[[bookmark]]\nfreq_hz = 0\nname = "x"',
        '[[bookmark]]\nfreq_hz = 1e6\nname = ""',
        "[[bookmark]]\nfreq_hz = 1e6",
        '[[bookmark]]\nfreq_hz = 1e6\nname = "x"\ndemod = "usb"',
        "[[ignore]]\nwidth_hz = 100",
        "[[ignore]]\nfreq_hz = -5",
        "[[ignore]]\nfreq_hz = 1e6\nwidth_hz = 0",
        "[[ignore]]\nfreq_hz = 1e6\nnote = 7",
    ],
)
def test_malformed_entries_are_rejected_with_location(text):
    with pytest.raises(ChannelsFileError) as exc:
        parse_user_channels(text)
    assert "channels.toml" in str(exc.value)


def test_load_malformed_file_degrades_to_empty_with_one_warning(tmp_path):
    path = book_path(tmp_path)
    path.write_text("{{{{ not toml", encoding="utf-8")
    loaded, warning = load_user_channels(path)
    assert loaded == UserChannels()
    assert warning is not None and "invalid TOML" in warning


def test_save_round_trip(tmp_path):
    path = book_path(tmp_path)
    source = UserChannels(
        bookmarks=[Bookmark(145.5e6, "2m calling", DemodMode.NFM), Bookmark(97.5e6, "local FM")],
        ignores=[IgnoreEntry(96.0e6, 20_000.0, "birdie"), IgnoreEntry(446.05e6)],
    )
    save_user_channels(source, path)
    text = path.read_text(encoding="utf-8")
    assert "freq_hz = 145500000" in text  # integral frequencies stay integers
    reloaded, warning = load_user_channels(path)
    assert warning is None
    assert reloaded == source


def test_name_for_matches_within_window_and_prefers_closest():
    book = UserChannels(
        bookmarks=[Bookmark(145.0e6, "wide"), Bookmark(145.02e6, "close")],
    )
    assert book.name_for(145.018e6) == "close"
    assert book.name_for(144.985e6) == "wide"
    assert book.name_for(146.0e6) == ""


def test_ignored_window():
    entry = IgnoreEntry(96.0e6, 20_000.0)
    assert entry.contains(96.005e6)
    assert not entry.contains(96.05e6)
    default = IgnoreEntry(121.5e6)
    assert default.width_hz == DEFAULT_IGNORE_WIDTH_HZ


def test_upsert_replaces_within_window():
    book = UserChannels(bookmarks=[Bookmark(145.5e6, "old name", DemodMode.NFM)])
    assert upsert_bookmark(book, Bookmark(145.503e6, "renamed")) is True
    assert book.bookmarks == [Bookmark(145.503e6, "renamed")]
    assert upsert_bookmark(book, Bookmark(446.0e6, "pmr")) is False
    assert len(book.bookmarks) == 2


def test_add_ignore_replaces_covering_entry():
    book = UserChannels()
    assert add_ignore(book, IgnoreEntry(96.0e6)) is False
    assert add_ignore(book, IgnoreEntry(96.002e6, note="wider")) is True
    assert book.ignores == [IgnoreEntry(96.002e6, note="wider")]


def test_remove_ignore():
    book = UserChannels(ignores=[IgnoreEntry(96.0e6)])
    assert remove_ignore(book, 96.001e6) is True
    assert book.ignores == []
    assert remove_ignore(book, 96.0e6) is False


# ---- tracker gating ---------------------------------------------------------


def tracker() -> ChannelTracker:
    return ChannelTracker(ScannerSettings(min_persist_frames=1))


def test_ignored_peaks_never_become_channels():
    tr = tracker()
    tr.set_user_channels(UserChannels(ignores=[IgnoreEntry(145.5e6)]))
    now = T0
    for _ in range(3):
        active = tr.update([make_peak(145.5e6)], now)
        now += 1
    assert active == []
    assert tr.channels == {}


def test_ignore_retires_tracked_channel_immediately():
    tr = tracker()
    peaks = [make_peak(145.5e6)]
    tr.update(peaks, T0)
    assert any(c.active for c in tr.channels.values())
    tr.set_user_channels(UserChannels(ignores=[IgnoreEntry(145.5e6)]))
    active = tr.update([], T0 + 1)
    assert active == []


def test_bookmark_names_attach_to_channels():
    tr = tracker()
    tr.set_user_channels(UserChannels(bookmarks=[Bookmark(145.5e6, "2m calling", DemodMode.NFM)]))
    active = tr.update([make_peak(145.503e6)], T0)  # within the association window
    assert len(active) == 1
    assert active[0].name == "2m calling"


# ---- export -----------------------------------------------------------------


def test_export_includes_names(tmp_path):
    channels = [make_channel(145.5e6, name="2m calling"), make_channel(98.0e6)]
    csv_path = export_channels(channels, tmp_path / "out.csv")
    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["name"] == ""
    assert rows[1]["name"] == "2m calling"
    json_path = export_channels(channels, tmp_path / "out.json")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    names = [ch["name"] for ch in payload["channels"]]
    assert names == ["", "2m calling"]


# ---- TUI --------------------------------------------------------------------


async def seed_table(app, *freqs):
    channels = [make_channel(freq, active=True) for freq in freqs]
    for channel in channels:
        app.sweeper._tracker.channels[channel.center_hz] = channel
    app.refresh_table(channels)
    return channels


def log_text(app: RadioTuiApp) -> str:
    lines = [str(line) for line in app.query_one("#log", RichLog).lines]
    return "\n".join(lines)


def table_cell(table: DataTable, row_key: str) -> str:
    return str(table.get_row(row_key)[0])


async def test_startup_loads_bookmarks_into_table_names(tmp_path):
    path = book_path(tmp_path)
    path.write_text(VALID_TOML, encoding="utf-8")
    settings = Settings(scanner=ScannerSettings(min_persist_frames=1))
    app = RadioTuiApp(force_sim=True, channels_path=path, settings=settings)
    async with app.run_test(size=(140, 40)) as pilot:
        if app.sweeper is not None:
            app.sweeper.stop()
        await pilot.pause(0.3)
        assert app.user_channels.name_for(145.501e6) == "2m calling"
        # Next detection frame: the bookmarked carrier gets its name, the
        # ignored birdie never surfaces.
        tracker = app.sweeper._tracker
        active = tracker.update([make_peak(145.503e6), make_peak(96.0e6)], T0)
        app.refresh_table(active)
        await pilot.pause(0.1)
        table = app.query_one("#channels", DataTable)
        assert [str(k.value) for k in table.rows] == ["145505000.0"]  # 5 kHz key rounding
        assert table_cell(table, "145505000.0") == "2m calling"


async def test_startup_warns_on_malformed_file(tmp_path):
    path = book_path(tmp_path)
    path.write_text("{{{ broken", encoding="utf-8")
    app = RadioTuiApp(force_sim=True, channels_path=path)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.3)
        assert "channels file ignored" in log_text(app)


async def test_b_keys_prompt_saves_named_bookmark(tmp_path):
    app = RadioTuiApp(force_sim=True, channels_path=book_path(tmp_path))
    async with app.run_test(size=(140, 40)) as pilot:
        if app.sweeper is not None:
            app.sweeper.stop()
        await pilot.pause(0.3)
        channels = await seed_table(app, 145.5e6)
        await pilot.press("b")
        await pilot.pause(0.2)
        for key in "2m call":
            await pilot.press(key)
        await pilot.press("enter")
        await pilot.pause(0.3)
        saved, warning = load_user_channels(book_path(tmp_path))
        assert warning is None
        assert saved.bookmarks == [Bookmark(145.5e6, "2m call", DemodMode.WFM)]
        table = app.query_one("#channels", DataTable)
        assert table_cell(table, str(channels[0].center_hz)) == "2m call"
        assert "bookmarked" in log_text(app)


async def test_escape_cancels_bookmark_without_writing(tmp_path):
    app = RadioTuiApp(force_sim=True, channels_path=book_path(tmp_path))
    async with app.run_test(size=(140, 40)) as pilot:
        if app.sweeper is not None:
            app.sweeper.stop()
        await pilot.pause(0.3)
        await seed_table(app, 145.5e6)
        await pilot.press("b")
        await pilot.pause(0.2)
        await pilot.press("escape")
        await pilot.pause(0.2)
        assert not book_path(tmp_path).exists()
        assert "cancelled" in log_text(app)


async def test_x_ignores_selected_channel_and_persists(tmp_path):
    app = RadioTuiApp(force_sim=True, channels_path=book_path(tmp_path))
    async with app.run_test(size=(140, 40)) as pilot:
        if app.sweeper is not None:
            app.sweeper.stop()
        await pilot.pause(0.3)
        channels = await seed_table(app, 145.5e6)
        app.refresh_table(channels)
        await pilot.press("x")
        await pilot.pause(0.3)
        saved, warning = load_user_channels(book_path(tmp_path))
        assert warning is None
        assert saved.ignores == [IgnoreEntry(145.5e6)]
        assert "ignored" in log_text(app)
