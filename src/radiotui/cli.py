"""radiotui command line interface."""

from __future__ import annotations

import argparse
import queue
import sys
import time
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from radiotui.antenna.advisor import analyze, format_report
from radiotui.audio.recorder import ClipInfo
from radiotui.channels_file import load_user_channels
from radiotui.config import (
    BANDS,
    DWELL_RANGE_S,
    MIN_SNR_RANGE,
    THRESHOLD_MARGIN_RANGE,
    Band,
    Settings,
    band_for_region,
    clamp,
    effective_sample_rate,
)
from radiotui.config_file import (
    ConfigError,
    apply_config_to_settings,
    apply_hardware_defaults,
    config_path,
    load_config,
    register_user_bands,
    runtime_settings,
)
from radiotui.core.models import DemodMode
from radiotui.export import export_channels
from radiotui.scanner.session import (
    ClipSaved,
    HoldEngaged,
    HoldReleased,
    ListenStarted,
    ListenStopped,
    Notice,
    Rssi,
    ScanUpdate,
    SessionError,
    SessionEvent,
    SweepFailed,
    SweepStarted,
    ToneDetected,
    TunerSession,
)
from radiotui.sdr.base import SdrDevice
from radiotui.sdr.manager import OpenedDevice, hardware_diagnosis, open_device
from radiotui.tui.app import run_tui
from radiotui.tuning import decay_peak_db, guess_demod, parse_freq

console = Console()


def resolve_band(args) -> Band:
    if args.band:
        return band_for_region(args.band, args._settings.scanner.region)
    if args.start is not None and args.end is not None:
        if args.end <= args.start:
            console.print("[red]--end must be greater than --start[/red]")
            raise SystemExit(2)
        demod = DemodMode(args.demod) if args.demod else DemodMode.NFM
        label = f"{args.start:g}-{args.end:g} MHz"
        return Band("custom", label, args.start * 1e6, args.end * 1e6, demod)
    console.print("[red]provide --band or both --start and --end[/red]")
    raise SystemExit(2)


def open_device_or_exit(force_sim: bool) -> SdrDevice:
    opened: OpenedDevice = open_device(prefer_real=not force_sim)
    if opened.is_real:
        console.print(f"[green]Using real device:[/] {opened.device.name}")
    else:
        console.print(
            "[black on yellow] NO HARDWARE - SIMULATED SIGNALS, NOT REAL RADIO [/]\n"
            "[dim]Run `radiotui devices` to see which layer is missing.[/dim]"
        )
    return opened.device


def add_hw_options(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Hardware flags with suppressed defaults.

    ``SUPPRESS`` keeps unset flags out of the namespace so that a value given
    before the subcommand is not clobbered by the subparser's defaults.
    """
    parser.add_argument(
        "--bias-tee",
        action=argparse.BooleanOptionalAction,
        default=argparse.SUPPRESS,
        help="power an LNA via bias tee (~4.5 V)",
    )
    parser.add_argument(
        "--ppm",
        type=int,
        default=argparse.SUPPRESS,
        metavar="N",
        help="crystal correction in ppm",
    )
    parser.add_argument(
        "--offset-tune",
        action=argparse.BooleanOptionalAction,
        default=argparse.SUPPRESS,
        help="tune LO offset by fs/4 (avoids DC spike)",
    )
    return parser


def common_options() -> argparse.ArgumentParser:
    """Shared parent parser: --sim plus the hardware flags, valid in either position."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--sim",
        action="store_true",
        default=argparse.SUPPRESS,
        help="force the simulated device",
    )
    common.add_argument(
        "--no-config",
        action="store_true",
        default=argparse.SUPPRESS,
        help="ignore the config file for this run",
    )
    return add_hw_options(common)


def apply_flag_defaults(args: argparse.Namespace) -> argparse.Namespace:
    """Resolve defaults for flags defined with ``SUPPRESS`` exactly once."""
    args.sim = args.sim if hasattr(args, "sim") else False
    args.bias_tee = args.bias_tee if hasattr(args, "bias_tee") else False
    args.ppm = args.ppm if hasattr(args, "ppm") else 0
    args.offset_tune = args.offset_tune if hasattr(args, "offset_tune") else False
    return args


def apply_hw_options(device: SdrDevice, args) -> None:
    sim_note = " (simulator: ignored)" if not device.is_real else ""
    ppm = getattr(args, "ppm", 0) or 0
    if ppm:
        ok = device.set_freq_correction(ppm)
        state = "[green]applied[/green]" if ok else "[red]unsupported by device[/red]"
        console.print(f"PPM correction {ppm:+d}: {state}{sim_note}")
    if getattr(args, "offset_tune", False):
        ok = device.set_offset_tuning(True)
        state = "[green]on[/green]" if ok else "[red]unsupported[/red]"
        console.print(f"Offset tuning: {state}{sim_note}")
    if getattr(args, "bias_tee", False):
        ok = device.set_bias_tee(True)
        state = (
            "[green]ON - ~4.5 V on antenna port[/green]"
            if ok
            else "[red]not supported by this librtlsdr build[/red]"
        )
        console.print(f"Bias tee: {state}{sim_note}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="radiotui",
        description="Autonomous RTL-SDR spectrum scanner with terminal UI.",
        parents=[common_options()],
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("devices", parents=[common_options()], help="list detected SDR hardware")

    p_scan = sub.add_parser(
        "scan", parents=[common_options()], help="headless sweep with live table"
    )
    p_scan.add_argument("--band", choices=sorted(BANDS), help="preset band")
    p_scan.add_argument("--start", type=float, metavar="MHZ")
    p_scan.add_argument("--end", type=float, metavar="MHZ")
    p_scan.add_argument("--demod", choices=[d.value for d in DemodMode], default=None)
    p_scan.add_argument("--gain", type=float, default=None)
    p_scan.add_argument("--seconds", type=float, default=None, help="stop after N seconds")
    p_scan.add_argument(
        "--autonomous",
        action="store_true",
        help="auto-hold on live channels: VOX-record, release on silence",
    )
    p_scan.add_argument(
        "--export",
        metavar="PATH",
        default=None,
        help="write discovered channels to PATH on exit (.csv or .json)",
    )
    p_scan.add_argument(
        "--threshold-margin",
        type=float,
        metavar="DB",
        default=None,
        help="detection threshold above noise floor in dB (0-40)",
    )
    p_scan.add_argument(
        "--min-snr",
        type=float,
        metavar="DB",
        default=None,
        help="minimum SNR for a channel to count (0-30)",
    )
    p_scan.add_argument(
        "--dwell",
        type=float,
        metavar="S",
        default=None,
        help="hop dwell time in seconds (0.02-1.0)",
    )

    p_listen = sub.add_parser(
        "listen", parents=[common_options()], help="tune a frequency and play audio"
    )
    p_listen.add_argument("freq", type=parse_freq, metavar="FREQ")
    p_listen.add_argument("--demod", choices=[d.value for d in DemodMode], default=None)
    p_listen.add_argument("--gain", type=float, default=None)
    p_listen.add_argument("--record", action="store_true", help="also VOX-record clips")
    p_listen.add_argument("--seconds", type=float, default=None)

    p_record = sub.add_parser(
        "record", parents=[common_options()], help="VOX-record transmissions to WAV files"
    )
    p_record.add_argument("freq", type=parse_freq, metavar="FREQ")
    p_record.add_argument("--demod", choices=[d.value for d in DemodMode], default=None)
    p_record.add_argument("--gain", type=float, default=None)
    p_record.add_argument("--seconds", type=float, default=None)

    p_ant = sub.add_parser("antenna", parents=[common_options()], help="antenna advisor report")
    p_ant.add_argument("freq", type=parse_freq, metavar="FREQ")

    p_tuner = sub.add_parser(
        "tuner", parents=[common_options()], help="live signal bar to optimize antenna placement"
    )
    p_tuner.add_argument("freq", type=parse_freq, metavar="FREQ")
    p_tuner.add_argument("--demod", choices=[d.value for d in DemodMode], default=None)
    p_tuner.add_argument("--gain", type=float, default=None)

    sub.add_parser("tui", parents=[common_options()], help="launch the terminal UI (default)")

    sub.add_parser("config", parents=[common_options()], help="show config file information")
    p_config = sub.choices["config"]
    p_config.add_argument("--path", action="store_true", help="print the config file path")

    return parser


def cmd_devices(_args) -> None:
    """Report each layer between the USB port and radiotui, not just the verdict.

    "Not detected" has several very different causes — pyrtlsdr absent,
    librtlsdr unloadable, no dongle attached — and they need different fixes.
    """
    steps = hardware_diagnosis()
    for step in steps:
        mark = "[green]✓[/green]" if step.ok else "[red]✗[/red]"
        console.print(f"{mark} [bold]{step.label}[/bold]: {step.detail}")
        if step.fix:
            console.print(f"   [cyan]fix:[/cyan] {step.fix}")
    if all(step.ok for step in steps):
        return
    console.print(
        "\n[yellow]No usable hardware; radiotui will fall back to the SIMULATOR,"
        " whose signals are synthetic.[/yellow]"
    )
    console.print(
        "[dim]Running under WSL? USB devices are not shared with WSL by default -"
        " see 'No device detected' in the README.[/dim]"
    )


def apply_scan_tuning(settings: Settings, args) -> None:
    """Headless squelch controls: flags override config-file values."""
    scanner = settings.scanner
    if args.threshold_margin is not None:
        lo, hi = THRESHOLD_MARGIN_RANGE
        scanner.threshold_margin_db = clamp(args.threshold_margin, lo, hi)
    if args.min_snr is not None:
        lo, hi = MIN_SNR_RANGE
        scanner.min_snr_db = clamp(args.min_snr, lo, hi)
    if args.dwell is not None:
        lo, hi = DWELL_RANGE_S
        scanner.hop_dwell_s = clamp(args.dwell, lo, hi)


def cmd_scan(args) -> None:
    band = resolve_band(args)
    settings = args._settings  # main() always provides the merged settings
    apply_scan_tuning(settings, args)
    settings.scanner.autonomous = args.autonomous
    device = open_device_or_exit(args.sim)
    apply_hw_options(device, args)
    events: queue.Queue = queue.Queue()
    session = TunerSession.start(device, settings, events.put)
    user_channels, channels_warning = load_user_channels()
    if channels_warning:
        console.print(f"[yellow]channels file ignored:[/] {channels_warning}")
    session.set_user_channels(user_channels)
    plan = session.sweep(band)
    console.print(
        f"Sweeping [bold]{band.label}[/bold]:"
        f" {band.start_hz / 1e6:.3f} - {band.end_hz / 1e6:.3f} MHz"
        f" ({len(plan.hop_centers_hz)} hops). Ctrl+C to stop."
    )

    t0 = time.time()
    last_export = 0.0
    sweeps = 0
    try:
        with Live(console=console, refresh_per_second=4) as live:
            while not (args.seconds and time.time() - t0 > args.seconds):
                try:
                    event = events.get(timeout=0.5)
                except queue.Empty:
                    continue
                if isinstance(event, SweepFailed):
                    console.print(f"[red]Device error: {escape(event.message)}[/red]")
                    break
                if not isinstance(event, ScanUpdate):
                    _print_session_event(event, device)
                    continue
                live.update(render_scan(event.state, simulated=not device.is_real))
                sweeps = event.state.sweeps_done
                if args.export and time.time() - last_export > 30.0:
                    channels = session.channels
                    if channels:
                        _export_sweep(channels, args.export, band, settings, sweeps, session.tones)
                        last_export = time.time()
    except KeyboardInterrupt:
        pass
    finally:
        channels = session.channels
        if args.export and channels:
            path = _export_sweep(channels, args.export, band, settings, sweeps, session.tones)
            console.print(f"[green]Exported {len(channels)} channel(s) to {path}[/green]")
        _close_session(session, events, device)
    console.print("[dim]Scan stopped.[/dim]")


def _export_sweep(
    channels, path_str: str, band: Band, settings, sweeps: int, tones: dict[float, str]
) -> Path:
    context = {
        "band": band.label,
        "start_hz": band.start_hz,
        "end_hz": band.end_hz,
        "gain_db": settings.scanner.gain_db,
        "freq_correction_ppm": settings.scanner.freq_correction_ppm,
        "bias_tee": settings.scanner.bias_tee,
        "offset_tune": settings.scanner.offset_tune,
        "sample_rate_hz": effective_sample_rate(settings.scanner),
        "sweeps_completed": sweeps,
    }
    return export_channels(channels, path_str, context=context, tones=tones)


def _print_clip(clip: ClipInfo) -> None:
    console.print(f"[green]clip saved[/green] {clip.path.name} ({clip.seconds:.1f}s)")


def _print_hf(hf_ok: bool | None, device: SdrDevice) -> None:
    if hf_ok is None:
        return
    if device.is_real:
        state = "[green]ON[/green]" if hf_ok else "[red]failed[/red]"
        console.print(f"HF direct sampling (Q-branch): {state}")
    else:
        console.print("HF band: simulator mode")


def _print_session_event(event: SessionEvent, device: SdrDevice) -> None:
    """What the headless commands print for everything but scan updates."""
    if isinstance(event, (SweepStarted, ListenStarted)):
        _print_hf(event.hf_ok, device)
    elif isinstance(event, ClipSaved):
        _print_clip(event.clip)
    elif isinstance(event, ToneDetected):
        if event.tone is not None:
            console.print(f"tone [cyan]{event.tone}[/cyan] on {event.freq_hz / 1e6:.4f} MHz")
    elif isinstance(event, HoldEngaged):
        req = event.request
        console.print(
            f"[magenta]AUTO[/magenta] holding {req.freq_hz / 1e6:.4f} MHz "
            f"(SNR {req.snr_db:.0f} dB), VOX recording"
        )
    elif isinstance(event, HoldReleased):
        if event.clips:
            console.print(f"[dim]AUTO released ({event.reason}) after {event.clips} clip(s).[/dim]")
        else:
            console.print(f"[dim]AUTO hold ended ({event.reason}) without transmissions.[/dim]")
    elif isinstance(event, (SessionError, SweepFailed)):
        console.print(f"[red]{escape(event.message)}[/red]")
    elif isinstance(event, Notice):
        console.print(escape(event.message))


def _close_session(session: TunerSession, events: queue.Queue, device: SdrDevice) -> int:
    """Close the session, print what it reports on the way out; returns clips saved."""
    session.close()
    if not session.wait_closed(TunerSession.CLOSE_WAIT_S):
        console.print("[yellow]A device read is stuck; leaving the device to the OS.[/yellow]")
    clips = 0
    while True:
        try:
            event = events.get_nowait()
        except queue.Empty:
            return clips
        if isinstance(event, ClipSaved):
            clips += 1
        if not isinstance(event, ScanUpdate):
            _print_session_event(event, device)


def render_scan(state, simulated: bool = False) -> Table:
    title = (
        f"sweep #{state.sweeps_done}  "
        f"floor {state.noise_floor_db:.1f} dB  threshold {state.threshold_db:.1f} dB"
    )
    if simulated:
        title += "  [yellow](SIMULATED)[/yellow]"
    table = Table(title=title)
    table.add_column("Name")
    table.add_column("Frequency", justify="right")
    table.add_column("BW kHz", justify="right")
    table.add_column("Peak dB", justify="right")
    table.add_column("SNR dB", justify="right")
    table.add_column("Hits", justify="right")
    table.add_column("Demod")
    for ch in sorted(state.channels, key=lambda c: c.peak_db, reverse=True)[:15]:
        table.add_row(
            ch.name,
            f"{ch.center_hz / 1e6:.4f}",
            f"{ch.bandwidth_hz / 1e3:.0f}",
            f"{ch.peak_db:.1f}",
            f"{ch.snr_db:.1f}",
            str(ch.hits),
            ch.demod.value,
        )
    return table


def _start_listening(
    args, freq: float, *, muted: bool, record: bool
) -> tuple[TunerSession, SdrDevice, queue.Queue, DemodMode]:
    demod = DemodMode(args.demod) if args.demod else guess_demod(freq)
    device = open_device_or_exit(args.sim)
    apply_hw_options(device, args)
    events: queue.Queue = queue.Queue()
    session = TunerSession.start(device, args._settings, events.put)
    session.listen(freq, demod, record=record, muted=muted)
    return session, device, events, demod


def _run_until_done(args, events: queue.Queue, device: SdrDevice) -> int:
    """Print events until the listen ends, --seconds pass, or Ctrl+C; returns clips."""
    t0 = time.time()
    clips = 0
    try:
        while not (args.seconds and time.time() - t0 > args.seconds):
            try:
                event = events.get(timeout=0.25)
            except queue.Empty:
                continue
            if isinstance(event, ListenStopped):
                break  # it ended on its own; the reason was already printed
            if isinstance(event, ClipSaved):
                clips += 1
            _print_session_event(event, device)
    except KeyboardInterrupt:
        pass
    return clips


def cmd_listen(args) -> None:
    session, device, events, demod = _start_listening(
        args, args.freq, muted=False, record=args.record
    )
    record_note = " Recording active clips." if args.record else ""
    console.print(
        f"Listening [bold]{args.freq / 1e6:.4f} MHz[/bold] ({demod.value}). Ctrl+C to stop."
        + record_note
    )
    try:
        _run_until_done(args, events, device)
    finally:
        _close_session(session, events, device)
    console.print("[dim]Stopped.[/dim]")


def cmd_record(args) -> None:
    session, device, events, demod = _start_listening(args, args.freq, muted=True, record=True)
    console.print(
        f"Recording [bold]{args.freq / 1e6:.4f} MHz[/bold] ({demod.value}) "
        f"to {args._settings.audio.recordings_dir}/ - Ctrl+C to stop."
    )
    try:
        clips = _run_until_done(args, events, device)
    finally:
        clips += _close_session(session, events, device)
    if not clips:
        console.print("[yellow]No transmissions captured.[/yellow]")


def cmd_antenna(args) -> None:
    report = analyze(args.freq)
    console.print(Panel(format_report(report), title="Antenna Advisor", border_style="cyan"))


def cmd_tuner(args) -> None:
    session, device, events, demod = _start_listening(args, args.freq, muted=True, record=False)
    console.print(
        f"Tuner on {args.freq / 1e6:.4f} MHz ({demod.value}) - move the antenna! Ctrl+C to exit."
    )
    rssi = peak = -120.0
    try:
        with Live(console=console, refresh_per_second=10) as live:
            listening = True
            while listening:
                time.sleep(0.08)
                while True:
                    try:
                        event = events.get_nowait()
                    except queue.Empty:
                        break
                    if isinstance(event, Rssi):
                        rssi = event.dbfs
                        peak = decay_peak_db(peak, rssi)
                    elif isinstance(event, ListenStopped):
                        listening = False
                    else:
                        _print_session_event(event, device)
                live.update(render_meter(rssi, peak, args.freq))
    except KeyboardInterrupt:
        pass
    finally:
        _close_session(session, events, device)


def render_meter(rssi: float, peak: float, freq: float) -> Panel:
    lo, hi = -70.0, -5.0
    frac = max(0.0, min(1.0, (rssi - lo) / (hi - lo)))
    width = 60
    filled = int(frac * width)
    bar = Text("█" * filled + "░" * (width - filled))
    bar.stylize("green" if frac < 0.7 else "yellow" if frac < 0.9 else "red", 0, filled)
    delta = rssi - peak
    if delta > -0.5:
        trend = ""
    elif delta > -3:
        trend = "  [red]▼ worse[/red]"
    else:
        trend = "  [red]▼▼ much worse[/red]"
    body = Group(
        Text(f"{freq / 1e6:.4f} MHz   RSSI {rssi:6.1f} dBFS   peak {peak:6.1f}"),
        bar,
        Text(f"{delta:+.1f} dB vs peak{trend}"),
    )
    return Panel(body, title="Antenna Tuner", border_style="cyan")


def cmd_config(args) -> int:
    path = config_path()
    exists = path.exists()
    console.print(
        f"Config file: [bold]{path}[/bold] ({'present' if exists else 'not created'})",
        soft_wrap=True,
    )
    if args.path or not exists:
        return 0
    data = load_config(path)
    if not data:
        console.print("[dim]Empty - running on built-in defaults.[/dim]")
        return 0
    settings = Settings()
    apply_config_to_settings(settings, data, path)
    console.print(escape(f"[scanner] threshold_margin_db = {settings.scanner.threshold_margin_db}"))
    console.print(escape(f"[audio] recordings_dir = {settings.audio.recordings_dir}"))
    return 0


COMMANDS = {
    "devices": cmd_devices,
    "scan": cmd_scan,
    "listen": cmd_listen,
    "record": cmd_record,
    "antenna": cmd_antenna,
    "tuner": cmd_tuner,
}


def resolve_args(argv: list[str], data: dict) -> argparse.Namespace:
    """Parse, then merge in precedence order: defaults <- config file <- flags.

    Config values only fill flags the user did not type (SUPPRESS keeps those
    absent), so this must run BEFORE apply_flag_defaults resolves the rest.
    """
    args = build_parser().parse_args(argv)
    apply_hardware_defaults(args, data)
    return apply_flag_defaults(args)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:]) if argv is None else list(argv)
    use_config = "--no-config" not in argv

    data: dict = {}
    try:
        if use_config:
            data = load_config()
            register_user_bands(data)
        args = resolve_args(argv, data)
        if args.command == "config":
            return cmd_config(args)
        args._settings = runtime_settings(args, data)
    except ConfigError as exc:
        # escape: "[scanner]" in a message is not Rich markup
        console.print(f"[red]Config error:[/] {escape(str(exc))}", soft_wrap=True)
        return 2
    if not args.command or args.command == "tui":
        return run_tui(
            force_sim=args.sim,
            bias_tee=args.bias_tee,
            ppm=args.ppm,
            offset_tune=args.offset_tune,
            settings=args._settings,
        )
    COMMANDS[args.command](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
