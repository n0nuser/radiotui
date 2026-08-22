"""radiotui terminal UI."""

from __future__ import annotations

import queue
import time

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, RichLog, Static

from radiotui.antenna.advisor import analyze, format_report
from radiotui.config import (
    BANDS,
    Settings,
    band_needs_hf,
    effective_sample_rate,
    enable_hf,
)
from radiotui.core.models import Channel, DemodMode, ScanState
from radiotui.dsp.spectrum import SweepPlan
from radiotui.scanner.monitor import ChannelMonitor, auto_hold_release_reason
from radiotui.scanner.sweeper import Sweeper
from radiotui.sdr.manager import open_device
from radiotui.tui.widgets.spectrum import SpectrumBar
from radiotui.tui.widgets.waterfall import Waterfall

GAIN_MIN, GAIN_MAX, GAIN_STEP = 0.0, 49.6, 4.8

KEY_DISPLAYS = {"plus": "+", "minus": "-", "question_mark": "?", "enter": "enter"}


def _key_label(binding: Binding) -> str:
    if binding.key_display:
        return str(binding.key_display)
    return KEY_DISPLAYS.get(binding.key, binding.key)


def _band_key(name: str) -> int:
    return sorted(BANDS).index(name) + 1


def _is_band_binding(binding: Binding) -> bool:
    return binding.action.startswith("band(")


def build_help_text() -> str:
    """Two-column key reference, generated from BINDINGS so it cannot go stale."""
    left: list[tuple[str, str]] = []
    right: list[tuple[str, str]] = []
    for binding in RadioTuiApp.BINDINGS:
        if _is_band_binding(binding):  # band presets get their own section below
            continue
        entry = (_key_label(binding), binding.description)
        (left if len(left) <= len(right) else right).append(entry)
    lines = ["[bold]Keys[/bold]"]
    width_l = max(len(k) for k, _ in left)
    width_r = max(len(k) for k, _ in right)
    rows = max(len(left), len(right))
    left += [("", "")] * (rows - len(left))
    right += [("", "")] * (rows - len(right))
    for (lk, ld), (rk, rd) in zip(left, right, strict=True):
        lines.append(f"{lk:<{width_l}}  {ld:<28}{rk:<{width_r}}  {rd}")
    lines += ["", "[bold]Band presets[/bold]"]
    row = []
    for i, name in enumerate(sorted(BANDS), start=1):
        row.append(f"[cyan]{i}[/cyan] {BANDS[name].label:<18}")
        if len(row) == 3:
            lines.append("".join(row))
            row = []
    if row:
        lines.append("".join(row))
    lines += ["", "[dim]q / esc closes this overlay[/dim]"]
    return "\n".join(lines)


class HelpModal(ModalScreen):
    BINDINGS = [Binding("q,escape", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        yield Static(build_help_text(), id="help-report")


class AntennaModal(ModalScreen):
    BINDINGS = [Binding("q,escape", "dismiss", "Close")]

    def __init__(self, report_text: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._text = report_text

    def compose(self) -> ComposeResult:
        yield Static(self._text, id="antenna-report")


class RadioTuiApp(App):
    TITLE = "radiotui"
    SUB_TITLE = "autonomous spectrum scanner"
    CSS = """
    Screen { layout: vertical; }
    #spectrum { height: 12; border: round #3b4d8f; }
    #waterfall { height: 14; border: round #3b4d8f; }
    #main { height: 1fr; }
    #channels { width: 2fr; border: round #3b4d8f; }
    #sidepanel { width: 1fr; layout: vertical; }
    #meter { height: 7; border: round #3b4d8f; content-align: center middle; padding: 0 1; }
    #log { height: 1fr; border: round #3b4d8f; }
    AntennaModal { align: center middle; background: #000000cc; }
    #antenna-report { width: 64; border: thick cyan; padding: 1 2; background: $surface; }
    HelpModal { align: center middle; background: #000000cc; }
    #help-report { width: 72; border: thick cyan; padding: 1 2; background: $surface; }
    """
    BINDINGS = [
        Binding("s", "toggle_sweep", "Sweep"),
        Binding("enter", "listen", "Listen", priority=True),
        Binding("l", "stop_listen", "Stop"),
        Binding("m", "mute", "Mute"),
        Binding("r", "record", "Record"),
        Binding("n", "next_channel", "Next"),
        Binding("p", "prev_channel", "Prev"),
        Binding("comma", "toggle_sort", "Sort", key_display=",", show=False),
        Binding("plus", "gain_up", "Gain+", key_display="+"),
        Binding("minus", "gain_down", "Gain-", key_display="-"),
        Binding("greater_than_sign", "volume_up", "Vol+", key_display=">", show=False),
        Binding("less_than_sign", "volume_down", "Vol-", key_display="<", show=False),
        Binding("a", "antenna", "Antenna"),
        Binding("o", "toggle_autonomous", "Auto"),
        Binding("question_mark", "help", "Help"),
        Binding("q", "quit", "Quit", priority=True),
    ]
    for i, name in enumerate(sorted(BANDS), start=1):
        BINDINGS.append(Binding(str(i), f"band('{name}')", BANDS[name].label, show=False))

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
    ) -> None:
        super().__init__()
        self._force_sim = force_sim
        self._bias_tee_requested = bias_tee
        self._ppm_requested = ppm
        self._offset_tune_requested = offset_tune
        self.bias_tee_on = False
        self.hf_active = False
        self.auto_hold_freq: float | None = None
        self._auto_hold_started_at = 0.0
        self.settings = Settings()
        self.device = None
        self.is_real = False
        self.plan: SweepPlan | None = None
        self.sweeper: Sweeper | None = None
        self.monitor: ChannelMonitor | None = None
        self.resume_sweep_after_listen = False
        self.queue: queue.Queue[ScanState] = queue.Queue(maxsize=4)
        self.band_name = "fm_broadcast"
        self.gain_db: float | None = None
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

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield SpectrumBar(id="spectrum")
        yield Waterfall(id="waterfall")
        with Horizontal(id="main"):
            yield DataTable(id="channels", cursor_type="row")
            with Vertical(id="sidepanel"):
                yield Static("", id="meter")
                yield RichLog(id="log", highlight=False, markup=True)
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(0.15, self.poll_queue)
        table = self.query_one("#channels", DataTable)
        for key, label, width in (
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
        self.start_band(self.band_name)

    def start_band(self, band_name: str) -> None:
        band = BANDS[band_name]
        self.band_name = band_name
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
        self.sweeper.start()
        self.log_line(
            f"Sweeping [bold]{band.label}[/bold] ({len(self.plan.hop_centers_hz)} hops)"
            + (f" @ {effective_sample_rate(self.settings.scanner) / 1e3:.0f} kS/s" if hf else "")
        )

    def log_line(self, text: str) -> None:
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
        active_freqs = [ch.center_hz for ch in latest.channels]
        self.query_one("#spectrum", SpectrumBar).update_frame(
            latest.frame.freqs_hz,
            latest.frame.power_db,
            latest.noise_floor_db,
            latest.threshold_db,
            active_freqs,
        )
        self.query_one("#waterfall", Waterfall).push_frame(
            latest.frame.freqs_hz, latest.frame.power_db, latest.noise_floor_db
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

    COLUMN_KEYS = ("freq", "bw", "peak", "snr", "hits", "demod", "age")

    @staticmethod
    def _row_cells(ch: Channel, now: float) -> tuple[str, ...]:
        return (
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
            f"band {self.band_name}",
            f"sweeps {self.sweeper.sweeps_done if self.sweeper else 0}",
        ]
        if self.last_state:
            parts.append(f"floor {self.last_state.noise_floor_db:.1f} dB")
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
        lines = [Text.from_markup(" │ ".join(parts))]
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

    def action_listen(self) -> None:
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
        monitor = ChannelMonitor(self.device, freq_hz, demod, self.settings, muted=muted)
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
        self.log_line(
            f"[green]clip saved[/green] {message.clip.path.name} ({message.clip.seconds:.1f}s)"
        )

    def stop_monitor(self, resume_sweep: bool = True) -> None:
        if self.monitor is not None:
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

    @on(RssiUpdate)
    def on_rssi_update(self, message: RadioTuiApp.RssiUpdate) -> None:
        self.last_rssi = message.rssi_dbfs
        self._peak_rssi = max(self._peak_rssi * 0.995, message.rssi_dbfs)
        self.refresh_status()

    @on(MonitorError)
    def on_monitor_error(self, message: RadioTuiApp.MonitorError) -> None:
        self.log_line(f"[red]{message.text}[/red]")

    def on_unmount(self) -> None:
        if self.sweeper is not None:
            self.sweeper.stop()
        if self.monitor is not None:
            self.monitor.stop()
        if self.device is not None:
            self.device.close()

    def action_band(self, name: str) -> None:
        self.start_band(name)


def run_tui(
    force_sim: bool = False,
    bias_tee: bool = False,
    ppm: int = 0,
    offset_tune: bool = False,
) -> int:
    app = RadioTuiApp(force_sim=force_sim, bias_tee=bias_tee, ppm=ppm, offset_tune=offset_tune)
    app.run()
    return 0
