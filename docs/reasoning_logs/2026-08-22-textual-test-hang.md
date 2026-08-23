# Reasoning log — Textual test hang (squelch key tests)

Date: 2026-08-22.
Context: after e5c1580 the full pytest suite hung at 81% on
`test_bracket_keys_adjust_threshold_with_status_readout`.

## Chronology of hypotheses

1. **Deadlock in app code?** Faulthandler dump showed the main thread parked in
   `selectors.select` and a sweeper thread busy in numpy. Idle select means
   "waiting for an event that never comes" — but which await?
2. **Probe scripts lied to me.** Standalone probes printed one marker then went
   quiet, suggesting a hang at construction.
   Wrong: Textual's test harness captures stdout once the app starts, so every
   marker after startup vanished into its buffer. The single visible marker was
   an artifact, not a location.
   Lesson: when a process under a capture layer goes quiet, switch to
   file-descriptor-level logging (`os.write`) or stderr with `-s`.
3. **faulthandler output invisible under pytest**: `dump_traceback_later`
   writes to fd 2, which pytest's default capture redirects; the dump was lost
   until I passed `-s`. Another capture-layer trap.
4. **My own probe caused a false bug**: subclassing the app to add markers made
   Textual dispatch `on_mount` to *both* definitions in the MRO, producing a
   spurious `DuplicateKey('freq')`.
   Lesson: Textual calls every handler definition along the MRO; never assume
   override-replaces-base for event handlers.
5. **Real mechanism found** via a step-by-step replica with per-step timing:
   every `pilot.press` took ~2.0 s flat, independent of direction.
   Root cause: `textual._wait.wait_for_idle` only returns early when the whole
   *process* uses <1 ms CPU during a 20 ms window.
   radiotui keeps a SIM sweeper thread crunching numpy forever, so that
   condition never occurs and each press/pause burns its full max_sleep
   (~2x1 s per press).

## Fix

Stop the live sweeper before driving keys in tests that press many keys
(`app.sweeper.stop()`), matching the pattern the table tests already used.

## Generalized rules

- In this repo, any Textual test that presses keys must first stop the live
  sweeper (or otherwise guarantee a CPU-idle process).
- Timing-based idle detection breaks under deliberately busy background
  threads; do not "fix" it by sleeping more.

Recorded as commit 5ff487e.
