# 0017 — CTCSS/DCS detection and a tone squelch

- Status: Accepted
- Date: 2026-10-12
- Issue: [#86](https://github.com/n0nuser/radiotui/issues/86), after the noise
  squelch ([ADR 0018](0018-nfm-noise-squelch.md))

## Context

Groups that share a land-mobile, PMR446 or repeater frequency are separated
by a CTCSS tone (67.0–254.1 Hz) or a DCS code (134.4 bps, Golay (23,12)).
radiotui plays and records every group together, and it doesn't show which
tone or code is in use.

## Alternatives considered

- **One fixed window for every tone.** The 1.5 s needed to separate
  150.0 Hz from 151.4 Hz would then be the latency for all of them, although
  most tones are at least 2.3 Hz from their nearest neighbour.
- **Goertzel bank over 100 ms (GNU Radio).** The bins are 10 Hz wide, so close
  tones can't be separated.
- **FFT peak snapped to the tone list (SDR++).** Good for *identifying* an
  unknown tone, but slow when one tone is already known.
- **Per-sample DCS bit timing.** A Python loop. Bits are sliced block by
  block instead.

GNU Radio and SDR++ are GPL and were read for the method only. The
implementation follows the published tone list and code structure.

## Decision

- **Tap point and state.** The detector reads the unfiltered NFM
  discriminator output (ADR 0015) and decimates it to about 1 kS/s with its
  own `DemodState`. Sharing the monitor's state would reset both decimators
  on every block.
- **Identify vs verify.**
  - *Identify* (display, `any`): a zero-padded `rfft` peak over a rolling
    window, refined by parabolic interpolation and snapped to the 50-tone list.
  - *Verify* a selected tone: narrowband energy at the target must beat its
    two nearest neighbours, over a window sized from that spacing
    (T ≈ 1.5 / spacing). Most tones need well under 1 s (E); only 150.0/151.4
    needs about 1 s.
  - Opening needs k consecutive matches. The squelch closes on tone loss.
- **DCS:** bit slicing at 134.4 bps and a ≤ 3-bit Hamming match against every
  rotation of the standard codewords, normal and inverted. Some inverted codes
  are the same bits as another normal code. The PR builds the table, asserts
  which pairs alias, shows the normal name, and opens on either name. DCS is
  checked first; while a code is locked, 131.8 Hz and 136.5 Hz CTCSS are not
  reported.
- **Gating.** `off` is the default and behaves as today. With a tone or code
  selected, a non-matching block is muted at the speaker, counts as unvoiced
  for the recorder and doesn't count as voice for the autonomous hold. The
  tone squelch is ANDed with the noise squelch and RF gate, so it closes at
  once when the carrier drops instead of a window later.
- **Speaker vs recorder.** The tone squelch is off by default until it is
  validated on hardware. When it is on, the speaker stays live, so a
  transmission loses its first verify window (≤ 1 s) at the speaker. Whether
  to switch to look-ahead playback instead is revisited after the hardware
  runs. The recorder keeps a pre-roll of one window and writes it when the
  squelch opens, so nothing is lost on disk.
- **Selector.** A key cycles `off → any → <the tone detected now>`. Other
  values come from the bookmark's optional `tone = "88.5"` / `"D023N"` field
  in `channels.toml`. Files without it load as `off` (ADR 0005).
- **Threads (ADR 0014).** Selector changes go to `TunerSession` as requests.
  Detected tones come out as session events. The app keeps a map from
  frequency to tone fed by those events, so the channel table and exports can
  show tones the sweeper never sees. Clip sidecars (ADR 0006) record the tone
  that was detected.
- **NFM only.** The selector does nothing in other modes.

## Consequences

The speaker misses up to one verify window at the start of each transmission
while a tone is selected. With `off`, nothing changes.
Male voice (85–180 Hz fundamentals) overlaps the CTCSS band. The acceptance
tests include voice-like interference (a harmonic series with wandering
pitch) as well as clean tones and noise.

## Implementation notes (#86, 2026-10-12)

- **Tone list.** 51 tones, the list in the
  [demodulation study](../research/demodulation-landscape.md), not 50.
- **Verify** uses one check: the tone's power share over a window of
  1.5 / spacing. That window puts each neighbour 1.5 Hann bins from the
  target (about 16 dB of leakage), so a separate neighbour comparison
  rejected nothing the share test did not. Identify takes the listed tone
  nearest the FFT peak and keeps it only if it passes the same share test,
  which also rejects off-list tones, so there is no snap tolerance or
  parabolic refinement.
- **DCS.** Golay generator 0xC75. It reproduces the published "+023 = −047"
  alias and 0xAE3 does not. Every window is syndrome-corrected (≤ 3 bits),
  and a lock needs the same corrected word over 4 consecutive 23-bit
  periods; the code is perfect, so a correction alone means nothing.
  **Not yet checked against a real radio:** the bit order on air (C1
  first) and which deviation direction is a 1. If either is wrong, normal
  and inverted names swap, or codes don't decode. The squelch is off by
  default until this is validated on hardware.
- **Tone map.** `TunerSession.tones` keeps the last tone heard on each
  listened frequency. Both the TUI table/export and the CLI export read it,
  instead of each adapter keeping its own map. It is fed on the session's
  monitor callback, the same source as the `ToneDetected` event.
- **Cost.** About 1.5 ms per 64 ms block for identify, DCS and verify
  together.

