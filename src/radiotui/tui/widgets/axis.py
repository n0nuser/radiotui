"""Shared frequency-axis row for the spectrum and waterfall widgets (#10).

One terminal row of round-number ticks (1/2/2.5/5/10 ladder) whose labels
never touch; narrow terminals get fewer ticks instead of truncated ones.
"""

from __future__ import annotations

import math

from rich.segment import Segment
from rich.style import Style

LINE_CHAR = "─"
TICK_CHAR = "┴"
MARK_CHAR = "│"
AXIS_STYLE = Style.parse("#5b6b8c")
MARK_STYLE = Style.parse("#8fd3ff bold")

#: Minimum columns between the end of one label and the start of the next.
_LABEL_GAP = 1
#: Ladder multipliers across decades of hertz, ascending.
_LADDER = sorted(
    multiplier * 10.0**exp for exp in range(-4, 11) for multiplier in (1.0, 2.0, 2.5, 5.0)
)


def _label_for(hz: float, step_hz: float) -> str:
    """Tick label in MHz with just enough decimals to show the step."""
    step_mhz = step_hz / 1e6
    decimals = 0
    value = step_mhz
    while decimals < 6 and abs(value - round(value)) > 1e-9:
        value *= 10.0
        decimals += 1
    return f"{hz / 1e6:.{decimals}f}"


def _column_of(hz: float, lo_hz: float, span_hz: float, width: int) -> int:
    return int(round((hz - lo_hz) / span_hz * (width - 1)))


def frequency_ticks(lo_hz: float, hi_hz: float, width: int) -> list[tuple[int, str]]:
    """Round-number ticks as ``(column, label)``, labels guaranteed not to touch.

    Walks the ladder upward until every label fits with a gap; a degenerate or
    tiny axis yields an empty list rather than crowding it.
    """
    if width < 4 or hi_hz <= lo_hz:
        return []
    span = hi_hz - lo_hz
    raw_step = span / max(width // 6, 1)
    start_index = next((i for i, step in enumerate(_LADDER) if step >= raw_step), len(_LADDER) - 1)
    for step in _LADDER[start_index:]:
        placed: list[tuple[int, str]] = []
        first = math.ceil(lo_hz / step) * step
        tick = first
        # Last occupied column: the tick mark plus its label written to the right.
        occupied_until = -_LABEL_GAP - 1
        while tick <= hi_hz:
            column = _column_of(tick, lo_hz, span, width)
            label = _label_for(tick, step)
            if column + len(label) > width - 1:
                break  # this and later ticks would overflow; drop them, not the layout
            if placed and column - occupied_until < _LABEL_GAP:
                break
            placed.append((column, label))
            occupied_until = column + len(label)
            tick += step
        if len(placed) >= 2:
            return placed
    return []


def axis_segments(
    lo_hz: float,
    hi_hz: float,
    width: int,
    selected_hz: float | None = None,
) -> list[Segment]:
    """Render the shared axis row: rule line, tick marks and MHz labels."""
    if width <= 0:
        return [Segment("", None)]
    chars = [LINE_CHAR] * width
    ticks = frequency_ticks(lo_hz, hi_hz, width)
    for column, label in ticks:
        chars[column] = TICK_CHAR
        for offset, char in enumerate(label):
            pos = column + 1 + offset
            if pos < width:
                chars[pos] = char
    selected_column = (
        _column_of(selected_hz, lo_hz, hi_hz - lo_hz, width)
        if selected_hz is not None and hi_hz > lo_hz
        else -1
    )
    if 0 <= selected_column < width:
        chars[selected_column] = MARK_CHAR
    segments: list[Segment] = []
    run: list[str] = []
    run_style: Style | None = AXIS_STYLE
    for i, char in enumerate(chars):
        style = MARK_STYLE if i == selected_column else AXIS_STYLE
        if style is run_style:
            run.append(char)
            continue
        segments.append(Segment("".join(run), run_style))
        run = [char]
        run_style = style
    segments.append(Segment("".join(run), run_style))
    return segments
