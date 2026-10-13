from __future__ import annotations

import json
from datetime import datetime

import pytest

from chttr import config as C
from chttr import library as L


def make_meeting(root, mid, started, title, lines, notes=None):
    d = root / mid
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"id": mid, "started": started}), encoding="utf-8")
    result = {
        "meta": {"id": mid, "started": started, "title": title}, "duration": 1800.0, "event": {"title": title},
        "speakers": {s: {"seconds": 60, "source": "captions"} for s in {sp for sp, _, _ in lines}},
        "utterances": [{"start": t, "end": t + 3, "channel": "them", "speaker": sp, "text": text}
                       for sp, t, text in lines],
        "notes": notes or {}, "markdown": f"/notes/{mid}.md",
    }
    (d / "result.json").write_text(json.dumps(result), encoding="utf-8")
    return d


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "MEETINGS_DATA_DIR", tmp_path / "meetings")
    monkeypatch.setattr(C, "HISTORY_FILE", tmp_path / "history.jsonl")
    monkeypatch.setattr(L, "DB_FILE", tmp_path / "library.sqlite")
    oct1 = datetime(2026, 10, 1, 14, 0).astimezone().timestamp()
    oct8 = datetime(2026, 10, 8, 14, 0).astimezone().timestamp()
    make_meeting(tmp_path / "meetings", "2026-10-01-140000", oct1, "Pricing review",
                 [("Dana", 10, "We agreed the budget is fifty thousand dollars."),
                  ("Ali", 40, "I will send the revised contract by Friday.")],
                 {"sections": [{"title": "Summary", "items": ["Budget set at $50k."]}],
                  "action_items": [{"owner": "Ali", "task": "Send the revised contract", "due": "Friday"}]})
    make_meeting(tmp_path / "meetings", "2026-10-08-140000", oct8, "Launch sync",
                 [("Dana", 5, "The launch moves to November."), ("Rob", 20, "Then the budget needs a second look.")])
    (tmp_path / "history.jsonl").write_text(json.dumps({"ts": "2026-10-09T10:00:00+00:00", "app": "Slack",
                                                        "text": "ping the vendor about the contract"}) + "\n",
                                            encoding="utf-8")
    return tmp_path


def test_fts_query_quotes_every_word():
    assert L.fts_query('budget AND "drop table" NEAR(x)') == '"budget" "and" "drop" "table" "near"'
    assert L.fts_query("a b", any_word=True) == ""
    assert L.fts_query("budget contract", any_word=True) == '"budget" OR "contract"'


def test_search_ranks_lines_and_notes(lib):
    hits = L.search("budget")
    kinds = {(h.kind, h.meeting_id) for h in hits}
    assert ("line", "2026-10-01-140000") in kinds and ("line", "2026-10-08-140000") in kinds
    line = next(h for h in hits if h.kind == "line" and h.speaker == "Dana")
    assert "**budget**" in line.snippet and line.title == "Pricing review" and line.start == 10


def test_search_filters_by_speaker_and_date(lib):
    assert {h.speaker for h in L.search("budget", speaker="rob")} == {"Rob"}
    assert {h.meeting_id for h in L.search("budget", after="2026-10-05")} == {"2026-10-08-140000"}
    assert {h.meeting_id for h in L.search("budget", before="2026-10-02")} == {"2026-10-01-140000"}


def test_search_falls_back_to_any_word_and_includes_dictations(lib):
    hits = L.search("contract unicorn", include_dictations=True)
    assert any(h.kind == "line" for h in hits)
    assert any(h.kind == "dictation" and h.title == "Slack" for h in hits)


def test_stemming_matches_word_forms(lib):
    assert L.search("agreeing")                          # porter stemmer: agreed / agreeing


def test_meetings_actions_and_context(lib):
    ms = L.meetings()
    assert [m["title"] for m in ms] == ["Launch sync", "Pricing review"]
    assert L.action_items(owner="ali")[0]["task"] == "Send the revised contract"
    ctx = L.context_around("2026-10-01-140000", 30, seconds=20)
    assert [c["speaker"] for c in ctx] == ["Dana", "Ali"]
    m = L.meeting("2026-10-01")
    assert m and m["title"] == "Pricing review" and len(m["transcript"]) == 2


def test_sync_is_incremental_and_forgets_deleted_meetings(lib):
    import shutil

    with L.connect() as conn:
        assert L.sync(conn) == 2
        assert L.sync(conn) == 0
        shutil.rmtree(lib / "meetings" / "2026-10-08-140000")
        L.sync(conn)
        assert [r["id"] for r in conn.execute("SELECT id FROM meetings")] == ["2026-10-01-140000"]
        assert not conn.execute("SELECT 1 FROM lines_fts WHERE meeting_id = '2026-10-08-140000'").fetchall()
