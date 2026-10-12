"""Simulated device: its IQ must look like one continuous transmission."""

import time

import numpy as np
import pytest

from radiotui.sdr.simulator import SimCarrier, SimulatedDevice


@pytest.mark.parametrize("mode", ["nfm", "wfm"])
def test_fm_carrier_is_continuous_across_reads(mode):
    """Split reads must equal one long read, or every block boundary clicks."""

    def device():
        carrier = SimCarrier(100.01e6, -10.0, mode, phase=0.3)
        return SimulatedDevice(carriers=[carrier], noise_floor_dbfs=-200.0, seed=1)

    whole = device().read_samples(8192)
    split = device()
    parts = np.concatenate([split.read_samples(4096), split.read_samples(4096)])
    np.testing.assert_allclose(parts, whole, atol=1e-6)


def test_reads_take_real_time_like_hardware():
    """The monitor relies on the device to pace it (ADR 0012); so must the simulator."""
    device = SimulatedDevice(carriers=[], seed=1)
    device.set_sample_rate_hz(1_024_000.0)
    t0 = time.monotonic()
    for _ in range(5):
        device.read_samples(65_536)  # 64 ms each at 1.024 MS/s
    assert time.monotonic() - t0 >= 0.3
