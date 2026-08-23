# ADR-0012: Decoupled reader thread for live monitoring

## Status

Accepted

## Decision

`ChannelMonitor` reads the device on a dedicated thread that does nothing but
`read_samples` into a two-block queue. Demodulation, playback and recording run
on a second thread that consumes that queue. Neither thread paces itself; the
hardware paces the reader.

## Rationale

A synchronous read is device-paced: a 65536-sample block at 1.024 MS/s takes
64 ms whatever the host does. Demodulating in the same loop therefore made each
iteration cost 64 ms + processing while yielding only 64 ms of audio, and the
device kept producing samples during the processing window with nobody reading
them. The resulting few-percent deficit drained the player's ~1.4 s buffer and
stalled it every few seconds, heard as roughly a second of silence at a regular
cadence.

`docs/research/audio-dropout-forensics.md` measured exactly this — 64 ms reads,
demod p95 12 ms — but filed it under "production starvation, killed", reading
the numbers as evidence of headroom rather than of a 16% shortfall.

With the split, a paced fake device measures audio production at 100.00% of real
time over 8 s, against 99.4% for the old serial loop on an idle machine and
considerably worse under a rendering TUI.
