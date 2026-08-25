# radiotui

<p align="center">
  <img src="docs/tui.svg" alt="radiotui terminal UI sweeping FM broadcast on a real RTL-SDR v3" width="100%">
</p>

Autonomous spectrum scanner for the RTL-SDR v3 with a terminal UI. It sweeps
bands on its own, estimates the noise floor, flags frequencies that show
"life", lets you listen and record them, and helps you place your antenna.

## Features

- **Sweep engine** — hops across any frequency range, computes an averaged PSD
  (FFT) per hop and stitches a full-range power spectrum.
- **Automatic squelch** — rolling noise-floor estimation (median + percentile)
  with persistence tracking: a channel only becomes "active" after it stays
  above the floor for several consecutive frames.
- **Radio-first terminal UI** — opens like an FM radio: full-height spectrum
  carousel with the frequency ruler beneath it, a bright tuning cursor you walk
  with the arrow keys, and a dial/RSSI meter under that; channel table, log and
  recordings pane are opt-in (`t`/`c`), settings live in a menu (`m`).
- **Listen & record** — NFM / WFM / AM demodulation in numpy, playback through
  `ffplay`/`aplay`, VOX-gated WAV recording of transmissions with an optional
  RF-carrier squelch gate so quiet channels stop producing hiss-only clips.
- **Bookmarks & ignore list** (`~/.config/radiotui/channels.toml`) — name the
  frequencies you care about, silence the birdies; names show in the table and
  exports, ignored windows never become channels or auto-holds.
- **Recordings browser** — session clip list in the TUI (`c`), enter replays a
  clip through the player; every kept WAV gets a `.json` sidecar with
  frequency, demod, band, timestamps, RSSI and hardware context.
- **Channel export** (`e`, `scan --export`) — CSV for spreadsheets, JSON with
  context for scripts.
- **Autonomous scan-and-hold** (`o` in the TUI, `--autonomous` headless) —
  when a channel crosses the activation gate the scanner pauses its sweep,
  VOX-records transmissions on that frequency and releases back to sweeping
  after a few seconds of silence, with per-channel cooldown.
- **Hardware controls** — bias tee (~4.5 V for LNAs / active antennas),
  PPM crystal correction and optional LO-offset tuning.
- **HF coverage** — bands below 24 MHz automatically switch the v3 into
  direct-sampling Q-branch mode (500 kHz - 28.8 MHz).
- **Antenna advisor** — wavelength math (quarter-wave, dipole legs in cm),
  band-specific antenna/orientation recommendations, and a live tuner mode
  with a big signal bar for physically optimizing your setup.
- **Simulator** — no dongle plugged in? A synthetic device generates realistic
  carriers and bursts so every feature is demoable and testable.

## Install

Pick **one** of these — they are alternatives, not steps:

```bash
uv sync --group dev --extra sdr  # real hardware (also runs the simulator)
uv sync --group dev              # simulator only, no pyrtlsdr
```

> **Do not run the second one afterwards.** `uv sync` makes the environment
> match exactly what you asked for, so a later `uv sync --group dev` *removes*
> `pyrtlsdr` again and radiotui silently drops back to the simulator. If
> `import rtlsdr` suddenly fails, this is almost always why.

You also need the RTL-SDR userspace tools or at least `librtlsdr` on your
system for real hardware (`sudo apt install rtl-sdr`), plus `ffmpeg` for audio
playback (optional; falls back to `aplay`).

Check what radiotui can actually see at any point:

```bash
radiotui devices        # reports each layer: pyrtlsdr -> librtlsdr -> dongle
```

If no hardware is usable, radiotui still starts, but the TUI shows a red
**NO SDR HARDWARE FOUND** dialog that you have to dismiss deliberately: the
simulator's signals are synthetic, and anything you test against them tells
you nothing about real reception.

### Linux hardware setup (validated on Ubuntu 24.04)

```bash
sudo apt install rtl-sdr ffmpeg   # librtlsdr userspace + audio playback
```

The DVB-T kernel driver (`dvb_usb_rtl28xxu`) claims the dongle by default.
librtlsdr detaches it automatically on each open (you'll see
`Detached kernel driver` on stderr), so no reboot is needed after dropping a
blacklist file; to make it permanent:

```bash
echo 'blacklist dvb_usb_rtl28xxu' | sudo tee /etc/modprobe.d/blacklist-dvb_usb_rtl28xxu.conf
```

**Ubuntu/Debian quirk:** the distro `librtlsdr2` package (2.0.1) lacks symbols
that `pyrtlsdr` requires at import time (`rtlsdr_set_dithering`, GPIO helpers,
`rtlsdr_set_and_get_tuner_bandwidth`), so `import rtlsdr` fails with
`AttributeError: undefined symbol`. Fix without touching system packages by
building a tiny forwarder library and putting it first on the loader path:

```bash
mkdir -p ~/.local/lib/rtlsdr-shim && cd ~/.local/lib/rtlsdr-shim
cat > shim.c <<'EOF'
/* Forwarder: adds symbols missing from Ubuntu's librtlsdr2 (2.0.1).
   Real functionality comes from the DT_NEEDED system library;
   dithering/GPIO stubs are functional no-ops (bias-tee still works,
   the system build exports rtlsdr_set_bias_tee). */
int rtlsdr_set_dithering(void *dev, int on) { (void)dev; (void)on; return 0; }
int rtlsdr_set_gpio_output(void *dev, unsigned char g) { (void)dev; (void)g; return 0; }
int rtlsdr_set_gpio_input(void *dev, unsigned char g) { (void)dev; (void)g; return 0; }
int rtlsdr_set_gpio_bit(void *dev, unsigned char g, int v) { (void)dev; (void)g; (void)v; return 0; }
unsigned char rtlsdr_get_gpio_bit(void *dev, unsigned char g) { (void)dev; (void)g; return 0; }
int rtlsdr_set_gpio_byte(void *dev, int v) { (void)dev; (void)v; return 0; }
unsigned char rtlsdr_get_gpio_byte(void *dev) { (void)dev; return 0; }
int rtlsdr_set_gpio_status(void *dev, unsigned char *b) { (void)dev; if(b)*b=0; return 0; }
int rtlsdr_set_and_get_tuner_bandwidth(void *dev, unsigned long bw,
                                       unsigned long *applied, int apply) {
    (void)dev; (void)bw; (void)apply; if(applied)*applied=0; return 0; }
EOF
gcc -shared -fPIC -Wl,-soname,librtlsdr.so -Wl,--no-as-needed \
    -o librtlsdr.so shim.c -l:librtlsdr.so.2 -Wl,-rpath,/lib/x86_64-linux-gnu
echo 'export LD_LIBRARY_PATH="$HOME/.local/lib/rtlsdr-shim${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"' >> ~/.bashrc
```

Then verify with `radiotui devices`.

### No device detected

`radiotui devices` reports the layers in order; fix the first one that fails.

| Symptom | Layer | Fix |
| --- | --- | --- |
| `ModuleNotFoundError: No module named 'rtlsdr'` | pyrtlsdr not installed | `uv sync --group dev --extra sdr` — and see the warning in [Install](#install) about re-running plain `uv sync` |
| `AttributeError: ... undefined symbol: rtlsdr_set_dithering` | librtlsdr too old | the shim above |
| `lsusb` shows nothing | the OS cannot see the dongle | different cable/port, avoid hubs; under WSL see below |
| `lsusb` shows it, `rtl_test` says "No supported devices found" | driver or permissions | blacklist `dvb_usb_rtl28xxu`, then try `sudo rtl_test -t` — if root works, it is udev rules |

#### WSL: the dongle must be attached to the VM

WSL does **not** share USB devices with Windows by default. A dongle plugged
into the host is invisible inside WSL: `lsusb` prints nothing and `rtl_test`
reports "No supported devices found", exactly as if nothing were connected.
Attach it explicitly with [usbipd-win](https://github.com/dorssel/usbipd-win):

```powershell
# On Windows. Install once:
winget install usbipd

# PowerShell as Administrator:
usbipd list                        # find the RTL-SDR, note its BUSID (e.g. 1-4)
usbipd bind --busid 1-4            # share it (one-time per device)

# Any PowerShell (keep a WSL shell open so the VM stays alive):
usbipd attach --wsl --busid 1-4
```

Then, inside WSL:

```bash
lsusb | grep -i realtek   # should now list the dongle
radiotui devices
```

Notes:

- **Attachment does not survive** a reboot, a replug, or a device reset — you
  must `usbipd attach` again each time.
- Requires a WSL kernel of 5.10.60.1 or newer (`uname -r` to check).
- `usbipd detach --busid 1-4` returns the device to Windows.

Microsoft documents the same flow in
[Connect USB devices](https://learn.microsoft.com/en-us/windows/wsl/connect-usb).

USB passthrough adds latency and can drop samples under load. If you hit audio
gaps *only* under WSL, try native Linux before assuming it is a radiotui bug.

### Measured performance (RTL-SDR v3, R820T, indoor dipole)

| Metric | Value |
| --- | --- |
| Device detection | `Realtek RTL2838UHIDIR SN 00000001`, works as non-root user |
| FM broadcast sweep (27 hops) | ~4 s/sweep (~7 hops/s) standalone, dwell 120 ms |
| Simulated sweep | ~50 hops/s (no USB latency) |
| Real noise floor | −49 dB @ gain 28 dB (simulator baseline: −62 dBFS); default threshold margin works unmodified |
| DC spike | masked automatically around every hop center (±3% of sample rate, real devices only); no phantom channels observed |
| Audio playback | `aplay` fallback works without ffmpeg |
| Recordings | WAV clips land in `recordings/` (48 kHz mono PCM16) |

### HF direct sampling — notes and gotchas

- Switching into a band below 24 MHz enables `set_direct_sampling(2)`
  (Q-branch) automatically; switching back to VHF/UHF disables it.
- **Sample rate is forced to 28.8 MHz / 115 ≈ 250.43 kS/s in HF mode.**
  Sync reads at ~1 MS/s in direct-sampling mode overflow the USB ring buffer
  (`LIBUSB_ERROR_OVERFLOW`) on this host. 250 kS/s is rock solid and still
  far wider than any HF channel.
- **Sync reads are aligned to multiples of 256 samples** inside
  `RtlSdrDevice.read_samples`: the RTL2832 bulk endpoint overruns unless
  transfer lengths are multiples of 512 bytes.
- Validated live: shortwave broadcast carriers at 9.815 / 11.765 / 11.910 MHz
  detected with the stock indoor dipole; AM listen works.

### Crystal PPM calibration

`rtl_test -p` is noisy on this host because of USB sample losses
(cumulative −70 ppm, per-window values −106…+320). FM carrier centroids gave
station-dependent results (+6…−22 ppm) because broadcast modulation skews the
centroid. Practical guidance: start with the cumulative `rtl_test -p` value,
then refine against a known narrowband reference (DAB or GSM downlink) if you
need sub-kHz accuracy. `--offset-tune` is exposed but **reports "unsupported"
on Ubuntu's librtlsdr2 2.0.1** even via raw symbol calls — the automatic DC
masking makes it unnecessary anyway.

## Usage

```bash
radiotui                       # launch the TUI (auto-detects device)
radiotui scan                  # headless sweep, prints live table
radiotui scan --band airband   # presets: hf_broadcast, hf_ham_40m, hf_ham_80m,
                               # fm_broadcast, airband, vhf_ham, vhf_marine,
                               # pmr446, uhf_ham
radiotui scan --autonomous     # sweep + auto-hold + VOX record live channels
radiotui listen 145.500e6      # tune + demodulate + play
radiotui listen 96.9e6 --bias-tee   # power an LNA while listening
radiotui record 446.00625e6    # VOX-record transmissions to WAV
radiotui scan --ppm -70        # correct crystal error (see measurement below)
radiotui scan --autonomous --export findings.json   # soak + machine-readable results
radiotui antenna 145.500e6     # antenna advisor report
radiotui tuner 145.5M           # live signal bar to optimize the antenna
radiotui devices               # list detected SDR hardware
```

Hardware flags `--bias-tee`, `--ppm N` and `--offset-tune` work on every
command (scan / listen / record / tuner / tui). Bands starting below 24 MHz
switch into HF direct sampling automatically.

### Config file (optional)

Stop retyping flags: `~/.config/radiotui/config.toml` merges as
**defaults → config file → CLI flags**, so flags still win. `radiotui config`
shows the path; `--no-config` ignores it for one run.

```toml
[hardware]
ppm = -70            # crystal correction, applied on every run
bias_tee = true      # keep your LNA powered
gain_db = 28.0       # default gain when --gain is not given

[scanner]
squelch_rssi_dbfs = -30   # RF gate for recordings; null = VOX level only
region = "r1"              # ITU region: r1, r2, or r3

[audio]
recordings_dir = "~/radio/recordings"
min_clip_seconds = 0.7     # shorter VOX blips are discarded
deemphasis_us = 50         # FM broadcast curve: 50 or 75

[[band]]             # custom presets join the built-ins:
name = "ism_433"     # --band ism_433; press 0 to cycle user bands in the TUI
label = "ISM 433"
start_hz = 433_050_000
end_hz = 434_790_000
demod = "nfm"
```

### Bookmarks and ignore list (optional)

`~/.config/radiotui/channels.toml` names the frequencies you care about and
silences the ones you don't.
Press `b` in the TUI to name the selected channel, `x` to ignore it, and `Shift-X`
to remove an ignore window at the selected frequency — all three actions
write this file immediately.

```toml
[[bookmark]]
freq_hz = 145_500_000
name = "2m calling"
demod = "nfm"

[[ignore]]
freq_hz = 96_000_000
width_hz = 20_000
note = "birdie"
```

Ignored windows never become channels, never reach exports and are never
auto-held; bookmarked channels carry their name in the table, in headless scan
output and in CSV/JSON exports.

### TUI keys

The TUI opens in the radio view: the spectrum carousel with the frequency ruler
beneath it, and the dial plus signal meter below that. The status line shows
`sweep #N hop 12/27` while a sweep is running and `paused` when it is not — a
full pass takes seconds (27 hops on FM broadcast), so the display only redraws
once per completed sweep.
`←/→` walk the tuning cursor across the band, `↑/↓` coarse-step 100 kHz,
`enter` plays what the cursor points at.

| Key | Action |
| --- | --- |
| `←` / `→` | tuning cursor one column left / right |
| `↑` / `↓` | cursor -/+ 100 kHz |
| `enter` | radio view: listen under the cursor · panels: listen to selected row |
| `l` | stop listening / replaying |
| `s` | start / stop sweeping (refused while listening) |
| `t` | show/hide analyst panels (channel table, log, recordings) |
| `m` | settings menu (threshold, dwell, min SNR, squelch, volume) |
| `M` | mute / unmute |
| `r` | record selected channel |
| `n` / `p` | jump to next / previous active channel (panels) |
| `+` / `-` | gain up / down |
| `<` / `>` | volume down / up while listening |
| `[` / `]` | detection threshold down / up |
| `{` / `}` | hop dwell down / up |
| `,` | sort table by frequency / peak |
| `b` | name (bookmark) the selected channel |
| `x` | add the selected channel to the ignore list |
| `Shift-X` | remove the ignore window at the selected frequency |
| `c` | show the session recordings (`enter` replays a clip) |
| `e` | export discovered channels to CSV |
| `f` | tune an arbitrary frequency or sweep a custom range |
| `a` | antenna advisor for selected channel |
| `o` | autonomous scan-and-hold on / off |
| `?` | help overlay generated from these bindings |
| number keys | 1-9 select built-ins; 0 cycles registered user bands |
| `q` | quit / close overlay |

While listening, sweeping stays paused by design: one tuner, one consumer.

## Layout

```
src/radiotui/
├── cli.py          argparse CLI, headless scan/listen/record/tuner
├── config.py       band presets, settings dataclasses, ranges
├── config_file.py  config.toml merge (defaults <- file <- flags)
├── channels_file.py user bookmarks + ignore list (channels.toml)
├── tuning.py        frequency/demod parsing helpers
├── export.py        CSV/JSON channel export
├── core/            shared models (Channel, ScanState, ...)
├── sdr/             device abstraction: real RtlSdr (+compat shim) and simulator
├── dsp/             FFT PSD, noise floor, peak detection/tracking
├── scanner/         sweeper thread, channel monitor (listen/record loop)
├── audio/           demodulators, DSP classifier, player, VOX recorder
├── antenna/         wavelength math + recommendations
└── tui/             Textual app: radio-first view, panels, widgets
```

## Documentation

Deeper material under [`docs/`](docs/):

- `docs/adr/` — architecture decision records (why the DSP is numpy-only, how
  tuner exclusivity works, where user channels live, ...).
- `docs/research/` — measurement studies with numbers: audio chain redesign,
  dropout forensics, hardware validation incl. the 30 min soak.
- `docs/reasoning_logs/` — chronological investigation logs, including the dead
  ends, for anyone re-tracing how a conclusion was reached.
