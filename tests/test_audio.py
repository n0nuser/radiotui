import numpy as np
import pytest

from radiotui.audio.demod import (
    audio_to_pcm16,
    channel_audio,
    demod_am,
    demod_nfm,
    frequency_shift,
    rssi_dbfs,
)
from radiotui.audio.recorder import VoxRecorder, pcm_rms_dbfs
from radiotui.config import AudioSettings
from radiotui.core.models import DemodMode


def make_nfm_iq(fs=1_024_000.0, tone_hz=1000.0, dev=5000.0, seconds=0.05, offset_hz=0.0):
    t = np.arange(int(fs * seconds)) / fs
    voice = np.sin(2 * np.pi * tone_hz * t)
    phase = 2 * np.pi * (offset_hz * t + dev * np.cumsum(voice) / fs)
    iq = 0.5 * np.exp(1j * phase)
    rng = np.random.default_rng(7)
    iq += rng.normal(0, 0.01, len(t)) + 1j * rng.normal(0, 0.01, len(t))
    return iq


def test_demod_nfm_recovers_tone():
    fs = 128_000.0
    iq = make_nfm_iq(fs=fs, tone_hz=1500.0)
    audio = demod_nfm(iq, fs, deviation_hz=5000.0)
    spectrum = np.abs(np.fft.rfft(audio * np.hanning(len(audio))))
    freqs = np.fft.rfftfreq(len(audio), 1 / fs)
    peak_hz = freqs[np.argmax(spectrum)]
    assert peak_hz == pytest.approx(1500.0, abs=250.0)


def test_frequency_shift_moves_tone():
    fs = 128_000.0
    iq = make_nfm_iq(fs=fs, tone_hz=1000.0, offset_hz=25_000.0)
    shifted = frequency_shift(iq, 25_000.0, fs)
    baseband = demod_nfm(shifted, fs)
    spectrum = np.abs(np.fft.rfft(baseband * np.hanning(len(baseband))))
    freqs = np.fft.rfftfreq(len(baseband), 1 / fs)
    assert freqs[np.argmax(spectrum)] == pytest.approx(1000.0, abs=300.0)


@pytest.mark.parametrize(
    "mode,decimation",
    [
        (DemodMode.NFM, 8),
        (DemodMode.WFM, 4),
        (DemodMode.AM, 8),
    ],
)
def test_channel_audio_output_rate(mode, decimation):
    iq = make_nfm_iq(seconds=0.2)
    audio = channel_audio(iq, 1_024_000.0, mode, output_rate_hz=48_000)
    expected = int(0.2 * 48_000) - decimation
    assert abs(len(audio) - expected) < 2000
    assert audio.dtype == np.float32


def test_demod_am_envelope():
    fs = 128_000.0
    t = np.arange(int(fs * 0.05)) / fs
    mod = 0.5 * np.sin(2 * np.pi * 800 * t)
    iq = (1 + mod) * np.exp(1j * 0)
    audio = demod_am(iq, fs)
    spectrum = np.abs(np.fft.rfft(audio * np.hanning(len(audio))))
    freqs = np.fft.rfftfreq(len(audio), 1 / fs)
    assert freqs[np.argmax(spectrum)] == pytest.approx(800.0, abs=200.0)


def test_rssi_and_pcm_conversion():
    iq = np.full(1024, 0.5) + 0j
    assert rssi_dbfs(iq) == pytest.approx(-6.02, abs=0.5)
    pcm = audio_to_pcm16(np.array([0.0, 0.5, -0.5]))
    assert len(pcm) == 6


def test_vox_recorder_gates_on_level(tmp_path):
    settings = AudioSettings(
        recordings_dir=str(tmp_path),
        vox_threshold_dbfs=-30.0,
        vox_hang_ms=50,
        min_clip_seconds=0.0,
    )
    recorder = VoxRecorder(145.5e6, settings)
    loud = audio_to_pcm16(np.full(480, 0.5))
    quiet = audio_to_pcm16(np.zeros(480))
    recorder.enabled = True
    recorder.feed(loud, 48_000)
    recorder.feed(loud, 48_000)
    recorder.feed(quiet, 48_000)
    clips = recorder.stop()
    assert len(clips) == 1
    assert clips[0].path.exists()
    assert clips[0].freq_hz == 145.5e6


def test_vox_recorder_disabled_writes_nothing(tmp_path):
    settings = AudioSettings(recordings_dir=str(tmp_path))
    recorder = VoxRecorder(145.5e6, settings)
    loud = audio_to_pcm16(np.full(480, 0.5))
    recorder.enabled = False
    recorder.feed(loud, 48_000)
    assert recorder.stop() == []


def test_vox_recorder_uses_utc_and_avoids_filename_collisions(tmp_path, monkeypatch):
    settings = AudioSettings(recordings_dir=str(tmp_path), min_clip_seconds=0.0)
    monkeypatch.setattr("radiotui.audio.recorder.time.time", lambda: 1_700_000_000.0)
    loud = audio_to_pcm16(np.full(480, 0.5))

    first = VoxRecorder(145.5e6, settings)
    first.enabled = True
    first.feed(loud, 48_000)
    (first_clip,) = first.stop()

    second = VoxRecorder(145.5e6, settings)
    second.enabled = True
    second.feed(loud, 48_000)
    (second_clip,) = second.stop()

    assert first_clip.path != second_clip.path
    assert first_clip.path.name.endswith("_20231114_221320.wav")
    assert second_clip.path.name.endswith("_20231114_221320_1.wav")


def test_pcm_rms_levels():
    silence = pcm_rms_dbfs(audio_to_pcm16(np.zeros(100)))
    loud = pcm_rms_dbfs(audio_to_pcm16(np.full(100, 0.9)))
    assert silence < -80
    assert loud > -5
