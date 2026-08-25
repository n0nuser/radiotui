# ADR-0010: Audio classifier as a VOX gate

## Status

Accepted

## Decision

VOX recording uses `StreamClassifier` in addition to the configured RMS
threshold. Classified signal blocks are recorded; unusually loud blocks retain
a 15 dB override to avoid losing strong constant carriers.

## Rationale

RMS alone records hiss and static. The override preserves the existing behavior
for clearly strong signals while the classifier rejects ordinary noise.

## Consequences

Both constants — the 0.45 score and the 15 dB override — are operating points
chosen by feel, and no choice of them is error-free: this is a blind detector
running on demodulated audio, the point in the chain where the most information
has already been discarded.
[`docs/research/signal-vs-noise-detection.md`](../research/signal-vs-noise-detection.md)
records why that is an informational limit rather than a tuning problem, and
which mode-specific detectors (FM pilot, CTCSS/DCS, AM carrier, noise squelch)
would replace the guesswork with physics.
