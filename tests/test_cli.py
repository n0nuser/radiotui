"""Headless CLI commands end to end on the simulator."""

import threading

import numpy as np
import pytest

from radiotui.cli import main
from radiotui.sdr.simulator import SimulatedDevice


def test_listen_honours_seconds():
    """`listen --seconds N` is advertised in --help; it must stop on its own."""
    done = threading.Event()
    result = []

    def run() -> None:
        result.append(main(["listen", "--sim", "--seconds", "1", "100M"]))
        done.set()

    threading.Thread(target=run, daemon=True).start()
    assert done.wait(timeout=10.0), "listen ignored --seconds"
    assert result == [0]


class UnpluggedDevice(SimulatedDevice):
    def read_samples(self, count: int) -> np.ndarray:
        raise OSError("device unplugged")


@pytest.mark.parametrize("command", ["listen", "record", "tuner"])
def test_monitor_failures_are_printed(monkeypatch, capsys, command):
    monkeypatch.setattr("radiotui.cli.open_device_or_exit", lambda sim: UnpluggedDevice())
    assert main([command, "--sim", "100M"]) == 0
    assert "device unplugged" in capsys.readouterr().out
