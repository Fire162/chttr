"""After the call: correct a speaker's name, and the desktop notification."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from .. import diarize as D
from ..config import Config
from .render import render
from .store import Speaker, Utterance, read_json, write_json
from .styles import _relabel


def rename(directory: Path, old: str, name: str, email: str, cfg: Config) -> Path:
    """Fix a speaker's name in a processed meeting and remember their voice."""
    result = read_json(directory / "result.json")
    if not result:
        raise SystemExit(f"{directory.name} has not been processed yet")
    speakers = {k: Speaker(**v) for k, v in result["speakers"].items()}
    if old not in speakers:
        raise SystemExit(f"no speaker {old!r} in this meeting; speakers: {', '.join(speakers)}")
    if speakers[old].source == "me":
        raise SystemExit("that's you; set [meeting] me = \"Your Name\" in the config instead")
    clusters = list(speakers[old].clusters)
    _relabel(result, old, name, email, "corrected", "corrected by hand")
    utts = [Utterance(**u) for u in result["utterances"]]
    md = render(directory, result, cfg)
    result["markdown"] = str(md)
    write_json(directory / "result.json", result)
    npz = directory / "speakers.npz"
    if npz.exists() and clusters:
        stored = np.load(npz)
        vecs = [stored[str(c)] for c in clusters if str(c) in stored]
        secs = sum(u.end - u.start for u in utts if u.speaker == name and u.channel == "them")
        if vecs:
            voices = D.load_voices()
            D.remember(voices, name, email, D.centroid(np.stack(vecs)), max(secs, 60))
            D.save_voices(voices)
    return md



def notify_done(md: Path, summary_line: str, offer: list[str] | None = None, on_send=None) -> None:
    """Notification with an Open button, and a "Send to ..." button per destination in
    `offer` (on_send(destination) does the sending). Waits for the click in this
    (background) process."""
    from ..integrations import label

    offer = offer or []
    actions = ["-A", "open=Open"]
    for d in offer:
        actions += ["-A", f"send:{d}=Send to {label(d)}"]
    if len(offer) > 1:
        actions += ["-A", "send:all=Send everywhere"]
    try:
        out = subprocess.run(["notify-send", "-a", "chttr", *actions, "-t", "120000", "--wait",
                              f"Meeting notes saved: {md.stem}", summary_line],
                             capture_output=True, text=True, timeout=180)
        choice = out.stdout.strip()
        if choice == "open":
            subprocess.Popen(["xdg-open", str(md)], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif choice.startswith("send:") and on_send:
            targets = offer if choice == "send:all" else [choice.removeprefix("send:")]
            for d in targets:
                if d in offer:
                    on_send(d)
    except (OSError, subprocess.TimeoutExpired):
        pass


def summary_line(result_md: Path, directory: Path) -> str:
    result = read_json(directory / "result.json", {}) or {}
    names = [k for k, v in result.get("speakers", {}).items() if v.get("seconds")]
    mins = round(result.get("duration", 0) / 60)
    return f"{mins} min · " + ", ".join(names[:6]) + ("…" if len(names) > 6 else "")
