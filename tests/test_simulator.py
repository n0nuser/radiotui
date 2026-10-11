"""Simulated device: its IQ must look like one continuous transmission."""

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
