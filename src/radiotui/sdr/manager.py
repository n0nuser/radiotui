"""Device discovery and factory."""

from __future__ import annotations

from dataclasses import dataclass

from radiotui.sdr.base import DeviceUnavailable, SdrDevice
from radiotui.sdr.rtlsdr_device import RtlSdrDevice, detect_real_devices
from radiotui.sdr.simulator import SimulatedDevice


@dataclass(frozen=True)
class OpenedDevice:
    device: SdrDevice
    is_real: bool


def open_device(prefer_real: bool = True) -> OpenedDevice:
    if prefer_real:
        try:
            dev = RtlSdrDevice()
            dev.open()
            return OpenedDevice(device=dev, is_real=True)
        except DeviceUnavailable:
            pass
    return OpenedDevice(device=SimulatedDevice(), is_real=False)


def describe_devices() -> list[str]:
    try:
        return detect_real_devices()
    except (OSError, RuntimeError, ValueError):
        return []
