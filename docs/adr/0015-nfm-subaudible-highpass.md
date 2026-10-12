# 0015 — NFM sub-audible high-pass as a long linear-phase FIR

- Status: Accepted
- Date: 2026-10-12
- Issue: [#84](https://github.com/n0nuser/radiotui/issues/84)

## Context

The NFM chain stops at the discriminator: `demod_nfm` output goes straight to
the resampler. CTCSS tones (67.0–254.1 Hz) and the 134.4 bps DCS stream are
part of that output, so they reach the speaker, the WAV clips and the VOX
classifier. Land-mobile radios remove everything below about 300 Hz
([the demodulation study](../research/demodulation-landscape.md)).
The NFM channel rate is about 30.1 kS/s (34× decimation from 1.024 MS/s), or
about 31.3 kS/s in HF direct sampling.

## Alternatives considered

- Single-pole high-pass (input minus the existing truncated-FIR low-pass):
  cheap, but only about 12 dB down at 100 Hz, and barely anything at 254 Hz.
- Short windowed sinc (~200 taps, as the study suggested): at 30 kS/s the
  transition is about 500 Hz wide, so voice at 300 Hz and the 254.1 Hz tone
  cannot both be on the right side of it.
- IIR (Butterworth/Chebyshev): the right tool in a radio, but a recursive
  filter is a per-sample loop under ADR 0002.
- Long Hamming-windowed sinc high-pass, passband ≥ 300 Hz, stopband ≤ 254.1 Hz:
  a ~46 Hz transition needs about 2,200 taps (E) at the NFM rate. That is
  about 4 M multiply-adds per 64 ms block (E), and 36 ms of group delay.

## Decision

The NFM chain gets a linear-phase FIR high-pass (spectral inversion of a
windowed-sinc low-pass), sized from the channel rate for a 254.1 → 300 Hz
transition and at least 40 dB of stopband. Its history is carried in
`DemodState` the same way as `audio_lowpass`. The tap count and the per-block
cost are measured in the PR. If direct `np.convolve` costs more than about
5 ms per block, the filter switches to `np.fft` overlap-save with the same taps.

The filter runs after the discriminator and before the resample. The
unfiltered discriminator output stays available inside `channel_audio` as the
tap point for the tone detector (ADR 0017).

WFM, AM and the new SSB/CW modes are unchanged.

## Consequences

NFM speaker audio, recordings and classifier input lose the tone hum.
The VOX threshold and the classifier operating points (0.45 score, 15 dB
override, ADR 0010) were set on unfiltered audio. They are rechecked on the
simulator in the PR, not retuned blind.
The 36 ms group delay adds to listening latency. It stays below the latency
the player buffer already adds.
