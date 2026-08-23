# Research: hardware validation report (RTL-SDR v3, Linux)

Issue: #1.
Period: 2026-08-22 → 2026-08-23.
Host: Ubuntu 24.04, librtlsdr 2.x via the compat shim, RTL-SDR v3 (R820T).

## Checklist results

| Check | Result |
| --- | --- |
| `radiotui devices` | enumerates dongle, kernel driver detach/reattach clean |
| `--sim scan` baseline | green; floor -61.9 dBFS vs reference -62 |
| Real FM sweep | 68 stations first pass; 165 exported after soak |
| Noise floor sanity | -48.5 dB real vs -62 simulated; defaults productive |
| DC spike masking | 0 phantom channels within ±2 kHz of any of 27 hop centers |
| Listen through speakers | audible via aplay (PipeWire default route); ffplay absent, fallback exercised |
| Record voice | Baofeng PMR capture confirmed by ear; sidecar metadata verified on hardware |
| TUI rendering / refresh | fine over 27 hops in a real terminal |
| Gain keys visible effect | observed during overload bracketing (see field study) |
| PPM flag | +23 applied and logged |
| Bias tee | switches ON, ~4.5 V on the port |
| Offset tuning | unsupported by librtlsdr2 2.0.1 even via raw symbols |
| HF direct sampling | Q-branch engages, forced 250.43 kS/s; SW carriers at 8.400/12.130/12.275 MHz |

## Field capture study (Baofeng as signal source)

A handheld on PMR taught three lessons that shaped the product:

1. **Overload signature**: with the transmitter within a few metres the tuner's
   raw RSSI pins at exactly +1.6 dBFS regardless of gain setting — ADC clipping
   destroys FM phase information and demodulation degrades into loud hiss with
   no voice. Any RSSI pinned near full scale means "reduce gain or add
   distance", never "increase gain".
2. **Frequency truth beats labels**: the radio labelled PMR ch2 (446.03125) but
   a sweep measured the carrier at 446.0350 — about +8.7 ppm of combined error.
   Tuning to the *measured* frequency is the reliable recipe; this also
   motivates proper PPM calibration work.
3. **Path loss through walls dominates**: 500 mW from another room arrived
   below the noise floor even at high gain, while same-room at gain 0 stayed
   linear. Bracket gain downward until RSSI stops pinning.

## Soak test (30 min autonomous FM broadcast)

- 16 full sweeps; 15 auto-holds engaged; releases: 14 max-hold (2 min cap),
  1 out-of-time budget — i.e., channels were genuinely active throughout.
- 19 VOX clips written (median 23.3 s, range 1.9–111.5 s), all with valid
  `.json` sidecars.
- CPU flat at 10% of one core for the entire run (135 samples); RSS flat at
  75 MB — no leak, no drift.
- Export: 165 channels with context block; strongest: 96.9 (+16 dB peak).
- Zero errors/tracebacks in the log.

## Performance summary

| Metric | Value |
| --- | --- |
| FM sweep (27 hops, dwell 120 ms) | ~4 s/sweep (~7 hops/s) standalone |
| CPU during listen/scan | ~10-11% of one core |
| RSS | 68-75 MB |
| Hop rate in HF broadcast (51 hops, short dwell) | ~10 sweeps/s equivalent |

## Known limitations found

- `set_offset_tune` unsupported on this librtlsdr build; automatic DC masking
  makes it unnecessary in practice.
- `rtl_test -p` PPM numbers are unusable here (USB sample losses skew windows);
  calibrate against narrowband references instead (see README guidance).
