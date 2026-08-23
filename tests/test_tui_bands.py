"""Issue #8: every band in BANDS must be reachable by its number key.

The TUI generates one numeric binding per band and dispatches generically,
so bindings and actions can never drift apart again.
"""

from radiotui.config import BANDS
from radiotui.tui.app import RadioTuiApp


def numeric_band_bindings() -> list[tuple[str, str]]:
    return [(str(i), name) for i, name in enumerate(sorted(BANDS)[:9], start=1)]


def test_numeric_keys_map_one_to_one_to_bands():
    pairs = numeric_band_bindings()
    expected = [(str(i), name) for i, name in enumerate(sorted(BANDS), start=1)]
    assert pairs == expected


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
