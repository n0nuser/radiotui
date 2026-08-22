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
    "hf_ham_80m": Band("hf_ham_80m", "80m Amateur", 3.5e6, 3.8e6, DemodMode.AM),
    "hf_broadcast": Band(
        "hf_broadcast", "HF Broadcast (SW)", 5.9e6, 15.6e6, DemodMode.AM, 10_000.0
    ),
    "hf_ham_40m": Band("hf_ham_40m", "40m Amateur", 7.0e6, 7.3e6, DemodMode.AM),
    "fm_broadcast": Band("fm_broadcast", "FM Broadcast", 87.5e6, 108.0e6, DemodMode.WFM, 200_000.0),
    "airband": Band("airband", "Airband (AM)", 118.0e6, 137.0e6, DemodMode.AM, 8_330.0),
    "vhf_ham": Band("vhf_ham", "2m Amateur", 144.0e6, 146.0e6, DemodMode.NFM),
    "vhf_marine": Band("vhf_marine", "Marine VHF", 156.0e6, 162.025e6, DemodMode.NFM),
    "pmr446": Band("pmr446", "PMR446", 446.00625e6, 446.09375e6, DemodMode.NFM, 6_250.0),
    "uhf_ham": Band("uhf_ham", "70cm Amateur", 430.0e6, 440.0e6, DemodMode.NFM),
}

HF_MAX_TUNER_HZ = 24.0e6
DIRECT_SAMPLING_CLOCK_HZ = 28_800_000.0
HF_SAMPLE_RATE_HZ = DIRECT_SAMPLING_CLOCK_HZ / 115


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
    freq_correction_ppm: int = 0
    offset_tune: bool = False
    bias_tee: bool = False
    hf_mode: bool = False
    fft_size: int = 1024
    hop_dwell_s: float = 0.12
    noise_percentile: float = 30.0
    threshold_margin_db: float = 9.0
    min_persist_frames: int = 2
    drop_after_misses: int = 6
    peak_merge_gap_bins: int = 3
    min_snr_db: float = 4.0
    history_size: int = 240
    autonomous: bool = False
    auto_hold_min_snr_db: float = 12.0
    hold_release_s: float = 4.0
    channel_cooldown_s: float = 45.0
    max_hold_s: float = 120.0


THRESHOLD_MARGIN_RANGE = (0.0, 40.0)
MIN_SNR_RANGE = (0.0, 30.0)
DWELL_RANGE_S = (0.02, 1.0)


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


@dataclass
class AudioSettings:
    output_rate_hz: int = 48_000
    block_size: int = 65_536
    recordings_dir: str = "recordings"
    vox_threshold_dbfs: float = -32.0
    vox_hang_ms: int = 900
    volume_db: float = 0.0
    min_clip_seconds: float = 0.7


def clamp_volume_db(volume_db: float) -> float:
    return max(-60.0, min(12.0, round(volume_db, 1)))


@dataclass
class Settings:
    scanner: ScannerSettings = field(default_factory=ScannerSettings)
    audio: AudioSettings = field(default_factory=AudioSettings)


def effective_sample_rate(scanner: ScannerSettings) -> float:
    """Sample rate to actually program; HF direct sampling needs a slow safe rate."""
    if not scanner.hf_mode:
        return scanner.sample_rate_hz
    return HF_SAMPLE_RATE_HZ


def enable_hf(needs_hf: bool, scanner: ScannerSettings) -> bool:
    """Toggle HF direct-sampling mode; the effective sample rate is clamped."""
    scanner.hf_mode = needs_hf
    return needs_hf


def band_needs_hf(band: Band) -> bool:
    return band.start_hz < HF_MAX_TUNER_HZ


def freq_needs_hf(freq_hz: float) -> bool:
    return freq_hz < HF_MAX_TUNER_HZ
