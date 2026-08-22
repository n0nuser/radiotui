"""Hardware option plumbing: HF mode helpers, device capability guards, CLI flags."""

import numpy as np
import pytest

from radiotui.cli import apply_flag_defaults, build_parser
from radiotui.config import (
    HF_SAMPLE_RATE_HZ,
    ScannerSettings,
    band_by_name,
    band_needs_hf,
    effective_sample_rate,
    enable_hf,
    freq_needs_hf,
)
from radiotui.sdr.base import SdrDevice
from radiotui.sdr.manager import open_device
from radiotui.sdr.simulator import SimulatedDevice


def test_band_needs_hf_for_new_presets():
    assert band_needs_hf(band_by_name("hf_broadcast"))
    assert band_needs_hf(band_by_name("hf_ham_40m"))
    assert band_needs_hf(band_by_name("hf_ham_80m"))
    assert not band_needs_hf(band_by_name("fm_broadcast"))
    assert not band_needs_hf(band_by_name("uhf_ham"))


def test_freq_needs_hf_boundary():
    assert freq_needs_hf(23.9e6)
    assert not freq_needs_hf(24.1e6)


def test_effective_sample_rate_clamps_to_direct_sampling_clock():
    scanner = ScannerSettings(sample_rate_hz=1_024_000.0)
    assert effective_sample_rate(scanner) == 1_024_000.0
    assert scanner.sample_rate_hz == 1_024_000.0
    enable_hf(True, scanner)
    assert scanner.hf_mode
    assert effective_sample_rate(scanner) == pytest.approx(HF_SAMPLE_RATE_HZ)
    assert scanner.sample_rate_hz == 1_024_000.0
    enable_hf(False, scanner)
    assert not scanner.hf_mode
    assert effective_sample_rate(scanner) == 1_024_000.0


def test_sim_device_reports_unsupported_capabilities():
    dev = SimulatedDevice(carriers=[])
    assert dev.set_bias_tee(True) is False
    assert dev.set_freq_correction(10) is False
    assert dev.set_offset_tuning(True) is False
    assert dev.set_hf_mode(True) is False


class _MinimalFake(SdrDevice):
    name = "fake"

    def open(self): ...

    def close(self): ...

    @property
    def center_freq_hz(self):
        return 0.0

    def set_center_freq_hz(self, freq_hz): ...

    def set_sample_rate_hz(self, rate_hz): ...

    def set_gain_db(self, gain_db): ...

    def read_samples(self, count):
        return np.zeros(count, dtype=np.complex128)


def test_base_class_defaults_are_safe_noops():
    dev = _MinimalFake()
    assert dev.set_bias_tee(True) is False
    assert dev.set_freq_correction(5) is False
    assert dev.set_offset_tuning(False) is False
    assert dev.set_hf_mode(True) is False


@pytest.mark.parametrize(
    ("command", "extra"),
    [
        ("devices", []),
        ("scan", []),
        ("listen", ["145.500e6"]),
        ("record", ["446.00625e6"]),
        ("tuner", ["145.500e6"]),
        ("tui", []),
    ],
)
def test_cli_hw_flags_on_all_commands(command, extra):

    args = apply_flag_defaults(build_parser().parse_args([command, "--bias-tee"] + extra))
    assert args.bias_tee
    assert args.ppm == 0
    assert not args.offset_tune

    args = apply_flag_defaults(
        build_parser().parse_args([command, "--ppm", "-70", "--offset-tune"] + extra)
    )
    assert args.ppm == -70
    assert args.offset_tune
    assert not args.bias_tee


@pytest.mark.parametrize("position", ["before", "after"])
@pytest.mark.parametrize(
    ("flags", "check"),
    [
        (["--sim"], lambda a: a.sim is True),
        (["--bias-tee"], lambda a: a.bias_tee is True),
        (["--ppm", "-70"], lambda a: a.ppm == -70),
        (["--offset-tune"], lambda a: a.offset_tune is True),
    ],
)
@pytest.mark.parametrize(
    ("command", "extra"),
    [
        ("devices", []),
        ("scan", []),
        ("listen", ["145.500e6"]),
        ("record", ["446.00625e6"]),
        ("tuner", ["145.500e6"]),
        ("tui", []),
    ],
)
def test_cli_flags_work_in_both_positions(command, extra, flags, check, position):
    """Issue #9: every common flag must work before AND after the subcommand."""

    tail = [command] + extra
    argv = flags + tail if position == "before" else tail + flags
    args = apply_flag_defaults(build_parser().parse_args(argv))
    assert check(args)


def test_cli_flag_defaults_resolved_once():

    args = apply_flag_defaults(build_parser().parse_args(["scan", "--band", "pmr446"]))
    assert not args.sim
    assert not args.bias_tee
    assert args.ppm == 0
    assert not args.offset_tune


def test_cli_main_parser_accepts_hw_flags():

    args = apply_flag_defaults(build_parser().parse_args(["--bias-tee"]))
    assert args.bias_tee


def test_cli_scan_has_autonomous_flag():

    args = build_parser().parse_args(["scan", "--autonomous", "--band", "pmr446"])
    assert args.autonomous


def test_open_device_sim_accepts_capability_calls():
    opened = open_device(prefer_real=False)
    assert not opened.is_real
    opened.device.set_bias_tee(False)
    opened.device.set_hf_mode(True)
