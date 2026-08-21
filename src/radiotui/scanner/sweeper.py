"""Background sweep loop producing ScanState snapshots."""

from __future__ import annotations

import queue
import threading
import time

import numpy as np

from radiotui.config import ScannerSettings
from radiotui.core.models import ScanState, SpectrumFrame
from radiotui.dsp.detector import ChannelTracker, NoiseFloorEstimator, extract_peaks
from radiotui.dsp.spectrum import SweepPlan, compute_psd, frame_from_plan
from radiotui.sdr.base import SdrDevice


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
        self.sweeps_done = 0

    @property
    def channels(self):
        return self._tracker.active_channels()

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
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def _run(self) -> None:
        samples_per_hop = max(
            int(self._plan.sample_rate_hz * self._settings.hop_dwell_s),
            self._plan.fft_size * 4,
        )
        dc_mask_hz = self._plan.sample_rate_hz * 0.03 if self._device.is_real else 0.0
        while not self._stop.is_set():
            t0 = time.time()
            hops = []
            for center in self._plan.hop_centers_hz:
                if self._stop.is_set():
                    return
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
            state = ScanState(
                frame=frame,
                channels=active,
                noise_floor_db=floor,
                threshold_db=self._floor.threshold_db,
                sweeps_done=self.sweeps_done,
                elapsed=time.time() - t0,
            )
            try:
                self._queue.put_nowait(state)
            except queue.Full:
                pass


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
