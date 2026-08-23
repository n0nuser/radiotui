# Reasoning log — radio-first UI redesign

Date: 2026-08-23.
Implements issue #23; decision recorded in
[ADR-0007](../adr/0007-radio-first-tui.md).
This log records the design translation and the traps found on the way.

## From user vision to implementation

The brief: "as simple as a years-ago FM radio — a carousel of waves, bigger
bars are louder, frequency under the bars, move left/right to select; menus for
threshold/squelch; gain and volume as buttons; depth available but hidden."

Mapping decisions:

- "Carousel of waves" already existed as `SpectrumBar`; the missing pieces were
  *space* (panels stole the screen) and *a cursor you can steer*.
  So the redesign was mostly subtraction: hide the analyst panels by default and
  give spectrum/waterfall the freed rows.
- "Frequency under the bars" already existed as the #10 axis row; it gained the
  bright cursor column so the ruler and the waves agree.
- "Marker showing where I am": the cursor is now drawn through the bars
  themselves (`CURSOR_STYLE`), not only as an axis tick.
- Left/right step one rendered column (`span / width`), up/down jump a fixed
  100 kHz: coarse motion must not require 140 presses.
- `enter` in radio view listens under the cursor; in advanced view the table's
  RowSelected keeps driving per-channel listen.

## Traps discovered

1. **Hidden widgets hold focus.** After hiding `#main`, its DataTable stayed
   focused and consumed every key: arrows moved table rows, enter fired
   RowSelected. Fix: `AUTO_FOCUS = None` app-wide, explicit `.focus()` when
   panels appear.
2. **App AUTO_FOCUS propagates to modals.** With `AUTO_FOCUS = None`, pushed
   modals no longer auto-focused their Input; typing went to band hotkeys while
   the modal stared back. Fix: `AUTO_FOCUS = "Input"` on TuneModal/NameModal.
3. **Textual dispatches handlers along the whole MRO.** A probe subclass adding
   prints to `on_mount` caused columns to be added twice (DuplicateKey). When
   instrumenting, never override event handlers on subclasses of App/Widget.
4. **RichLog drops writes at zero size.** Hiding the log pane silently discarded
   all history written while hidden; the canonical log therefore lives on the
   app (`_events`) and the widget is a projection flushed on reveal.

## Settings menu shape

A list modal with ↑/↓ selection and ←/± adjustment applies changes instantly to
the shared settings object (and any running monitor for volume), because a radio
knob that does nothing until "OK" feels broken.
Squelch cycles off ↔ range edges deliberately: from "off", right enters at the
low edge; below the low edge it returns to off.

## What was deliberately not built yet

Content-based recording gating using the new classifier, per-user theming of
the carousel density, and a "favourites" quick-dial row — all noted as natural
follow-ups once field feedback lands.
