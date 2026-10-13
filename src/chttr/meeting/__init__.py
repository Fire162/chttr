"""Meeting transcripts: recording sessions, the post-call pipeline, and the Markdown notes.

A meeting lives in ~/.local/share/chttr/meetings/<id>/ while it is recorded and
processed:

    meta.json      when, Meet code, title, status
    mic.raw        your microphone (s16le, 16 kHz)
    them.raw       the speaker output, i.e. everyone else
    events.jsonl   from the Meet extension: who the captions say is speaking,
                   mute changes; from the daemon: dictations made during the call
    result.json    utterances, speakers and notes after processing
    speakers.npz   voice embeddings per speaker, for `chttr meeting rename`

The notes go to [meeting] dir as "<date> <time> <title>.md". The raw audio is
deleted once the notes are written (or kept as Opus with keep_audio = true).

Who said what:
- Your words are whatever your microphone picked up (minus the moments Meet had
  you muted, dictations you made with chttr, and the other side's voices leaking
  from your speakers).
- The other side is split into voices by speaker embeddings. Each voice is named
  by, in order: Meet's captions (the extension reports the captioned speaker
  over time), a remembered voice, the calendar (a 1:1 has only one other
  person), or Claude reading the conversation. Whatever is left stays
  "Speaker N"; `chttr meeting rename "Speaker 2" "Name"` fixes it and teaches
  chttr that voice.
"""

from __future__ import annotations

from .analysis import SELF_CAPTION_NAMES
from .edit import notify_done, rename, summary_line
from .pipeline import process
from .render import render
from .session import Session, launch_processing
from .store import (
    SAMPLE_RATE,
    Speaker,
    Utterance,
    meeting_dirs,
    new_meeting_dir,
    read_json,
    resolve,
    update_meta,
    write_json,
)
from .styles import choose_style, restyle, write_notes

__all__ = [
    "SAMPLE_RATE", "SELF_CAPTION_NAMES", "Session", "Speaker", "Utterance", "choose_style", "launch_processing",
    "meeting_dirs", "new_meeting_dir", "notify_done", "process", "read_json", "rename", "render", "resolve",
    "restyle", "summary_line", "update_meta", "write_json", "write_notes",
]
