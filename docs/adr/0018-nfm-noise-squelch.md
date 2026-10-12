# 0018 — NFM noise squelch on discriminator noise above the voice band

- Status: Accepted
- Date: 2026-10-12
- Issue: [#87](https://github.com/n0nuser/radiotui/issues/87)

## Context

Nothing mutes the speaker today. The RF gate (ADR 0008) and VOX (ADR 0010)
only decide what is recorded and what counts as voice for the autonomous
hold. The RF gate reads raw tuner RSSI, so its threshold changes from unit to
unit, and it is off by default. VOX is a blind detector working on
demodulated audio
([signal vs noise study](../research/signal-vs-noise-detection.md)).
Hardware FM radios use a noise squelch instead. With no carrier, the
discriminator outputs strong noise above the voice band. A carrier strong
enough to capture the limiter silences that noise. The NFM channel rate
(about 30 kS/s) keeps that band.

## Alternatives considered

- **Tighten the RF gate.** Still a per-dongle number, and it can't tell a
  carrier from a hot noise floor at the same level.
- **Classifier score as squelch.** The operating points are set by feel
  (ADR 0010), and it reacts to audio content, not to whether a carrier is
  present.
- **Noise-band power after the discriminator.** It reads the physics, it is
  independent of the RF level because the discriminator ignores amplitude,
  and its cost is one band-pass and an RMS per block.

## Decision

- **Measure.** Power in about 5–10 kHz of the NFM discriminator output,
  relative to full-scale discriminator noise, computed per sub-block (about
  20 ms).
- **Open and close.** Open below a threshold, close above it plus
  hysteresis, with a short hang so syllable gaps don't chop the audio.
- **DC first.** The tuner's LO sits on the channel, so the RTL-SDR DC spike
  lands inside it and can quieten the discriminator with no signal present.
  The NFM chain removes IQ DC (a slow mean subtraction carried in
  `DemodState`) before the discriminator. The fix is validated on real
  hardware, not only on the simulator.
- **What it gates.** The speaker, the recorder and the hold's voice signal,
  ANDed with the RF gate when that is set. The CTCSS/DCS squelch
  (ADR 0017) builds on it.
- **Mode.** NFM only, because AM, SSB and CW keep amplitude and have no
  limiter noise. WFM is out of scope.
- **Default.** `[scanner] noise_squelch` is off, as ADR 0008 was, until
  hardware validation shows that one threshold works across dongles. The
  setting goes in the settings menu next to the RF gate.

## Consequences

When enabled, NFM listening goes silent between transmissions, as on a radio.
If the threshold transfers between units, this can become the default and
reduce the RF gate to a fallback. That would amend ADR 0008.
