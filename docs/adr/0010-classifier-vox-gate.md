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
