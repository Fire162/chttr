"""A meeting being recorded (owned by the daemon), and the hand-off to processing."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from ..config import Config
from .store import new_meeting_dir, update_meta


def launch_processing(directory: Path) -> None:
    """Run `chttr meeting process` in its own session at low priority, so the
    daemon stays responsive and a daemon restart doesn't kill it."""
    log_file = (directory / "process.log").open("ab")
    cmd = ["nice", "-n", "10", sys.executable, "-m", "chttr", "meeting", "process", "--notify", str(directory)]
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True)


class Session:
    """One meeting being recorded (owned by the daemon)."""

    def __init__(self, cfg: Config, code: str = "", title: str = "", source: str = "manual") -> None:
        from .recorder import MeetingRecorder

        self.code, self.title, self.source = code, title, source
        self.dir = new_meeting_dir(time.time(), code)
        self.recorder = MeetingRecorder(self.dir, cfg.audio.device)
        self._events = None

    @property
    def elapsed(self) -> float:
        return self.recorder.elapsed

    def start(self) -> None:
        self.recorder.start()
        self._events = (self.dir / "events.jsonl").open("a")
        self.save_meta(status="recording")

    def save_meta(self, **extra) -> None:
        update_meta(self.dir, id=self.dir.name, started=self.recorder.started, code=self.code,
                    title=self.title, source=self.source, **extra)

    def log(self, event: dict) -> None:
        if self._events:
            self._events.write(json.dumps(event, ensure_ascii=False) + "\n")
            self._events.flush()

    def stop(self) -> float:
        secs = self.recorder.stop()
        if self._events:
            self._events.close()
            self._events = None
        self.save_meta(status="recorded", duration=round(secs, 1))
        return secs

    def discard(self) -> None:
        self.recorder.stop()
        if self._events:
            self._events.close()
            self._events = None
        shutil.rmtree(self.dir, ignore_errors=True)

