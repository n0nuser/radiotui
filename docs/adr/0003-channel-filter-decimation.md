# 0003 — Channel filter via bandwidth-derived decimation and a triangular kernel

- Status: Accepted
- Date: 2026-08-22

## Context

The original chain decimated by fixed factors (NFM 8, WFM 4, AM 8) with a
plain boxcar, leaving NFM with a ~128 kHz "channel" where the band plan says
12.5 kHz.
Adjacent PMR channels bled together and `Band.channel_bw_hz` was dead data.

## Alternatives considered

- Windowed-sinc low-pass at the full sample rate: a transition width worth
  caring about needs thousands of taps at 1.024 MS/s; too slow per block.
- CIC / multi-stage halfbands: best stopband discipline but a lot of machinery
  for scanner-grade audio.
- Decimate first to roughly the channel rate, shape there: one cheap kernel at
  the high rate does the alias rejection, and the narrow channel work happens
  where samples are cheap.

## Decision

Derive the decimation factor from `Band.channel_bw_hz` and a per-mode minimum
demodulation rate (`MIN_CHANNEL_RATE_HZ`), then anti-alias with a triangular
kernel (one convolution, two boxcars cascaded) before striding.
At FM broadcast rates this puts >=28 dB on a carrier one PMR slot away
(verified in [the study](../research/audio-chain-redesign.md)), and in HF mode
the forced 250 kS/s rate still yields a valid factor.

## Consequences

`Band.channel_bw_hz` now reaches the DSP through monitor/app/CLI wiring.
The AM DC estimator had to become rate-adaptive (a fixed 64-tap window ate the
modulation once AM channel rates dropped to ~16 kS/s).
Stopband performance is scanner-grade, not broadcast-grade: strong immediate
neighbours are suppressed ~28 dB, not 60 dB.
