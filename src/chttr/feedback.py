"""Desktop notifications and audio cues."""

from __future__ import annotations

import logging
import math
import struct
import subprocess
import wave
from pathlib import Path

from .config import FeedbackCfg

log = logging.getLogger("chttr.feedback")
_NOTIFY_ID = "92110"


class Feedback:
    def __init__(self, cfg: FeedbackCfg, runtime_dir: Path) -> None:
        self.cfg = cfg
        self.sounds = {
            "start": runtime_dir / "start.wav",
            "stop": runtime_dir / "stop.wav",
            "error": runtime_dir / "error.wav",
        }
        if cfg.sounds:
            _tone(self.sounds["start"], [(660, 0.07), (880, 0.09)])
            _tone(self.sounds["stop"], [(880, 0.07), (660, 0.09)])
            _tone(self.sounds["error"], [(330, 0.12), (0, 0.04), (330, 0.12)])

    def play(self, name: str) -> None:
        if not self.cfg.sounds:
            return
        try:
            subprocess.Popen(
                ["pw-play", "--volume", "0.35", "--media-role", "Notification", str(self.sounds[name])],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            pass

    def notify(self, title: str, body: str = "", *, urgency: str = "low", timeout_ms: int = 3000) -> None:
        if not self.cfg.notifications:
            return
        cmd = ["notify-send", "-a", "chttr", "-r", _NOTIFY_ID, "-u", urgency, "-t", str(timeout_ms), title]
        if body:
            cmd.append(body)
        try:
            subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            log.info("%s %s", title, body)


def _tone(path: Path, notes: list[tuple[float, float]], rate: int = 44100) -> None:
    """Write a tiny WAV of sine notes [(hz, seconds), ...]; hz=0 is silence."""
    frames = bytearray()
    for hz, secs in notes:
        n = int(rate * secs)
        for i in range(n):
            env = min(1.0, i / (rate * 0.008), (n - i) / (rate * 0.02))  # soft attack/release
            v = math.sin(2 * math.pi * hz * i / rate) * 0.6 * env if hz else 0.0
            frames += struct.pack("<h", int(v * 32767))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(bytes(frames))
