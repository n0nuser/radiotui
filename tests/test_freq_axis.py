"""Issue #10: frequency axis rendered under the spectrum."""

import numpy as np
from rich.segment import Segment
from textual.widgets import Static

from radiotui.core.models import ScanState, SpectrumFrame
from radiotui.tui.app import RadioTuiApp
from radiotui.tui.widgets.axis import MARK_STYLE, axis_segments, frequency_ticks


def test_ticks_are_round_numbers_that_fit():
    ticks = frequency_ticks(87.5e6, 108e6, 60)
    assert len(ticks) >= 2
    previous_end = -2
    for column, label in ticks:
        assert 0 <= column < 60
        assert column > previous_end, "labels must never touch"
        previous_end = column + len(label)
        # Round numbers: one decimal suffices across the FM broadcast span.
        value_mhz = float(label)
        assert abs(value_mhz * 10 - round(value_mhz * 10)) < 1e-6


def test_narrower_terminal_yields_fewer_ticks():
    wide = frequency_ticks(87.5e6, 108e6, 100)
    narrow = frequency_ticks(87.5e6, 108e6, 16)
    assert len(narrow) < len(wide)
    assert len(narrow) >= 0  # may degrade to no ticks at all rather than crowd


def test_degenerate_spans_yield_no_ticks():
    assert frequency_ticks(100e6, 100e6, 80) == []
    assert frequency_ticks(108e6, 87.5e6, 80) == []
    assert frequency_ticks(87.5e6, 108e6, 3) == []


def test_small_spans_get_sub_mhz_steps():
    ticks = frequency_ticks(7.0e6, 7.3e6, 60)
    assert any(label.startswith("7.1") for _, label in ticks)


def test_axis_segments_render_rule_and_labels():
    segments = axis_segments(87.5e6, 108e6, 60)
    text = "".join(s.text for s in segments)
    assert len(text) == 60
    assert "┴" in text
    assert any(char.isdigit() for char in text)


def test_selected_channel_marks_the_axis_column():
    plain = axis_segments(87.5e6, 108e6, 60)
    marked = axis_segments(87.5e6, 108e6, 60, selected_hz=97.5e6)
    marked_text = "".join(s.text for s in marked)
    assert "│" in marked_text
    assert any(segment.style == MARK_STYLE for segment in marked)
    assert not any(segment.style == MARK_STYLE for segment in plain)


async def test_spectrum_bottom_row_is_the_axis():
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        if app.sweeper is not None:
            app.sweeper.stop()
        await pilot.pause(0.3)
        freqs = np.linspace(87.5e6, 108e6, 512)
        power = np.full_like(freqs, -60.0)
        spectrum = app.query_one("#spectrum")
        spectrum.update_frame(freqs, power, -62.0, -50.0, [])
        height = spectrum.size.height
        axis = spectrum.render_line(height - 1)
        bars = spectrum.render_line(height - 3)
        assert "┴" in axis.text
        assert "┴" not in bars.text


async def test_meter_reports_the_db_window_in_use():
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        frame = SpectrumFrame(freqs_hz=np.array([1e8, 1.01e8]), power_db=np.array([-60.0, -30.0]))
        app.last_state = ScanState(
            frame=frame,
            channels=[],
            noise_floor_db=-62.0,
            threshold_db=-50.0,
            sweeps_done=1,
            elapsed=0.0,
        )
        app.refresh_status()
        meter = str(app.query_one("#meter", Static).render())
        assert "dB -67→-17" in meter


def test_axis_segment_runs_merge_to_full_width():
    segments = axis_segments(430e6, 440e6, 47)
    total = sum(len(s.text) for s in segments)
    assert total == 47
    assert all(isinstance(s, Segment) for s in segments)
