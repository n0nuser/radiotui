"""Bar-style spectrum widget."""

from __future__ import annotations

import numpy as np
from rich.segment import Segment
from rich.style import Style
from textual.strip import Strip
from textual.widget import Widget

BLOCKS = " ▁▂▃▄▅▆▇█"
COLD_STYLE = Style.parse("#3b4d8f")
HOT_GRADIENT = ["#00e676", "#76ff03", "#ffee58", "#ff9800", "#ff5722", "#e91e63"]
HOT_STYLES = [Style.parse(c) for c in HOT_GRADIENT]
HOT_BOLD_STYLES = [Style.parse(c + " bold") for c in HOT_GRADIENT]


class SpectrumBar(Widget):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.freqs: np.ndarray | None = None
        self.power: np.ndarray | None = None
        self.floor_db = -100.0
        self.threshold_db = -90.0
        self.active_freqs: list[float] = []

    def update_frame(
        self,
        freqs: np.ndarray,
        power: np.ndarray,
        floor_db: float,
        threshold_db: float,
        active_freqs: list[float],
    ) -> None:
        self.freqs = freqs
        self.power = power
        self.floor_db = floor_db
        self.threshold_db = threshold_db
        self.active_freqs = active_freqs
        self.refresh()

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        height = self.size.height or 1
        if self.freqs is None or len(self.freqs) == 0 or width <= 0 or y >= height - 1:
            return Strip.blank(width)

        db_lo = self.floor_db - 5.0
        db_hi = max(self.floor_db + 45.0, float(np.max(self.power)))
        level_row = height - 2 - y

        cols = np.array_split(np.arange(len(self.freqs)), width)
        segments: list[Segment] = []
        for bin_idx in cols:
            if len(bin_idx) == 0:
                segments.append(Segment(" ", None))
                continue
            peak_db = float(np.max(self.power[bin_idx]))
            frac = (peak_db - db_lo) / (db_hi - db_lo)
            frac = min(max(frac, 0.0), 1.0)
            bar_height = frac * (height - 1)
            if bar_height < level_row + 0.001:
                segments.append(Segment(" ", None))
                continue
            block_idx = min(int((bar_height - level_row) * 8), 7)
            char = BLOCKS[block_idx]
            is_active = self._column_active(bin_idx)
            if peak_db >= self.threshold_db:
                hot_idx = min(int((peak_db - self.threshold_db) / 12.0), len(HOT_STYLES) - 1)
                style = HOT_BOLD_STYLES[hot_idx] if is_active else HOT_STYLES[hot_idx]
            else:
                style = COLD_STYLE
            segments.append(Segment(char, style))
        return Strip(segments, width)

    def _column_active(self, bin_idx: np.ndarray) -> bool:
        lo = float(self.freqs[bin_idx[0]])
        hi = float(self.freqs[bin_idx[-1]])
        return any(lo <= f <= hi for f in self.active_freqs)
