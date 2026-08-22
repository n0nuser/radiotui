"""radiotui command line interface."""

from __future__ import annotations

import argparse
import queue
import sys
import time
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from radiotui.antenna.advisor import analyze, format_report
from radiotui.channels_file import load_user_channels
from radiotui.config import (
    BANDS,
    DWELL_RANGE_S,
    MIN_SNR_RANGE,
    THRESHOLD_MARGIN_RANGE,
    Band,
    Settings,
    band_by_name,
    band_needs_hf,
    clamp,
    effective_sample_rate,
    enable_hf,
    freq_needs_hf,
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
from radiotui.dsp.spectrum import SweepPlan
from radiotui.export import export_channels
from radiotui.scanner.monitor import ChannelMonitor, auto_hold_release_reason
from radiotui.scanner.sweeper import Sweeper
from radiotui.sdr.base import SdrDevice
from radiotui.sdr.manager import OpenedDevice, describe_devices, open_device
from radiotui.tui.app import run_tui
from radiotui.tuning import guess_demod, parse_freq

console = Console()


def resolve_band(args) -> Band:
    if args.band:
        return band_by_name(args.band)
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
            "[yellow]No RTL-SDR hardware found - using SIMULATED device.[/yellow]\n"
            "[dim]Install pyrtlsdr + librtlsdr for real reception "
            "(uv sync --extra sdr).[/dim]"
        )
    return opened.device


def add_hw_options(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Hardware flags with suppressed defaults.

    ``SUPPRESS`` keeps unset flags out of the namespace so that a value given
    before the subcommand is not clobbered by the subparser's defaults.
    """
    parser.add_argument(
        "--bias-tee",
        action="store_true",
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
        action="store_true",
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


def announce_hf(device: SdrDevice) -> None:
    if device.is_real:
        ok = device.set_hf_mode(True)
        state = "[green]ON[/green]" if ok else "[red]failed[/red]"
        console.print(f"HF direct sampling (Q-branch): {state}")
    else:
        console.print("HF band: simulator mode")


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
    found = describe_devices()
    if not found:
        console.print("[yellow]No RTL-SDR devices detected.[/yellow]")
        console.print("Check: lsusb, driver blocking (dvb_usb_rtl28xxu), librtlsdr install.")
        return
    for entry in found:
        console.print(f"[green]•[/green] {entry}")


def apply_scan_tuning(settings: Settings, args) -> None:
    """Headless squelch controls (#17): flags override config-file values."""
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
    if enable_hf(band_needs_hf(band), settings.scanner):
        announce_hf(device)
    out: queue.Queue = queue.Queue(maxsize=4)

    plan = SweepPlan.build(
        band.start_hz,
        band.end_hz,
        effective_sample_rate(settings.scanner),
        settings.scanner.fft_size,
    )
    sweeper = Sweeper(device, plan, settings.scanner, out)
    user_channels, channels_warning = load_user_channels()
    if channels_warning:
        console.print(f"[yellow]channels file ignored:[/] {channels_warning}")
    sweeper.set_user_channels(user_channels)
    sweeper.start()
    console.print(
        f"Sweeping [bold]{band.label}[/bold]:"
        f" {band.start_hz / 1e6:.3f} - {band.end_hz / 1e6:.3f} MHz"
        f" ({len(plan.hop_centers_hz)} hops). Ctrl+C to stop."
    )

    t0 = time.time()
    last_export = 0.0
    try:
        with Live(console=console, refresh_per_second=4) as live:
            while True:
                try:
                    state = out.get(timeout=0.5)
                except queue.Empty:
                    continue
                if state.error:
                    console.print(f"[red]Device error: {state.error}[/red]")
                    break
                live.update(render_scan(state, simulated=not device.is_real))
                if args.export and sweeper.channels and time.time() - last_export > 30.0:
                    _export_sweep(sweeper.channels, args.export, band, settings, sweeper)
                    last_export = time.time()
                if state.hold_request is not None:
                    budget = None
                    if args.seconds:
                        budget = max(1.0, args.seconds - (time.time() - t0))
                    _headless_auto_hold(
                        device, sweeper, settings, state.hold_request, budget, band.label
                    )
                if args.seconds and time.time() - t0 > args.seconds:
                    break
    except KeyboardInterrupt:
        pass
    finally:
        if args.export and sweeper.channels:
            path = _export_sweep(sweeper.channels, args.export, band, settings, sweeper)
            console.print(f"[green]Exported {len(sweeper.channels)} channel(s) to {path}[/green]")
        sweeper.stop()
        device.close()
    console.print("[dim]Scan stopped.[/dim]")


def _export_sweep(channels, path_str: str, band: Band, settings, sweeper) -> Path:
    context = {
        "band": band.label,
        "start_hz": band.start_hz,
        "end_hz": band.end_hz,
        "gain_db": settings.scanner.gain_db,
        "sample_rate_hz": effective_sample_rate(settings.scanner),
        "sweeps_completed": sweeper.sweeps_done,
    }
    return export_channels(channels, path_str, context=context)


def _headless_auto_hold(
    device, sweeper, settings, req, budget_s: float | None = None, band_label: str = ""
) -> None:
    freq_mhz = req.freq_hz / 1e6
    console.print(
        f"[magenta]AUTO[/magenta] holding {freq_mhz:.4f} MHz "
        f"(SNR {req.snr_db:.0f} dB), VOX recording"
    )
    monitor = ChannelMonitor(
        device, req.freq_hz, req.demod, settings, muted=True, band_label=band_label
    )
    monitor.recorder.enabled = True
    monitor.recorder.on_clip_end = lambda clip: console.print(
        f"[green]clip saved[/green] {clip.path.name} ({clip.seconds:.1f}s)"
    )
    monitor.start()
    t_hold = time.time()
    reason = "monitor stopped"
    while monitor.running:
        time.sleep(0.2)
        silent_for = monitor.recorder.seconds_since_voice()
        held_for = time.time() - t_hold
        silent = silent_for >= settings.scanner.hold_release_s
        expired = held_for >= settings.scanner.max_hold_s
        out_of_time = budget_s is not None and held_for >= budget_s
        if silent or expired or out_of_time:
            if out_of_time and not silent:
                reason = "out of time"
            else:
                reason = auto_hold_release_reason(silent_for, settings.scanner.hold_release_s)
            break
    clips = monitor.recorder.stop()
    monitor.stop()
    if not clips:
        console.print(f"[dim]AUTO hold ended ({reason}) without transmissions.[/dim]")
    else:
        console.print(f"[dim]AUTO released ({reason}) after {len(clips)} clip(s).[/dim]")
    sweeper.cooldown_channel(req.freq_hz)
    sweeper.release_hold()


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


def run_monitor(args, freq: float):
    settings = args._settings  # main() always provides the merged settings
    demod = DemodMode(args.demod) if args.demod else guess_demod(freq)
    device = open_device_or_exit(args.sim)
    apply_hw_options(device, args)
    if enable_hf(freq_needs_hf(freq), settings.scanner):
        announce_hf(device)
    monitor = ChannelMonitor(device, freq, demod, settings, muted=False)
    monitor.recorder.on_clip_end = lambda clip: console.print(
        f"[green]clip saved[/green] {clip.path.name} ({clip.seconds:.1f}s)"
    )
    return device, monitor


def cmd_listen(args) -> None:
    device, monitor = run_monitor(args, args.freq)
    mode = monitor.demod.value
    monitor.recorder.enabled = args.record
    record_note = " Recording active clips." if args.record else ""
    console.print(
        f"Listening [bold]{args.freq / 1e6:.4f} MHz[/bold] ({mode}). Ctrl+C to stop." + record_note
    )
    try:
        monitor.start()
        while monitor.running:
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        monitor.stop()
        device.close()
    console.print("[dim]Stopped.[/dim]")


def cmd_record(args) -> None:
    device, monitor = run_monitor(args, args.freq)
    monitor.set_muted(True)
    monitor.recorder.enabled = True
    clips_dir = monitor.recorder.directory
    console.print(
        f"Recording [bold]{args.freq / 1e6:.4f} MHz[/bold] ({monitor.demod.value}) "
        f"to {clips_dir}/ - Ctrl+C to stop."
    )
    t0 = time.time()
    try:
        monitor.start()
        while monitor.running:
            time.sleep(0.25)
            if args.seconds and time.time() - t0 > args.seconds:
                break
    except KeyboardInterrupt:
        pass
    finally:
        clips = monitor.recorder.stop()
        monitor.stop()
        device.close()
    if not clips:
        console.print("[yellow]No transmissions captured.[/yellow]")


def cmd_antenna(args) -> None:
    report = analyze(args.freq)
    console.print(Panel(format_report(report), title="Antenna Advisor", border_style="cyan"))


def cmd_tuner(args) -> None:
    settings = args._settings  # main() always provides the merged settings
    demod = DemodMode(args.demod) if args.demod else guess_demod(args.freq)
    device = open_device_or_exit(args.sim)
    monitor = ChannelMonitor(device, args.freq, demod, settings, muted=True)
    peak_hold = {"db": -120.0}

    def on_rssi(db: float) -> None:
        peak_hold["db"] = max(peak_hold["db"] * 0.995, db)

    monitor.on_rssi = on_rssi
    console.print(
        f"Tuner on {args.freq / 1e6:.4f} MHz ({demod.value}) - move the antenna! Ctrl+C to exit."
    )
    try:
        monitor.start()
        with Live(console=console, refresh_per_second=10) as live:
            while monitor.running:
                live.update(render_meter(monitor.rssi_dbfs, peak_hold["db"], args.freq))
                time.sleep(0.08)
    except KeyboardInterrupt:
        pass
    finally:
        monitor.stop()
        device.close()


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
    console.print(f"Config file: [bold]{path}[/bold] ({'present' if exists else 'not created'})")
    if args.path or not exists:
        return 0
    data = load_config(path)
    if not data:
        console.print("[dim]Empty - running on built-in defaults.[/dim]")
        return 0
    settings = Settings()
    apply_config_to_settings(settings, data, path)
    console.print(f"[scanner] threshold_margin_db = {settings.scanner.threshold_margin_db}")
    console.print(f"[audio] recordings_dir = {settings.audio.recordings_dir}")
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
    if use_config:
        try:
            data = load_config()
            register_user_bands(data)
        except ConfigError as exc:
            console.print(f"[red]Config error:[/] {exc}")
            return 2

    try:
        args = resolve_args(argv, data)
    except ConfigError as exc:
        console.print(f"[red]Config error:[/] {exc}")
        return 2
    if args.command == "config":
        return cmd_config(args)
    args._settings = runtime_settings(args, data)
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
