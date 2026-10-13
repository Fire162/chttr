from __future__ import annotations

import wave

import numpy as np
import pytest

from chttr.transcriber import SAMPLE_RATE, _clean, load_wav, trim_silence


def tone(seconds: float, amplitude: float = 0.3, hz: float = 220.0) -> np.ndarray:
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    return (amplitude * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SAMPLE_RATE), dtype=np.float32)


def test_trim_silence_cuts_both_ends_but_keeps_padding():
    audio = np.concatenate([silence(2.0), tone(1.0), silence(2.0)])
    out = trim_silence(audio, threshold_db=-40, pad=0.3)
    assert 1.5 <= len(out) / SAMPLE_RATE <= 1.7


def test_trim_silence_returns_empty_for_pure_silence():
    assert len(trim_silence(silence(1.0), threshold_db=-40)) == 0


def test_trim_silence_handles_tiny_input():
    tiny = tone(0.005)
    assert np.array_equal(trim_silence(tiny, -40), tiny)


@pytest.mark.parametrize("text", ["", "   ", "[BLANK_AUDIO]", "(music)", "*coughs*", "Thank you.", "you"])
def test_clean_drops_non_speech_and_whisper_hallucinations(text):
    assert _clean(text) == ""


def test_clean_normalizes_whitespace():
    assert _clean("  hello \n  world ") == "hello world"


def test_load_wav_downmixes_and_resamples(tmp_path):
    path = tmp_path / "stereo48k.wav"
    rate = 48000
    t = np.arange(rate) / rate
    left = (0.5 * np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
    stereo = np.stack([left, left], axis=1).reshape(-1)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(stereo.tobytes())
    audio = load_wav(path)
    assert audio.dtype == np.float32
    assert len(audio) == SAMPLE_RATE
    assert 0.45 < float(np.abs(audio).max()) < 0.55
