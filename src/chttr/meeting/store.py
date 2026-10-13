"""Meeting directories on disk and the data they hold."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .. import config as C

SAMPLE_RATE = 16000


@dataclass
class Utterance:
    start: float            # seconds from the start of the recording
    end: float
    channel: str            # "mic" | "them"
    text: str = ""
    cluster: int = -1       # voice cluster on the "them" channel
    speaker: str = ""       # label in the transcript


@dataclass
class Speaker:
    label: str
    name: str = ""
    email: str = ""
    source: str = ""        # me | captions | voice | calendar | claude | unknown
    evidence: str = ""
    clusters: list[int] = field(default_factory=list)
    seconds: float = 0.0


def meeting_dirs() -> list[Path]:
    if not C.MEETINGS_DATA_DIR.exists():
        return []
    return sorted((d for d in C.MEETINGS_DATA_DIR.iterdir() if (d / "meta.json").exists()), key=lambda d: d.name)


def resolve(ref: str | None) -> Path:
    """A meeting directory from an id, a path, or None for the latest one."""
    if ref:
        p = Path(ref).expanduser()
        if (p / "meta.json").exists():
            return p
        matches = [d for d in meeting_dirs() if d.name == ref or d.name.startswith(ref)]
        if len(matches) == 1:
            return matches[0]
        raise SystemExit(f"no single meeting matches {ref!r} (see: chttr meeting list)")
    dirs = meeting_dirs()
    if not dirs:
        raise SystemExit("no meetings recorded yet")
    return dirs[-1]


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def update_meta(directory: Path, **changes) -> dict:
    meta = read_json(directory / "meta.json", {}) or {}
    meta.update(changes)
    write_json(directory / "meta.json", meta)
    return meta


def new_meeting_dir(started: float, code: str) -> Path:
    stamp = datetime.fromtimestamp(started).strftime("%Y-%m-%d-%H%M%S")
    d = C.MEETINGS_DATA_DIR / (stamp + (f"-{code}" if code else ""))
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d

