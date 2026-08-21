# radiotui

Autonomous spectrum scanner for the RTL-SDR v3 with a terminal UI. It sweeps
bands on its own, estimates the noise floor, flags frequencies that show
"life", lets you listen and record them, and helps you place your antenna.

## Quickstart

```bash
# 1. Get the code and dependencies (simulator works with no hardware)
uv sync --group dev

# 2. Try it immediately - synthetic FM stations, airband bursts, PMR446
uv run radiotui                 # TUI in simulator mode
uv run radiotui scan            # or headless scan

# 3. Real hardware
uv sync --group dev --extra sdr # adds pyrtlsdr
uv run radiotui devices         # should list your dongle
uv run radiotui                 # TUI on real RF
```

System packages you need for real reception:

```bash
sudo apt install rtl-sdr ffmpeg   # librtlsdr + rtl_* tools; ffmpeg = audio playback
```

If `radiotui devices` finds nothing but the dongle is plugged in, another
kernel driver is holding it:

```bash
echo 'blacklist dvb_usb_rtl28xxu' | sudo tee /etc/modprobe.d/blacklist-rtl.conf
sudo rmmod dvb_usb_rtl28xxu 2>/dev/null; exit   # reopen your shell
```

### WSL2 (Windows)

WSL2 cannot see USB directly; share the dongle with [usbipd-win](https://github.com/dorssel/usbipd-win):

```powershell
# Windows, admin PowerShell - one time:
usbipd bind --hardware-id 0bda:2838        # RTL-SDR v3 VID:PID (check: usbipd list)

# Every session:
usbipd attach --wsl --hardware-id 0bda:2838
```

```bash
# WSL side:
sudo apt install rtl-sdr ffmpeg
lsusb | grep 2838                          # confirm it arrived
```

Audio plays through WSLg PulseAudio automatically.

### Windows (native, no WSL)

Use Python + [Zadig](https://zadig.akeo.ie) to switch the dongle to WinUSB,
and put `librtlsdr.dll` (from the [RTL-SDR Blog drivers](https://www.rtl-sdr.com/rtl-sdr-quick-start-guide/))
on `PATH`. Install `ffmpeg` for audio playback. See issue #2 for status.

## Features

- **Sweep engine** - hops across any frequency range, computes an averaged PSD
  (FFT) per hop and stitches a full-range power spectrum.
- **Automatic squelch** - rolling noise-floor estimation (median + percentile)
  with persistence tracking: a channel only becomes "active" after it stays
  above the floor for several consecutive frames.
- **Terminal UI** - live bar spectrum, scrolling waterfall, active-channel
  table, signal meter, keyboard controls.
- **Listen & record** - NFM / WFM / AM demodulation in numpy, playback through
  `ffplay`/`aplay`, VOX-gated WAV recording of transmissions.
- **Antenna advisor** - wavelength math (quarter-wave, dipole legs in cm),
  band-specific antenna/orientation recommendations, and a live tuner mode
  with a big signal bar for physically optimizing your setup.
- **Simulator** - no dongle plugged in? A synthetic device generates realistic
  carriers and bursts so every feature is demoable and testable.

## Usage

```bash
radiotui                       # launch the TUI (auto-detects device)
radiotui scan                  # headless sweep, prints live table
radiotui scan --band airband   # preset bands: fm_broadcast, airband,
                               # vhf_ham, vhf_marine, pmr446, uhf_ham
radiotui scan --start 144 --end 146   # custom range
radiotui listen 145.500e6      # tune + demodulate + play
radiotui record 446.00625e6    # VOX-record transmissions to WAV
radiotui antenna 145.500e6     # antenna advisor report
radiotui tuner 105.4e6         # live signal bar to optimize the antenna
radiotui devices               # list detected SDR hardware
```

Frequencies accept suffixes: `105.4M`, `446006k`, `145500000`.

### TUI keys

| Key | Action |
| --- | --- |
| `s` | start / stop sweeping |
| `enter` | listen to selected channel |
| `l` | stop listening (sweep resumes) |
| `m` | mute / unmute |
| `r` | record selected channel (VOX) |
| `n` / `p` | next / previous active channel |
| `+` / `-` | gain up / down |
| `a` | antenna advisor for selected channel |
| `1-6` | switch band preset |
| `q` | quit |

## Layout

```
src/radiotui/
├── config.py     band presets, settings
├── sdr/          device abstraction: real RtlSdr + simulator
├── dsp/          FFT PSD, noise floor, peak detection/tracking
├── scanner/      sweep loop, channel monitor (listen/record)
├── audio/        demodulators, player, VOX recorder
├── antenna/      wavelength math + recommendations
└── tui/          Textual app and widgets
```

## Development

```bash
uv run pytest        # unit tests (no hardware needed)
uv run ruff check .  # lint
uv run ruff format . # format
```
