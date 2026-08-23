# 0001 — pyrtlsdr compatibility shim over librtlsdr 2.x

- Status: Accepted
- Date: 2026-08-22

## Context

Ubuntu 24.04 ships `librtlsdr2` 2.0.1, which dropped symbols that `pyrtlsdr`
requires at import time (`rtlsdr_set_dithering`, GPIO helpers,
`rtlsdr_set_and_get_tuner_bandwidth`).
`import rtlsdr` fails with `AttributeError: undefined symbol`, so the whole
real-hardware path is dead on current Debian/Ubuntu.

## Alternatives considered

- Pin or build an older librtlsdr (0.6/1.x): fights the distro, needs sudo on
  every machine, and blocks `apt` upgrades.
- Vendor ctypes bindings to librtlsdr and drop pyrtlsdr: largest change, loses
  the maintained wrapper for no functional gain at our call surface.
- Move to SoapySDR: a much bigger abstraction for one dongle family.
- Forwarder shim library: zero product code, fixes any machine in ~10 lines of
  shell, keeps system packages intact.

## Decision

Keep pyrtlsdr as the API and ship a tiny ELF forwarder library that re-exports
the missing symbols from the system librtlsdr, placed first on
`LD_LIBRARY_PATH`.
The full recipe lives in [`../../README.md`](../../README.md)
("Linux hardware setup"); the runtime detection logic is
`src/radiotui/sdr/rtlsdr_compat.py`.

Functional notes: dithering/GPIO stubs are safe no-ops for our use, bias-tee
still works through the real system export, and offset tuning remains
unsupported because 2.0.1 genuinely lacks it.

## Consequences

Every fresh Ubuntu 24.04 machine needs the one-time shim step documented in the
README before real hardware works.
The simulator path never touches librtlsdr, so tests are unaffected either way.
