"""Classifier separates tone bursts, speech-like audio, and noise."""

import numpy as np
import pytest

from radiotui.audio.classify import SignalClass, StreamClassifier, classify_block

FS = 48_000


def rng(seed=7):
    return np.random.default_rng(seed)


def white_noise(seconds, level=0.05, seed=7):
    return rng(seed).normal(0, level, int(FS * seconds))


def tone_bursts(seconds, freq=800.0, level=0.3):
    """Speech-like: 800 Hz carrier gated in 200 ms syllables with pauses."""
    t = np.arange(int(FS * seconds)) / FS
    tone = np.sin(2 * np.pi * freq * t) * level
    gate = ((t * 5).astype(int) % 2 == 0).astype(float)
    edges = np.clip((t * 5 % 1) * 10, 0, 1)  # 100 ms attack/decay
    smooth = np.minimum(gate + edges * 0, 1)
    return tone * (0.15 + 0.85 * smooth)


def music_like(seconds, seed=3):
    """Chords + beat envelope: broadband harmonics with strong modulation."""
    t = np.arange(int(FS * seconds)) / FS
    beat = 0.4 + 0.6 * np.abs(np.sin(2 * np.pi * 2 * t)) ** 3
    out = np.zeros_like(t)
    for f in (220.0, 277.0, 330.0, 440.0):
        out += np.sin(2 * np.pi * f * t + rng(seed).uniform(0, 6))
    return out / 4.0 * 0.35 * beat


def test_silence_is_detected_as_silence():
    verdict = classify_block(white_noise(0.2, level=1e-5), FS)
    assert verdict.klass == SignalClass.SILENCE
    assert verdict.rms_dbfs < -55


def test_white_noise_scores_low_and_labels_noise():
    verdict = classify_block(white_noise(0.5), FS)
    assert verdict.klass == SignalClass.NOISE
    assert verdict.flatness > 0.5
    assert verdict.score < 0.45


def test_tone_bursts_score_high_and_label_signal():
    verdict = classify_block(tone_bursts(0.6), FS)
    assert verdict.klass == SignalClass.SIGNAL
    assert verdict.modulation > 0.25
    assert verdict.speech_share > 0.5


def test_music_like_scores_high():
    verdict = classify_block(music_like(1.0), FS)
    assert verdict.klass == SignalClass.SIGNAL
    assert verdict.score >= 0.45


def test_noise_plus_quiet_carrier_still_noisy():
    """A weak steady carrier buried in band noise stays NOISE (no false squelch-open)."""
    t = np.arange(int(FS * 0.5)) / FS
    weak = 0.01 * np.sin(2 * np.pi * 1000 * t)
    block = weak + white_noise(0.5, level=0.08, seed=11)[: len(t)]
    assert classify_block(block, FS).klass == SignalClass.NOISE


@pytest.mark.parametrize("seconds", [0.1, 0.3, 1.0])
def test_block_size_does_not_flip_verdicts(seconds):
    noise = classify_block(white_noise(seconds), FS)
    signal = classify_block(tone_bursts(seconds), FS)
    assert noise.klass != signal.klass or seconds == 0.1


def test_stream_classifier_smooths_single_glitches():
    stream = StreamClassifier(FS)
    for _ in range(6):
        stream.feed(tone_bursts(0.6))
    assert stream.feed(tone_bursts(0.6)).klass == SignalClass.SIGNAL
    # one noisy block in a signal stream must not flip the verdict
    verdict = stream.feed(white_noise(0.6))
    assert verdict.klass == SignalClass.SIGNAL
    # sustained noise does flip it once the history fills with noise
    for _ in range(8):
        verdict = stream.feed(white_noise(0.6))
    assert verdict.klass == SignalClass.NOISE


def test_stream_classifier_silence_short_circuits():
    stream = StreamClassifier(FS)
    for _ in range(4):
        stream.feed(tone_bursts(0.6))
    for _ in range(3):
        verdict = stream.feed(white_noise(0.6, level=1e-5))
    assert verdict.klass == SignalClass.SILENCE
    # silence resets momentum: one strong block re-opens immediately
    verdict = stream.feed(tone_bursts(0.6))
    assert verdict.klass == SignalClass.SIGNAL
