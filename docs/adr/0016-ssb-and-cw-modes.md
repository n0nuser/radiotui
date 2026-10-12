# 0016 — SSB and CW by shift, complex low-pass and real part

- Status: Proposed
- Date: 2026-10-12
- Issue: [#85](https://github.com/n0nuser/radiotui/issues/85)

## Context

`DemodMode` has `NFM`, `WFM` and `AM`. Voice on `hf_ham_80m` and `hf_ham_40m`
is single sideband, but both bands are set to AM, so the audio is
unintelligible. Those bands also have the default 12.5 kHz `channel_bw_hz`,
which is far wider than an SSB channel (about 2.7 kHz). There is no CW mode,
and once listening starts the demod mode cannot be changed.

## Alternatives considered

- `rtl_fm`'s shortcut, `I+Q` for USB and `I−Q` for LSB: no sideband filter, so
  the opposite sideband comes through as well.
- Phasing (Hilbert) demodulator: needs a long Hilbert FIR for the same
  rejection, and it is no simpler than a complex low-pass.
- Weaver-style: shift the channel so the wanted sideband is centred on 0 Hz,
  apply a complex low-pass at half the SSB bandwidth (which removes the other
  sideband), shift back and take the real part. This is the SDR++ approach,
  read for the algorithm only. It is built from `frequency_shift`, the
  existing windowed-sinc helpers and the AM AGC.

## Decision

- New modes `USB`, `LSB` and `CW`. SSB passes 300–2,700 Hz. CW shifts the
  carrier to a 700 Hz beat note and passes ±250 Hz around it. A minimum
  channel rate of about 12 kS/s gives 20× decimation from the HF rate (E).
- `frequency_shift` carries its phase in `DemodState`. A shift that restarts
  at t = 0 on every block clicks every 64 ms.
- Level is set by the AM block AGC, moved into a shared helper.
- The tuned frequency is the suppressed carrier, as on every radio. A
  detected SSB peak's centre is mapped to its carrier, with a half-bandwidth
  offset: centre − 1.5 kHz for USB, centre + 1.5 kHz for LSB. Holds and
  "listen to channel" use that carrier frequency.
- Defaults: LSB below 10 MHz and USB above, the usual amateur practice. 80m
  and 40m become LSB with a 2.7 kHz channel bandwidth. `hf_broadcast` and
  airband stay AM.
- A free key (`d`) cycles `NFM → WFM → AM → USB → LSB → CW` while listening.
  It sends a listen request with the new mode to `TunerSession` (ADR 0014).
  It never calls the monitor directly. A bookmark keeps the mode it was
  saved with.
- CLI `--demod` and the tune-entry suffix take their choices from `DemodMode`,
  so the new modes need no changes there.

- A clarifier fixes the pitch without retuning. Two keys step an offset
  (±10 Hz, limited to ±2.5 kHz) that is added to the phase-continuous shift.
  The offset reaches the monitor through the session, in the same way as
  volume and mute. Retuning would restart the monitor and leave a gap in the
  audio at every step. A new listen resets the offset.

## Consequences

The ham HF bands become listenable. The carrier mapping assumes the voice sits
in the middle of the passband, so the pitch starts up to a few hundred Hz off
until the clarifier corrects it. Changing the mode restarts the monitor, which
leaves a short gap in the audio.
The tracker merges peaks within its fixed 25 kHz window (ADR 0005), so SSB
stations 3 kHz apart on 80m and 40m become one channel. Fixing that reopens
ADR 0005 and is not part of this decision.
