"""Real RTL-SDR hardware via pyrtlsdr."""

from __future__ import annotations

import numpy as np

from radiotui.sdr.base import DeviceUnavailable, SdrDevice

try:
    from rtlsdr import RtlSdr

    _HAS_RTLSDR = True
except Exception:
    RtlSdr = None
    _HAS_RTLSDR = False


class RtlSdrDevice(SdrDevice):
    name = "rtl-sdr"
    is_real = True
    _READ_CHUNK = 32_768

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

    def set_hf_mode(self, enabled: bool) -> bool:
        assert self._sdr is not None
        try:
            self._sdr.set_direct_sampling(2 if enabled else 0)
            return True
        except (AttributeError, OSError, RuntimeError, ValueError):
            return False

    def read_samples(self, count: int) -> np.ndarray:
        assert self._sdr is not None
        parts: list[np.ndarray] = []
        remaining = count
        while remaining > 0:
            n = min(remaining, self._READ_CHUNK)
            n -= n % 256
            if n == 0:
                n = 256
            raw = self._sdr.read_samples(n)
            parts.append(np.asarray(raw, dtype=np.complex128))
            remaining -= n
        result = parts[0] if len(parts) == 1 else np.concatenate(parts)
        return result[:count]

    def set_bias_tee(self, enabled: bool) -> bool:
        assert self._sdr is not None
        try:
            self._sdr.set_bias_tee(bool(enabled))
            return True
        except (AttributeError, OSError, RuntimeError, ValueError):
            return False

    def set_freq_correction(self, ppm: int) -> bool:
        assert self._sdr is not None
        try:
            self._sdr.set_freq_correction(int(ppm))
            return True
        except (AttributeError, OSError, RuntimeError, ValueError):
            return False

    def set_offset_tuning(self, enabled: bool) -> bool:
        assert self._sdr is not None
        try:
            from rtlsdr.librtlsdr import librtlsdr

            result = librtlsdr.rtlsdr_set_offset_tuning(self._sdr.dev_p, 1 if enabled else 0)
            return result == 0
        except (AttributeError, OSError, RuntimeError, ValueError):
            return False


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
