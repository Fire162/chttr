"""Recognize while you talk, so letting go of the key leaves almost nothing to do.

Every pause of PAUSE seconds or more closes a segment: the audio up to the middle
of the pause is transcribed in the background while you keep talking. When the
recording stops, only the audio after the last pause is left to recognize, and
the segments are joined. A dictation of any length therefore costs about one
second of recognition after release instead of a pass over the whole recording.

The same thread drives live captions: the closed segments plus a quick pass over
the segment being spoken, instead of re-transcribing the last few seconds over
and over.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Protocol

import numpy as np

log = logging.getLogger("chttr.streaming")

CHUNKS_PER_SECOND = 50          # the recorder's 20 ms chunks
CHUNK_FRAMES = 320
PAUSE = 0.45                    # silence that ends a segment
MIN_SPEECH = 0.6                # don't close a segment with less speech than this
MAX_SEGMENT = 20.0              # force a cut (at the quietest moment) in a long unbroken run
POLL = 0.1
CAPTION_EVERY = 0.7


class Source(Protocol):
    levels_db: list[float]

    @property
    def chunk_count(self) -> int: ...

    def audio_range(self, start: int, end: int) -> np.ndarray: ...


def find_cut(levels: list[float], threshold_db: float, start: int = 0) -> int | None:
    """Where to close the segment that begins at chunk `start`: the middle of the first
    long-enough pause after enough speech, or the quietest chunk of the last two
    seconds when the segment has run too long. None while the segment is still open."""
    seg = levels[start:]
    loud = [i for i, db in enumerate(seg) if db > threshold_db]
    if not loud:
        return None
    pause = int(PAUSE * CHUNKS_PER_SECOND)
    min_speech = int(MIN_SPEECH * CHUNKS_PER_SECOND)
    first = loud[0]
    for a, b in zip(loud, [*loud[1:], len(seg)], strict=True):
        gap = b - a - 1                     # quiet chunks after chunk a (the last gap runs to "now")
        if gap >= pause and a - first + 1 >= min_speech:
            return start + a + 1 + gap // 2
    if len(seg) >= MAX_SEGMENT * CHUNKS_PER_SECOND:
        tail = 2 * CHUNKS_PER_SECOND
        window = seg[-tail:]
        return start + len(seg) - tail + int(np.argmin(window))
    return None


class Streamer:
    def __init__(self, source: Source, transcribe: Callable[[np.ndarray], str], threshold_db: float,
                 on_caption: Callable[[str], None] | None = None) -> None:
        self.source = source
        self.transcribe = transcribe
        self.threshold_db = threshold_db
        self.on_caption = on_caption
        self.done = 0                       # chunks already recognized
        self.segments: list[str] = []
        self.busy_seconds = 0.0             # recognition time spent while recording
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="streaming-asr", daemon=True)

    def start(self) -> Streamer:
        self._thread.start()
        return self

    @property
    def text(self) -> str:
        return " ".join(s for s in self.segments if s)

    def _recognize(self, audio: np.ndarray) -> str:
        t0 = time.monotonic()
        try:
            return self.transcribe(audio)
        finally:
            self.busy_seconds += time.monotonic() - t0

    def _loop(self) -> None:
        last_caption, captioned = 0.0, -1
        while not self._stop.wait(POLL):
            try:
                n = self.source.chunk_count
                cut = find_cut(self.source.levels_db[:n], self.threshold_db, self.done)
                if cut is not None and cut > self.done:
                    text = self._recognize(self.source.audio_range(self.done, cut))
                    if self._stop.is_set() and not text:
                        break
                    self.segments.append(text)
                    self.done = cut
                    self._caption(self.text)
                    captioned = n
                elif (self.on_caption and n != captioned and time.monotonic() - last_caption >= CAPTION_EVERY
                      and n - self.done >= MIN_SPEECH * CHUNKS_PER_SECOND):
                    last_caption, captioned = time.monotonic(), n
                    partial = self._recognize(self.source.audio_range(self.done, n))
                    if not self._stop.is_set():
                        self._caption(" ".join(x for x in (self.text, partial) if x))
            except Exception as exc:  # noqa: BLE001 - the final pass covers whatever this missed
                log.debug("streaming pass failed: %s", exc)

    def _caption(self, text: str) -> None:
        if self.on_caption and text:
            try:
                self.on_caption(text)
            except Exception:  # noqa: BLE001 - captions are cosmetic
                pass

    def finish(self, audio: np.ndarray, timeout: float = 15.0) -> str:
        """Stop, recognize what came after the last closed segment of `audio` (the
        whole recording) and return the full transcript."""
        self._stop.set()
        if self._thread.ident is not None:
            self._thread.join(timeout)
        tail = audio[self.done * CHUNK_FRAMES:]
        tail_text = self.transcribe(tail) if len(tail) else ""
        log.info("streaming: %d segment(s) during recording (%.2fs of recognition), tail %.1fs",
                 len([s for s in self.segments if s]), self.busy_seconds, len(tail) / 16000)
        return " ".join(x for x in (self.text, tail_text) if x)

    def abort(self) -> None:
        self._stop.set()
