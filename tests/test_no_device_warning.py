"""Falling back to the simulator must be impossible to miss.

The warning used to be a log line, but the log pane is `display: none` in the
default radio view, so it was never rendered at all — the only cue that nothing
was real radio was a three-character SIM badge.
"""

from textual.widgets import Static

from radiotui.sdr.manager import DiagnosisStep, hardware_diagnosis
from radiotui.tui.app import NoDeviceModal, RadioTuiApp, no_device_text


def meter_text(app: RadioTuiApp) -> str:
    return str(app.query_one("#meter", Static).render())


async def test_missing_hardware_raises_a_blocking_dialog():
    app = RadioTuiApp(force_sim=True)
    # force_sim=True is how the test suite avoids real hardware, but the app
    # must treat "asked for the simulator" and "fell back to it" differently.
    app._force_sim = False
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause(0.3)
        assert isinstance(app.screen, NoDeviceModal), "no dialog: the fallback was silent"

        rendered = str(app.screen.query_one("#no-device", Static).render())
        assert "NO SDR HARDWARE FOUND" in rendered
        assert "will be real radio" in rendered, "must say the signals are not real"
        assert "89.0" in rendered, "the dialog should name the fake carriers"


async def test_dialog_ignores_stray_keys_and_needs_a_deliberate_choice():
    app = RadioTuiApp(force_sim=True)
    app._force_sim = False
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause(0.3)
        for key in ("enter", "escape", "space", "t"):
            await pilot.press(key)
            await pilot.pause(0.05)
            assert isinstance(app.screen, NoDeviceModal), f"{key!r} dismissed the warning"

        await pilot.press("s")
        await pilot.pause(0.2)
        assert not isinstance(app.screen, NoDeviceModal), "'s' should continue to the simulator"


async def test_no_dialog_when_the_simulator_was_asked_for():
    """`--sim` is an informed choice; nagging about it would train people to dismiss."""
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause(0.3)
        assert not isinstance(app.screen, NoDeviceModal)


async def test_status_bar_says_simulated_in_words():
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause(0.3)
        assert "SIMULATED" in meter_text(app)
        assert "NOT REAL RADIO" in meter_text(app)


def test_diagnosis_names_the_failing_layer_and_its_fix():
    steps = hardware_diagnosis()
    assert steps, "the diagnosis must always report something"
    failed = [step for step in steps if not step.ok]
    for step in failed:
        assert step.detail, f"{step.label} failed without saying why"
        assert step.fix, f"{step.label} failed without suggesting a fix"


def test_diagnosis_text_renders_every_step():
    steps = [
        DiagnosisStep("pyrtlsdr binding", True, "imported"),
        DiagnosisStep("RTL-SDR device", False, "enumerated no devices", "check lsusb"),
    ]
    rendered = str(no_device_text(steps))
    assert "pyrtlsdr binding" in rendered
    assert "enumerated no devices" in rendered
    assert "check lsusb" in rendered
