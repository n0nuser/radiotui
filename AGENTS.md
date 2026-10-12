# AGENTS.md

radiotui: an autonomous RTL-SDR spectrum scanner with a Textual TUI and a
headless CLI. The README is the source of truth for features, the module
layout, CLI usage, TUI keys and the config/channels file formats.

## Commands

CI runs the first three on Linux and Windows, Python 3.10 and 3.11:

```bash
uv sync --group dev        # add --extra sdr for real hardware (pyrtlsdr)
uv run ruff check .
uv run pytest
uv run radiotui --sim      # run the app on the simulator, no dongle needed
```

Target Python 3.10 syntax, and keep paths in `pathlib` because the Windows
job runs the full suite.

## Invariants

- **One tuner, one consumer.** The sweeper or a single monitor reads the
  device, never both. Only `TunerSession` starts, stops or hands over a
  reader, on its own thread; the TUI and CLI send it requests and never touch
  the sweeper, monitor or device directly. Device commands go through the
  session so they run between reads
  ([ADR 0004](docs/adr/0004-single-consumer-tuner.md),
  [ADR 0014](docs/adr/0014-tuner-session-owns-handoffs.md)).
- **Threads reach the UI through messages.** Session events reach the TUI
  through `post_message`; never `call_from_thread` from the session thread,
  which must not block on the event loop.
- **DSP is NumPy-only.** New filters extend the existing NumPy patterns
  ([ADR 0002](docs/adr/0002-numpy-only-receive-chain.md)).

## Where to look

- **Threading, device handoff, autonomous hold** (`scanner/`, `sdr/`,
  `tui/app.py`): [docs/architecture.md](docs/architecture.md)
- **Writing or debugging a test**, especially a TUI test or a hanging test:
  [docs/testing.md](docs/testing.md)
- **Audio chain, demod, filters**: ADRs 0002, 0003, 0009 and 0010 in
  [docs/adr/](docs/adr/README.md), with the measurements in
  [docs/research/audio-chain-redesign.md](docs/research/audio-chain-redesign.md)
- **Real hardware, HF direct sampling, librtlsdr quirks**: the README
  hardware sections, [docs/research/hardware-validation.md](docs/research/hardware-validation.md)
  and, on Windows, [docs/windows.md](docs/windows.md)
- **A change that reverses or makes a design decision**: read the matching
  ADR first, then record the new decision as described in
  [docs/adr/README.md](docs/adr/README.md)
- **Retracing why something is the way it is**: [docs/reasoning_logs/](docs/reasoning_logs/)

## Agent skills

### Issue tracker

GitHub Issues on n0nuser/radiotui via `gh`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five roles (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: root `CONTEXT.md` (created lazily) plus `docs/adr/`. See `docs/agents/domain.md`.
