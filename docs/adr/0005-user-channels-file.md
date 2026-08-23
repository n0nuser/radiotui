# 0005 — User channels live in a dedicated channels.toml

- Status: Accepted
- Date: 2026-08-22

## Context

Bookmarks (name the channels you care about) and ignores (silence the birdies)
are user content that must persist across runs and survive headless and TUI
sessions alike.
The existing `~/.config/radiotui/config.toml` holds settings, not entities.

## Alternatives considered

- Extend config.toml with `[[bookmark]]` / `[[ignore]]`: mixes mutable runtime
  state (the TUI writes on every `b`/`x`) with hand-edited preferences; a TUI
  rewrite of your settings file is unacceptable.
- SQLite: proper concurrency, but a database file for two arrays is overhead
  and unreviewable by eye.
- One sidecar JSON per bookmark: filesystem litter.

## Decision

A single `~/.config/radiotui/channels.toml` holding `[[bookmark]]` (freq_hz,
name, demod) and `[[ignore]]` (freq_hz, width_hz, note).
`src/radiotui/channels_file.py` owns parsing, validation, serialization, and
the upsert/match helpers; association uses the same 25 kHz window as the
tracker.
Writes are full-file rewrites from the in-memory book: deterministic and simple,
at the cost of not preserving hand-written comments in that one file.

Malformed or missing files degrade to "no bookmarks, no ignores" plus exactly
one warning — never a crash.

Ignore windows gate peaks inside `ChannelTracker.update`, so birdies never
reach the table, exports, or the auto-hold gate.

## Consequences

Comments inside channels.toml do not survive a TUI save.
Matching tolerance is fixed at 25 kHz rather than configurable; if drift-prone
hardware needs wider association, this ADR is the place to reopen.
