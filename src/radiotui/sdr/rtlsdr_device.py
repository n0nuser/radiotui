"""Real RTL-SDR hardware via pyrtlsdr."""

from __future__ import annotations

import warnings

import numpy as np

from radiotui.sdr.base import DeviceUnavailable, SdrDevice

try:
    # Business decision: pinned pyrtlsdr 0.2.92 emits import-time
    # deprecation/syntax warnings from its own internals; nothing actionable.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        warnings.simplefilter("ignore", SyntaxWarning)
        from rtlsdr import RtlSdr

    _HAS_RTLSDR = True
except ImportError:
    RtlSdr = None
    _HAS_RTLSDR = False


class RtlSdrDevice(SdrDevice):
    name = "rtl-sdr"
    is_real = True

    def __init__(self, device_index: int = 0) -> None:
        if not _HAS_RTLSDR:
            raise DeviceUnavailable("pyrtlsdr is not installed (uv sync --extra sdr)")
        self._index = device_index
        self._sdr: RtlSdr | None = None

    def open(self) -> None:
        try:
            sdr = RtlSdr(device_index=self._index)
            sdr.set_direct_sampling(0)
        except (OSError, RuntimeError, ValueError) as exc:
            raise DeviceUnavailable(f"no RTL-SDR device found: {exc}") from exc
        self._sdr = sdr

    def close(self) -> None:
        if self._sdr is not None:
            try:
                self._sdr.close()
            except OSError:
                pass
            self._sdr = None

    @property
    def center_freq_hz(self) -> float:
        assert self._sdr is not None
        return float(self._sdr.center_freq)

    def set_center_freq_hz(self, freq_hz: float) -> None:
        assert self._sdr is not None
        self._sdr.center_freq = int(freq_hz)

    def set_sample_rate_hz(self, rate_hz: float) -> None:
        assert self._sdr is not None
        self._sdr.sample_rate = int(rate_hz)

    def set_gain_db(self, gain_db: float | None) -> None:
        assert self._sdr is not None
        if gain_db is None:
            self._sdr.gain = "auto"
        else:
            self._sdr.gain = float(gain_db)

    def read_samples(self, count: int) -> np.ndarray:
        assert self._sdr is not None
        raw = self._sdr.read_samples(count)
        return np.asarray(raw, dtype=np.complex128)


def detect_real_devices(max_probe: int = 4) -> list[str]:
    if not _HAS_RTLSDR:
        return []
    found: list[str] = []
    for idx in range(max_probe):
        try:
            dev = RtlSdrDevice(idx)
            with dev:
                serial = f"index {idx}"
                if hasattr(dev._sdr, "device_serial_number"):
                    serial = dev._sdr.device_serial_number
                found.append(f"RTL-SDR #{idx} (serial {serial})")
        except (OSError, RuntimeError, ValueError):
            continue
    return found
