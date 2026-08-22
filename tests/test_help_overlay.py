"""Issue #11: `?` help overlay listing every binding and band key."""

from textual.binding import Binding
from textual.widgets import Static

from radiotui.config import BANDS
from radiotui.tui.app import HelpModal, RadioTuiApp, build_help_text


def test_help_lists_every_visible_binding_once():
    text = build_help_text()
    for binding in RadioTuiApp.BINDINGS:
        if not binding.show:
            continue
        assert binding.description in text, f"missing: {binding.description}"


def test_help_lists_band_keys_from_bands():
    text = build_help_text()
    for i, name in enumerate(sorted(BANDS), start=1):
        assert BANDS[name].label in text
        assert str(i) in text


async def test_question_mark_opens_and_escape_closes():
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        assert not isinstance(app.screen, HelpModal)
        await pilot.press("?")
        await pilot.pause(0.2)
        assert isinstance(app.screen, HelpModal)
        rendered = str(app.screen.query_one(Static).render())
        assert "FM Broadcast" in rendered
        await pilot.press("escape")
        await pilot.pause(0.2)
        assert not isinstance(app.screen, HelpModal)


async def test_q_also_closes():
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("?")
        await pilot.pause(0.2)
        await pilot.press("q")
        await pilot.pause(0.2)
        assert not isinstance(app.screen, HelpModal)


def test_footer_gains_only_one_help_entry():
    help_bindings = [b for b in RadioTuiApp.BINDINGS if b.action == "help"]
    assert len(help_bindings) == 1
    assert isinstance(help_bindings[0], Binding)
