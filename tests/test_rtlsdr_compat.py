"""pyrtlsdr ↔ librtlsdr 2.x compatibility shim (#1 hardware bring-up).

Ubuntu 24.04 ships a librtlsdr that lacks several symbols pyrtlsdr resolves
at import time; without the shim, real-hardware support silently degrades to
"not installed".
"""

import ctypes
import ctypes.util

import pytest

from radiotui.sdr import rtlsdr_device
from radiotui.sdr.rtlsdr_compat import apply_librtlsdr_compat


def test_compat_patch_is_idempotent():
    assert apply_librtlsdr_compat() is True
    assert apply_librtlsdr_compat() is True


@pytest.mark.skipif(
    ctypes.util.find_library("rtlsdr") is None,
    reason="librtlsdr not installed on this system",
)
def test_binding_imports_against_system_librtlsdr():
    """With the shim applied, pyrtlsdr imports even against librtlsdr 2.x."""
    assert rtlsdr_device._HAS_RTLSDR, rtlsdr_device._IMPORT_ERROR
    assert rtlsdr_device.RtlSdr is not None


def test_missing_symbol_lookup_returns_noop_stub():
    """Direct unit proof of the shim: unknown rtlsdr_* symbols become no-ops."""

    class FakeLib:
        pass

    lib = FakeLib()
    resolved = ctypes.CDLL.__getattr__(lib, "rtlsdr_set_dithering")
    assert callable(resolved)
    # pyrtlsdr assigns restype/argtypes right after resolving; must be supported
    resolved.restype = ctypes.c_int
    resolved.argtypes = [ctypes.c_void_p, ctypes.c_int]
    assert resolved(None, 1) == 0
    with pytest.raises(AttributeError):
        ctypes.CDLL.__getattr__(lib, "definitely_not_a_real_function_name")


@pytest.mark.skipif(not rtlsdr_device._HAS_RTLSDR, reason="pyrtlsdr unavailable")
def test_detect_real_devices_does_not_crash():
    found = rtlsdr_device.detect_real_devices(max_probe=1)
    assert isinstance(found, list)
