# ADR-0011: Asynchronous monitor handoff

## Status

Accepted

## Decision

Monitor teardown runs in a Textual worker. A replacement monitor or sweep is
started only from the worker completion callback, after the old reader has
stopped. Device close is likewise deferred while teardown workers remain.

On quit, Textual no longer starts workers, so the final teardown runs in a
plain daemon thread that closes the device itself. `run_tui` waits for that
thread for a bounded time rather than forever, because a wedged reader must
not hang the process.

## Rationale

Joining a potentially blocked USB read on the event loop freezes the UI, but
retuning before that read ends violates the single-consumer tuner invariant.
The explicit completion handoff satisfies both requirements.
