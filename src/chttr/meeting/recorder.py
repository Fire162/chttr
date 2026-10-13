"""Two-channel meeting capture: your microphone and what the speakers play, each to its own file.

Both are raw s16le mono at 16 kHz, written straight to disk (an hour is ~115 MB
per channel). The two pw-record streams start a few milliseconds apart and can
stall independently, so each writer keeps itself on the wall clock: whenever it
falls more than PAD_AFTER behind, it writes silence to catch up. Sample n of
either file is therefore always about `started + n / 16000` in wall-clock time,
which is what lines the channels up with each other and with the Meet
extension's caption events.
"""

from __future__ import annotations

import logging
import signal
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger("chttr.meeting")

SAMPLE_RATE = 16000
CHUNK_FRAMES = 1600         # 100 ms
PAD_AFTER = 0.5             # seconds behind the wall clock before padding with silence


class ChannelWriter:
    def __init__(self, name: str, path: Path, cmd: list[str], t0: float) -> None:
        self.name = name
        self.path = path
        self.cmd = cmd
        self.t0 = t0
        self.samples = 0
        self.proc: subprocess.Popen | None = None
        self.thread: threading.Thread | None = None
        self.error = ""

    def start(self) -> None:
        self.proc = subprocess.Popen(self.cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(0.05)
        if self.proc.poll() is not None:
            err = (self.proc.stderr.read() if self.proc.stderr else b"").decode(errors="replace").strip()
            raise RuntimeError(f"pw-record ({self.name}) failed: {err or 'exited immediately'}")
        self.thread = threading.Thread(target=self._run, name=f"meeting-{self.name}", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        assert self.proc and self.proc.stdout
        with self.path.open("ab") as fh:
            while True:
                data = self.proc.stdout.read(CHUNK_FRAMES * 2)
                if not data:
                    break
                behind = (time.time() - self.t0) * SAMPLE_RATE - self.samples - len(data) // 2
                if behind > PAD_AFTER * SAMPLE_RATE:
                    pad = int(behind)
                    log.info("%s stream stalled; padding %.1fs of silence", self.name, pad / SAMPLE_RATE)
                    fh.write(b"\0\0" * pad)
                    self.samples += pad
                fh.write(data)
                self.samples += len(data) // 2
        if self.proc.returncode not in (0, None, -signal.SIGINT, 130):
            self.error = (self.proc.stderr.read() if self.proc.stderr else b"").decode(errors="replace").strip()

    def stop(self) -> None:
        proc = self.proc
        if proc and proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.terminate()
                proc.wait(timeout=2)
        if self.thread:
            self.thread.join(timeout=3)


class MeetingRecorder:
    """Records mic.raw and them.raw into `directory` until stop()."""

    def __init__(self, directory: Path, mic_device: str = "") -> None:
        self.dir = directory
        self.mic_device = mic_device
        self.started = 0.0
        self.channels: list[ChannelWriter] = []

    @property
    def elapsed(self) -> float:
        return time.time() - self.started if self.started else 0.0

    def start(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        base = ["pw-record", "--raw", "--rate", str(SAMPLE_RATE), "--channels", "1", "--format", "s16", "--latency", "100ms"]
        mic = base + ["--media-role", "Communication"]
        if self.mic_device:
            mic += ["--target", self.mic_device]
        # No --target: WirePlumber links us to the default sink's monitor and moves
        # the stream along when the default changes (headphones plugged in mid-call).
        them = base + ["-P", "stream.capture.sink=true"]
        self.started = time.time()
        self.channels = [
            ChannelWriter("mic", self.dir / "mic.raw", mic + ["-"], self.started),
            ChannelWriter("them", self.dir / "them.raw", them + ["-"], self.started),
        ]
        try:
            for ch in self.channels:
                ch.start()
        except RuntimeError:
            self.stop()
            raise

    def stop(self) -> float:
        """Stop both channels; returns the recorded duration in seconds."""
        for ch in self.channels:
            ch.stop()
        for ch in self.channels:
            if ch.error:
                log.warning("%s channel: %s", ch.name, ch.error)
        return max((ch.samples for ch in self.channels), default=0) / SAMPLE_RATE
