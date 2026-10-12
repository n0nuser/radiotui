# 0014 — One session thread owns every tuner handoff

- Status: Accepted
- Date: 2026-10-12
- Supersedes: [0011](0011-asynchronous-monitor-handoff.md). Changes how
  [0004](0004-single-consumer-tuner.md) is enforced, not the invariant.

## Context

ADR 0004 keeps one reader on the tuner and enforced it by policy spread over
the TUI: about fifteen `RadioTuiApp` methods, a stop worker per handoff
(ADR 0011), and ownership state in eight fields. The headless CLI had its
own copy of the sweep and hold orchestration. Every call site had to
re-apply the rule, and several did not:

- The TUI hold released itself and put the sweeper and a monitor on the
  tuner together, while the CLI copy worked (#61).
- A second listen during a teardown started a reader the app no longer
  referenced (#49).
- Gain changes ran on the UI thread during `read_samples`, the same
  concurrent access behind the r82xx i2c errors.
- A single-frequency listen never switched HF direct sampling.

ADR 0004 had rejected an arbiter thread as over-engineered for one tuner.
These bugs show the cost of not having one.

## Alternatives considered

- Keep the worker-plus-callback handoff and fix each call site: that is
  what produced the bugs above, and the CLI would still duplicate it.
- A lock around the device: stops crashes but still interleaves two
  readers' streams (the reason ADR 0004 rejected it).

## Decision

`scanner.session.TunerSession` owns the device, sweeper, monitor, hold and
resume-after-listen state, on its own `tuner-session` thread.

- Public methods only queue a request; stops, which can block on a USB read
  for `STOP_TIMEOUT_S` per thread, run on the session thread, so a caller
  never blocks and the next reader starts only after the previous one has
  gone. A stop that fails is retried until it succeeds.
- When requests queue up during a slow stop, only the latest tuner request
  runs.
- Device commands (gain) run between reads on whichever thread is reading,
  through a `before_read` hook on `Sweeper` and `ChannelMonitor`, or on the
  session thread when nothing is reading.
- `sweep()` and `listen()` choose HF direct sampling from the band or
  frequency.
- The autonomous hold policy (engage, release, cooldown, pre-emption by a
  user listen) lives in the session, shared by the TUI and the CLI.
- Results go out as events through one non-blocking callback. The TUI wraps
  them in `post_message`, never `call_from_thread`, so the session thread
  never waits on the event loop.
- `close()` stops every reader, then closes the device; `wait_closed()`
  bounds the wait, and a wedged read leaves the device to the OS rather
  than closing it under a live read.

## Consequences

The TUI and headless CLI are two thin adapters, and `RadioTuiApp` holds no
tuner state. Session behaviour is tested without Textual against a paced
fake device. The session thread sleeps until a request arrives, except while
a hold is active, so idle TUI tests stay idle.
