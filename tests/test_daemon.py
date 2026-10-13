"""The daemon's state machine with fake audio, recognizer and output: no PipeWire,
no models, no compositor."""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from chttr import config as C
from chttr import daemon as Dm


class FakeRecorder:
    def __init__(self, device="", on_level=None):
        self.active = False
        self.peak_db = -12.0
        self.elapsed = 0.0
        self.device = device

    def start(self):
        self.active = True

    def stop(self):
        self.active = False
        return np.zeros(16000, dtype=np.float32), 1.0

    def cancel(self):
        self.active = False

    def snapshot(self, max_seconds=0):
        return np.zeros(0, dtype=np.float32)


class FakeTranscriber:
    """Returns queued transcripts; `delays` slows down particular calls."""

    def __init__(self, cfg):
        self.texts: list[str] = []
        self.delays: list[float] = []
        self.engine = type("E", (), {"name": "fake"})()
        self.key = ("fake",)

    def transcribe(self, audio, prompt=None, trim=True):
        delay = self.delays.pop(0) if self.delays else 0.0
        text = self.texts.pop(0) if self.texts else ""
        time.sleep(delay)
        return text


class QuietFeedback:
    def __init__(self, cfg, runtime):
        self.cfg = cfg
        self.notes: list[str] = []

    def play(self, name):
        pass

    def notify(self, title, body="", **kw):
        self.notes.append(title)


@pytest.fixture
def daemon(monkeypatch):
    monkeypatch.setattr(Dm, "Recorder", FakeRecorder)
    monkeypatch.setattr(Dm, "Transcriber", FakeTranscriber)
    monkeypatch.setattr(Dm, "Feedback", QuietFeedback)
    typed: list[str] = []
    monkeypatch.setattr(Dm, "deliver", lambda text, cfg, **kw: typed.append(text) or "type")
    monkeypatch.setattr(Dm, "focused_window", lambda: ("kitty", "~"))
    monkeypatch.setattr(Dm, "grab_selection", lambda terminal: "")
    cfg = C.Config(modes=C._default_modes())
    cfg.audio.unmute = False
    cfg.feedback.overlay = False
    cfg.feedback.live_captions = False
    cfg.polish.backend = "none"
    cfg.history.enabled = False
    d = Dm.Daemon(cfg)
    d.typed = typed
    return d


def wait_idle(d, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if d.state == Dm.IDLE and not d.jobs:
            return
        time.sleep(0.01)
    raise AssertionError(f"daemon stuck in {d.state} with {d.jobs} jobs")


def test_push_to_talk_types_the_transcript(daemon):
    daemon.transcriber.texts = ["hello world"]
    assert daemon.handle("start")["state"] == Dm.RECORDING
    assert daemon.handle("release")["state"] == Dm.TRANSCRIBING
    wait_idle(daemon)
    assert daemon.typed == ["hello world"]


def test_release_during_a_slow_start_still_stops(daemon, monkeypatch):
    daemon.transcriber.texts = ["quick tap"]
    gate = threading.Event()

    def slow_window():
        gate.wait(2)
        return "kitty", "~"

    monkeypatch.setattr(Dm, "focused_window", slow_window)
    replies = {}
    t = threading.Thread(target=lambda: replies.setdefault("start", daemon.handle("start")))
    t.start()
    time.sleep(0.05)
    assert "will stop" in daemon.handle("release")["message"]
    gate.set()
    t.join(2)
    wait_idle(daemon)
    assert daemon.typed == ["quick tap"]


def test_a_new_dictation_can_start_while_the_last_one_finishes(daemon):
    daemon.transcriber.texts = ["first", "second"]
    daemon.transcriber.delays = [0.3, 0.0]
    daemon.handle("start")
    daemon.handle("release")
    assert daemon.handle("start")["state"] == Dm.RECORDING      # no "busy transcribing"
    daemon.handle("release")
    wait_idle(daemon)
    assert daemon.typed == ["first", "second"]                   # typed in the order spoken


def test_hands_free_ignores_key_release_and_stops_on_toggle(daemon):
    daemon.transcriber.texts = ["hands free"]
    assert daemon.handle("toggle")["kind"] == Dm.HANDSFREE
    assert "ignoring" in daemon.handle("release")["message"]
    daemon.handle("toggle")
    wait_idle(daemon)
    assert daemon.typed == ["hands free"]


def test_no_speech_types_nothing(daemon):
    daemon.transcriber.texts = [""]
    daemon.handle("start")
    daemon.handle("release")
    wait_idle(daemon)
    assert daemon.typed == []
    assert any("No speech" in n for n in daemon.feedback.notes)


def test_cancel_discards(daemon):
    daemon.handle("start")
    assert daemon.handle("cancel")["state"] == Dm.IDLE
    assert daemon.typed == []


def test_command_mode_without_a_model_explains_itself(daemon):
    daemon.transcriber.texts = ["make it shorter"]
    daemon.handle("command")
    daemon.handle("release")
    wait_idle(daemon)
    assert daemon.typed == []
    assert "Command mode needs a model" in daemon.feedback.notes


def test_unknown_command(daemon):
    assert daemon.handle("dance")["ok"] is False
