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
([ADR 0004](adr/0004-single-consumer-tuner.md)) enforced by `TunerSession`
([ADR 0014](adr/0014-tuner-session-owns-handoffs.md)).

`sdr/rtlsdr_compat.py` patches ctypes so pyrtlsdr imports against librtlsdr
2.x ([ADR 0001](adr/0001-pyrtlsdr-compat-shim.md)). HF bands (below 24 MHz)
switch the device into direct sampling at a forced lower sample rate.
`effective_sample_rate` in `config.py` is the single source for the rate in
use.

## Threads

| Thread | Owner | Talks to the UI via |
| --- | --- | --- |
| Textual event loop | `RadioTuiApp` | n/a |
| `tuner-session` | `scanner.session.TunerSession` | its `on_event` callback; the TUI wraps each event in `post_message(SessionUpdate(event))`, the CLI puts it on a queue its main thread drains |
| `sweeper` | `scanner.sweeper.Sweeper` | none: `ScanState`s go into the session's inbox, tagged with the sweeper's generation; the session forwards the latest as `ScanUpdate` and handles hold requests and device errors (`SweepFailed`) |
| `reader-<MHz>` | `scanner.monitor.ChannelMonitor` | none: the only thread that calls `read_samples` while listening; feeds a two-block queue to the demod thread ([ADR 0012](adr/0012-decoupled-reader-thread.md)) |
| `monitor-<MHz>` | `scanner.monitor.ChannelMonitor` | `on_rssi` / `on_clip` / `on_error` callbacks that the session turns into `Rssi`, `ClipSaved` and `SessionError` events. It is the only thread that touches the recorder while it runs: `set_recording` queues a request it applies between blocks. An exception escaping it is reported through `on_error` and ends the monitor |
| player subprocess | `audio.player.AudioPlayer` | none (`ffplay` or `aplay` fed PCM over stdin) |

## Receive chain (reader and monitor threads)

`ChannelMonitor._read_loop` (the `reader-<MHz>` thread) only calls
`read_samples(block)` and hands each block to a two-block queue, so the
device is always drained ([ADR 0012](adr/0012-decoupled-reader-thread.md)).
`ChannelMonitor._run` (the `monitor-<MHz>` thread) takes blocks from that
queue: `rssi_dbfs`, then `channel_audio` (windowed-sinc channel filter and
decimation, demod, de-emphasis, with `DemodState` carried across blocks),
then `audio_to_pcm16`. The PCM goes to the player, scaled by volume
(playback only), and to `VoxRecorder.feed`, which applies the classifier VOX
gate and the RF squelch gate. See ADRs 0002, 0003, 0006, 0008, 0010 and 0013.

## Tuner handoff

Every handoff runs on the `tuner-session` thread
([ADR 0014](adr/0014-tuner-session-owns-handoffs.md)). `TunerSession`'s public
methods (`sweep`, `listen`, `stop_listening`, `pause_sweep`, `resume_sweep`,
`set_autonomous`, `close`) only queue a request; the session thread handles
them one at a time:

1. Stop the current reader. `ChannelMonitor.stop()` and `Sweeper.stop()`
   join their threads for up to `STOP_TIMEOUT_S` each and return False while
   one is still alive (a USB read can block); the session reports that once
   and retries until the reader is gone.
2. Apply HF direct sampling for the band (`sweep`) or frequency (`listen`).
3. Start the next reader.

When requests pile up behind a slow stop, only the latest tuner request runs,
so rapid listen/listen/band presses never leave an orphan reader (#49). A
listen pauses the sweep and remembers to resume it on `stop_listening`; `s`
while listening is refused with a `Notice`.

Device commands that must not overlap a read (`set_gain`) are queued and run
by whichever thread is reading, through the `before_read` hook that `Sweeper`
and `ChannelMonitor` call before every `read_samples`; with nothing reading,
the session thread runs them.

`close()` stops every reader and then closes the device. `run_tui` and the CLI
wait up to `TunerSession.CLOSE_WAIT_S` with `wait_closed()`; a wedged read
leaves the device to the OS (closing it under a live librtlsdr read can
crash) and a warning goes to stderr.

## Autonomous scan-and-hold

With `scanner.autonomous` set, the sweeper picks a `HoldRequest` from active
channels (`_auto_hold_candidate`, which respects SNR and per-channel cooldown).
It attaches the request to the `ScanState` it publishes and parks until
`release_hold()`. The session engages the hold by starting a recording,
muted monitor on that channel (`HoldEngaged`). While a hold is active the
session thread wakes every `HOLD_CHECK_S`, because the parked sweeper sends
nothing, and asks `auto_hold_release_reason`: silence counts from the later
of the hold start and the last voice, so a channel gets `hold_release_s` of
grace before it is dropped as "no traffic". On release the session puts the
channel on cooldown, stops the monitor, and only then releases the parked
sweeper (`HoldReleased`). A user listen pre-empts a hold: the session stops
the hold's monitor and then the parked sweeper, and resumes sweeping when the
listen ends. Turning autonomous off releases an active hold. The TUI and
`scan --autonomous` share this one policy.
