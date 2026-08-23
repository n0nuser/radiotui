"""Power spectrum computation and sweep planning."""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from radiotui.core.models import SpectrumFrame


def compute_psd(iq: np.ndarray, fft_size: int) -> np.ndarray:
    """Averaged periodogram of complex baseband, fftshifted to [-fs/2, fs/2).

    Normalized so white noise at complex variance v reads 10*log10(v) dB,
    i.e. roughly dBFS per bin.
    """
    usable = (len(iq) // fft_size) * fft_size
    if usable < fft_size:
        return np.full(fft_size, -200.0)
    rows = iq[:usable].reshape(-1, fft_size)
    window = np.hanning(fft_size)[None, :]
    spec = np.fft.fft(rows * window, axis=1)
    power = np.mean(np.abs(spec) ** 2, axis=0)
    power /= fft_size * np.mean(window**2)
    power = np.fft.fftshift(power)
    return 10.0 * np.log10(power + 1e-20)


@dataclass
class SweepPlan:
    start_hz: float
    end_hz: float
    sample_rate_hz: float
    fft_size: int
    hop_centers_hz: list[float]
    bin_hz: float
    edge_fraction: float = 0.12

    @classmethod
    def build(
        cls, start_hz: float, end_hz: float, sample_rate_hz: float, fft_size: int
    ) -> SweepPlan:
        if end_hz <= start_hz:
            raise ValueError("end must be greater than start")
        usable_bw = sample_rate_hz * (1.0 - 2 * 0.12)
        span = end_hz - start_hz
        if span <= usable_bw:
            centers = [(start_hz + end_hz) / 2]
        else:
            n_hops = int(np.ceil(span / usable_bw))
            step = span / n_hops
            centers = [start_hz + step * (i + 0.5) for i in range(n_hops)]
        return cls(
            start_hz=start_hz,
            end_hz=end_hz,
            sample_rate_hz=sample_rate_hz,
            fft_size=fft_size,
            hop_centers_hz=centers,
            bin_hz=sample_rate_hz / fft_size,
        )

    def freqs_for_hop(self, center_hz: float) -> np.ndarray:
        half = self.sample_rate_hz / 2
        return center_hz + np.linspace(-half, half, self.fft_size, endpoint=False)


@dataclass
class _Segment:
    freqs: np.ndarray
    power: np.ndarray


def stitch_segments(segments: list[_Segment]) -> tuple[np.ndarray, np.ndarray]:
    order = np.argsort([s.freqs[0] for s in segments])
    segments = [segments[i] for i in order]
    freqs = np.concatenate([s.freqs for s in segments])
    power = np.concatenate([s.power for s in segments])
    sort_idx = np.argsort(freqs)
    return freqs[sort_idx], power[sort_idx]


def frame_from_plan(plan: SweepPlan, hop_powers: list[tuple[float, np.ndarray]]) -> SpectrumFrame:
    keep_half = plan.sample_rate_hz * (0.5 - plan.edge_fraction)
    segments = []
    for center, psd in hop_powers:
        freqs = plan.freqs_for_hop(center)
        lo = max(plan.start_hz, center - keep_half)
        hi = min(plan.end_hz, center + keep_half)
        keep = (freqs >= lo) & (freqs <= hi)
        segments.append(_Segment(freqs[keep], psd[keep]))
    freqs, power = stitch_segments(segments)
    return SpectrumFrame(freqs_hz=freqs, power_db=power, timestamp=time.time())
