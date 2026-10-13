"""Microphone capture through PipeWire (pw-record), streamed so levels can be metered."""

from __future__ import annotations

import math
import signal
import subprocess
import threading
import time
from collections.abc import Callable

import numpy as np

SAMPLE_RATE = 16000
CHUNK_FRAMES = 320          # 20 ms
CHUNK_BYTES = CHUNK_FRAMES * 2

LevelCallback = Callable[[float, float], None]   # (rms 0..1, peak_db)


class Recorder:
    def __init__(self, device: str = "", on_level: LevelCallback | None = None) -> None:
        self.device = device
        self.on_level = on_level
        self._proc: subprocess.Popen | None = None
        self._chunks: list[bytes] = []
        self.levels_db: list[float] = []     # RMS level of every 20 ms chunk, for pause detection
        self._reader: threading.Thread | None = None
        self._started = 0.0
        self.peak_db = -90.0

    @property
    def active(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started if self._proc else 0.0

    def start(self) -> None:
        if self.active:
            raise RuntimeError("already recording")
        cmd = [
            "pw-record", "--raw",
            "--rate", str(SAMPLE_RATE), "--channels", "1", "--format", "s16",
            "--media-role", "Communication", "--latency", "20ms",
        ]
        if self.device:
            cmd += ["--target", self.device]
        cmd.append("-")
        self._chunks = []
        self.levels_db = []
        self.peak_db = -90.0
        self._proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self._started = time.monotonic()
        time.sleep(0.05)
        if self._proc.poll() is not None:
            err = (self._proc.stderr.read() if self._proc.stderr else b"").decode(errors="replace").strip()
            self._proc = None
            raise RuntimeError(f"pw-record failed: {err or 'exited immediately'}")
        self._reader = threading.Thread(target=self._read_loop, args=(self._proc,), daemon=True)
        self._reader.start()

    def _read_loop(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        while True:
            data = proc.stdout.read(CHUNK_BYTES)
            if not data:
                break
            samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
            rms = float(np.sqrt(np.mean(samples * samples))) if len(samples) else 0.0
            # level before the chunk itself, so a reader never sees audio without its level
            self.levels_db.append(20 * math.log10(max(rms, 1e-6)))
            self._chunks.append(data)
            peak = float(np.abs(samples).max()) if len(samples) else 0.0
            peak_db = 20 * math.log10(max(peak, 1e-6))
            self.peak_db = max(self.peak_db, peak_db)
            if self.on_level:
                try:
                    self.on_level(rms, peak_db)
                except Exception:  # noqa: BLE001 - metering must never break recording
                    pass

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    def audio_range(self, start: int, end: int) -> np.ndarray:
        """Chunks [start, end) of the recording so far, as float32."""
        return np.frombuffer(b"".join(self._chunks[start:end]), dtype=np.int16).astype(np.float32) / 32768.0

    def snapshot(self, max_seconds: float = 0) -> np.ndarray:
        """The audio recorded so far (the last max_seconds of it, if given), without stopping."""
        chunks = list(self._chunks)
        if max_seconds:
            chunks = chunks[-int(max_seconds * SAMPLE_RATE / CHUNK_FRAMES):]
        return np.frombuffer(b"".join(chunks), dtype=np.int16).astype(np.float32) / 32768.0

    def stop(self) -> tuple[np.ndarray, float]:
        """Stop recording; returns (float32 mono audio at 16 kHz, seconds recorded)."""
        if self._proc is None:
            raise RuntimeError("not recording")
        proc, secs = self._proc, self.elapsed
        self._proc = None
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.terminate()
                proc.wait(timeout=2)
        if self._reader:
            self._reader.join(timeout=2)
        raw = b"".join(self._chunks)
        self._chunks = []
        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        return audio, secs

    def cancel(self) -> None:
        if self._proc is None:
            return
        self.stop()
