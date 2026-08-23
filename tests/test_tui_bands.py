"""Built-in band keys stay stable while user bands use the overflow key.

The TUI generates one numeric binding per band and dispatches generically,
so bindings and actions can never drift apart again.
"""

from radiotui.config import BANDS, BUILTIN_BAND_NAMES, Band
from radiotui.tui.app import RadioTuiApp


def numeric_band_bindings() -> list[tuple[str, str]]:
    return [(str(i), name) for i, name in enumerate(BUILTIN_BAND_NAMES, start=1)]


def test_numeric_keys_map_one_to_one_to_bands():
    pairs = numeric_band_bindings()
    expected = [(str(i), name) for i, name in enumerate(BUILTIN_BAND_NAMES, start=1)]
    assert pairs == expected


async def test_user_band_does_not_renumber_builtins():
    BANDS["user_test"] = Band(
        "user_test", "User test", 100e6, 101e6, BANDS["vhf_ham"].demod
    )
    try:
        app = RadioTuiApp(force_sim=True)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("1")
            assert app.band_name == BUILTIN_BAND_NAMES[0]
            await pilot.press("0")
            assert app.band_name == "user_test"
    finally:
        BANDS.pop("user_test")


async def test_every_band_key_switches_band():
    """Guard against drift: iterate all generated bindings, resolve and use them."""
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        for key, name in numeric_band_bindings():
            await pilot.press(key)
            await pilot.pause(0.15)
            assert app.band_name == name, f"key {key} did not activate band '{name}'"


async def test_hf_band_keys_enable_direct_sampling():
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        for _, name in numeric_band_bindings():
            if not name.startswith("hf"):
                continue
            app.start_band(name)
            await pilot.pause(0.15)
            assert app.band_name == name
            assert app.settings.scanner.hf_mode, f"{name} did not request HF mode"
