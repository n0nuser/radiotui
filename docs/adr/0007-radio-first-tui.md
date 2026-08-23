# 0007 — Radio-first TUI: analyst panels opt-in, app-owned event history

- Status: Accepted
- Date: 2026-08-23

## Context

The original TUI opened as a wall of analyst tooling: channel table, log pane,
meter.
The product vision is an old FM radio — waves carousel, frequency ruler, a
tuning cursor, and everything else behind a toggle.

Two implementation traps were hit while building this:

- A `display: none` container still holds focus if it had it: the hidden
  channel table swallowed every key (arrows and enter included), so
  `AUTO_FOCUS` must be `None` on the app and focus granted explicitly when
  panels appear.
- Textual's `RichLog` silently drops writes while its size is zero, so a log
  inside hidden panels loses everything written there; modal screens also need
  explicit `AUTO_FOCUS = "Input"` or their inputs never receive keys because
  the app-level `AUTO_FOCUS = None` wins.

## Alternatives considered

- Keep all panels visible but shrink them: fails the "as simple as possible"
  brief for zero engineering gain.
- Move history into the widget subclass (buffered RichLog): keeps tests scraping
  widget internals and still needs flush-on-reveal plumbing.

## Decision

Default view is spectrum + waterfall + ruler + meter dial only; the tuning
cursor walks the band with arrow keys and `enter` listens under it.
`#main` (table, log, clips) is opt-in via `t`, with explicit focus management.

Canonical event history lives on the app (`RadioTuiApp._events`); the visible
log widget is a projection that receives writes only while shown, and gets the
recent history flushed in on reveal.
Tests assert against `_events`, not widget internals.

Settings live in a navigable modal (`m`) that applies changes immediately to
the shared settings object and any running monitor.

## Consequences

Any new panel must be added to `#main` to inherit show/hide behaviour.
Anything that logs should use `log_line`, never write to `#log` directly.
New modals with text input must set `AUTO_FOCUS = "Input"`.
