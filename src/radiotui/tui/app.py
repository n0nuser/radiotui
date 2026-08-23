"""radiotui terminal UI."""

from __future__ import annotations

import queue
import threading
import time
import wave
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, RichLog, Static

from radiotui.antenna.advisor import analyze, format_report
from radiotui.audio.player import AudioPlayer
from radiotui.channels_file import (
    DEFAULT_IGNORE_WIDTH_HZ,
    Bookmark,
    IgnoreEntry,
    UserChannels,
    add_ignore,
    load_user_channels,
    save_user_channels,
    upsert_bookmark,
)
from radiotui.config import (
    BANDS,
    DWELL_RANGE_S,
    MIN_SNR_RANGE,
    THRESHOLD_MARGIN_RANGE,
    Band,
    Settings,
    band_needs_hf,
    clamp,
    clamp_volume_db,
    effective_sample_rate,
    enable_hf,
)
from radiotui.core.models import Channel, DemodMode, ScanState
from radiotui.dsp.spectrum import SweepPlan
from radiotui.export import export_channels, export_path_for
from radiotui.scanner.monitor import ChannelMonitor, auto_hold_release_reason
from radiotui.scanner.sweeper import Sweeper
from radiotui.sdr.manager import open_device
from radiotui.tui.widgets.spectrum import SpectrumBar
from radiotui.tui.widgets.waterfall import Waterfall
from radiotui.tuning import guess_demod, parse_tune_request

GAIN_MIN, GAIN_MAX, GAIN_STEP = 0.0, 49.6, 4.8

KEY_DISPLAYS = {"plus": "+", "minus": "-", "question_mark": "?", "enter": "enter"}


def _key_label(binding: Binding) -> str:
    if binding.key_display:
        return str(binding.key_display)
    return KEY_DISPLAYS.get(binding.key, binding.key)


def _is_band_binding(binding: Binding) -> bool:
    return binding.action.startswith("band_key(")


def build_help_text() -> Text:
    """Key reference generated from BINDINGS so it cannot go stale.

    Built as styled ``Text`` rather than a markup string because key labels
    like ``[`` are literal characters that a markup parser would misread.
    """
    left: list[tuple[str, str]] = []
    right: list[tuple[str, str]] = []
    for binding in RadioTuiApp.BINDINGS:
        if _is_band_binding(binding):  # band presets get their own section below
            continue
        entry = (_key_label(binding), binding.description)
        (left if len(left) <= len(right) else right).append(entry)
    width_l = max(len(k) for k, _ in left)
    width_r = max(len(k) for k, _ in right)
    rows = max(len(left), len(right))
    left += [("", "")] * (rows - len(left))
    right += [("", "")] * (rows - len(right))

    text = Text()
    text.append("Keys\n", style="bold")
    for (lk, ld), (rk, rd) in zip(left, right, strict=True):
        text.append(f"{lk:<{width_l}}  {ld:<28}{rk:<{width_r}}  {rd}\n")
    text.append("\nBand presets\n", style="bold")
    row_count = 0
    for i, name in enumerate(sorted(BANDS)[:9], start=1):
        text.append(str(i), style="cyan")
        text.append(f" {BANDS[name].label:<18}")
        row_count += 1
        if row_count == 3:
            text.append("\n")
            row_count = 0
    if row_count:
        text.append("\n")
    text.append("\nq / esc closes this overlay", style="dim")
    return text


class HelpModal(ModalScreen):
    BINDINGS = [Binding("q,escape", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        yield Static(build_help_text(), id="help-report")


class TuneModal(ModalScreen):
    AUTO_FOCUS = "Input"
    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def compose(self) -> ComposeResult:
        yield Vertical(
            Input(
                placeholder="145.5 · 430-440 · 96.9 wfm · esc cancels",
                id="tune-input",
            ),
            Static("", id="tune-error"),
            id="tune-box",
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        try:
            start_hz, end_hz, demod = parse_tune_request(event.value)
        except ValueError as exc:
            self.query_one("#tune-error", Static).update(f"[red]{exc}[/red]")
            return
        self.dismiss((start_hz, end_hz, demod))


class AntennaModal(ModalScreen):
    BINDINGS = [Binding("q,escape", "dismiss", "Close")]

    def __init__(self, report_text: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._text = report_text

    def compose(self) -> ComposeResult:
        yield Static(self._text, id="antenna-report")


@dataclass
class SettingRow:
    """One editable line of the settings menu: value read/applied live."""

    label: str
    get: Callable[[], float | None]
    set: Callable[[float | None], None]
    lo: float
    hi: float
    step: float
    fmt: str
    scale: float = 1.0  # display multiplier (e.g. seconds -> ms)
    none_label: str = ""


class SettingsModal(ModalScreen):
    """Navigable list of the knobs a radio user actually touches (#23)."""

    BINDINGS = [
        Binding("escape,q", "dismiss", "Close"),
        Binding("down,j", "next_item", show=False),
        Binding("up,k", "prev_item", show=False),
        Binding("right,l,+,=", "bigger", show=False),
        Binding("left,h,-,_", "smaller", show=False),
    ]

    def __init__(self, rows: list[SettingRow], **kwargs) -> None:
        super().__init__(**kwargs)
        self._rows = rows
        self._index = 0

    def compose(self) -> ComposeResult:
        yield Vertical(Static("", id="settings-body"), id="settings-box")

    def on_mount(self) -> None:
        self._redraw()

    def _current(self) -> SettingRow:
        return self._rows[self._index]

    def _nudge(self, direction: int) -> None:
        row = self._current()
        value = row.get()
        if value is None:
            value = row.lo if direction > 0 else row.hi
        else:
            value = value + direction * row.step * row.scale
            if (value < row.lo or value > row.hi) and row.none_label:
                value = None  # stepped past the end: switch off
            elif value < row.lo or value > row.hi:
                return
        row.set(None if value is None else round(clamp(value, row.lo, row.hi), 3))
        self.app.refresh_status()
        self._redraw()

    def action_bigger(self) -> None:
        self._nudge(+1)

    def action_smaller(self) -> None:
        self._nudge(-1)

    def action_next_item(self) -> None:
        self._index = (self._index + 1) % len(self._rows)
        self._redraw()

    def action_prev_item(self) -> None:
        self._index = (self._index - 1) % len(self._rows)
        self._redraw()

    def _redraw(self) -> None:
        lines = Text()
        for i, row in enumerate(self._rows):
            marker = "▶ " if i == self._index else "  "
            value = row.get()
            shown = row.none_label if value is None else row.fmt.format(value * row.scale)
            style = "bold cyan" if i == self._index else ""
            lines.append(f"{marker}{row.label:<20}", style=style)
            lines.append(f"{shown:>10}\n", style=style)
        lines.append("\n↑/↓ select · ←/→ adjust · esc close", style="dim")
        self.query_one("#settings-body", Static).update(lines)


class NameModal(ModalScreen):
    AUTO_FOCUS = "Input"
    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, hint: str, initial: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self._hint = hint
        self._initial = initial

    def compose(self) -> ComposeResult:
        yield Vertical(
            Input(value=self._initial, placeholder="name · enter saves · esc cancels"),
            Static(self._hint, id="name-hint"),
            id="name-box",
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())


class RadioTuiApp(App):
    # The hidden-by-default channel table must not grab focus on mount,
    # or it eats every key (arrows/enter) in the radio view.
    AUTO_FOCUS = None
    TITLE = "radiotui"
    SUB_TITLE = "autonomous spectrum scanner"
    CSS = """
    Screen { layout: vertical; }
    #spectrum { height: 1fr; border: round #3b4d8f; }
    #waterfall { height: 1fr; border: round #3b4d8f; }
    #meter { height: 7; border: round #3b4d8f; content-align: center middle; padding: 0 1; }
    #main { height: 14; display: none; }
    #main.shown { display: block; }
    #channels { width: 2fr; border: round #3b4d8f; }
    #sidepanel { width: 1fr; layout: vertical; }
    #log { height: 1fr; border: round #3b4d8f; }
    #clips { height: 1fr; border: round #3b4d8f; display: none; }
    #clips.shown { display: block; }
    AntennaModal { align: center middle; background: #000000cc; }
    #antenna-report { width: 64; border: thick cyan; padding: 1 2; background: $surface; }
    HelpModal { align: center middle; background: #000000cc; }
    #help-report { width: 72; border: thick cyan; padding: 1 2; background: $surface; }
    TuneModal { align: center middle; background: #000000cc; }
    #tune-box { width: 60; border: thick cyan; padding: 1 2; background: $surface; }
    NameModal { align: center middle; background: #000000cc; }
    #name-box { width: 60; border: thick magenta; padding: 1 2; background: $surface; }
    #name-hint { color: $text-muted; padding-top: 1; }
    SettingsModal { align: center middle; background: #000000cc; }
    #settings-box { width: 56; border: thick green; padding: 1 2; background: $surface; }
    """
    BINDINGS = [
        Binding("s", "toggle_sweep", "Sweep"),
        Binding("enter", "listen", "Listen"),
        Binding("l", "stop_listen", "Stop"),
        Binding("M", "mute", "Mute", key_display="M"),
        Binding("r", "record", "Record"),
        Binding("n", "next_channel", "Next", show=False),
        Binding("p", "prev_channel", "Prev", show=False),
        Binding("right", "cursor_right", "Tune+", key_display="→"),
        Binding("left", "cursor_left", "Tune-", key_display="←"),
        Binding("up", "cursor_up", "Coarse+", key_display="↑", show=False),
        Binding("down", "cursor_down", "Coarse-", key_display="↓", show=False),
        Binding("t", "toggle_advanced", "Panels", key_display="t"),
        Binding("m", "settings", "Menu"),
        Binding("comma", "toggle_sort", "Sort", key_display=",", show=False),
        Binding("plus", "gain_up", "Gain+", key_display="+"),
        Binding("minus", "gain_down", "Gain-", key_display="-"),
        Binding("greater_than_sign", "volume_up", "Vol+", key_display=">", show=False),
        Binding("less_than_sign", "volume_down", "Vol-", key_display="<", show=False),
        Binding("right_square_bracket", "threshold_up", "Thr+", key_display="]", show=False),
        Binding("left_square_bracket", "threshold_down", "Thr-", key_display="[", show=False),
        Binding("right_curly_bracket", "dwell_up", "Dwell+", key_display="}", show=False),
        Binding("left_curly_bracket", "dwell_down", "Dwell-", key_display="{", show=False),
        Binding("a", "antenna", "Antenna"),
        Binding("o", "toggle_autonomous", "Auto"),
        Binding("e", "export_channels", "Export", show=False),
        Binding("f", "tune", "Freq"),
        Binding("b", "bookmark", "Name"),
        Binding("x", "ignore_channel", "Ignore"),
        Binding("c", "toggle_clips", "Clips"),
        Binding("question_mark", "help", "Help"),
        Binding("q", "quit", "Quit", priority=True),
    ]
    class RssiUpdate(Message):
        def __init__(self, rssi_dbfs: float) -> None:
            super().__init__()
            self.rssi_dbfs = rssi_dbfs

    class MonitorError(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class ClipSaved(Message):
        def __init__(self, clip) -> None:
            super().__init__()
            self.clip = clip

    def __init__(
        self,
        force_sim: bool = False,
        bias_tee: bool = False,
        ppm: int = 0,
        offset_tune: bool = False,
        settings: Settings | None = None,
        channels_path: Path | None = None,
        start_sweeper: bool = False,
    ) -> None:
        super().__init__()
        self._force_sim = force_sim
        self._bias_tee_requested = bias_tee
        self._ppm_requested = ppm
        self._offset_tune_requested = offset_tune
        self._channels_path = channels_path
        self._start_sweeper = start_sweeper
        self.bias_tee_on = False
        self.hf_active = False
        self.auto_hold_freq: float | None = None
        self._auto_hold_started_at = 0.0
        self.settings = settings or Settings()
        self.device = None
        self.is_real = False
        self.plan: SweepPlan | None = None
        self.sweeper: Sweeper | None = None
        self.monitor: ChannelMonitor | None = None
        self.resume_sweep_after_listen = False
        self.queue: queue.Queue[ScanState] = queue.Queue(maxsize=4)
        self.band_name = "fm_broadcast"
        self.band_label = BANDS["fm_broadcast"].label
        self.gain_db: float | None = self.settings.scanner.gain_db
        self.muted = False
        self.clips_saved = 0
        self.row_keys: list[float] = []
        self.sort_by_peak = False
        self._rendered_cells: dict[str, tuple[str, ...]] = {}
        self._last_channels: list[Channel] = []
        self.selected_hz: float | None = None
        self.last_state: ScanState | None = None
        self.last_rssi: float | None = None
        self._peak_rssi: float = -120.0
        self._ignore_rssi = False
        self._last_meter_paint = 0.0
        self.user_channels = UserChannels()
        self.session_clips: list = []
        self._channel_bw_hz: float | None = None
        self.cursor_hz: float | None = None
        self._events: deque[str] = deque(maxlen=300)
        self._replay_player: AudioPlayer | None = None
        self._replay_thread: threading.Thread | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield SpectrumBar(id="spectrum")
        yield Waterfall(id="waterfall")
        yield Static("", id="meter")
        with Horizontal(id="main"):
            yield DataTable(id="channels", cursor_type="row")
            with Vertical(id="sidepanel"):
                yield RichLog(id="log", highlight=False, markup=True)
                yield DataTable(id="clips", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(0.15, self.poll_queue)
        table = self.query_one("#channels", DataTable)
        for key, label, width in (
            ("name", "Name", 14),
            ("freq", "Freq MHz", 11),
            ("bw", "BW kHz", 8),
            ("peak", "Peak dB", 9),
            ("snr", "SNR dB", 8),
            ("hits", "Hits", 6),
            ("demod", "Demod", 6),
            ("age", "Age s", 7),
        ):
            table.add_column(label, key=key, width=width)
        self._apply_sort_markers()

        self.user_channels, channels_warning = load_user_channels(self._channels_path)
        if channels_warning:
            self.log_line(f"[yellow]channels file ignored:[/] {channels_warning}")
        clips_table = self.query_one("#clips", DataTable)
        for key, label, width in (
            ("time", "Time", 8),
            ("freq", "Freq MHz", 11),
            ("dur", "Dur s", 6),
            ("peak", "Peak dB", 8),
            ("band", "Band", 10),
        ):
            clips_table.add_column(label, key=key, width=width)

        try:
            opened = open_device(prefer_real=not self._force_sim)
            self.device = opened.device
            self.is_real = opened.is_real
        except (OSError, RuntimeError) as exc:
            self.log_line(f"[red]Failed to open any device: {exc}[/red]")
            return
        if self.device.is_real:
            if self._ppm_requested:
                ok = self.device.set_freq_correction(self._ppm_requested)
                self.log_line(
                    f"PPM correction {self._ppm_requested:+d}: "
                    + ("[green]applied[/green]" if ok else "[red]unsupported[/red]")
                )
            if self._offset_tune_requested:
                ok = self.device.set_offset_tuning(True)
                self.log_line(
                    "Offset tuning: [green]on[/green]"
                    if ok
                    else "Offset tuning: [red]unsupported[/red]"
                )
            if self._bias_tee_requested:
                ok = self.device.set_bias_tee(True)
                self.bias_tee_on = ok
                self.log_line(
                    "Bias tee: [green]ON - ~4.5 V on antenna port[/green]"
                    if ok
                    else "Bias tee: [red]not supported by this librtlsdr build[/red]"
                )
        mode = "REAL" if self.is_real else "SIMULATED"
        self.log_line(
            f"Device: [bold]{self.device.name}[/bold] ({mode})."
            if self.is_real
            else f"Device: [bold]{self.device.name}[/bold] ({mode}) - "
            "[yellow]no hardware found, signals are synthetic[/yellow]"
        )
        self.log_line(
            f"Recordings: {Path(self.settings.audio.recordings_dir).expanduser().resolve()}"
        )
        self.start_band(self.band_name)
        self.refresh_status()

    def start_band(self, band_name: str) -> None:
        self._start_sweep(BANDS[band_name], band_name)

    def _start_sweep(self, band: Band, name: str | None = None) -> None:
        if self.monitor is not None:
            # Band switches retune the tuner the listener is using.
            self.stop_monitor(resume_sweep=False)
        self.band_name = name or "custom"
        self.band_label = band.label
        self._channel_bw_hz = band.channel_bw_hz
        if self.plan is None or (
            self.cursor_hz is None or not (self.plan.start_hz <= self.cursor_hz <= self.plan.end_hz)
        ):
            self.cursor_hz = (band.start_hz + band.end_hz) / 2
        if self.sweeper is not None:
            self.sweeper.stop()
        waterfall = self.query_one("#waterfall", Waterfall)
        waterfall.rows.clear()
        hf = enable_hf(band_needs_hf(band), self.settings.scanner)
        self.hf_active = False
        if self.device.is_real:
            ok = self.device.set_hf_mode(hf)
            if hf:
                self.hf_active = ok
                state = "[green]ON[/green]" if ok else "[red]failed[/red]"
                self.log_line(f"HF direct sampling (Q-branch): {state}")
        self.plan = SweepPlan.build(
            band.start_hz,
            band.end_hz,
            effective_sample_rate(self.settings.scanner),
            self.settings.scanner.fft_size,
        )
        self.sweeper = Sweeper(self.device, self.plan, self.settings.scanner, self.queue)
        self.sweeper.set_user_channels(self.user_channels)
        for i, name in enumerate(sorted(BANDS)[:9], start=1):
            self.bind(str(i), f"band_key('{name}')", description=BANDS[name].label, show=False)
        if self._start_sweeper:
            self.sweeper.start()
        self.log_line(
            f"Sweeping [bold]{band.label}[/bold] ({len(self.plan.hop_centers_hz)} hops)"
            + (f" @ {effective_sample_rate(self.settings.scanner) / 1e3:.0f} kS/s" if hf else "")
        )
        self.refresh_status()

    def log_line(self, text: str) -> None:
        # The log pane lives in the advanced panels and may be display:none;
        # keep the canonical history on the app so nothing is lost while hidden.
        self._events.append(text)
        if self._main_shown():
            self.query_one("#log", RichLog).write(text)

    def poll_queue(self) -> None:
        latest: ScanState | None = None
        while True:
            try:
                latest = self.queue.get_nowait()
            except queue.Empty:
                break
        if latest is None:
            return
        if latest.error:
            self.log_line(f"[red]Device error: {latest.error}[/red]")
            if self.sweeper:
                self.sweeper.stop()
            return
        self.last_state = latest
        try:
            spectrum = self.query_one("#spectrum", SpectrumBar)
            waterfall = self.query_one("#waterfall", Waterfall)
        except NoMatches:
            # Shutdown can race this interval tick: the main DOM is already
            # gone and queries resolve against a bare default screen.
            return
        active_freqs = [ch.center_hz for ch in latest.channels]
        selected_hz = self.cursor_hz
        spectrum.update_frame(
            latest.frame.freqs_hz,
            latest.frame.power_db,
            latest.noise_floor_db,
            latest.threshold_db,
            active_freqs,
            selected_hz=selected_hz,
        )
        waterfall.push_frame(
            latest.frame.freqs_hz,
            latest.frame.power_db,
            latest.noise_floor_db,
            selected_hz=selected_hz,
        )
        self.refresh_table(latest.channels)
        self.refresh_status()
        if latest.hold_request is not None:
            self._engage_auto_hold(latest.hold_request)
        elif self.auto_hold_freq is not None and self.monitor is not None:
            self._auto_release_check()

    def refresh_table(self, channels: list[Channel]) -> None:
        table = self.query_one("#channels", DataTable)
        self._last_channels = channels
        previous_key = self.selected_key()
        channels_sorted = self._sorted_channels(channels)
        self.row_keys = [ch.center_hz for ch in channels_sorted]
        now = time.time()
        wanted_keys = [str(ch.center_hz) for ch in channels_sorted]
        cells_by_key = {str(ch.center_hz): self._row_cells(ch, now) for ch in channels_sorted}
        if [row_key.value for row_key in table.rows] != wanted_keys:
            # Membership or order changed: rebuild the row structure.
            table.clear()
            for key, cells in zip(wanted_keys, cells_by_key.values(), strict=True):
                table.add_row(*cells, key=key)
        else:
            for key, cells in cells_by_key.items():
                if cells == self._rendered_cells.get(key):
                    continue
                for column_key, value in zip(self.COLUMN_KEYS, cells, strict=True):
                    table.update_cell(key, column_key, value)
        self._rendered_cells = cells_by_key
        if previous_key is not None:
            try:
                row_index = self.row_keys.index(previous_key)
                table.move_cursor(row=row_index, column=0)
            except ValueError:
                pass

    COLUMN_KEYS = ("name", "freq", "bw", "peak", "snr", "hits", "demod", "age")

    @staticmethod
    def _row_cells(ch: Channel, now: float) -> tuple[str, ...]:
        return (
            ch.name,
            f"{ch.center_hz / 1e6:.4f}",
            f"{ch.bandwidth_hz / 1e3:.0f}",
            f"{ch.peak_db:.1f}",
            f"{ch.snr_db:.1f}",
            str(ch.hits),
            ch.demod.value,
            f"{now - ch.last_seen:.0f}",
        )

    def _sorted_channels(self, channels: list[Channel]) -> list[Channel]:
        if self.sort_by_peak:
            return sorted(channels, key=lambda c: c.peak_db, reverse=True)
        return sorted(channels, key=lambda c: c.center_hz)

    def _apply_sort_markers(self) -> None:
        table = self.query_one("#channels", DataTable)
        table.columns["freq"].label = "Freq MHz" if self.sort_by_peak else "Freq MHz ▾"
        table.columns["peak"].label = "Peak dB ▾" if self.sort_by_peak else "Peak dB"

    def action_toggle_sort(self) -> None:
        self.sort_by_peak = not self.sort_by_peak
        self._apply_sort_markers()
        self.log_line("Sorted by peak strength" if self.sort_by_peak else "Sorted by frequency")
        if self._last_channels:
            self.refresh_table(self._last_channels)

    def selected_key(self) -> float | None:
        table = self.query_one("#channels", DataTable)
        if 0 <= table.cursor_row < len(self.row_keys):
            return self.row_keys[table.cursor_row]
        return None

    def selected_channel(self) -> Channel | None:
        key = self.selected_key()
        if key is None or self.sweeper is None:
            return None
        for ch in self.sweeper.channels:
            if ch.center_hz == key:
                return ch
        return None

    def refresh_status(self) -> None:
        meter = self.query_one("#meter", Static)
        parts = [
            f"band {self.band_label}",
            f"sweeps {self.sweeper.sweeps_done if self.sweeper else 0}",
        ]
        if self.last_state:
            floor = self.last_state.noise_floor_db
            parts.append(f"floor {floor:.1f} dB")
            # Vertical reference for the spectrum/waterfall colour mapping.
            parts.append(f"dB {floor - 5.0:.0f}→{floor + 45.0:.0f}")
        parts.append(f"thr {self.settings.scanner.threshold_margin_db:+.1f} dB")
        parts.append(f"dwell {self.settings.scanner.hop_dwell_s * 1000:.0f} ms")
        gain_label = f"{self.gain_db:.1f} dB" if self.gain_db is not None else "auto"
        parts.append(f"gain {gain_label}")
        if self.hf_active:
            parts.append("[cyan]HF[/cyan]")
        if self.bias_tee_on:
            parts.append("[yellow]bias ⚡[/yellow]")
        if not self.is_real:
            parts.append("[yellow]SIM[/yellow]")
        if self.settings.scanner.autonomous:
            hold = (
                f" [magenta]HOLD {self.auto_hold_freq / 1e6:.3f}[/magenta]"
                if self.auto_hold_freq is not None
                else ""
            )
            parts.append(f"[magenta]AUTO{hold}[/magenta]")
        if self.monitor is not None and self.monitor.running:
            rec = " ●REC" if self.monitor.recorder.recording else ""
            mute = " 🔇" if self.muted else ""
            parts.append(
                f"listening {self.monitor.freq_hz / 1e6:.4f} MHz "
                f"({self.monitor.demod.value}){mute}{rec}"
            )
            backend = self.monitor.player.backend
            vol = f"vol {self.monitor.volume_db:+.0f}dB"
            if backend:
                parts.append(f"{vol} [dim]({backend})[/dim]")
            else:
                parts.append(f"[red]{vol} no audio backend[/red]")
        elif self.resume_sweep_after_listen:
            parts.append("sweep paused")
        if self.session_clips:
            count = len(self.session_clips)
            parts.append(f"{count} clip{'s' if count != 1 else ''}")
        lines = [Text.from_markup(" │ ".join(parts))]
        tuned = self.monitor.freq_hz if self.monitor is not None else self.cursor_hz
        if tuned is not None:
            # The dial: the number a radio user actually cares about, on top.
            lines.insert(0, Text(f" ▶ {tuned / 1e6:.3f} MHz ", style="bold cyan"))
        if self.last_rssi is not None:
            if self.monitor is not None:
                lines.append(Text(f"{self.monitor.freq_hz / 1e6:.4f} MHz"))
            lines.append(self.render_meter_bar(self.last_rssi, self._peak_rssi))
        meter.update(Text("\n").join(lines))

    def render_meter_bar(self, rssi: float, peak: float | None = None) -> Text:
        lo, hi = -60.0, -10.0
        width = 30

        def frac(db: float) -> float:
            return max(0.0, min(1.0, (db - lo) / (hi - lo)))

        filled = int(frac(rssi) * width)
        color = "green" if frac(rssi) < 0.6 else "yellow" if frac(rssi) < 0.85 else "red"
        bar = Text("█" * filled + "░" * (width - filled))
        bar.stylize(color, 0, max(filled, 0))
        header = f"RSSI {rssi:6.1f} dBFS"
        if peak is not None:
            marker = min(int(frac(peak) * width), width - 1)
            bar.stylize("reverse", marker, marker + 1)
            delta = rssi - peak
            header += "  ● at peak" if delta > -0.5 else f"  Δ{delta:+.1f} dB vs peak"
        return Text(header + "\n") + bar

    # ---- actions ----

    def action_toggle_sweep(self) -> None:
        if self.monitor is not None:
            # One tuner, two consumers: sweeping while listening corrupts both
            # streams and hammers the tuner with concurrent retunes.
            self.log_line("[yellow]Sweep stays paused while listening - press l first.[/yellow]")
            return
        if self.sweeper is None:
            return
        if self.sweeper.running:
            self.sweeper.stop()
            self.log_line("Sweep paused")
        else:
            self.sweeper.start()
            self.log_line("Sweep resumed")

    def _pause_sweeper_for_monitor(self) -> None:
        if self.sweeper is not None and self.sweeper.running:
            self.resume_sweep_after_listen = True
            self.sweeper.stop()
            time.sleep(0.05)

    @on(DataTable.RowSelected)
    def on_row_selected(self, event: DataTable.RowSelected) -> None:
        # enter is not a priority binding (so inputs can receive it); the
        # table's own select action fires RowSelected, which we listen to.
        if event.data_table.id == "clips":
            self._replay_selected_clip()
            return
        self.action_listen()

    def _main_shown(self) -> bool:
        return self.query_one("#main").has_class("shown")

    def action_toggle_advanced(self) -> None:
        main = self.query_one("#main")
        if main.has_class("shown"):
            main.remove_class("shown")
            self.log_line("Analyst panels hidden - radio view")
        else:
            main.add_class("shown")
            self.query_one("#channels", DataTable).focus()
            log_widget = self.query_one("#log", RichLog)
            for line in list(self._events)[-50:]:
                log_widget.write(line)
            if self._last_channels:
                self.refresh_table(self._last_channels)
            self.log_line(f"Analyst panels shown ({len(self.session_clips)} clip(s))")

    def _cursor_step_hz(self) -> float:
        if self.plan is None:
            return 25_000.0
        try:
            width = max(self.query_one("#spectrum", SpectrumBar).size.width - 2, 8)
        except NoMatches:
            return 25_000.0
        span = self.plan.end_hz - self.plan.start_hz
        return max(span / width, 100.0)

    def _move_cursor(self, delta_hz: float) -> None:
        if self.plan is None or self.cursor_hz is None:
            return
        lo, hi = self.plan.start_hz, self.plan.end_hz
        self.cursor_hz = round(clamp(self.cursor_hz + delta_hz, lo, hi), 0)
        spectrum = self.query_one("#spectrum", SpectrumBar)
        spectrum.selected_hz = self.cursor_hz
        spectrum.refresh()
        self.refresh_status()

    def action_cursor_right(self) -> None:
        self._move_cursor(+self._cursor_step_hz())

    def action_cursor_left(self) -> None:
        self._move_cursor(-self._cursor_step_hz())

    def action_cursor_up(self) -> None:
        self._move_cursor(+100_000.0)

    def action_cursor_down(self) -> None:
        self._move_cursor(-100_000.0)

    def action_settings(self) -> None:
        self.push_screen(SettingsModal(self._setting_rows()))

    def _setting_rows(self) -> list[SettingRow]:
        scanner, audio = self.settings.scanner, self.settings.audio

        def set_threshold(v: float) -> None:
            scanner.threshold_margin_db = clamp(v, *THRESHOLD_MARGIN_RANGE)

        def set_dwell(v: float) -> None:
            scanner.hop_dwell_s = clamp(v, *DWELL_RANGE_S)

        def set_snr(v: float) -> None:
            scanner.min_snr_db = clamp(v, *MIN_SNR_RANGE)

        def set_squelch(v: float | None) -> None:
            scanner.squelch_rssi_dbfs = None if v is None else clamp(v, -70.0, -10.0)

        def set_volume(v: float) -> None:
            audio.volume_db = clamp_volume_db(v)
            if self.monitor is not None:
                self.monitor.set_volume_db(audio.volume_db)

        return [
            SettingRow(
                "Threshold margin",
                lambda: scanner.threshold_margin_db,
                set_threshold,
                *THRESHOLD_MARGIN_RANGE,
                1.0,
                "{:+.1f} dB",
            ),
            SettingRow(
                "Hop dwell",
                lambda: scanner.hop_dwell_s,
                set_dwell,
                *DWELL_RANGE_S,
                0.02,
                "{:.0f} ms",
                scale=1000.0,
            ),
            SettingRow(
                "Min SNR",
                lambda: scanner.min_snr_db,
                set_snr,
                *MIN_SNR_RANGE,
                1.0,
                "{:.0f} dB",
            ),
            SettingRow(
                "Squelch (RF gate)",
                lambda: scanner.squelch_rssi_dbfs,
                set_squelch,
                -70.0,
                -10.0,
                5.0,
                "{:.0f} dBFS",
                none_label="off (VOX only)",
            ),
            SettingRow(
                "Volume",
                lambda: audio.volume_db,
                set_volume,
                -60.0,
                12.0,
                3.0,
                "{:+.0f} dB",
            ),
        ]

    def action_toggle_clips(self) -> None:
        clips = self.query_one("#clips", DataTable)
        if clips.has_class("shown"):
            clips.remove_class("shown")
            self.query_one("#channels", DataTable).focus()
            self.log_line("Clips panel hidden")
            return
        clips.add_class("shown")
        self._refresh_clips_table()
        clips.focus()
        self.log_line(f"{len(self.session_clips)} clip(s) this session - enter replays")

    def _refresh_clips_table(self) -> None:
        table = self.query_one("#clips", DataTable)
        table.clear()
        for clip in reversed(self.session_clips):
            table.add_row(
                time.strftime("%H:%M:%S", time.localtime(clip.started_at)),
                f"{clip.freq_hz / 1e6:.4f}",
                f"{clip.seconds:.1f}",
                f"{clip.peak_rssi_dbfs:.0f}",
                clip.band,
                key=str(clip.path),
            )

    def _replay_selected_clip(self) -> None:
        table = self.query_one("#clips", DataTable)
        if not (0 <= table.cursor_row < len(self.session_clips)):
            self.log_line("[yellow]No clip selected.[/yellow]")
            return
        clip = self.session_clips[len(self.session_clips) - 1 - table.cursor_row]
        try:
            with wave.open(str(clip.path), "rb") as wf:
                rate_hz = wf.getframerate()
        except (wave.Error, OSError) as exc:
            self.log_line(f"[red]cannot open {clip.path.name}: {exc}[/red]")
            return
        self._start_replay(clip, rate_hz)

    def _start_replay(self, clip, rate_hz: int) -> None:
        self._stop_replay()
        player = AudioPlayer(rate_hz)
        if not player.start():
            self.log_line("[red]no audio backend found: install ffmpeg or alsa-utils[/red]")
            return
        self._replay_player = player

        def stream() -> None:
            try:
                with wave.open(str(clip.path), "rb") as wf:
                    while player.running:
                        frames = wf.readframes(4096)
                        if not frames:
                            break
                        player.write(frames)
            except (wave.Error, OSError) as exc:
                self.post_message(self.MonitorError(f"replay failed: {exc}"))
            finally:
                player.stop()

        self._replay_thread = threading.Thread(target=stream, name="clip-replay", daemon=True)
        self._replay_thread.start()
        self.log_line(f"[green]replaying[/green] {clip.path.name}")

    def _stop_replay(self) -> None:
        player, self._replay_player = self._replay_player, None
        thread, self._replay_thread = self._replay_thread, None
        if player is not None:
            player.stop()
        if thread is not None:
            thread.join(timeout=2.0)

    def action_listen(self) -> None:
        if len(self.screen_stack) > 1:
            return  # a modal owns the keyboard (priority bindings fire before it)
        if not self._main_shown() and self.cursor_hz is not None:
            # Radio view: enter tunes what the dial cursor points at.
            self.listen_frequency(self.cursor_hz)
            return
        channel = self.selected_channel()
        if channel is None:
            self.log_line("[yellow]No channel selected.[/yellow]")
            return
        self.start_monitor(channel.center_hz, channel.demod, muted=False, enable_recorder=False)

    def _engage_auto_hold(self, req) -> None:
        if not self.settings.scanner.autonomous or self.monitor is not None:
            return
        if self.sweeper is None or not self.sweeper.running:
            return
        self.auto_hold_freq = req.freq_hz
        self._auto_hold_started_at = time.time()
        self.start_monitor(
            req.freq_hz, req.demod, muted=True, enable_recorder=True, pause_sweep=False
        )
        self.log_line(
            f"[magenta]AUTO[/magenta] holding {req.freq_hz / 1e6:.4f} MHz "
            f"(SNR {req.snr_db:.0f} dB), VOX recording"
        )

    def _auto_release_check(self) -> None:
        assert self.monitor is not None and self.auto_hold_freq is not None
        silent_for = self.monitor.recorder.seconds_since_voice()
        held_for = time.time() - self._auto_hold_started_at
        if (
            silent_for >= self.settings.scanner.hold_release_s
            or held_for >= self.settings.scanner.max_hold_s
        ):
            freq = self.auto_hold_freq
            reason = auto_hold_release_reason(silent_for, self.settings.scanner.hold_release_s)
            self.log_line(f"[magenta]AUTO[/magenta] releasing {freq / 1e6:.4f} MHz ({reason})")
            self.stop_monitor(resume_sweep=False)
            if self.sweeper is not None:
                self.sweeper.cooldown_channel(freq)
                self.sweeper.release_hold()
            self.auto_hold_freq = None

    def action_toggle_autonomous(self) -> None:
        scanner = self.settings.scanner
        scanner.autonomous = not scanner.autonomous
        if not scanner.autonomous:
            if self.sweeper is not None and self.sweeper.holding_active():
                if self.monitor is not None and self.auto_hold_freq is not None:
                    self.stop_monitor(resume_sweep=False)
                self.sweeper.release_hold()
            self.auto_hold_freq = None
        self.log_line(
            "[magenta]Autonomous scan-and-hold ON[/magenta]"
            if scanner.autonomous
            else "Autonomous mode OFF"
        )
        self.refresh_status()

    def start_monitor(
        self,
        freq_hz: float,
        demod: DemodMode,
        muted: bool,
        enable_recorder: bool,
        pause_sweep: bool = True,
    ) -> None:
        if self.sweeper is not None and self.sweeper.holding_active():
            self.sweeper.release_hold()
            self.auto_hold_freq = None
        self.stop_monitor(resume_sweep=False)
        if pause_sweep:
            self._pause_sweeper_for_monitor()
        self.muted = muted
        self._ignore_rssi = False
        monitor = ChannelMonitor(
            self.device,
            freq_hz,
            demod,
            self.settings,
            muted=muted,
            band_label=self.band_label,
            channel_bw_hz=self._channel_bw_hz,
        )
        monitor.recorder.enabled = enable_recorder
        monitor.recorder.on_clip_end = self.on_clip_end
        monitor.on_rssi = lambda db: self.post_message(self.RssiUpdate(db))
        monitor.on_error = lambda msg: self.post_message(self.MonitorError(msg))
        monitor.start()
        self.monitor = monitor
        verb = "Recording" if enable_recorder else "Listening"
        self.log_line(f"{verb} [bold]{freq_hz / 1e6:.4f} MHz[/bold] ({demod.value})")

    def on_clip_end(self, clip) -> None:
        self.post_message(self.ClipSaved(clip))

    @on(ClipSaved)
    def on_clip_saved(self, message) -> None:
        self.clips_saved += 1
        self.session_clips.append(message.clip)
        clips = self.query_one("#clips", DataTable)
        if clips.has_class("shown"):
            self._refresh_clips_table()
        self.refresh_status()
        self.log_line(
            f"[green]clip saved[/green] {message.clip.path.name} ({message.clip.seconds:.1f}s)"
        )

    def stop_monitor(self, resume_sweep: bool = True) -> None:
        if self.monitor is not None:
            self._ignore_rssi = True  # the stopping thread may still post readings
            self.monitor.stop()
            self.monitor = None
            self.last_rssi = None
            self._peak_rssi = -120.0
            self.log_line("Monitor stopped")
        if resume_sweep and self.resume_sweep_after_listen:
            self.resume_sweep_after_listen = False
            if self.sweeper is not None:
                self.sweeper.start()

    def action_stop_listen(self) -> None:
        self.stop_monitor()

    def action_mute(self) -> None:
        if self.monitor is None:
            return
        self.muted = not self.muted
        self.monitor.set_muted(self.muted)
        self.log_line("Muted" if self.muted else "Unmuted")

    def action_record(self) -> None:
        if self.monitor is not None:
            recorder = self.monitor.recorder
            recorder.enabled = not recorder.enabled
            if not recorder.enabled:
                recorder.stop()
            self.log_line("Recording ON" if recorder.enabled else "Recording OFF")
            return
        channel = self.selected_channel()
        if channel is None:
            self.log_line("[yellow]Select a channel first (or press Enter to listen).[/yellow]")
            return
        self.start_monitor(channel.center_hz, channel.demod, muted=True, enable_recorder=True)

    def _step_cursor(self, delta: int) -> None:
        table = self.query_one("#channels", DataTable)
        if not self.row_keys:
            return
        new_row = min(max(table.cursor_row + delta, 0), len(self.row_keys) - 1)
        table.move_cursor(row=new_row, column=0)
        self.selected_hz = self.row_keys[new_row]

    def action_next_channel(self) -> None:
        self._step_cursor(1)

    def action_prev_channel(self) -> None:
        self._step_cursor(-1)

    def action_gain_up(self) -> None:
        self._adjust_gain(+GAIN_STEP)

    def action_gain_down(self) -> None:
        self._adjust_gain(-GAIN_STEP)

    def _adjust_gain(self, delta: float) -> None:
        current = self.gain_db if self.gain_db is not None else 20.0
        new = min(max(current + delta, GAIN_MIN), GAIN_MAX)
        self.gain_db = round(new, 1)
        self.settings.scanner.gain_db = self.gain_db
        try:
            self.device.set_gain_db(self.gain_db)
        except (OSError, RuntimeError, ValueError) as exc:
            self.log_line(f"[red]gain change failed: {exc}[/red]")
        self.refresh_status()

    def action_volume_up(self) -> None:
        self._adjust_volume(+3.0)

    def action_volume_down(self) -> None:
        self._adjust_volume(-3.0)

    def _adjust_volume(self, delta_db: float) -> None:
        if self.monitor is None:
            self.log_line("[yellow]Nothing is playing - press enter to listen first.[/yellow]")
            return
        self.monitor.set_volume_db(self.monitor.volume_db + delta_db)
        self.refresh_status()

    def action_threshold_up(self) -> None:
        self._adjust_threshold(+1.0)

    def action_threshold_down(self) -> None:
        self._adjust_threshold(-1.0)

    def _adjust_threshold(self, delta_db: float) -> None:
        lo, hi = THRESHOLD_MARGIN_RANGE
        scanner = self.settings.scanner
        margin = clamp(scanner.threshold_margin_db + delta_db, lo, hi)
        scanner.threshold_margin_db = round(margin, 1)
        self.refresh_status()

    def action_dwell_up(self) -> None:
        self._adjust_dwell(+0.02)

    def action_dwell_down(self) -> None:
        self._adjust_dwell(-0.02)

    def _adjust_dwell(self, delta_s: float) -> None:
        lo, hi = DWELL_RANGE_S
        scanner = self.settings.scanner
        scanner.hop_dwell_s = round(clamp(scanner.hop_dwell_s + delta_s, lo, hi), 3)
        self.refresh_status()

    def action_antenna(self) -> None:
        channel = self.selected_channel()
        freq = channel.center_hz if channel else (self.monitor.freq_hz if self.monitor else None)
        if freq is None:
            self.log_line("[yellow]No frequency selected.[/yellow]")
            return
        report = format_report(analyze(freq))
        self.push_screen(AntennaModal(report))

    def action_quit(self) -> None:
        # App-level q has priority over screen bindings, so a modal can never
        # intercept it: close the topmost overlay instead of killing the app.
        if len(self.screen_stack) > 1:
            self.pop_screen()
        else:
            self.exit()

    def action_help(self) -> None:
        self.push_screen(HelpModal())

    def action_export_channels(self) -> None:
        channels = self.sweeper.channels if self.sweeper is not None else []
        if not channels:
            self.log_line("[yellow]Nothing to export yet - no channels discovered.[/yellow]")
            return
        target = export_path_for(
            self.band_name, Path(self.settings.audio.recordings_dir), fmt="csv"
        )
        path = export_channels(channels, target)
        self.log_line(f"[green]Exported {len(channels)} channel(s)[/] -> {path}")

    def action_tune(self) -> None:
        self.push_screen(TuneModal(), self._apply_tune_request)

    def action_bookmark(self) -> None:
        channel = self.selected_channel()
        if channel is None:
            self.log_line("[yellow]Select a channel to name.[/yellow]")
            return
        hint = f"{channel.center_hz / 1e6:.4f} MHz ({channel.demod.value})"
        self.push_screen(
            NameModal(hint, channel.name),
            lambda name: self._save_bookmark(channel.center_hz, channel.demod, name),
        )

    def _save_bookmark(self, freq_hz: float, demod: DemodMode, name: str | None) -> None:
        if not name:
            self.log_line("Bookmark cancelled")
            return
        replaced = upsert_bookmark(self.user_channels, Bookmark(freq_hz, name, demod))
        verb = "updated bookmark" if replaced else "bookmarked"
        self.log_line(f"[green]{verb}[/green] {freq_hz / 1e6:.4f} MHz as '{name}'")
        self._apply_user_channels()
        # Annotate immediately; the tracker would otherwise wait a frame.
        for channel in self._last_channels:
            channel.name = self.user_channels.name_for(channel.center_hz)
        if self._last_channels:
            self.refresh_table(self._last_channels)

    def action_ignore_channel(self) -> None:
        channel = self.selected_channel()
        if channel is None:
            self.log_line("[yellow]Select a channel to ignore.[/yellow]")
            return
        width_hz = max(DEFAULT_IGNORE_WIDTH_HZ, channel.bandwidth_hz)
        add_ignore(self.user_channels, IgnoreEntry(channel.center_hz, width_hz))
        self.log_line(
            f"[green]ignored[/green] {channel.center_hz / 1e6:.4f} MHz "
            f"(±{width_hz / 2 / 1e3:.0f} kHz) - it will drop off the table"
        )
        self._apply_user_channels()

    def _apply_user_channels(self) -> None:
        """Persist the book and push it into the running sweep."""
        try:
            save_user_channels(self.user_channels, self._channels_path)
        except OSError as exc:
            self.log_line(f"[red]cannot save channels file: {exc}[/red]")
        if self.sweeper is not None:
            self.sweeper.set_user_channels(self.user_channels)

    def _apply_tune_request(self, request) -> None:
        if request is None:
            return
        start_hz, end_hz, demod = request
        if end_hz is None:
            self.listen_frequency(start_hz, demod)
        else:
            self.sweep_range(start_hz, end_hz, demod)

    def listen_frequency(self, freq_hz: float, demod: DemodMode | None = None) -> None:
        """Tune straight to a known frequency and listen, bypassing the detector."""
        self.start_monitor(
            freq_hz, demod or guess_demod(freq_hz), muted=False, enable_recorder=False
        )

    def sweep_range(self, start_hz: float, end_hz: float, demod: DemodMode | None = None) -> None:
        """Sweep an arbitrary range; sub-24 MHz switches to HF direct sampling."""
        demod = demod or guess_demod((start_hz + end_hz) / 2)
        label = f"{start_hz / 1e6:g}-{end_hz / 1e6:g} MHz"
        self._start_sweep(Band("custom", label, start_hz, end_hz, demod))

    @on(RssiUpdate)
    def on_rssi_update(self, message: RadioTuiApp.RssiUpdate) -> None:
        if self._ignore_rssi:
            return  # stale reading from a monitor that was just stopped
        self.last_rssi = message.rssi_dbfs
        now = time.monotonic()
        # Repainting the meter for every block starves the audio thread of the
        # GIL; a quarter-Hz status cadence is plenty for a human meter.
        if now - self._last_meter_paint >= 0.25:
            self._last_meter_paint = now
            self._peak_rssi = max(self._peak_rssi * 0.995, message.rssi_dbfs)
            self.refresh_status()

    @on(MonitorError)
    def on_monitor_error(self, message: RadioTuiApp.MonitorError) -> None:
        self.log_line(f"[red]{message.text}[/red]")

    def on_unmount(self) -> None:
        self._stop_replay()
        if self.sweeper is not None:
            self.sweeper.stop()
        if self.monitor is not None:
            self.monitor.stop()
        if self.device is not None:
            self.device.close()

    def action_band(self, name: str) -> None:
        self.start_band(name)

    def action_band_key(self, name: str) -> None:
        if name in BANDS:
            self.start_band(name)


def run_tui(
    force_sim: bool = False,
    bias_tee: bool = False,
    ppm: int = 0,
    offset_tune: bool = False,
    settings: Settings | None = None,
) -> int:
    app = RadioTuiApp(
        force_sim=force_sim,
        bias_tee=bias_tee,
        ppm=ppm,
        offset_tune=offset_tune,
        settings=settings,
        start_sweeper=True,
    )
    app.run()
    return 0
