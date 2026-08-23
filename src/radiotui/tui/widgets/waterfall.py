"""Scrolling waterfall widget."""

from __future__ import annotations

from collections import deque

import numpy as np
from rich.segment import Segment
from rich.style import Style
from textual.strip import Strip
from textual.widget import Widget

from radiotui.tui.widgets.axis import axis_segments

LEVEL_CHARS = " .:-=+*#%@"
LEVEL_COLORS = [
    "#101038",
    "#1c2a5e",
    "#23407f",
    "#1f5f9e",
    "#1d84a2",
    "#22a884",
    "#7ad151",
    "#fde725",
    "#ffa600",
    "#ff3f3f",
]
LEVEL_STYLES = [Style.parse(c) for c in LEVEL_COLORS]
HOT_STYLES = [Style.parse(c + " reverse") for c in LEVEL_COLORS[-2:]]


class Waterfall(Widget):
    def __init__(self, max_rows: int = 200, **kwargs) -> None:
        super().__init__(**kwargs)
        self.rows: deque[tuple[np.ndarray, np.ndarray]] = deque(maxlen=max_rows)
        self.floor_db = -100.0
        self.selected_hz: float | None = None

    def push_frame(
        self,
        freqs: np.ndarray,
        power: np.ndarray,
        floor_db: float,
        selected_hz: float | None = None,
    ) -> None:
        width = max(self.size.width, 1)
        columns = np.array_split(power, width)
        reduced = np.array(
            [np.max(column) if len(column) else -200.0 for column in columns], dtype=np.float32
        )
        self.rows.append((np.array([freqs[0], freqs[-1]]), reduced))
        self.floor_db = floor_db
        self.selected_hz = selected_hz
        self.refresh()

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        height = self.size.height or 1
        if width <= 0 or not self.rows:
            return Strip.blank(width)
        if y == height - 1:
            # Bottom row used to show the oldest (least interesting) line; it
            # now carries the shared frequency axis.
            freqs = self.rows[-1][0]
            if len(freqs) < 2:
                return Strip.blank(width)
            return Strip(
                axis_segments(
                    float(freqs[0]), float(freqs[-1]), width, selected_hz=self.selected_hz
                ),
                width,
            )
        row_index_from_bottom = height - 1 - y
        if row_index_from_bottom < 0 or row_index_from_bottom >= len(self.rows):
            return Strip.blank(width)
        freqs, power = self.rows[len(self.rows) - 1 - row_index_from_bottom]
        if len(freqs) == 0:
            return Strip.blank(width)

        db_lo = self.floor_db - 5.0
        db_hi = self.floor_db + 45.0
        segments: list[Segment] = []
        for peak_db in power[:width]:
            peak_db = float(peak_db)
            frac = (peak_db - db_lo) / (db_hi - db_lo)
            level = min(max(int(frac * len(LEVEL_CHARS)), 0), len(LEVEL_CHARS) - 1)
            char = LEVEL_CHARS[level]
            if level >= len(LEVEL_STYLES) - 2:
                style = HOT_STYLES[level - (len(LEVEL_STYLES) - 2)]
            else:
                style = LEVEL_STYLES[level]
            segments.append(Segment(char, style))
        return Strip(segments, width)

    def on_resize(self) -> None:
        # Rows are intentionally stored at display resolution; discard stale
        # columns and let the next frame use the new width.
        self.rows.clear()
