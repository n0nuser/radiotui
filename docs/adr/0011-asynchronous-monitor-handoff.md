# ADR-0011: Asynchronous monitor handoff

## Status

Accepted

## Decision

Monitor teardown runs in a Textual worker. A replacement monitor or sweep is
started only from the worker completion callback, after the old reader has
stopped. Device close is likewise deferred while teardown workers remain.

## Rationale

Joining a potentially blocked USB read on the event loop freezes the UI, but
retuning before that read ends violates the single-consumer tuner invariant.
The explicit completion handoff satisfies both requirements.
