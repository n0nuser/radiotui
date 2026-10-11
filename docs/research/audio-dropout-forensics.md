# Research: audio dropout forensics

Period: 2026-08-22 → 2026-08-23.
Symptom reported by the user: during FM listening, audio drops out for ~0.5 s
every ~2 s (first report) and later "every 8 seconds or so".

## Symptom model

A periodic dropout has a cause with a matching period.
Candidate period sources in the system: sweep cadence (~2-4 s at default dwell),
ext4 journal commits (~5 s), PipeWire quantum cycling, USB autosuspend timers,
desktop clock ticks (1 s).

## Hypotheses tested and killed

1. **Production starvation** — monitor thread too slow, pipe drains.
   Measured per-block read/demod/write timing headless and inside a scripted
   TUI: reads are device-paced (~64 ms), demod p95 ≈ 12 ms (max 35 ms), write
   gaps max 116 ms across three runs.
   Killed.
2. **Player pipe backpressure** — `aplay` blocking writes.
   Instrumented `AudioPlayer.write`: mean duration 0.1 ms, never blocks.
   Killed.
3. **PipeWire / desktop audio server** — A/B test routing to
   `sysdefault:CARD=PCH` bypassing it: user heard no difference (clean both).
   Killed as primary cause; note the server is still in the path by default.
4. **Recorder disk I/O on the audio thread** — ext4 commit stalls.
   Same listen with an active VOX recorder writing WAV: clean.
   Killed.
5. **USB autosuspend** — `/sys/.../power/control` is `on` (suspend disabled)
   for the RTL2838. Killed.
6. **Silent data from the driver** — zero-filled buffers would evade all timing
   probes. Dual-trace detector built (`watch_fm.py`): RF RSSI and demodulated
   audio energy sampled per block for 209 s of real radio plus WAV post-mortem;
   zero silent windows >=200 ms, zero RF collapses, zero read stalls.
   Killed for the observed sessions.
7. **Concurrent tuner access** — sweeper retuning while a monitor listens.
   Reproduced on purpose: `r82xx i2c wr failed=-9`, corrupt demodulated audio,
   dropouts matching the sweep cadence.
   The product allowed this via `s` during a listen.

## Root cause

Two threads using one tuner concurrently.
The sweep cycle yanks the LO out from under the demodulator every hop, which is
exactly a periodic dropout whose period tracks the sweep cadence (reported ~2 s;
~8 s after dwell/settings changed mid-session).

## Fixes shipped

- `s` while listening refuses with a hint; band switching stops the monitor
  first (commit abb47e6, [ADR-0004](../adr/0004-single-consumer-tuner.md)).
- Playback robustness regardless of residual stalls: 1.4 s alsa buffer,
  meter repaints throttled to 4 Hz (same commit family, 13c487a).

## Open items

The user had not yet re-validated listening after the guard when this document
was written.
If a dropout ever recurs, redo the elimination ladder in the companion
reasoning log
([2026-08-23-audio-dropout-hunt.md](../reasoning_logs/2026-08-23-audio-dropout-hunt.md)),
starting from the concurrent-access guard as a now-controlled variable.

## Correction (2026-08-23)

Hypothesis 1 was dismissed too early. The measurements taken to kill it —
device-paced 64 ms reads, demod p95 12 ms — are themselves the proof of
starvation: a serial read-then-process loop yields 64 ms of audio per 76 ms of
wall time, and the device keeps producing during the processing window with
nobody reading it. That deficit drains the 1.4 s playback buffer and stalls it
on a regular cadence, which is what the user reported again after the
concurrent-access guard shipped.

Fixed by splitting reading onto its own thread
([ADR-0012](../adr/0012-decoupled-reader-thread.md)); a paced fake device now
measures audio production at 100.00% of real time.

The lesson for the ladder: "the loop is fast enough" is not the same claim as
"the loop keeps up with real time", and only the second one matters here.

