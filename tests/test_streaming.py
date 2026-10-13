from __future__ import annotations

import threading
import time

import numpy as np

from chttr import streaming as S

LOUD, QUIET = -20.0, -60.0
THRESHOLD = -38.0


def levels(*parts: tuple[str, float]) -> list[float]:
    """levels(("speech", 1.0), ("pause", 0.6), ...) in 20 ms chunks."""
    out: list[float] = []
    for kind, seconds in parts:
        out += [LOUD if kind == "speech" else QUIET] * int(seconds * S.CHUNKS_PER_SECOND)
    return out


def test_no_cut_while_still_talking():
    assert S.find_cut(levels(("speech", 3.0)), THRESHOLD) is None


def test_cut_in_the_middle_of_the_first_long_pause():
    lv = levels(("speech", 1.0), ("pause", 0.6), ("speech", 1.0))
    cut = S.find_cut(lv, THRESHOLD)
    assert cut is not None and 50 < cut < 80


def test_short_breaths_do_not_cut():
    lv = levels(("speech", 1.0), ("pause", 0.2), ("speech", 1.0), ("pause", 0.2), ("speech", 0.5))
    assert S.find_cut(lv, THRESHOLD) is None


def test_a_click_before_a_pause_is_not_a_segment():
    lv = levels(("speech", 0.1), ("pause", 1.0))
    assert S.find_cut(lv, THRESHOLD) is None


def test_trailing_pause_closes_the_segment():
    cut = S.find_cut(levels(("speech", 1.5), ("pause", 0.5)), THRESHOLD)
    assert cut is not None and cut >= 75


def test_cut_respects_the_start_offset():
    lv = levels(("speech", 1.0), ("pause", 0.6), ("speech", 1.0), ("pause", 0.6))
    first = S.find_cut(lv, THRESHOLD)
    second = S.find_cut(lv, THRESHOLD, first)
    assert second is not None and second > first


def test_long_unbroken_speech_is_cut_at_the_quietest_moment():
    lv = levels(("speech", S.MAX_SEGMENT + 0.5))
    lv[-30] = -35.0              # still "speech", but the quietest chunk of the last two seconds
    assert S.find_cut(lv, THRESHOLD) == len(lv) - 30


class GrowingSource:
    """Feeds pre-planned levels over (fast) time, like the recorder."""

    def __init__(self, plan: list[float]):
        self.plan = plan
        self.levels_db: list[float] = []
        self.audio = np.zeros(len(plan) * S.CHUNK_FRAMES, dtype=np.float32)

    @property
    def chunk_count(self) -> int:
        return len(self.levels_db)

    def feed(self, chunks: int) -> None:
        self.levels_db.extend(self.plan[len(self.levels_db):len(self.levels_db) + chunks])

    def audio_range(self, start: int, end: int) -> np.ndarray:
        return self.audio[start * S.CHUNK_FRAMES:end * S.CHUNK_FRAMES]


def test_streamer_recognizes_segments_during_recording_and_only_the_tail_after(monkeypatch):
    monkeypatch.setattr(S, "POLL", 0.005)
    plan = levels(("speech", 1.0), ("pause", 0.6), ("speech", 1.2), ("pause", 0.6), ("speech", 0.8))
    source = GrowingSource(plan)
    calls: list[float] = []
    words = iter(["first part.", "second part.", "the tail."])

    def transcribe(audio: np.ndarray) -> str:
        calls.append(len(audio) / 16000)
        return next(words)

    captions: list[str] = []
    streamer = S.Streamer(source, transcribe, THRESHOLD, on_caption=None).start()
    while source.chunk_count < len(plan):
        source.feed(10)
        time.sleep(0.01)
    time.sleep(0.05)
    text = streamer.finish(source.audio)
    assert text == "first part. second part. the tail."
    assert len(calls) == 3
    assert calls[-1] < 1.2           # after release only the last stretch was recognized
    assert captions == []


def test_streamer_captions_show_closed_segments_plus_the_open_one(monkeypatch):
    monkeypatch.setattr(S, "POLL", 0.005)
    monkeypatch.setattr(S, "CAPTION_EVERY", 0.0)
    plan = levels(("speech", 1.0), ("pause", 0.6), ("speech", 1.5))
    source = GrowingSource(plan)
    lock = threading.Lock()
    captions: list[str] = []

    def transcribe(audio: np.ndarray) -> str:
        return f"w{len(audio) // S.CHUNK_FRAMES}"      # names each pass by its length

    def on_caption(text: str) -> None:
        with lock:
            captions.append(text)

    streamer = S.Streamer(source, transcribe, THRESHOLD, on_caption=on_caption).start()
    while source.chunk_count < len(plan):
        source.feed(5)
        time.sleep(0.01)
    time.sleep(0.05)
    streamer.abort()
    closed = streamer.segments[0]
    assert closed in captions                                         # the segment, once closed
    assert any(c.startswith(closed + " w") for c in captions)        # then closed + the open one


def test_finish_without_any_pause_is_one_pass_over_everything():
    plan = levels(("speech", 2.0))
    source = GrowingSource(plan)
    source.feed(len(plan))
    lengths = []
    streamer = S.Streamer(source, lambda a: lengths.append(len(a)) or "all of it", THRESHOLD)
    assert streamer.finish(source.audio) == "all of it"
    assert lengths == [len(source.audio)]
