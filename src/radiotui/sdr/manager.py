"""Device discovery and factory."""

from __future__ import annotations

from dataclasses import dataclass

from radiotui.sdr.base import DeviceUnavailable, SdrDevice
from radiotui.sdr.rtlsdr_device import RtlSdrDevice, binding_status, detect_real_devices
from radiotui.sdr.simulator import SimulatedDevice

#: What the simulator actually transmits, so a fallback can say so plainly
#: instead of leaving people to wonder why every band looks the same.
SIMULATOR_FM_CARRIERS_MHZ = (89.0, 93.2, 97.5, 101.3)


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


@dataclass(frozen=True)
class DiagnosisStep:
    """One layer of the "why is there no hardware" check."""

    label: str
    ok: bool
    detail: str = ""
    fix: str = ""


def hardware_diagnosis() -> list[DiagnosisStep]:
    """Check each layer between the USB port and radiotui, in order.

    Reported layer by layer because the layers fail differently: pyrtlsdr can be
    absent, present but unable to load librtlsdr, or working while no dongle is
    plugged in. A single "not detected" cannot tell those apart, and each needs
    a different fix.
    """
    imported, error = binding_status()
    if not imported:
        missing_module = "No module named" in error
        return [
            DiagnosisStep(
                "pyrtlsdr binding",
                False,
                error or "import failed",
                "uv sync --group dev --extra sdr"
                if missing_module
                else "librtlsdr is missing or incompatible - see the "
                "'Linux hardware setup' section of the README",
            )
        ]
    steps = [DiagnosisStep("pyrtlsdr binding", True, "imported")]
    found = describe_devices()
    steps.append(
        DiagnosisStep(
            "RTL-SDR device",
            bool(found),
            "; ".join(found) if found else "librtlsdr enumerated no devices",
            ""
            if found
            else "check `lsusb | grep -i realtek`, then the dvb_usb_rtl28xxu "
            "kernel driver, then udev permissions (try `sudo rtl_test -t`)",
        )
    )
    return steps
