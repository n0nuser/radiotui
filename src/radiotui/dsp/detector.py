"""Noise-floor estimation, peak extraction and channel tracking."""

from __future__ import annotations

import time
from collections import deque

import numpy as np

from radiotui.config import ScannerSettings
from radiotui.core.models import Channel, DemodMode, Peak, SpectrumFrame


class NoiseFloorEstimator:
    def __init__(self, settings: ScannerSettings) -> None:
        self._settings = settings
        self._recent_floors: deque[float] = deque(maxlen=32)

    def update(self, frame: SpectrumFrame) -> float:
        floor = float(np.percentile(frame.power_db, self._settings.noise_percentile))
        self._recent_floors.append(floor)
        return float(np.median(self._recent_floors))

    @property
    def threshold_db(self) -> float:
        if not self._recent_floors:
            return -200.0
        return float(np.median(self._recent_floors)) + self._settings.threshold_margin_db


def extract_peaks(
    frame: SpectrumFrame,
    floor_db: float,
    threshold_db: float,
    min_snr_db: float,
    merge_gap_bins: int,
) -> list[Peak]:
    above = frame.power_db > threshold_db
    if not np.any(above):
        return []

    bin_hz = float(frame.freqs_hz[1] - frame.freqs_hz[0]) if len(frame.freqs_hz) > 1 else 1.0
    peaks: list[Peak] = []
    idx = np.flatnonzero(above)
    splits = np.where(np.diff(idx) > merge_gap_bins + 1)[0] + 1
    for group in np.split(idx, splits):
        if len(group) < 2:
            continue
        power_lin = 10 ** (frame.power_db[group] / 10.0)
        center_hz = float(np.sum(frame.freqs_hz[group] * power_lin) / np.sum(power_lin))
        peak_idx = group[np.argmax(frame.power_db[group])]
        peak_db = float(frame.power_db[peak_idx])
        snr_db = peak_db - floor_db
        if snr_db < min_snr_db:
            continue
        bandwidth_hz = max(len(group) * bin_hz, bin_hz)
        peaks.append(Peak(center_hz=center_hz, bandwidth_hz=bandwidth_hz, peak_db=peak_db, snr_db=snr_db))
    return peaks


class ChannelTracker:
    def __init__(self, settings: ScannerSettings) -> None:
        self._settings = settings
        self.channels: dict[float, Channel] = {}
        self._pending: dict[float, int] = {}

    def _key(self, freq_hz: float) -> float:
        return round(freq_hz / 5_000.0) * 5_000.0

    def _demod_for(self, freq_hz: float) -> DemodMode:
        from radiotui.config import BANDS

        for band in BANDS.values():
            if band.start_hz <= freq_hz <= band.end_hz:
                return band.demod
        if freq_hz > 300e6:
            return DemodMode.NFM
        return DemodMode.NFM

    def update(self, peaks: list[Peak], now: float | None = None) -> list[Channel]:
        now = now if now is not None else time.time()
        matched_keys: set[float] = set()

        for peak in peaks:
            key = self._existing_or_new_key(peak.center_hz, matched_keys)
            matched_keys.add(key)
            channel = self.channels.get(key)
            if channel is not None:
                self.channels[key] = channel.touch(peak.peak_db, peak.snr_db, peak.bandwidth_hz, now)
                self._pending.pop(key, None)
            else:
                count = self._pending.get(key, 0) + 1
                self._pending[key] = count
                if count >= self._settings.min_persist_frames:
                    self._pending.pop(key, None)
                    self.channels[key] = Channel(
                        center_hz=key,
                        bandwidth_hz=peak.bandwidth_hz,
                        peak_db=peak.peak_db,
                        snr_db=peak.snr_db,
                        demod=self._demod_for(key),
                        first_seen=now,
                        last_seen=now,
                        hits=count,
                        active=True,
                    )

        for key, channel in self.channels.items():
            if key not in matched_keys:
                channel.misses += 1
                if channel.misses > self._settings.drop_after_misses:
                    channel.active = False

        return [c for c in self.channels.values() if c.active]

    def _existing_or_new_key(self, center_hz: float, taken: set[float]) -> float:
        tolerance = 25_000.0
        best_key: float | None = None
        best_dist = tolerance
        for key in list(self.channels) + list(self._pending):
            if key in taken:
                continue
            dist = abs(key - center_hz)
            if dist <= best_dist:
                best_dist = dist
                best_key = key
        return best_key if best_key is not None else self._key(center_hz)

    def active_channels(self) -> list[Channel]:
        return sorted(
            (c for c in self.channels.values() if c.active),
            key=lambda c: c.peak_db,
            reverse=True,
        )
