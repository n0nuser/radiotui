"""Demodulation and audio conditioning (numpy only).

Receive chain (#18): channel-filter to the band's ``channel_bw_hz``, decimate
with a triangular (cascaded-boxcar) kernel whose nulls land on the aliases,
demodulate, then resample to the output rate. De-emphasis is a truncated-FIR
single-pole equivalent carried across blocks — no per-sample Python loop —
and AM level is held by a slow AGC instead of per-block peak normalization.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from radiotui.core.models import DemodMode


def frequency_shift(iq: np.ndarray, offset_hz: float, fs: float) -> np.ndarray:
    if offset_hz == 0.0:
        return iq
    t = np.arange(len(iq)) / fs
    return iq * np.exp(-2j * np.pi * offset_hz * t)


def decimate(x: np.ndarray, factor: int) -> np.ndarray:
    """Anti-alias and downsample by ``factor`` with a triangular kernel.

    The kernel is a cascaded double boxcar, which costs one convolution but
    puts far more attenuation on the fold-in bands than a single average.
    """
    if factor <= 1:
        return x
    kernel = np.convolve(np.ones(factor), np.ones(factor), mode="full")
    kernel /= kernel.sum()
    filtered = np.convolve(x, kernel, mode="valid")
    return filtered[::factor]


def deemphasize_loop(audio: np.ndarray, fs: float, tau_s: float) -> np.ndarray:
    """Reference single-pole de-emphasis; kept for verification and benchmarks."""
    alpha = np.exp(-1.0 / (tau_s * fs))
    out = np.empty_like(audio)
    acc = 0.0
    for i, sample in enumerate(audio):
        acc = alpha * acc + (1.0 - alpha) * sample
        out[i] = acc
    return out


def fir_taps_for_pole(alpha: float, floor: float = 1e-5) -> np.ndarray:
    """Truncated impulse response of the y=a*y+x pole; length for ``floor`` decay."""
    span = int(np.ceil(np.log(floor) / np.log(alpha)))
    n = np.arange(span, dtype=np.float64)
    return (1.0 - alpha) * alpha**n


def deemphasize_fir(
    audio: np.ndarray,
    fs: float,
    tau_s: float,
    history: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Vectorized de-emphasis: the pole as a truncated FIR with carry-in state.

    Returns ``(filtered, new_history)`` where ``new_history`` feeds the next
    block so the response is continuous across calls.
    """
    taps = fir_taps_for_pole(np.exp(-1.0 / (tau_s * fs)))
    if history is None or len(history) != len(taps) - 1:
        history = np.zeros(len(taps) - 1)
    padded = np.concatenate([history, audio])
    out = np.convolve(padded, taps, mode="valid")
    return out, padded[-(len(taps) - 1) :] if len(taps) > 1 else np.zeros(0)


def demod_nfm(iq: np.ndarray, fs: float, deviation_hz: float = 5_000.0) -> np.ndarray:
    phase = np.angle(iq[1:] * np.conj(iq[:-1]))
    audio = phase * fs / (2 * np.pi * deviation_hz)
    return np.clip(audio, -1.5, 1.5)


def demod_wfm(
    iq: np.ndarray,
    fs: float,
    deviation_hz: float = 75_000.0,
    state: DemodState | None = None,
) -> np.ndarray:
    """WFM with 50 us de-emphasis as a state-carried FIR (vectorized)."""
    audio = demod_nfm(iq, fs, deviation_hz)
    history = None if state is None else state.deemph_history
    out, history = deemphasize_fir(audio, fs, 50e-6, history)
    if state is not None:
        state.deemph_history = history
    return out


def demod_am(iq: np.ndarray, fs: float, state: DemodState | None = None) -> np.ndarray:
    """Envelope detection with slow AGC instead of per-block peak normalization."""
    envelope = np.abs(iq)
    # Carrier/offset estimator: ~4 ms of audio so it tracks the tuner's DC
    # offset at any channel rate without eating the modulation.
    window = max(int(fs * 0.004) | 1, 9)
    dc = np.convolve(envelope, np.ones(window) / window, mode="same")
    audio = envelope - dc
    rms = float(np.sqrt(np.mean(audio**2))) + 1e-9
    target = 0.35
    instant = min(max(target / rms, 0.05), 16.0)
    if state is None:
        gain = instant
    elif state.am_gain is None:
        gain = state.am_gain = instant
    else:
        step_db = abs(20.0 * np.log10(instant / state.am_gain))
        # Big steps (signal appeared/vanished) track at once; small drift is
        # smoothed so the level does not pump between blocks.
        gain = instant if step_db > 12.0 else state.am_gain + 0.25 * (instant - state.am_gain)
        state.am_gain = gain
    return audio * gain


#: Noise/capture floor per mode: demodulation breaks below these rates.
MIN_CHANNEL_RATE_HZ = {
    DemodMode.NFM: 30_000.0,
    DemodMode.WFM: 240_000.0,
    DemodMode.AM: 16_000.0,
}

DEFAULT_CHANNEL_BW_HZ = {
    DemodMode.NFM: 12_500.0,
    DemodMode.WFM: 200_000.0,
    DemodMode.AM: 8_330.0,
}


@dataclass
class DemodState:
    """Per-monitor demodulation memory: de-emphasis history and AGC gain."""

    deemph_history: np.ndarray = field(default_factory=lambda: np.zeros(0))
    am_gain: float | None = None


def channel_decimation(fs: float, channel_bw_hz: float, mode: DemodMode) -> int:
    """Decimate to the channel bandwidth or the mode's demodulation floor,
    whichever is higher — stays correct in HF mode where the rate is forced."""
    target = max(channel_bw_hz, MIN_CHANNEL_RATE_HZ[mode])
    return max(1, int(fs // target))


def channel_decimate(
    iq: np.ndarray, fs: float, channel_bw_hz: float, mode: DemodMode
) -> np.ndarray:
    """Low-pass to the channel bandwidth and decimate to the channel rate."""
    factor = channel_decimation(fs, channel_bw_hz, mode)
    return decimate(iq, factor)


def channel_audio(
    iq: np.ndarray,
    fs: float,
    mode: DemodMode,
    output_rate_hz: int = 48_000,
    channel_bw_hz: float | None = None,
    state: DemodState | None = None,
) -> np.ndarray:
    """Full receive chain: channelize, demodulate, resample to output_rate_hz."""
    bw = DEFAULT_CHANNEL_BW_HZ[mode] if channel_bw_hz is None else channel_bw_hz
    factor = channel_decimation(fs, bw, mode)
    chan_fs = fs / factor
    low = channel_decimate(iq, fs, bw, mode)
    if mode is DemodMode.WFM:
        audio = demod_wfm(low, chan_fs, state=state)
    elif mode is DemodMode.AM:
        audio = demod_am(low, chan_fs, state=state)
    else:
        audio = demod_nfm(low, chan_fs)
    if len(audio) < 8:
        return np.zeros(output_rate_hz // 10, dtype=np.float32)
    target_len = max(int(len(audio) * output_rate_hz / chan_fs), 1)
    src_idx = np.linspace(0.0, len(audio) - 1, num=target_len)
    return np.interp(src_idx, np.arange(len(audio)), audio).astype(np.float32)


def rssi_dbfs(iq: np.ndarray) -> float:
    power = float(np.mean(np.abs(iq) ** 2))
    return 10.0 * np.log10(power + 1e-20)


def audio_to_pcm16(audio: np.ndarray) -> bytes:
    scaled = np.clip(audio, -1.0, 1.0) * 32767.0
    return scaled.astype("<i2").tobytes()


def scale_pcm16(pcm: bytes, gain: float) -> bytes:
    """Scale little-endian int16 PCM by a linear gain factor, clipping to full scale."""
    if gain == 1.0:
        return pcm
    samples = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2")
    out = np.clip(samples.astype(np.float32) * gain, -32768.0, 32767.0)
    return out.astype("<i2").tobytes()
