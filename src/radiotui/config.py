"""Band presets and scanner settings."""

from __future__ import annotations

from dataclasses import dataclass, field

from radiotui.core.models import DemodMode


@dataclass(frozen=True)
class Band:
    name: str
    label: str
    start_hz: float
    end_hz: float
    demod: DemodMode
    channel_bw_hz: float = 12_500.0


BANDS: dict[str, Band] = {
    "fm_broadcast": Band("fm_broadcast", "FM Broadcast", 87.5e6, 108.0e6, DemodMode.WFM, 200_000.0),
    "airband": Band("airband", "Airband (AM)", 118.0e6, 137.0e6, DemodMode.AM, 8_330.0),
    "vhf_ham": Band("vhf_ham", "2m Amateur", 144.0e6, 146.0e6, DemodMode.NFM),
    "vhf_marine": Band("vhf_marine", "Marine VHF", 156.0e6, 162.025e6, DemodMode.NFM),
    "pmr446": Band("pmr446", "PMR446", 446.00625e6, 446.09375e6, DemodMode.NFM, 6_250.0),
    "uhf_ham": Band("uhf_ham", "70cm Amateur", 430.0e6, 440.0e6, DemodMode.NFM),
}


def band_by_name(name: str) -> Band:
    try:
        return BANDS[name.lower()]
    except KeyError:
        known = ", ".join(sorted(BANDS))
        raise ValueError(f"unknown band '{name}'. Known bands: {known}") from None


@dataclass
class ScannerSettings:
    sample_rate_hz: float = 1_024_000.0
    gain_db: float | None = None
    fft_size: int = 1024
    hop_dwell_s: float = 0.12
    noise_percentile: float = 30.0
    threshold_margin_db: float = 9.0
    min_persist_frames: int = 2
    drop_after_misses: int = 6
    peak_merge_gap_bins: int = 3
    min_snr_db: float = 4.0
    history_size: int = 240


@dataclass
class AudioSettings:
    output_rate_hz: int = 48_000
    block_size: int = 65_536
    recordings_dir: str = "recordings"
    vox_threshold_dbfs: float = -32.0
    vox_hang_ms: int = 900


@dataclass
class Settings:
    scanner: ScannerSettings = field(default_factory=ScannerSettings)
    audio: AudioSettings = field(default_factory=AudioSettings)
