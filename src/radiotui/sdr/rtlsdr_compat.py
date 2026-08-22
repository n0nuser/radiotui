"""Compatibility shim between pyrtlsdr and stripped-down librtlsdr builds.

Ubuntu 24.04's librtlsdr 2.x dropped legacy API entry points (GPIO access,
dithering, ``rtlsdr_set_and_get_tuner_bandwidth``, ...) that pyrtlsdr still
resolves eagerly at import time, making ``import rtlsdr`` crash with
``undefined symbol`` even though core reception works fine.

This module installs a tolerant attribute lookup on ``ctypes.CDLL`` so any
missing ``rtlsdr_*`` symbol becomes a harmless no-op returning 0. Core
symbols are unaffected, and on full-featured libraries the shim changes
nothing.
"""

from __future__ import annotations

import ctypes
import threading

_LOCK = threading.Lock()
_APPLIED = False


def apply_librtlsdr_compat() -> bool:
    """Allow missing ``rtlsdr_*`` symbols to resolve to no-op stubs.

    Idempotent; must run before ``import rtlsdr``.
    """
    global _APPLIED
    with _LOCK:
        if _APPLIED:
            return True
        original = ctypes.CDLL.__getattr__

        def tolerant_getattr(lib: ctypes.CDLL, name: str):
            try:
                return original(lib, name)
            except AttributeError:
                if name.startswith("rtlsdr_"):
                    return ctypes.CFUNCTYPE(ctypes.c_int)(lambda *_args: 0)
                raise

        # ctypes.CDLL.__getattr__ is being replaced deliberately; there is no
        # other way to make symbol resolution tolerant for pyrtlsdr's eager lookups.
        ctypes.CDLL.__getattr__ = tolerant_getattr  # type: ignore[method-assign] — intentional monkeypatch, see comment above
        _APPLIED = True
        return True
