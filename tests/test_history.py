from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from chttr import history


def test_stats_counts_words_speed_and_apps(tmp_path, monkeypatch):
    path = tmp_path / "history.jsonl"
    now = datetime.now(UTC)
    rows = [
        {"ts": now.isoformat(), "seconds": 6, "app": "Slack", "text": "one two three four five six seven eight nine ten"},
        {"ts": (now - timedelta(days=1)).isoformat(), "seconds": 3, "app": "kitty", "raw": "git status"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
    monkeypatch.setattr(history.C, "HISTORY_FILE", path)
    entries = history.load()
    assert len(entries) == 2
    out = history.stats(entries, days=3)
    assert "dictations   2" in out
    assert "words        12" in out
    assert "80 wpm" in out
    assert "Slack" in out and "kitty" in out


def test_stats_with_no_history():
    assert history.stats([]) == "no dictations in history yet"


def test_pick_gives_the_menu_an_icon_label_and_subtext(monkeypatch):
    from chttr import desktop

    shown = []

    def fake_menu(title, options):
        shown.extend(options)
        return desktop.match_option("\t".join(options[1].split("\t")[1:]), options)   # Omarchy drops the icon

    monkeypatch.setattr(desktop, "menu", fake_menu)
    entries = [{"ts": "2026-10-13T13:52:00+00:00", "app": "foot", "text": "first one", "output": "type"},
               {"ts": "2026-10-13T13:53:00+00:00", "app": "chromium", "text": "second one", "output": "type"}]
    assert history.pick(entries) == "first one"
    icon, label, sub = shown[0].split("\t")
    assert icon and label == "second one" and sub.startswith("#1 · ") and sub.endswith("chromium")
