# 0004 — Single-consumer tuner exclusivity

- Status: Accepted
- Date: 2026-08-23

## Context

The RTL-SDR is one physical radio: its LO can only be at one frequency and its
USB stream has one consumer.
During [dropout forensics](../research/audio-dropout-forensics.md) we ran the
sweeper and a monitor against the same device concurrently and observed
`r82xx i2c wr failed=-9` retune errors, corrupt demodulated audio, and
periodic dropouts matching the sweep cadence.
The product had a path that allowed exactly this: pressing `s` while listening.

## Alternatives considered

- Serialize all device calls behind a mutex: prevents crashes but both streams
  still interleave reads, so audio remains garbage — correctness of output, not
  just safety of input, requires exclusivity.
- Route everything through one arbiter thread with priority preemption:
  over-engineered for a single-tuner toy; the pause/hold mechanisms already
  express "who owns the radio".

## Decision

One consumer at a time, enforced by policy in the app layer:

- Starting a listen pauses the sweeper (`_pause_sweeper_for_monitor`) or parks
  it on a hold request (autonomous auto-hold).
- `s` while a monitor is active refuses with a hint instead of resuming the
  sweeper.
- Switching bands while listening stops the monitor first.
- The autonomous release path re-grants the device to the sweeper only after
  the monitor is fully stopped.

The SDR layer stays lock-free; exclusivity is a scheduling invariant, not a
locking discipline.

## Consequences

Audio dropouts from tuner contention are structurally impossible now.
Any future multi-device or split-IQ feature reopens this ADR.
