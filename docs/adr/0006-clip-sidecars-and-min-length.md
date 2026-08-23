# 0006 — Per-clip JSON sidecars with minimum-length discard

- Status: Accepted
- Date: 2026-08-22

## Context

An overnight autonomous run can leave hundreds of `*.wav` files with no
context (no frequency, demod, RSSI, hardware settings) and no way to tell real
transmissions from sub-second VOX noise blips.

## Alternatives considered

- One appended `index.jsonl`: single-file convenience, but any crash mid-append
  can corrupt every entry after the bad line, and per-clip files travel with
  their WAV when copied or deleted.
- Metadata in the WAV header: no standard home for our fields; custom chunks
  break players.
- No discard, filter at review time: pushes the pain onto the user.

## Decision

Every kept clip gets a `.json` sidecar next to the WAV (`clip.wav` ->
`clip.json`) written by `VoxRecorder._write_sidecar`, carrying freq_hz, demod,
band, ISO start/end, duration, peak/mean RSSI, and a hardware snapshot.
Sidecar write failures surface through an `on_error` hook instead of vanishing.

Clips shorter than `[audio] min_clip_seconds` (default 0.7 s) are closed,
deleted from disk, and never reported as clips: the file is removed rather
than buffered, because VOX opens on onset and only the hang timer knows when a
blip has ended.

## Consequences

Renaming or moving a WAV by hand desyncs it from its sidecar.
The discard path writes then unlinks, so very noisy channels still touch the
disk briefly; if that ever matters, buffering until minimum length reopens this
ADR.
