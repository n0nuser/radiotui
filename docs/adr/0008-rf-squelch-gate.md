# 0008 — RF squelch gate for recordings on raw tuner RSSI

- Status: Accepted
- Date: 2026-08-22 (gate), 2026-08-23 (exposed in the settings menu)

## Context

VOX alone gates on demodulated level, which fails both ways on real hardware:
a hot noise floor sits above the VOX threshold and wedges the recorder open
(hiss-only clips), while a quiet channel with conservative gain never opens at
all.
Both symptoms were observed during hardware validation.

## Alternatives considered

- Audio content classification as the gate: built (`radiotui/audio/classify.py`,
  envelope modulation + spectral flatness + speech-band share) and intended for
  this role, but it needs more field tuning before it decides what hits disk.
- RSSI-based gate: one number, directly meaningful ("is there a carrier"),
  already flowing into the recorder every block.

## Decision

`[scanner] squelch_rssi_dbfs` (default `None` = off, preserving VOX-only
behaviour) blocks the recorder whenever the tuner's raw RSSI is below the gate;
`VoxRecorder.feed` treats gated blocks as unvoiced regardless of audio level,
so an open clip hangs and closes naturally when the carrier drops.
Exposed in the settings menu under "Squelch (RF gate)".

## Consequences

The gate reads *raw* tuner RSSI, which includes LO-leak/DC offset: the number
that separates carrier from noise on one unit can differ on another, so the
default is off and the useful range observed so far is roughly -40..-20 dBFS.
Content-based gating is expected to complement or replace this once the
classifier has enough field hours; that change would reopen this ADR.
