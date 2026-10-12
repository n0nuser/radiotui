# Testing

`uv run pytest` runs everything. `asyncio_mode = "auto"` is set in
`pyproject.toml`, so `async def test_*` needs no marker. Tests never need a
dongle: they use `force_sim=True` or a fake device.

## TUI tests

Copy the shape of `open_radio_app` in `tests/test_radio_view.py`:

```python
app = RadioTuiApp(force_sim=True, settings=Settings())
async with app.run_test(size=(140, 40)) as pilot:
    ...
```

- **Keep the process CPU-idle.** Textual's `wait_for_idle` only returns early
  when the whole process is idle, so every `pilot.press` against a running
  SIM sweeper burns about 2 s and a key-heavy test appears to hang.
  `RadioTuiApp` defaults to `start_sweeper=False` (only `run_tui` passes
  `True`). If a test passes `start_sweeper=True` or presses `s`, call
  `app.session.pause_sweep()` and wait for it before it drives keys.
- **Wait for the session, don't read it straight away.** Listening, band
  switches and sweep pauses happen on the `tuner-session` thread, so assert
  through the waits in `tests/tui_waits.py` (`wait_listening`,
  `wait_sweeping`, ...) and `app.session.status()`. Session behaviour itself
  is tested without Textual in `tests/test_session.py`.
- **Fakes return what the real thing returns.** `ChannelMonitor.stop()` and
  `Sweeper.stop()` return a bool that the session retries on; a fake that
  returns None makes the retry loop spin forever.
- **User files are isolated for you.** The autouse `isolate_user_files`
  fixture in `tests/conftest.py` points `HOME`, `USERPROFILE` and
  `XDG_CONFIG_HOME` at `tmp_path` and makes it the working directory, so
  `config.toml`, `channels.toml` and the relative default `recordings/` all
  land there. Write fixture files to `channels_file_path()` or
  `config_path()`; don't pass `channels_path`. `tests/test_isolation.py`
  fails if a path escapes to the real `~/.config/radiotui`.
- **Restore global registries.** Tests that add to `config.BANDS` remove the
  entry in a `finally` (see `tests/test_tui_bands.py`).
- **Use a fixed `size=`.** Layout assertions depend on it.

## Thread and DSP tests

- **Fake devices:** subclass `sdr.base.SdrDevice` and return synthetic IQ from
  `read_samples` (`CountingDevice` in `tests/test_squelch_controls.py`).
- **Sweeper tests:** build a `SweepPlan` and a `queue.Queue`, then wait on the
  queue with a deadline. Don't use fixed sleeps.
- **Audio chain changes:** keep the equivalence tests against reference
  implementations (for example `deemphasize_loop`) passing
  ([ADR 0002](adr/0002-numpy-only-receive-chain.md)).
- Use `monkeypatch` to override attributes. Don't assign and suppress the
  type error.

## Debugging a hang

Pytest and Textual both capture output, which hides where a test is stuck.

On CI, a test that runs for more than 120 s (`faulthandler_timeout` in
`pyproject.toml`) dumps the stack of every thread into the job log, and
`-v` names the test. Look for `Timeout (0:02:00)!`: the thread stuck in
`_read_loop`, `_run` or the session loop shows where the deadlock is. A job
is killed after 15 minutes, and `--durations=15` lists the slowest tests, so
slow waits are visible before they become hangs.

- Run with `-s` so `faulthandler.dump_traceback_later` output on fd 2 is
  visible.
- Inside a running Textual app, stdout is captured, so log with
  `os.write(2, ...)`.
- Textual calls every event handler definition along the MRO. Subclassing
  the app to add probes can cause spurious errors such as a duplicate
  `on_mount`.

[The full investigation is in docs/reasoning_logs/2026-08-22-textual-test-hang.md.](reasoning_logs/2026-08-22-textual-test-hang.md)
