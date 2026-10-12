"""TunerSession: the one owner of the tuner, for the TUI and the headless CLI.

ADR 0004 allows a single reader on the tuner at a time. Every handoff (sweep
to listen, listen to sweep, band switch, hold engage and release, close) runs
on the session's own thread, one after another, so a stop that blocks on a
USB read delays the next reader instead of overlapping it, and the caller's
thread never blocks. Public methods only queue a request; when several tuner
requests queue up during a slow stop, only the latest one runs.
"""

from __future__ import annotations

import itertools
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from radiotui.audio.recorder import ClipInfo
from radiotui.channels_file import UserChannels
from radiotui.config import (
    Band,
    Settings,
    band_needs_hf,
    clamp_volume_db,
    effective_sample_rate,
    enable_hf,
    freq_needs_hf,
)
from radiotui.core.models import Channel, DemodMode, HoldRequest, ScanState
from radiotui.dsp.spectrum import SweepPlan
from radiotui.scanner.monitor import ChannelMonitor, MonitorStatus, auto_hold_release_reason
from radiotui.scanner.sweeper import Sweeper
from radiotui.sdr.base import SdrDevice

# ---- events, delivered to on_event from the session and monitor threads ----


@dataclass(frozen=True)
class ScanUpdate:
    state: ScanState


@dataclass(frozen=True)
class SweepStarted:
    band: Band
    hops: int
    #: Direct-sampling result when the band needs HF, else None.
    hf_ok: bool | None


@dataclass(frozen=True)
class SweepFailed:
    """The device failed mid-sweep; the sweep has stopped."""

    message: str


@dataclass(frozen=True)
class ListenStarted:
    freq_hz: float
    demod: DemodMode
    recording: bool
    hf_ok: bool | None


@dataclass(frozen=True)
class ListenStopped:
    freq_hz: float


@dataclass(frozen=True)
class Rssi:
    dbfs: float


@dataclass(frozen=True)
class ToneDetected:
    """The CTCSS tone or DCS code heard on ``freq_hz`` changed (None = gone)."""

    freq_hz: float
    tone: str | None


@dataclass(frozen=True)
class ClipSaved:
    clip: ClipInfo


@dataclass(frozen=True)
class HoldEngaged:
    request: HoldRequest


@dataclass(frozen=True)
class HoldReleased:
    freq_hz: float
    reason: str
    clips: int


@dataclass(frozen=True)
class Notice:
    """A user-facing remark about a request, such as a refused sweep resume."""

    message: str


@dataclass(frozen=True)
class SessionError:
    message: str


SessionEvent = (
    ScanUpdate
    | SweepStarted
    | SweepFailed
    | ListenStarted
    | ListenStopped
    | Rssi
    | ClipSaved
    | ToneDetected
    | HoldEngaged
    | HoldReleased
    | Notice
    | SessionError
)


@dataclass(frozen=True)
class SessionStatus:
    #: The sweeper thread is alive (sweeping, or parked by a hold).
    sweeping: bool
    #: Parked waiting for a hold to end: alive, but not reading the tuner.
    sweep_parked: bool
    sweeps_done: int
    hops_done: int
    hop_total: int
    listening: MonitorStatus | None
    listen_freq_hz: float | None
    listen_demod: DemodMode | None
    hold_freq_hz: float | None
    #: A listen paused the sweep; stopping it resumes the sweep.
    sweep_paused_for_listen: bool


# ---- requests, handled on the session thread ----


@dataclass(frozen=True)
class _Sweep:
    band: Band
    plan: SweepPlan
    start: bool


@dataclass(frozen=True)
class _Listen:
    freq_hz: float
    demod: DemodMode
    record: bool
    muted: bool


@dataclass(frozen=True)
class _StopListening:
    pass


@dataclass(frozen=True)
class _PauseSweep:
    pass


@dataclass(frozen=True)
class _ResumeSweep:
    pass


@dataclass(frozen=True)
class _SetAutonomous:
    enabled: bool


@dataclass(frozen=True)
class _Wake:
    """Re-check the monitor and pending device commands."""


@dataclass(frozen=True)
class _Close:
    pass


@dataclass(frozen=True)
class _FromSweeper:
    generation: int
    state: ScanState


_TunerRequest = _Sweep | _Listen | _StopListening | _PauseSweep | _ResumeSweep
_TUNER_REQUESTS = (_Sweep, _Listen, _StopListening, _PauseSweep, _ResumeSweep)


class _SweeperOutbox:
    """The queue a Sweeper writes to: tags each state with its generation.

    A state queued by a sweeper that has since been replaced must not engage
    a hold on its successor.
    """

    def __init__(self, inbox: queue.Queue, generation: int) -> None:
        self._inbox = inbox
        self._generation = generation

    def put(self, state: ScanState, block: bool = True, timeout: float | None = None) -> None:
        self._inbox.put(_FromSweeper(self._generation, state))


class TunerSession:
    #: How long close may take: the monitor's two joins and the sweeper's
    #: join (STOP_TIMEOUT_S each), plus the device close.
    CLOSE_WAIT_S = 3 * ChannelMonitor.STOP_TIMEOUT_S + 1.0

    #: While a hold is active, how often the release policy is checked. With
    #: no hold the session thread sleeps until a request arrives.
    HOLD_CHECK_S = 0.15

    def __init__(
        self,
        device: SdrDevice,
        settings: Settings,
        on_event: Callable[[SessionEvent], None],
    ) -> None:
        self._device = device
        self._settings = settings
        self._emit = on_event
        self._user_channels = UserChannels()
        self._tones: dict[float, str] = {}
        self._inbox: queue.Queue = queue.Queue()
        self._ops_lock = threading.Lock()
        self._device_ops: list[Callable[[SdrDevice], None]] = []
        self._generations = itertools.count()
        self._thread = threading.Thread(target=self._run, name="tuner-session", daemon=True)
        # Owned by the session thread; other threads only read the references.
        self._band: Band | None = None
        self._sweeper: Sweeper | None = None
        self._sweeper_generation = -1
        self._monitor: ChannelMonitor | None = None
        self._hold: HoldRequest | None = None
        self._hold_started_at = 0.0
        self._resume_after_listen = False
        self._closing = False

    @classmethod
    def start(
        cls,
        device: SdrDevice,
        settings: Settings,
        on_event: Callable[[SessionEvent], None],
    ) -> TunerSession:
        session = cls(device, settings, on_event)
        session._thread.start()
        return session

    # ---- requests ----

    def sweep(self, band: Band, *, start: bool = True) -> SweepPlan:
        """Sweep ``band`` (paused until resume_sweep when ``start`` is False)."""
        scanner = replace(self._settings.scanner, hf_mode=band_needs_hf(band))
        plan = SweepPlan.build(
            band.start_hz, band.end_hz, effective_sample_rate(scanner), scanner.fft_size
        )
        self._inbox.put(_Sweep(band, plan, start))
        return plan

    def listen(
        self, freq_hz: float, demod: DemodMode, *, record: bool = False, muted: bool = False
    ) -> None:
        self._inbox.put(_Listen(freq_hz, demod, record, muted))

    def stop_listening(self) -> None:
        """Stop the listen or hold; resumes a sweep that the listen paused."""
        self._inbox.put(_StopListening())

    def pause_sweep(self) -> None:
        self._inbox.put(_PauseSweep())

    def resume_sweep(self) -> None:
        self._inbox.put(_ResumeSweep())

    def set_autonomous(self, enabled: bool) -> None:
        # The flag first, so the sweeper stops asking for holds right away.
        self._settings.scanner.autonomous = enabled
        self._inbox.put(_SetAutonomous(enabled))

    def set_gain(self, gain_db: float | None) -> None:
        """Applied between reads by whichever thread reads the tuner."""
        self._settings.scanner.gain_db = gain_db
        with self._ops_lock:
            self._device_ops.append(lambda device: device.set_gain_db(gain_db))
        self._inbox.put(_Wake())  # nobody may be reading to pick it up

    def set_user_channels(self, user_channels: UserChannels) -> None:
        self._user_channels = user_channels
        sweeper = self._sweeper
        if sweeper is not None:
            sweeper.set_user_channels(user_channels)

    def set_muted(self, muted: bool) -> None:
        monitor = self._monitor
        if monitor is not None:
            monitor.set_muted(muted)

    def set_clarifier_hz(self, clarifier_hz: float) -> None:
        """SSB/CW fine tune: shifts the demodulator, never retunes (ADR 0016)."""
        monitor = self._monitor
        if monitor is not None:
            monitor.set_clarifier_hz(clarifier_hz)

    def set_tone_squelch(self, tone: str | None) -> None:
        """None (off), "any", or a CTCSS tone / DCS code (ADR 0017)."""
        monitor = self._monitor
        if monitor is not None:
            monitor.set_tone_squelch(tone)

    @property
    def tones(self) -> dict[float, str]:
        """Last tone heard on each listened frequency, for the table and exports."""
        return self._tones

    def set_volume_db(self, volume_db: float) -> None:
        self._settings.audio.volume_db = clamp_volume_db(volume_db)

    def set_recording(self, enabled: bool) -> None:
        monitor = self._monitor
        if monitor is not None:
            monitor.set_recording(enabled)

    def close(self) -> None:
        """Stop every reader, then close the device; see wait_closed."""
        self._inbox.put(_Close())

    def wait_closed(self, timeout_s: float) -> bool:
        """False if a wedged read still holds the device after ``timeout_s``."""
        self._thread.join(timeout_s)
        return not self._thread.is_alive()

    # ---- queries ----

    @property
    def channels(self) -> list[Channel]:
        sweeper = self._sweeper
        return sweeper.channels if sweeper is not None else []

    def status(self) -> SessionStatus:
        sweeper, monitor, hold = self._sweeper, self._monitor, self._hold
        return SessionStatus(
            sweeping=sweeper is not None and sweeper.running,
            sweep_parked=sweeper is not None and sweeper.holding_active(),
            sweeps_done=sweeper.sweeps_done if sweeper is not None else 0,
            hops_done=sweeper.hops_done if sweeper is not None else 0,
            hop_total=sweeper.hop_total if sweeper is not None else 0,
            listening=monitor.status() if monitor is not None else None,
            listen_freq_hz=monitor.freq_hz if monitor is not None else None,
            listen_demod=monitor.demod if monitor is not None else None,
            hold_freq_hz=hold.freq_hz if hold is not None else None,
            sweep_paused_for_listen=self._resume_after_listen,
        )

    # ---- session thread ----

    def _run(self) -> None:
        try:
            while not self._closing:
                timeout = self.HOLD_CHECK_S if self._hold is not None else None
                try:
                    batch = [self._inbox.get(timeout=timeout)]
                except queue.Empty:
                    batch = []
                while True:
                    try:
                        batch.append(self._inbox.get_nowait())
                    except queue.Empty:
                        break
                self._handle(batch)
        except Exception as exc:
            # Last resort: steps are guarded, so reaching here is a bug; say so
            # rather than leaving an app that looks alive but ignores requests.
            self._emit(SessionError(f"tuner session stopped: {exc}"))
        finally:
            self._shutdown()

    def _handle(self, batch: list) -> None:
        if any(isinstance(item, _Close) for item in batch):
            self._closing = True
            return
        for item in batch:
            if isinstance(item, _SetAutonomous):
                self._guarded(self._set_autonomous, item.enabled)
        tuner = [item for item in batch if isinstance(item, _TUNER_REQUESTS)]
        states = [
            item.state
            for item in batch
            if isinstance(item, _FromSweeper) and item.generation == self._sweeper_generation
        ]
        if states:
            self._guarded(self._on_scan_states, states, bool(tuner))
        if tuner:
            # Latest wins: requests that queued up during a slow stop are
            # superseded by the newest one.
            self._guarded(self._execute, tuner[-1])
        self._guarded(self._check_monitor)
        if not self._reader_active():
            self._guarded(self._run_device_ops)

    def _guarded(self, step: Callable, *args) -> None:
        # The session thread must survive a failing step, or every later
        # request (and the device close) would be lost.
        try:
            step(*args)
        except Exception as exc:
            self._emit(SessionError(f"tuner session: {exc}"))

    def _on_scan_states(self, states: list[ScanState], request_pending: bool) -> None:
        for state in states:
            if state.error:
                self._emit(SweepFailed(state.error))
                self._stop_sweeper()
                return
        latest = states[-1]
        self._emit(ScanUpdate(latest))
        if latest.hold_request is None:
            return
        if request_pending:
            # The pending request decides what the tuner does next.
            self._release_parked_sweeper()
        else:
            self._engage_hold(latest.hold_request)

    def _execute(self, request: _TunerRequest) -> None:
        if isinstance(request, _Sweep):
            self._do_sweep(request)
        elif isinstance(request, _Listen):
            self._do_listen(request)
        elif isinstance(request, _StopListening):
            self._end_listen("stopped")
        elif isinstance(request, _PauseSweep):
            self._do_pause_sweep()
        else:
            self._do_resume_sweep()

    def _do_sweep(self, request: _Sweep) -> None:
        self._stop_monitor()
        self._hold = None
        self._resume_after_listen = False
        self._stop_sweeper()
        generation = next(self._generations)
        sweeper = Sweeper(
            self._device,
            request.plan,
            self._settings.scanner,
            _SweeperOutbox(self._inbox, generation),
            before_read=self._run_device_ops,
        )
        sweeper.set_user_channels(self._user_channels)
        self._band = request.band
        self._sweeper = sweeper
        self._sweeper_generation = generation
        hf_ok = self._apply_hf(band_needs_hf(request.band))
        self._emit(SweepStarted(request.band, len(request.plan.hop_centers_hz), hf_ok))
        if request.start:
            sweeper.start()

    def _do_listen(self, request: _Listen) -> None:
        self._stop_monitor()
        self._hold = None  # a user listen pre-empts a hold; its sweeper is stopped below
        sweeper = self._sweeper
        if sweeper is not None and sweeper.running:
            self._stop_sweeper()
            self._resume_after_listen = True
        hf_ok = self._apply_hf(freq_needs_hf(request.freq_hz))
        self._start_monitor(request.freq_hz, request.demod, request.record, request.muted)
        self._emit(ListenStarted(request.freq_hz, request.demod, request.record, hf_ok))

    def _end_listen(self, reason: str) -> None:
        """Stop listening: a hold hands the tuner back to its parked sweeper."""
        if self._hold is not None:
            self._release_hold(reason)
            return
        self._stop_monitor()
        if self._resume_after_listen:
            self._resume_after_listen = False
            self._start_sweeper()

    def _do_pause_sweep(self) -> None:
        if self._monitor is not None:
            self._emit(Notice("Sweep stays paused while listening - press l first."))
            return
        sweeper = self._sweeper
        if sweeper is not None and sweeper.running:
            self._stop_sweeper()
            self._emit(Notice("Sweep paused"))

    def _do_resume_sweep(self) -> None:
        if self._monitor is not None:
            self._emit(Notice("Sweep stays paused while listening - press l first."))
            return
        sweeper = self._sweeper
        if sweeper is not None and not sweeper.running:
            self._start_sweeper()
            self._emit(Notice("Sweep resumed"))

    def _set_autonomous(self, enabled: bool) -> None:
        if not enabled and self._hold is not None:
            self._release_hold("autonomous off")

    def _engage_hold(self, request: HoldRequest) -> None:
        sweeper = self._sweeper
        if sweeper is None or not sweeper.running:
            return
        if not self._settings.scanner.autonomous or self._monitor is not None:
            sweeper.release_hold()  # don't leave it parked on a refused hold
            return
        self._hold = request
        self._hold_started_at = time.time()
        self._start_monitor(request.freq_hz, request.demod, record=True, muted=True)
        self._emit(HoldEngaged(request))

    def _check_monitor(self) -> None:
        monitor = self._monitor
        if monitor is None:
            return
        if not monitor.running:
            # It ended on its own (read failure, crash) and already reported why.
            self._end_listen("monitor stopped")
            return
        if self._hold is None:
            return
        reason = auto_hold_release_reason(
            monitor.silent_for(),
            time.time() - self._hold_started_at,
            self._settings.scanner.hold_release_s,
            self._settings.scanner.max_hold_s,
        )
        if reason is not None:
            self._release_hold(reason)

    def _release_hold(self, reason: str) -> None:
        request, self._hold = self._hold, None
        assert request is not None
        sweeper = self._sweeper
        if sweeper is not None:
            sweeper.cooldown_channel(request.channel_hz)
        clips = self._stop_monitor()
        self._emit(HoldReleased(request.freq_hz, reason, clips))
        self._release_parked_sweeper()

    def _release_parked_sweeper(self) -> None:
        sweeper = self._sweeper
        if sweeper is not None:
            sweeper.release_hold()

    def _start_monitor(self, freq_hz: float, demod: DemodMode, record: bool, muted: bool) -> None:
        band = self._band

        def on_rssi(dbfs: float) -> None:
            if self._monitor is monitor:  # drop readings from a monitor being replaced
                self._emit(Rssi(dbfs))

        def on_tone(tone: str | None) -> None:
            if self._monitor is not monitor:
                return
            if tone is not None:
                # Rebind, never mutate: other threads copy this map.
                self._tones = {**self._tones, freq_hz: tone}
            self._emit(ToneDetected(freq_hz, tone))

        def on_error(message: str) -> None:
            self._emit(SessionError(message))
            self._inbox.put(_Wake())  # let the session notice if the monitor ended

        monitor = ChannelMonitor(
            self._device,
            freq_hz,
            demod,
            self._settings,
            muted=muted,
            record=record,
            band_label=band.label if band is not None else "",
            # A band's channel width belongs to its own mode: 200 kHz of FM
            # broadcast would swamp a USB listen on the same band.
            channel_bw_hz=band.channel_bw_hz if band is not None and band.demod is demod else None,
            on_rssi=on_rssi,
            on_clip=lambda clip: self._emit(ClipSaved(clip)),
            on_error=on_error,
            on_tone=on_tone,
            before_read=self._run_device_ops,
        )
        monitor.set_tone_squelch(self._user_channels.tone_for(freq_hz))
        self._monitor = monitor
        monitor.start()

    def _stop_monitor(self) -> int:
        """Stop and forget the monitor; returns how many clips it saved."""
        monitor = self._monitor
        if monitor is None:
            return 0
        self._stop_reader(monitor)
        self._monitor = None
        self._emit(ListenStopped(monitor.freq_hz))
        return len(monitor.clips)

    def _start_sweeper(self) -> None:
        sweeper = self._sweeper
        if sweeper is None or sweeper.running:
            return
        if self._band is not None:
            self._apply_hf(band_needs_hf(self._band))  # a listen may have changed it
        sweeper.start()

    def _stop_sweeper(self) -> None:
        sweeper = self._sweeper
        if sweeper is not None:
            self._stop_reader(sweeper)

    def _stop_reader(self, reader: ChannelMonitor | Sweeper) -> None:
        """Return only once the reader's threads are gone (ADR 0004)."""
        if reader.stop():
            return
        self._emit(SessionError("waiting for the device read to return"))
        while not reader.stop():
            pass  # each attempt already waits STOP_TIMEOUT_S

    def _apply_hf(self, needs_hf: bool) -> bool | None:
        enable_hf(needs_hf, self._settings.scanner)
        ok = self._device.set_hf_mode(needs_hf)
        return ok if needs_hf else None

    def _reader_active(self) -> bool:
        sweeper, monitor = self._sweeper, self._monitor
        sweeping = sweeper is not None and sweeper.running and not sweeper.holding_active()
        return sweeping or (monitor is not None and monitor.running)

    def _run_device_ops(self) -> None:
        """Run queued device commands on the calling (reading) thread."""
        with self._ops_lock:
            ops, self._device_ops = self._device_ops, []
        for op in ops:
            try:
                op(self._device)
            except (OSError, RuntimeError, ValueError) as exc:
                self._emit(SessionError(f"device command failed: {exc}"))

    def _shutdown(self) -> None:
        self._guarded(self._stop_monitor)
        self._guarded(self._stop_sweeper)
        sweeper, monitor = self._sweeper, self._monitor
        readers_alive = (sweeper is not None and sweeper.running) or (
            monitor is not None and monitor.running
        )
        # Closing under a live read can crash librtlsdr; a wedged reader
        # leaves the device to the OS instead.
        if not readers_alive:
            self._device.close()
