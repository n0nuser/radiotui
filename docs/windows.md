# Windows Setup

Native Windows hardware use requires an RTL-SDR v3 with the WinUSB driver and
the rtl-sdr native library available to Python.

1. Install Python 3.10 or newer and `uv`.
2. Install Zadig, select the RTL-SDR USB device, and replace its driver with
   `WinUSB`. Do not replace the driver for unrelated USB devices.
3. Install the Windows rtl-sdr binaries and put the directory containing
   `librtlsdr.dll` on `PATH`.
4. Install FFmpeg and put `ffplay.exe` on `PATH`; radiotui uses it for audio
   playback on Windows.
5. From PowerShell, run `uv sync --group dev --extra sdr`.

Verify the installation with:

```powershell
uv run python -c "import rtlsdr; print('pyrtlsdr import OK')"
radiotui devices
radiotui scan --band fm_broadcast
radiotui listen 145.5M
radiotui record 145.5M --seconds 10
```

Use Windows Terminal rather than legacy conhost for the Unicode spectrum and
waterfall characters. Recordings use `pathlib` and are written beneath the
configured recordings directory.

The simulator remains available with `--sim` and is useful for checking the
CLI and TUI without a USB device. Native device access still depends on the
specific dongle, WinUSB installation, and DLL search path.
