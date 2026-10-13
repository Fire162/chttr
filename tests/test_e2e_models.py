"""End to end with the real models (Parakeet, Silero VAD, WeSpeaker) on synthesized
speech. Slow; run with `uv run pytest -m model`. CI runs them in the Benchmark
workflow after downloading the pinned models and installing espeak-ng."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.model


def _parakeet_ready() -> bool:
    real = Path(os.environ["CHTTR_TEST_REAL_MODELS"]) / "parakeet-tdt-0.6b-v3-int8"
    voxtype = Path.home() / ".local/share/voxtype/models/parakeet-tdt-0.6b-v3-int8"
    return any((d / "encoder-model.int8.onnx").exists() for d in (real, voxtype))


needs_models = pytest.mark.skipif(not _parakeet_ready(), reason="Parakeet model not downloaded")
needs_espeak = pytest.mark.skipif(shutil.which("espeak-ng") is None, reason="espeak-ng not installed")


def speak(text: str, voice: str, tmp: Path) -> np.ndarray:
    from chttr.transcriber import load_wav

    out = tmp / f"{abs(hash((text, voice)))}.wav"
    subprocess.run(["espeak-ng", "-v", voice, "-s", "160", "-w", str(out), text], check=True)
    return load_wav(out)


@needs_models
@needs_espeak
def test_meeting_pipeline_names_two_voices_from_captions(tmp_path, monkeypatch):
    from chttr import config as C
    from chttr.meeting import process

    monkeypatch.setattr(C, "VOICES_FILE", tmp_path / "voices.json")
    import chttr.diarize as D

    monkeypatch.setattr(D, "VOICES_FILE", tmp_path / "voices.json")
    script = [
        ("them", "en-us+f3", "Dana Lee", "Thanks for joining. Let's go over the launch plan for next week."),
        ("them", "en-us+m3", "Ali Khan", "The build is ready and the tests passed this morning."),
        ("mic", "en-gb", "", "Great. I will write the announcement and send it on Monday."),
        ("them", "en-us+f3", "Dana Lee", "Ali, can you check the pricing page before Friday?"),
        ("them", "en-us+m3", "Ali Khan", "Yes, I will fix the broken links on the pricing page by Thursday."),
    ]
    started = time.time() - 600
    mic, them, events, t = [np.zeros(16000, np.float32)], [np.zeros(16000, np.float32)], [], 1.0
    gap = np.zeros(int(0.8 * 16000), np.float32)
    for channel, voice, name, text in script:
        audio = speak(text, voice, tmp_path)
        (mic if channel == "mic" else them).append(audio)
        (them if channel == "mic" else mic).append(np.zeros_like(audio))
        if name:
            events += [{"type": "speaking", "name": name, "t": started + t + k}
                       for k in np.arange(0.4, len(audio) / 16000 + 1.0, 0.3)]
        t += len(audio) / 16000 + 0.8
        mic.append(gap)
        them.append(gap)
    d = tmp_path / "meeting"
    d.mkdir()
    for name, parts in (("mic", mic), ("them", them)):
        (d / f"{name}.raw").write_bytes((np.concatenate(parts) * 32767).astype(np.int16).tobytes())
    (d / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({"id": "e2e", "started": started, "code": "abc-defg-hij",
                                             "title": "Launch plan", "status": "recorded"}), encoding="utf-8")
    cfg = C.Config()
    cfg.meeting.dir = str(tmp_path / "notes")
    cfg.meeting.me = "Rob"

    md = process(d, cfg, summary=False).read_text(encoding="utf-8")

    assert "**Dana Lee**" in md and "**Ali Khan**" in md and "**Rob**" in md
    assert "pricing page" in md.lower()
    result = json.loads((d / "result.json").read_text(encoding="utf-8"))
    by_speaker = {}
    for u in result["utterances"]:
        by_speaker.setdefault(u["speaker"], []).append(u["text"].lower())
    assert any("announcement" in x for x in by_speaker["Rob"])
    assert any("build is ready" in x for x in by_speaker["Ali Khan"])
    assert not (d / "mic.raw").exists()              # raw audio is deleted once the notes exist


@needs_models
def test_streaming_is_as_accurate_as_one_full_pass():
    from chttr import bench
    from chttr import streaming as S
    from chttr.config import Config
    from chttr.transcriber import Transcriber, load_wav

    files = sorted((Path(__file__).parent / "bench").glob("*.wav"))
    if not files:
        pytest.skip("no bench recordings (python3 scripts/make-bench-audio.py)")
    cfg = Config()
    transcriber = Transcriber(cfg)
    for f in files:
        audio = load_wav(f)
        ref = f.with_suffix(".txt").read_text(encoding="utf-8")
        levels = bench.chunk_levels(audio)
        end = bench.release_point(levels, cfg.audio.speech_threshold_db)
        segments = bench.stream_segments(levels, cfg.audio.speech_threshold_db, end)
        texts = [transcriber.transcribe(audio[a * S.CHUNK_FRAMES:b * S.CHUNK_FRAMES]) for a, b in segments]
        texts.append(transcriber.transcribe(audio[(segments[-1][1] if segments else 0) * S.CHUNK_FRAMES:]))
        streamed = " ".join(x for x in texts if x)
        full = transcriber.transcribe(audio)
        assert bench.wer(ref, streamed) <= bench.wer(ref, full) + 0.1, (f.name, streamed, full)
