"""Device discovery and factory."""

from __future__ import annotations

from radiotui.sdr.base import DeviceUnavailable, SdrDevice
from radiotui.sdr.simulator import SimulatedDevice


def open_device(prefer_real: bool = True) -> tuple[SdrDevice, bool]:
    """Return an open device. Second element is True when it is real hardware."""
    if prefer_real:
        try:
            from radiotui.sdr.rtlsdr_device import RtlSdrDevice

            dev = RtlSdrDevice()
            dev.open()
            return dev, True
        except DeviceUnavailable:
            pass
    return SimulatedDevice(), False


def describe_devices() -> list[str]:
    try:
        from radiotui.sdr.rtlsdr_device import detect_real_devices

        return detect_real_devices()
    except Exception:
        return []
