"""Synthetic SDR device for hardware-free demos and tests.

Generates a realistic RF environment: white noise floor plus FM broadcast
carriers, airband AM bursts, PMR446 voice bursts and 2m amateur traffic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from radiotui.sdr.base import SdrDevice


@dataclass
class SimCarrier:
    freq_hz: float
    power_dbfs: float
    mode: str = "fm"
    deviation_hz: float = 5_000.0
    duty_cycle: float = 1.0
    burst_period_s: float = 6.0
    burst_offset_s: float = 0.0
    phase: float = field(default_factory=lambda: float(np.random.uniform(0, 2 * np.pi)))
    _burst_phase: float = field(default=0.0, repr=False)


DEFAULT_ENVIRONMENT: list[SimCarrier] = [
    SimCarrier(89.0e6, -18.0, "wfm", deviation_hz=75_000.0),
    SimCarrier(93.2e6, -24.0, "wfm", deviation_hz=75_000.0),
    SimCarrier(97.5e6, -30.0, "wfm", deviation_hz=75_000.0),
    SimCarrier(101.3e6, -22.0, "wfm", deviation_hz=75_000.0),
    SimCarrier(121.5e6, -26.0, "am", duty_cycle=0.25, burst_period_s=7.0),
    SimCarrier(128.35e6, -34.0, "am", duty_cycle=0.18, burst_period_s=9.0),
    SimCarrier(145.500e6, -28.0, "nfm", duty_cycle=0.30, burst_period_s=8.0),
    SimCarrier(145.7125e6, -38.0, "nfm", duty_cycle=0.20, burst_period_s=11.0),
    SimCarrier(446.00625e6, -27.0, "nfm", duty_cycle=0.35, burst_period_s=6.0),
    SimCarrier(446.03125e6, -33.0, "nfm", duty_cycle=0.25, burst_period_s=10.0),
    SimCarrier(446.05625e6, -40.0, "nfm", duty_cycle=0.15, burst_period_s=13.0),
]


class SimulatedDevice(SdrDevice):
    name = "simulator"
    is_real = False

    def __init__(
        self,
        carriers: list[SimCarrier] | None = None,
        noise_floor_dbfs: float = -62.0,
        seed: int | None = None,
    ) -> None:
        self._carriers = list(carriers) if carriers is not None else DEFAULT_ENVIRONMENT
        self._noise_dbfs = noise_floor_dbfs
        self._rng = np.random.default_rng(seed)
        self._center_hz = 100.0e6
        self._sample_rate = 1_024_000.0
        self._t = 0.0

    @property
    def center_freq_hz(self) -> float:
        return self._center_hz

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def set_center_freq_hz(self, freq_hz: float) -> None:
        self._center_hz = float(freq_hz)

    def set_sample_rate_hz(self, rate_hz: float) -> None:
        self._sample_rate = float(rate_hz)

    def set_gain_db(self, gain_db: float | None) -> None:
        pass

    def read_samples(self, count: int) -> np.ndarray:
        fs = self._sample_rate
        t_start = self._t
        t = t_start + np.arange(count) / fs
        self._t += count / fs

        noise_sigma = 10 ** (self._noise_dbfs / 20.0) / np.sqrt(2)
        iq = self._rng.normal(0.0, noise_sigma, count) + 1j * self._rng.normal(
            0.0, noise_sigma, count
        )

        span = fs * 0.55
        for carrier in self._carriers:
            offset = carrier.freq_hz - self._center_hz
            if abs(offset) > span:
                continue
            envelope = self._envelope(carrier, count, fs, t_start)
            if not np.any(envelope):
                continue
            amp = 10 ** (carrier.power_dbfs / 20.0)
            if carrier.mode == "am":
                mod = 0.6 * (
                    0.5 * np.sin(2 * np.pi * 350 * t + carrier.phase)
                    + 0.5 * np.sin(2 * np.pi * 900 * t + 1.3 * carrier.phase)
                )
                sig = amp * (1.0 + mod) * np.exp(1j * (2 * np.pi * offset * t)) * envelope
            else:
                dev_scale = carrier.deviation_hz / 5_000.0
                voice = (
                    np.sin(2 * np.pi * 300 * t + carrier.phase)
                    + 0.7 * np.sin(2 * np.pi * 1300 * t + 2.1 * carrier.phase)
                    + 0.4 * np.sin(2 * np.pi * 2400 * t + 0.7 * carrier.phase)
                )
                inst_phase = 2 * np.pi * (
                    offset * t + dev_scale * 1_200 * np.cumsum(voice) / fs
                )
                sig = amp * np.exp(1j * inst_phase) * envelope
            iq += sig.astype(np.complex128)

        peak = np.max(np.abs(iq))
        if peak > 0.95:
            iq *= 0.95 / peak
        return iq

    def _envelope(
        self, carrier: SimCarrier, count: int, fs: float, t_start: float
    ) -> np.ndarray:
        if carrier.duty_cycle >= 1.0:
            return np.ones(count)
        period_samples = int(carrier.burst_period_s * fs)
        phase_samples = (t_start * fs + carrier.burst_offset_s * fs) % period_samples
        idx = phase_samples + np.arange(count)
        on_len = period_samples * carrier.duty_cycle
        env = ((idx % period_samples) < on_len).astype(np.float64)
        edge = max(int(0.01 * fs), 1)
        kernel = np.ones(edge) / edge
        return np.convolve(env, kernel, mode="same")
