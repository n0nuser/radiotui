# radiotui

Autonomous spectrum scanner for the RTL-SDR v3 with a terminal UI. It sweeps
bands on its own, estimates the noise floor, flags frequencies that show
"life", lets you listen and record them, and helps you place your antenna.

## Features

- **Sweep engine** — hops across any frequency range, computes an averaged PSD
  (FFT) per hop and stitches a full-range power spectrum.
- **Automatic squelch** — rolling noise-floor estimation (median + percentile)
  with persistence tracking: a channel only becomes "active" after it stays
  above the floor for several consecutive frames.
- **Terminal UI** — live bar spectrum, scrolling waterfall, active-channel
  table, signal meter, keyboard controls.
- **Listen & record** — NFM / WFM / AM demodulation in numpy, playback through
  `ffplay`/`aplay`, VOX-gated WAV recording of transmissions.
- **Antenna advisor** — wavelength math (quarter-wave, dipole legs in cm),
  band-specific antenna/orientation recommendations, and a live tuner mode
  with a big signal bar for physically optimizing your setup.
- **Simulator** — no dongle plugged in? A synthetic device generates realistic
  carriers and bursts so every feature is demoable and testable.

## Install

```bash
uv sync --group dev            # core (simulator mode)
uv sync --group dev --extra sdr  # + pyrtlsdr for real hardware
```

You also need the RTL-SDR userspace tools or at least `librtlsdr` on your
system for real hardware (`sudo apt install rtl-sdr`), plus `ffmpeg` for audio
playback (optional; falls back to `aplay`).

## Usage

```bash
radiotui                       # launch the TUI (auto-detects device)
radiotui scan                  # headless sweep, prints live table
radiotui scan --band airband   # preset bands: fm_broadcast, airband,
                               # vhf_ham, uhf_ham, pmr446
radiotui listen 145.500e6      # tune + demodulate + play
radiotui record 446.00625e6    # VOX-record transmissions to WAV
radiotui antenna 145.500e6     # antenna advisor report
radiotui tuner                 # live signal bar to optimize the antenna
radiotui devices               # list detected SDR hardware
```

### TUI keys

| Key | Action |
| --- | --- |
| `s` | start / stop sweeping |
| `enter` | listen to selected channel |
| `m` | mute / unmute |
| `r` | record selected channel |
| `n` / `p` | jump to next / previous active channel |
| `+` / `-` | gain up / down |
| `a` | antenna advisor for selected channel |
| `q` | quit |

## Layout

```
src/radiotui/
├── config.py     band presets, settings
├── sdr/          device abstraction: real RtlSdr + simulator
├── dsp/          FFT PSD, noise floor, peak detection/tracking
├── scanner/      async sweep loop, channel monitor
├── audio/        demodulators, player, VOX recorder
├── antenna/      wavelength math + recommendations
└── tui/          Textual app and widgets
```
