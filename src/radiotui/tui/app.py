"""radiotui terminal UI."""

from __future__ import annotations

import queue
import time

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, RichLog, Static

from radiotui.antenna.advisor import analyze, format_report
from radiotui.config import BANDS, Settings
from radiotui.core.models import Channel, DemodMode, ScanState
from radiotui.dsp.spectrum import SweepPlan
from radiotui.sdr.manager import open_device
from radiotui.scanner.monitor import ChannelMonitor
from radiotui.scanner.sweeper import Sweeper
from radiotui.tui.widgets.spectrum import SpectrumBar
from radiotui.tui.widgets.waterfall import Waterfall

GAIN_MIN, GAIN_MAX, GAIN_STEP = 0.0, 49.6, 4.8


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
    """
    BINDINGS = [
        Binding("s", "toggle_sweep", "Sweep"),
        Binding("enter", "listen", "Listen", priority=True),
        Binding("l", "stop_listen", "Stop"),
        Binding("m", "mute", "Mute"),
        Binding("r", "record", "Record"),
        Binding("n", "next_channel", "Next"),
        Binding("p", "prev_channel", "Prev"),
        Binding("plus", "gain_up", "Gain+", key_display="+"),
        Binding("minus", "gain_down", "Gain-", key_display="-"),
        Binding("a", "antenna", "Antenna"),
        Binding("q", "quit", "Quit", priority=True),
    ]
    for i, name in enumerate(sorted(BANDS), start=1):
        BINDINGS.append(Binding(str(i), f"band_{name}", BANDS[name].label, show=False))

    class RssiUpdate(Message):
        def __init__(self, rssi_dbfs: float) -> None:
            super().__init__()
            self.rssi_dbfs = rssi_dbfs

    class MonitorError(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def __init__(self, force_sim: bool = False) -> None:
        super().__init__()
        self._force_sim = force_sim
        self.settings = Settings()
        self.device = None
        self.is_real = False
        self.plan: SweepPlan | None = None
        self.sweeper: Sweeper | None = None
        self.monitor: ChannelMonitor | None = None
        self.resume_sweep_after_listen = False
        self.queue: "queue.Queue[ScanState]" = queue.Queue(maxsize=4)
        self.band_name = "fm_broadcast"
        self.gain_db: float | None = None
        self.muted = False
        self.clips_saved = 0
        self.row_keys: list[float] = []
        self.selected_hz: float | None = None
        self.last_state: ScanState | None = None

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

        try:
            self.device, self.is_real = open_device(prefer_real=not self._force_sim)
        except Exception as exc:
            self.log_line(f"[red]Failed to open any device: {exc}[/red]")
            return
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
        self.plan = SweepPlan.build(
            band.start_hz,
            band.end_hz,
            self.settings.scanner.sample_rate_hz,
            self.settings.scanner.fft_size,
        )
        self.sweeper = Sweeper(self.device, self.plan, self.settings.scanner, self.queue)
        self.sweeper.start()
        self.log_line(f"Sweeping [bold]{band.label}[/bold] ({len(self.plan.hop_centers_hz)} hops)")

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

    def refresh_table(self, channels: list[Channel]) -> None:
        table = self.query_one("#channels", DataTable)
        previous_key = self.selected_key()
        channels_sorted = sorted(channels, key=lambda c: c.peak_db, reverse=True)
        self.row_keys = [ch.center_hz for ch in channels_sorted]
        table.clear()
        now = time.time()
        for ch in channels_sorted:
            table.add_row(
                f"{ch.center_hz/1e6:.4f}",
                f"{ch.bandwidth_hz/1e3:.0f}",
                f"{ch.peak_db:.1f}",
                f"{ch.snr_db:.1f}",
                str(ch.hits),
                ch.demod.value,
                f"{now - ch.last_seen:.0f}",
                key=str(ch.center_hz),
            )
        if previous_key is not None:
            try:
                row_index = self.row_keys.index(previous_key)
                table.move_cursor(row=row_index, column=0)
            except ValueError:
                pass

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
        if self.monitor is not None and self.monitor.running:
            rec = " ●REC" if self.monitor.recorder.recording else ""
            mute = " 🔇" if self.muted else ""
            parts.append(
                f"listening {self.monitor.freq_hz/1e6:.4f} MHz "
                f"({self.monitor.demod.value}) {self.monitor.rssi_dbfs:.0f} dBFS{mute}{rec}"
            )
        elif self.resume_sweep_after_listen:
            parts.append("sweep paused")
        meter.update(Text.from_markup(" │ ".join(parts)))

    def render_meter_bar(self, rssi: float) -> Text:
        lo, hi = -60.0, -10.0
        frac = max(0.0, min(1.0, (rssi - lo) / (hi - lo)))
        filled = int(frac * 30)
        bar = Text("█" * filled + "░" * (30 - filled))
        color = "green" if frac < 0.6 else "yellow" if frac < 0.85 else "red"
        bar.stylize(color, 0, max(filled, 0))
        return Text(f"RSSI {rssi:6.1f} dBFS\n") + bar

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

    def start_monitor(
        self, freq_hz: float, demod: DemodMode, muted: bool, enable_recorder: bool
    ) -> None:
        self.stop_monitor(resume_sweep=False)
        self._pause_sweeper_for_monitor()
        self.muted = muted
        monitor = ChannelMonitor(
            self.device, freq_hz, demod, self.settings, muted=muted
        )
        monitor.recorder.enabled = enable_recorder
        monitor.recorder.on_clip_end = self.on_clip_end
        monitor.on_rssi = lambda db: self.post_message(self.RssiUpdate(db))
        monitor.on_error = lambda msg: self.post_message(self.MonitorError(msg))
        monitor.start()
        self.monitor = monitor
        verb = "Recording" if enable_recorder else "Listening"
        self.log_line(f"{verb} [bold]{freq_hz/1e6:.4f} MHz[/bold] ({demod.value})")

    def on_clip_end(self, clip) -> None:
        self.clips_saved += 1
        self.log_line(f"[green]clip saved[/green] {clip.path.name} ({clip.seconds:.1f}s)")

    def stop_monitor(self, resume_sweep: bool = True) -> None:
        if self.monitor is not None:
            self.monitor.stop()
            self.monitor = None
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
                clips = recorder.stop()
                _ = clips
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
        except Exception as exc:
            self.log_line(f"[red]gain change failed: {exc}[/red]")
        self.refresh_status()

    def action_antenna(self) -> None:
        channel = self.selected_channel()
        freq = channel.center_hz if channel else (
            self.monitor.freq_hz if self.monitor else None
        )
        if freq is None:
            self.log_line("[yellow]No frequency selected.[/yellow]")
            return
        report = format_report(analyze(freq))
        self.push_screen(AntennaModal(report))

    def on_rssi_update(self, message: "RadioTuiApp.RssiUpdate") -> None:
        meter = self.query_one("#meter", Static)
        freq_line = (
            Text(f"{self.monitor.freq_hz/1e6:.4f} MHz\n")
            if self.monitor is not None
            else Text("")
        )
        meter.update(freq_line + self.render_meter_bar(message.rssi_dbfs))
        self.refresh_status()

    def on_monitor_error(self, message: "RadioTuiApp.MonitorError") -> None:
        self.log_line(f"[red]{message.text}[/red]")

    def on_unmount(self) -> None:
        if self.sweeper is not None:
            self.sweeper.stop()
        if self.monitor is not None:
            self.monitor.stop()
        if self.device is not None:
            self.device.close()


def _make_band_action(band_name: str):
    def action(self: RadioTuiApp) -> None:
        self.start_band(band_name)

    return action


for _name in sorted(BANDS):
    setattr(RadioTuiApp, f"action_band_{_name}", _make_band_action(_name))


def run_tui(force_sim: bool = False) -> int:
    app = RadioTuiApp(force_sim=force_sim)
    app.run()
    return 0
