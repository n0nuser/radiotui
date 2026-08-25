"""Background sweep loop producing ScanState snapshots."""

from __future__ import annotations

import queue
import threading
import time

import numpy as np

from radiotui.channels_file import UserChannels
from radiotui.config import ScannerSettings
from radiotui.core.models import HoldRequest, ScanState, SpectrumFrame
from radiotui.dsp.detector import ChannelTracker, NoiseFloorEstimator, extract_peaks
from radiotui.dsp.spectrum import SweepPlan, compute_psd, frame_from_plan
from radiotui.sdr.base import SdrDevice


def channel_key(freq_hz: float) -> float:
    return round(freq_hz / 5_000.0) * 5_000.0


class Sweeper:
    def __init__(
        self,
        device: SdrDevice,
        plan: SweepPlan,
        settings: ScannerSettings,
        out_queue: queue.Queue[ScanState],
    ) -> None:
        self._device = device
        self._plan = plan
        self._settings = settings
        self._queue = out_queue
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._floor = NoiseFloorEstimator(settings)
        self._tracker = ChannelTracker(settings)
        self._release_event = threading.Event()
        self._cooldown_until: dict[float, float] = {}
        self.hold_request: HoldRequest | None = None
        self.holding = False
        self.sweeps_done = 0
        #: Live hop counter so the UI can show progress. A full sweep of FM
        #: broadcast is 27 hops and takes seconds; without this the display
        #: sits motionless and looks hung between frames.
        self.hops_done = 0
        self.hop_total = len(plan.hop_centers_hz)

    @property
    def channels(self):
        return self._tracker.active_channels()

    def set_user_channels(self, user_channels: UserChannels) -> None:
        """Live-apply bookmarks (names) and ignores (birdie silencing)."""
        self._tracker.set_user_channels(user_channels)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="sweeper", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._release_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def release_hold(self) -> None:
        self._release_event.set()

    def holding_active(self) -> bool:
        return self.holding

    def cooldown_channel(self, freq_hz: float) -> None:
        self._cooldown_until[channel_key(freq_hz)] = time.time() + self._settings.channel_cooldown_s

    def _auto_hold_candidate(self, active: list) -> HoldRequest | None:
        now = time.time()
        for ch in sorted(active, key=lambda c: c.snr_db, reverse=True):
            if ch.snr_db < self._settings.auto_hold_min_snr_db:
                break
            if now < self._cooldown_until.get(channel_key(ch.center_hz), 0.0):
                continue
            return HoldRequest(freq_hz=ch.center_hz, demod=ch.demod, snr_db=ch.snr_db)
        return None

    def _run(self) -> None:
        dc_mask_hz = self._plan.sample_rate_hz * 0.03 if self._device.is_real else 0.0
        try:
            self._device.set_sample_rate_hz(self._plan.sample_rate_hz)
            self._device.set_gain_db(self._settings.gain_db)
        except (OSError, RuntimeError, ValueError) as exc:
            self._queue.put(_error_state(str(exc)))
            return
        while not self._stop.is_set():
            t0 = time.time()
            # Recomputed every sweep so runtime dwell changes apply live
            samples_per_hop = max(
                int(self._plan.sample_rate_hz * self._settings.hop_dwell_s),
                self._plan.fft_size * 4,
            )
            hops = []
            self.hops_done = 0
            for center in self._plan.hop_centers_hz:
                if self._stop.is_set():
                    return
                self.hops_done += 1
                try:
                    self._device.set_center_freq_hz(center)
                    iq = self._device.read_samples(samples_per_hop)
                except (OSError, RuntimeError, ValueError) as exc:
                    self._queue.put(_error_state(str(exc)))
                    return
                psd = compute_psd(iq, self._plan.fft_size)
                if dc_mask_hz > 0.0:
                    freqs = self._plan.freqs_for_hop(center)
                    psd[np.abs(freqs - center) <= dc_mask_hz / 2] = -200.0
                hops.append((center, psd))
            frame = frame_from_plan(self._plan, hops)
            floor = self._floor.update(frame)
            peaks = extract_peaks(
                frame,
                floor,
                self._floor.threshold_db,
                self._settings.min_snr_db,
                self._settings.peak_merge_gap_bins,
            )
            active = self._tracker.update(peaks)
            self.sweeps_done += 1
            hold = None
            if self._settings.autonomous and self.hold_request is None:
                hold = self._auto_hold_candidate(active)
                if hold is not None:
                    self.hold_request = hold
                    self.holding = True
            state = ScanState(
                frame=frame,
                channels=active,
                noise_floor_db=floor,
                threshold_db=self._floor.threshold_db,
                sweeps_done=self.sweeps_done,
                elapsed=time.time() - t0,
                hold_request=hold,
            )
            try:
                self._queue.put_nowait(state)
            except queue.Full:
                pass
            if hold is not None and not self._stop.is_set():
                self._release_event.wait()
                self._release_event.clear()
                self.hold_request = None
                self.holding = False


def _error_state(message: str) -> ScanState:
    return ScanState(
        frame=SpectrumFrame(freqs_hz=np.array([0.0]), power_db=np.array([-200.0])),
        channels=[],
        noise_floor_db=-200.0,
        threshold_db=-200.0,
        sweeps_done=-1,
        elapsed=0.0,
        error=message,
    )
