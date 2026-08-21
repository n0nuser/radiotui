"""Abstract SDR device interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class DeviceUnavailable(RuntimeError):
    pass


class SdrDevice(ABC):
    name: str = "sdr"
    is_real: bool = False

    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @property
    @abstractmethod
    def center_freq_hz(self) -> float: ...

    @abstractmethod
    def set_center_freq_hz(self, freq_hz: float) -> None: ...

    @abstractmethod
    def set_sample_rate_hz(self, rate_hz: float) -> None: ...

    @abstractmethod
    def set_gain_db(self, gain_db: float | None) -> None: ...

    @abstractmethod
    def read_samples(self, count: int) -> np.ndarray: ...

    def set_bias_tee(self, enabled: bool) -> bool:
        """Enable ~4.5 V bias voltage on the antenna port. No-op by default."""
        return False

    def set_freq_correction(self, ppm: int) -> bool:
        return False

    def set_offset_tuning(self, enabled: bool) -> bool:
        return False

    def set_hf_mode(self, enabled: bool) -> bool:
        """Direct-sampling Q-branch for HF reception. No-op by default."""
        return False

    def __enter__(self) -> SdrDevice:
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()
