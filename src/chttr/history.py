"""Dictation history (~/.local/share/chttr/history.jsonl): picker and stats."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections import Counter
from datetime import datetime, timedelta

from .config import HISTORY_FILE

TYPING_WPM = 40          # an average typist, for "time saved"
MENU = "omarchy-menu-select"


def load() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    entries = []
    for line in HISTORY_FILE.read_text().splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("text") or e.get("raw"):
            entries.append(e)
    return entries


def _local(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts).astimezone()
    except (TypeError, ValueError):
        return None


def _ago(when: datetime | None) -> str:
    if when is None:
        return ""
    secs = (datetime.now().astimezone() - when).total_seconds()
    if secs < 3600:
        return f"{max(1, int(secs // 60))}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    return when.strftime("%b %-d")


def pick(entries: list[dict], limit: int = 50) -> str | None:
    """Show the newest dictations in Omarchy's menu; return the chosen text."""
    if not shutil.which(MENU):
        raise SystemExit(f"{MENU} not found; use: chttr history --print")
    recent = list(reversed(entries[-limit:]))
    options = []
    for i, e in enumerate(recent):
        text = " ".join(str(e.get("text") or e.get("raw")).split())
        label = text if len(text) <= 90 else text[:89] + "…"
        sub = " · ".join(x for x in (f"#{i + 1}", _ago(_local(e.get("ts", ""))), e.get("app", "")) if x)
        options.append(f"{label}\t{sub}")
    out = subprocess.run([MENU, "Dictation history", *options, "--", "--width", "720"],
                         capture_output=True, text=True)
    choice = out.stdout.strip()
    if out.returncode != 0 or not choice:
        return None
    try:
        idx = int(choice.rsplit("\t", 1)[1].split(" ", 1)[0].lstrip("#")) - 1
    except (IndexError, ValueError):
        return None
    e = recent[idx]
    return str(e.get("text") or e.get("raw"))


def stats(entries: list[dict], days: int = 7) -> str:
    if not entries:
        return "no dictations in history yet"
    today = datetime.now().astimezone().date()
    per_day: Counter[str] = Counter()
    apps: Counter[str] = Counter()
    words = secs = 0
    for e in entries:
        n = len(str(e.get("text") or e.get("raw")).split())
        words += n
        secs += float(e.get("seconds") or 0)
        when = _local(e.get("ts", ""))
        if when:
            per_day[when.date().isoformat()] += n
        apps[e.get("app") or "unknown"] += n
    wpm = words / (secs / 60) if secs else 0
    saved = words / TYPING_WPM - secs / 60
    lines = [
        f"dictations   {len(entries)}",
        f"words        {words}",
        f"speaking     {wpm:.0f} wpm (vs ~{TYPING_WPM} typing)",
        f"time saved   {saved:.0f} min" if saved >= 1 else f"time saved   {saved * 60:.0f} s",
        "",
        f"last {days} days:",
    ]
    peak = max((per_day.get((today - timedelta(d)).isoformat(), 0) for d in range(days)), default=0) or 1
    for d in reversed(range(days)):
        day = today - timedelta(d)
        n = per_day.get(day.isoformat(), 0)
        bar = "█" * round(24 * n / peak) if n else ""
        lines.append(f"  {day.strftime('%a %b %d')}  {n:5}  {bar}")
    lines += ["", "top apps:"]
    for app, n in apps.most_common(5):
        lines.append(f"  {app[:24]:24} {n:5} words")
    return "\n".join(lines)
