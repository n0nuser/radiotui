"""Scrolling waterfall widget."""

from __future__ import annotations

from collections import deque

import numpy as np
from rich.segment import Segment
from rich.style import Style
from textual.strip import Strip
from textual.widget import Widget

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

    def push_frame(self, freqs: np.ndarray, power: np.ndarray, floor_db: float) -> None:
        self.rows.append((freqs, power))
        self.floor_db = floor_db
        self.refresh()

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        if width <= 0 or not self.rows:
            return Strip.blank(width)
        row_index_from_bottom = self.size.height - 1 - y
        if row_index_from_bottom < 0 or row_index_from_bottom >= len(self.rows):
            return Strip.blank(width)
        freqs, power = self.rows[len(self.rows) - 1 - row_index_from_bottom]
        if len(freqs) == 0:
            return Strip.blank(width)

        db_lo = self.floor_db - 5.0
        db_hi = self.floor_db + 45.0
        cols = np.array_split(np.arange(len(freqs)), width)
        segments: list[Segment] = []
        for bin_idx in cols:
            if len(bin_idx) == 0:
                segments.append(Segment(" ", None))
                continue
            peak_db = float(np.max(power[bin_idx]))
            frac = (peak_db - db_lo) / (db_hi - db_lo)
            level = min(max(int(frac * len(LEVEL_CHARS)), 0), len(LEVEL_CHARS) - 1)
            char = LEVEL_CHARS[level]
            if level >= len(LEVEL_STYLES) - 2:
                style = HOT_STYLES[level - (len(LEVEL_STYLES) - 2)]
            else:
                style = LEVEL_STYLES[level]
            segments.append(Segment(char, style))
        return Strip(segments, width)
