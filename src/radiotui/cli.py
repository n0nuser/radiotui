"""radiotui command line interface."""

from __future__ import annotations

import argparse
import queue
import sys
import time

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from radiotui.antenna.advisor import analyze, format_report
from radiotui.config import BANDS, Band, Settings, band_by_name
from radiotui.core.models import DemodMode
from radiotui.dsp.spectrum import SweepPlan
from radiotui.sdr.manager import describe_devices, open_device

console = Console()


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


def resolve_band(args) -> Band:
    if args.band:
        return band_by_name(args.band)
    if args.start is not None and args.end is not None:
        if args.end <= args.start:
            console.print("[red]--end must be greater than --start[/red]")
            raise SystemExit(2)
        demod = DemodMode(args.demod) if args.demod else DemodMode.NFM
        return Band("custom", f"{args.start:g}-{args.end:g} MHz", args.start * 1e6, args.end * 1e6, demod)
    console.print("[red]provide --band or both --start and --end[/red]")
    raise SystemExit(2)


def open_device_or_exit(force_sim: bool):
    device, is_real = open_device(prefer_real=not force_sim)
    if is_real:
        console.print(f"[green]Using real device:[/] {device.name}")
    else:
        console.print(
            "[yellow]No RTL-SDR hardware found - using SIMULATED device.[/yellow]\n"
            "[dim]Install pyrtlsdr + librtlsdr for real reception "
            "(uv sync --extra sdr).[/dim]"
        )
    return device


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="radiotui",
        description="Autonomous RTL-SDR spectrum scanner with terminal UI.",
    )
    parser.add_argument("--sim", action="store_true", help="force the simulated device")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("devices", help="list detected SDR hardware")

    p_scan = sub.add_parser("scan", help="headless sweep with live table")
    p_scan.add_argument("--band", choices=sorted(BANDS), help="preset band")
    p_scan.add_argument("--start", type=float, metavar="MHZ")
    p_scan.add_argument("--end", type=float, metavar="MHZ")
    p_scan.add_argument("--demod", choices=[d.value for d in DemodMode], default=None)
    p_scan.add_argument("--gain", type=float, default=None)
    p_scan.add_argument("--seconds", type=float, default=None, help="stop after N seconds")

    p_listen = sub.add_parser("listen", help="tune a frequency and play audio")
    p_listen.add_argument("freq", type=parse_freq, metavar="FREQ")
    p_listen.add_argument("--demod", choices=[d.value for d in DemodMode], default=None)
    p_listen.add_argument("--gain", type=float, default=None)
    p_listen.add_argument("--record", action="store_true", help="also VOX-record clips")
    p_listen.add_argument("--seconds", type=float, default=None)

    p_record = sub.add_parser("record", help="VOX-record transmissions to WAV files")
    p_record.add_argument("freq", type=parse_freq, metavar="FREQ")
    p_record.add_argument("--demod", choices=[d.value for d in DemodMode], default=None)
    p_record.add_argument("--gain", type=float, default=None)
    p_record.add_argument("--seconds", type=float, default=None)

    p_ant = sub.add_parser("antenna", help="antenna advisor report")
    p_ant.add_argument("freq", type=parse_freq, metavar="FREQ")

    p_tuner = sub.add_parser("tuner", help="live signal bar to optimize antenna placement")
    p_tuner.add_argument("freq", type=parse_freq, metavar="FREQ")
    p_tuner.add_argument("--demod", choices=[d.value for d in DemodMode], default=None)
    p_tuner.add_argument("--gain", type=float, default=None)

    sub.add_parser("tui", help="launch the terminal UI (default)")

    return parser


def cmd_devices(_args) -> None:
    found = describe_devices()
    if not found:
        console.print("[yellow]No RTL-SDR devices detected.[/yellow]")
        console.print("Check: lsusb, driver blocking (dvb_usb_rtl28xxu), librtlsdr install.")
        return
    for entry in found:
        console.print(f"[green]•[/green] {entry}")


def cmd_scan(args) -> None:
    band = resolve_band(args)
    settings = Settings()
    settings.scanner.gain_db = args.gain
    device = open_device_or_exit(args.sim)
    out: queue.Queue = queue.Queue(maxsize=4)

    from radiotui.scanner.sweeper import Sweeper

    plan = SweepPlan.build(band.start_hz, band.end_hz, settings.scanner.sample_rate_hz, settings.scanner.fft_size)
    sweeper = Sweeper(device, plan, settings.scanner, out)
    sweeper.start()
    console.print(f"Sweeping [bold]{band.label}[/bold]: {band.start_hz/1e6:.3f} - {band.end_hz/1e6:.3f} MHz ({len(plan.hop_centers_hz)} hops). Ctrl+C to stop.")

    t0 = time.time()
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
                live.update(render_scan(state))
                if args.seconds and time.time() - t0 > args.seconds:
                    break
    except KeyboardInterrupt:
        pass
    finally:
        sweeper.stop()
        device.close()
    console.print("[dim]Scan stopped.[/dim]")


def render_scan(state) -> Table:
    table = Table(title=f"sweep #{state.sweeps_done}  floor {state.noise_floor_db:.1f} dB  threshold {state.threshold_db:.1f} dB")
    table.add_column("Frequency", justify="right")
    table.add_column("BW kHz", justify="right")
    table.add_column("Peak dB", justify="right")
    table.add_column("SNR dB", justify="right")
    table.add_column("Hits", justify="right")
    table.add_column("Demod")
    for ch in sorted(state.channels, key=lambda c: c.peak_db, reverse=True)[:15]:
        table.add_row(
            f"{ch.center_hz/1e6:.4f}",
            f"{ch.bandwidth_hz/1e3:.0f}",
            f"{ch.peak_db:.1f}",
            f"{ch.snr_db:.1f}",
            str(ch.hits),
            ch.demod.value,
        )
    return table


def run_monitor(args, freq: float, record_only: bool = False):
    from radiotui.audio.recorder import VoxRecorder
    from radiotui.scanner.monitor import ChannelMonitor

    settings = Settings()
    settings.scanner.gain_db = args.gain
    demod = DemodMode(args.demod) if args.demod else guess_demod(freq)
    device = open_device_or_exit(getattr(args, "sim", False))
    monitor = ChannelMonitor(device, freq, demod, settings, muted=False)
    monitor.recorder.on_clip_end = lambda clip: console.print(
        f"[green]clip saved[/green] {clip.path.name} ({clip.seconds:.1f}s)"
    )
    return device, monitor


def cmd_listen(args) -> None:
    device, monitor = run_monitor(args, args.freq)
    mode = monitor.demod.value
    console.print(
        f"Listening [bold]{args.freq/1e6:.4f} MHz[/bold] ({mode}). "
        "Ctrl+C to stop." + (" Recording active clips." if args.record else "")
    )
    if args.record:
        monitor.recorder.on_clip_end = lambda clip: console.print(
            f"[green]clip saved[/green] {clip.path.name} ({clip.seconds:.1f}s)"
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
    console.print(
        f"Recording [bold]{args.freq/1e6:.4f} MHz[/bold] ({monitor.demod.value}) "
        f"to ./{monitor.recorder._dir}/ - Ctrl+C to stop."
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
    from radiotui.scanner.monitor import ChannelMonitor

    settings = Settings()
    settings.scanner.gain_db = args.gain
    demod = DemodMode(args.demod) if args.demod else guess_demod(args.freq)
    device = open_device_or_exit(args.sim)
    monitor = ChannelMonitor(device, args.freq, demod, settings, muted=True)
    peak_hold = {"db": -120.0}

    def on_rssi(db: float) -> None:
        peak_hold["db"] = max(peak_hold["db"] * 0.995, db)

    monitor.on_rssi = on_rssi
    console.print(f"Tuner on {args.freq/1e6:.4f} MHz ({demod.value}) - move the antenna! Ctrl+C to exit.")
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
    trend = "" if delta > -0.5 else "  [red]▼ worse[/red]" if delta > -3 else "  [red]▼▼ much worse[/red]"
    body = Group(
        Text(f"{freq/1e6:.4f} MHz   RSSI {rssi:6.1f} dBFS   peak {peak:6.1f}"),
        bar,
        Text(f"{delta:+.1f} dB vs peak{trend}"),
    )
    return Panel(body, title="Antenna Tuner", border_style="cyan")


COMMANDS = {
    "devices": cmd_devices,
    "scan": cmd_scan,
    "listen": cmd_listen,
    "record": cmd_record,
    "antenna": cmd_antenna,
    "tuner": cmd_tuner,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command or args.command == "tui":
        from radiotui.tui.app import run_tui

        return run_tui(force_sim=args.sim)
    COMMANDS[args.command](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
