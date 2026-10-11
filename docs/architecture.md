# Architecture: threads and tuner ownership

The README's Layout section maps modules to responsibilities. This page covers
what the layout doesn't show: which thread runs what, how data reaches the UI,
and how the single tuner changes hands. Symbols are named rather than
line-numbered; find them with `grep` or CodeGraph.

## Device

`sdr.manager.open_device` returns an `OpenedDevice`: a real `RtlSdrDevice`, or
`SimulatedDevice` when no dongle opens (`DeviceUnavailable`) or `--sim` is set.
Both implement `sdr.base.SdrDevice`. Tests subclass `SdrDevice` directly for
fakes. The SDR layer has no locks. Exclusivity is a scheduling invariant
enforced by the app ([ADR 0004](adr/0004-single-consumer-tuner.md)).

`sdr/rtlsdr_compat.py` patches ctypes so pyrtlsdr imports against librtlsdr
2.x ([ADR 0001](adr/0001-pyrtlsdr-compat-shim.md)). HF bands (below 24 MHz)
switch the device into direct sampling at a forced lower sample rate.
`effective_sample_rate` in `config.py` is the single source for the rate in
use.

## Threads

| Thread | Owner | Talks to the UI via |
| --- | --- | --- |
| Textual event loop | `RadioTuiApp` | n/a |
| `sweeper` | `scanner.sweeper.Sweeper` | bounded `queue.Queue[ScanState]` (maxsize 4, drops when full, except that a hold-carrying state blocks until queued), drained by `RadioTuiApp.poll_queue` every 0.15 s; only the latest state is used |
| `monitor-<MHz>` | `scanner.monitor.ChannelMonitor` | `on_rssi` / `on_error` callbacks that the app wires to `post_message` (`RssiUpdate`, `MonitorError`); recorder clips arrive as `ClipSaved` |
| teardown worker | `RadioTuiApp._stop_monitor_worker` (`run_worker(thread=True)`) | `call_from_thread(_monitor_stop_finished, callback)` |
| player subprocess | `audio.player.AudioPlayer` | none (`ffplay` or `aplay` fed PCM over stdin) |

Device errors in the sweeper arrive as a `ScanState` with `error` set.
`poll_queue` logs the error and stops the sweeper.

## Receive chain (monitor thread)

`ChannelMonitor._run` repeats a fixed loop: `read_samples(block)`, then
`rssi_dbfs`, then `channel_audio` (filter, decimate, demod, de-emphasis, with
`DemodState` carried across blocks), then `audio_to_pcm16`. The PCM goes to
the player, scaled by volume (playback only), and to `VoxRecorder.feed`, which
applies the classifier VOX gate and the RF squelch gate. The loop sleeps out
the rest of each block's real-time budget. See ADRs 0002, 0003, 0006, 0008
and 0010.

## Tuner handoff

**Sweeper to monitor** is synchronous. `_pause_sweeper_for_monitor` calls
`sweeper.stop()` on the event loop, and auto-hold needs no stop at all because
the sweeper has already parked itself (see below).

**Monitor to anything** (a band switch, a new listen, a hold release, quit)
always goes through the worker path, because `monitor.stop()` can block on a
USB read:

1. `_detach_monitor` drops `self.monitor` immediately and sets `_ignore_rssi`,
   so late readings from the dying thread are discarded.
2. `_stop_monitor_worker(monitor, callback)` runs `monitor.stop()` (which
   joins a possibly blocked USB read) in a worker thread and increments
   `_monitor_stops_pending`.
3. `_monitor_stop_finished` runs `callback` back on the event loop. That
   callback is where the next sweep or monitor starts.

On shutdown, `_close_device` waits until `_monitor_stops_pending` reaches 0.
Textual starts no workers while the app unmounts, so `on_unmount` stops a
live monitor in a plain non-daemon `tuner-teardown` thread instead. That
thread closes the device when it is the last pending stop, and the process
cannot exit before it does. `_stops_lock` guards the counter both threads
touch.
Starting a listen pauses the sweeper with `_pause_sweeper_for_monitor` and
sets `resume_sweep_after_listen`. Pressing `s` while a monitor is active is
refused. See [ADR 0011](adr/0011-asynchronous-monitor-handoff.md).

## Autonomous scan-and-hold

With `scanner.autonomous` set, the sweeper picks a `HoldRequest` from active
channels (`_auto_hold_candidate`, which respects SNR and per-channel
cooldown). It attaches the request to the `ScanState`, blocks until that state
is queued (a dropped hold would park it forever), and then parks until
`release_hold()`. `poll_queue` calls `_engage_auto_hold`, which starts the
recording monitor directly (not through `start_monitor`, which is the user
path and pre-empts a hold) and remembers the `(monitor, sweeper)` pair in
`_hold`. Every `poll_queue` tick runs `_auto_release_check`, because the parked
sweeper sends no states. `auto_hold_release_reason` decides when to let go:
silence counts from the later of the hold start and the last voice, so a
channel gets `hold_release_s` of grace before it is dropped as "no traffic".
Release tears the monitor down through the handoff path, and
`_monitor_stop_finished` calls `release_hold()` on the remembered sweeper only
after that monitor has stopped. A user listen detaches the hold's monitor the
same way, and its callback stops the parked sweeper instead. `Sweeper.start()`
clears the release event that `stop()` leaves set. The
headless `scan --autonomous` in `cli.py` reuses the same sweeper and monitor
pieces without Textual.
