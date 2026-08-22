"""Demodulation and audio conditioning (numpy only)."""

from __future__ import annotations

import numpy as np

from radiotui.core.models import DemodMode


def frequency_shift(iq: np.ndarray, offset_hz: float, fs: float) -> np.ndarray:
    if offset_hz == 0.0:
        return iq
    t = np.arange(len(iq)) / fs
    return iq * np.exp(-2j * np.pi * offset_hz * t)


def decimate(x: np.ndarray, factor: int) -> np.ndarray:
    if factor <= 1:
        return x
    kernel = np.ones(factor) / factor
    filtered = np.convolve(x, kernel, mode="valid")
    return filtered[::factor]


def _deemphasize(audio: np.ndarray, fs: float, tau_s: float) -> np.ndarray:
    alpha = np.exp(-1.0 / (tau_s * fs))
    out = np.empty_like(audio)
    acc = 0.0
    for i, sample in enumerate(audio):
        acc = alpha * acc + (1.0 - alpha) * sample
        out[i] = acc
    return out


def demod_nfm(iq: np.ndarray, fs: float, deviation_hz: float = 5_000.0) -> np.ndarray:
    phase = np.angle(iq[1:] * np.conj(iq[:-1]))
    audio = phase * fs / (2 * np.pi * deviation_hz)
    return np.clip(audio, -1.5, 1.5)


def demod_wfm(iq: np.ndarray, fs: float, deviation_hz: float = 75_000.0) -> np.ndarray:
    audio = demod_nfm(iq, fs, deviation_hz)
    tau = 50e-6
    return _deemphasize(audio, fs, tau)


def demod_am(iq: np.ndarray, fs: float) -> np.ndarray:
    envelope = np.abs(iq)
    dc = np.convolve(envelope, np.ones(64) / 64, mode="same")
    audio = envelope - dc
    peak = np.max(np.abs(audio)) + 1e-9
    return audio / peak


DEMOD_FUNCS = {
    DemodMode.NFM: demod_nfm,
    DemodMode.WFM: demod_wfm,
    DemodMode.AM: demod_am,
}

CHANNEL_DECIMATION = {DemodMode.NFM: 8, DemodMode.WFM: 4, DemodMode.AM: 8}


def channel_audio(
    iq: np.ndarray,
    fs: float,
    mode: DemodMode,
    output_rate_hz: int = 48_000,
) -> np.ndarray:
    """Full receive chain: channelize, demodulate, resample to output_rate_hz."""
    factor = CHANNEL_DECIMATION[mode]
    chan_fs = fs / factor
    low = decimate(iq, factor)
    demod = DEMOD_FUNCS[mode]
    audio = demod(low, chan_fs)
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
