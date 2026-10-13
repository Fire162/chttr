#!/usr/bin/env python3
"""Synthesize the benchmark recordings in tests/bench/ with espeak-ng.

    python3 scripts/make-bench-audio.py            # writes tests/bench/<name>.wav + .txt

Synthetic speech is not a person, so absolute accuracy here is only a smoke test;
the point is a fixed, reproducible input for timing every change in CI.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "tests" / "bench"
RATE = 16000
PAUSE = 0.7


def blocks(path: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    name = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1]
            out[name] = []
        elif name:
            out[name].append(line)
    return out


def speak(text: str) -> np.ndarray:
    with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
        subprocess.run(["espeak-ng", "-v", "en-us", "-s", "165", "-w", tmp.name, text], check=True)
        with wave.open(tmp.name, "rb") as wf:
            rate = wf.getframerate()
            audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    n = int(len(audio) * RATE / rate)
    return np.interp(np.linspace(0, len(audio), n, endpoint=False), np.arange(len(audio)), audio).astype(np.float32)


def main() -> int:
    silence = np.zeros(int(PAUSE * RATE), dtype=np.float32)
    for name, sentences in blocks(BENCH / "sentences.txt").items():
        parts = [silence[: RATE // 4]]
        for s in sentences:
            parts += [speak(s), silence]
        audio = np.concatenate(parts)
        with wave.open(str(BENCH / f"{name}.wav"), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(RATE)
            wf.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
        (BENCH / f"{name}.txt").write_text(" ".join(sentences) + "\n", encoding="utf-8")
        print(f"{name}.wav  {len(audio) / RATE:.1f}s  {len(sentences)} sentence(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
