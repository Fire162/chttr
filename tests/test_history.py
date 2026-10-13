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
    monkeypatch.setattr(history, "HISTORY_FILE", path)
    entries = history.load()
    assert len(entries) == 2
    out = history.stats(entries, days=3)
    assert "dictations   2" in out
    assert "words        12" in out
    assert "80 wpm" in out
    assert "Slack" in out and "kitty" in out


def test_stats_with_no_history():
    assert history.stats([]) == "no dictations in history yet"
