# 0017 — CTCSS/DCS detection and a tone squelch

- Status: Proposed
- Date: 2026-10-12
- Issue: [#86](https://github.com/n0nuser/radiotui/issues/86)

## Context

Groups that share a land-mobile, PMR446 or repeater frequency are separated
by a CTCSS tone (67.0–254.1 Hz) or a DCS code (134.4 bps, Golay (23,12)).
radiotui plays and records all of them together, and it does not show which
tone or code is in use. Today the only gates are RSSI (ADR 0008) and VOX
(ADR 0010). Neither gate mutes the speaker.

## Alternatives considered

- Goertzel bank over a 100 ms window (the GNU Radio approach): bins are
  10 Hz wide, so 150.0 Hz and 151.4 Hz cannot be told apart.
- FFT peak plus snapping to the nearest standard tone (the SDR++ approach)
  over a longer window: one `rfft` per update, which is plain NumPy.
- DCS by tracking bit timing per sample: a Python loop. Instead, slice the
  bits block by block and slide a Hamming-distance match against
  precomputed codewords (all rotations, both polarities).

GNU Radio and SDR++ are GPL. Both were read for the method only. The
implementation follows the published tone list and code structure.

## Decision

- The detector takes the **unfiltered** NFM discriminator output (the tap
  point defined in ADR 0015), low-passes it and decimates it to about
  1 kS/s, and keeps a rolling 1.5 s window (E).
  - CTCSS: a zero-padded `rfft` peak between 60 and 260 Hz, refined by
    parabolic interpolation and snapped to the standard 50-tone list when
    within ±0.5 Hz. A minimum peak-to-floor ratio applies.
  - DCS: bit slicing at 134.4 bps and a ≤ 3-bit Hamming match against the
    standard codes, normal and inverted.
- The result is a `Tone` value: a CTCSS frequency or a code such as `D023N` /
  `D023I`, or none. It is shown while listening and written to the clip
  sidecar (ADR 0006) and to exports.
- The tone squelch is `off / any / <tone or code>`, cycled on the monitor
  view. `off` is the default and behaves as radiotui does today. With
  anything else, a block whose detected tone does not match is muted at the
  speaker, counts as unvoiced for the recorder, and does not count as voice
  for the autonomous hold.
- Bookmarks gain an optional `tone = "88.5"` / `tone = "D023N"` field in
  `channels.toml`. Files without it load as `off` (ADR 0005).
- NFM only. The selector is inert in other modes.

## Consequences

The 1.5 s window cuts off the first second or so of every transmission when
the squelch is on, the same as a slow radio. `off` keeps that cost away from
users who don't select a tone.
A DCS stream can false-detect as 131.8 Hz or 136.5 Hz CTCSS. DCS is checked
first, and a CTCSS peak at one of those two tones is not reported while a DCS
code is locked.
