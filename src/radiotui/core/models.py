"""Shared data structures."""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from enum import Enum

import numpy as np


class DemodMode(str, Enum):
    NFM = "nfm"
    WFM = "wfm"
    AM = "am"
    USB = "usb"
    LSB = "lsb"
    CW = "cw"


#: A detected SSB peak is centred on the voice, about this far from the
#: suppressed carrier a radio tunes to (ADR 0016).
SSB_VOICE_CENTRE_HZ = 1_500.0
_CARRIER_OFFSET_HZ = {
    DemodMode.USB: -SSB_VOICE_CENTRE_HZ,
    DemodMode.LSB: +SSB_VOICE_CENTRE_HZ,
}


@dataclass(frozen=True)
class SpectrumFrame:
    freqs_hz: np.ndarray
    power_db: np.ndarray
    timestamp: float = field(default_factory=time.time)


@dataclass
class Channel:
    center_hz: float
    bandwidth_hz: float
    peak_db: float
    snr_db: float
    demod: DemodMode
    first_seen: float
    last_seen: float
    hits: int = 1
    misses: int = 0
    active: bool = False
    name: str = ""

    @property
    def listen_hz(self) -> float:
        """Where to tune to hear this peak: SSB is tuned at its carrier."""
        return self.center_hz + _CARRIER_OFFSET_HZ.get(self.demod, 0.0)

    def age_seconds(self, now: float | None = None) -> float:
        return (now if now is not None else time.time()) - self.last_seen

    def touch(self, peak_db: float, snr_db: float, bandwidth_hz: float, now: float) -> Channel:
        alpha = 0.35
        return replace(
            self,
            peak_db=self.peak_db * (1 - alpha) + peak_db * alpha,
            snr_db=max(self.snr_db, snr_db),
            bandwidth_hz=max(self.bandwidth_hz, bandwidth_hz),
            last_seen=now,
            hits=self.hits + 1,
            misses=0,
        )


@dataclass(frozen=True)
class Peak:
    center_hz: float
    bandwidth_hz: float
    peak_db: float
    snr_db: float


@dataclass(frozen=True)
class HoldRequest:
    """Autonomous-mode request to pause sweeping and monitor a live channel."""

    #: Where to tune: the carrier for SSB, else the peak.
    freq_hz: float
    demod: DemodMode
    snr_db: float
    #: The detected peak; cooldown is keyed on it, like the tracker.
    channel_hz: float | None = None

    def __post_init__(self) -> None:
        if self.channel_hz is None:
            object.__setattr__(self, "channel_hz", self.freq_hz)


@dataclass(frozen=True)
class ScanState:
    frame: SpectrumFrame
    channels: list[Channel]
    noise_floor_db: float
    threshold_db: float
    sweeps_done: int
    elapsed: float
    error: str | None = None
    hold_request: HoldRequest | None = None
