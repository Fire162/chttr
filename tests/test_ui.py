from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from chttr import ui as U
from tests.test_library import lib  # noqa: F401 - the fixture


@pytest.fixture
def server(lib):  # noqa: F811
    srv = ThreadingHTTPServer(("127.0.0.1", 0), U.Handler)
    port = srv.server_address[1]
    U.Handler.token, U.Handler.host = "secret", f"127.0.0.1:{port}"
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()
    srv.server_close()


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def post(url, body, headers=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers or {}, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


TOKEN = {"X-Chttr-Token": "secret"}


def test_page_and_assets_are_served_without_the_token(server):
    status, body, headers = get(server + "/")
    assert status == 200 and b"static/app.js" in body
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    assert get(server + "/static/app.js")[0] == 200
    assert get(server + "/static/../ui.py")[0] == 404


def test_api_needs_the_token(server):
    assert get(server + "/api/meetings")[0] == 403
    assert get(server + "/api/meetings", {"X-Chttr-Token": "wrong"})[0] == 403
    status, body, _ = get(server + "/api/meetings", TOKEN)
    assert status == 200 and [m["title"] for m in json.loads(body)] == ["Launch sync", "Pricing review"]


def test_api_rejects_a_foreign_host_header(server):
    # a page on evil.example resolving to 127.0.0.1 (DNS rebinding) sends its own Host
    assert get(server + "/api/meetings", {**TOKEN, "Host": "evil.example"})[0] == 403


def test_search_meeting_and_actions(server):
    hits = json.loads(get(server + "/api/search?q=budget", TOKEN)[1])
    assert any(h["title"] == "Pricing review" for h in hits)
    m = json.loads(get(server + "/api/meeting/2026-10-01-140000", TOKEN)[1])
    assert m["title"] == "Pricing review" and m["has_audio"] is False
    assert get(server + "/api/meeting/does-not-exist", TOKEN)[0] == 404
    assert json.loads(get(server + "/api/actions?owner=ali", TOKEN)[1])[0]["due"] == "Friday"


def test_posts_need_the_header_not_just_the_query_token(server, monkeypatch):
    status, _ = post(server + "/api/meeting/2026-10-01-140000/rename?token=secret", {"old": "Ali", "new": "Ali Khan"})
    assert status == 403                                     # a cross-site form can't set headers
    import chttr.meeting as M

    renamed = {}
    monkeypatch.setattr(M, "rename", lambda d, old, new, email, cfg: renamed.update(old=old, new=new))
    status, _ = post(server + "/api/meeting/2026-10-01-140000/rename", {"old": "Ali", "new": "Ali Khan"}, TOKEN)
    assert status == 200 and renamed == {"old": "Ali", "new": "Ali Khan"}
    status, _ = post(server + "/api/meeting/2026-10-01-140000/rename", {"old": "", "new": "x"}, TOKEN)
    assert status == 400


@pytest.fixture
def lingo_files(monkeypatch, tmp_path):
    from chttr import dictionary, history

    log = tmp_path / "history.jsonl"
    log.write_text("\n".join(json.dumps(e) for e in [
        {"ts": "2026-10-13T13:52:37+00:00", "kind": "dictate", "app": "foot",
         "raw": "So my name is Sora Beth Ani.", "text": "So my name is Sora Beth Ani."},
        {"ts": "2026-10-13T13:53:00+00:00", "kind": "command", "raw": "make it shorter", "text": "Short."},
    ]) + "\n")
    monkeypatch.setattr(history.C, "HISTORY_FILE", log)
    dictionary.DICTIONARY_FILE.unlink(missing_ok=True)
    yield
    dictionary.DICTIONARY_FILE.unlink(missing_ok=True)


def test_lingo_needs_the_token_and_the_header(server, lingo_files):
    assert get(server + "/api/lingo")[0] == 403
    assert post(server + "/api/lingo/learn?token=secret", {"word": "Sohrab"})[0] == 403


def test_lingo_learns_from_a_dictation_and_applies_it(server, lingo_files):
    state = json.loads(get(server + "/api/lingo", TOKEN)[1])
    assert state["words"] == []
    assert [r["heard"] for r in state["recent"]] == ["So my name is Sora Beth Ani."]   # commands are left out

    status, state = post(server + "/api/lingo/learn", {"word": "Sohrab Behbahani", "heard": ["Sora Beth Ani"]}, TOKEN)
    assert status == 200
    assert state["words"] == [{"word": "Sohrab Behbahani", "heard": ["sora beth ani"], "vocabulary": True}]
    status, out = post(server + "/api/lingo/try", {"text": "So my name is Sora, Beth Ani."}, TOKEN)
    assert status == 200 and out["typed"] == "So my name is Sohrab Behbahani."

    from chttr.dictionary import Dictionary

    assert Dictionary.load().replacements == {"sora beth ani": "Sohrab Behbahani"}   # saved for the daemon


def test_lingo_rename_unhear_and_forget(server, lingo_files):
    post(server + "/api/lingo/learn", {"word": "Sorab", "heard": ["so rob", "sorob"]}, TOKEN)
    status, state = post(server + "/api/lingo/rename", {"old": "Sorab", "new": "Sohrab"}, TOKEN)
    assert status == 200 and state["words"][0] == {"word": "Sohrab", "heard": ["so rob", "sorob"], "vocabulary": True}
    status, state = post(server + "/api/lingo/unhear", {"heard": "sorob"}, TOKEN)
    assert status == 200 and state["words"][0]["heard"] == ["so rob"]
    status, state = post(server + "/api/lingo/forget", {"word": "Sohrab"}, TOKEN)
    assert status == 200 and state["words"] == []


def test_lingo_rejects_bad_input(server, lingo_files):
    assert post(server + "/api/lingo/learn", {"word": "  ", "heard": ["x"]}, TOKEN)[0] == 400
    assert post(server + "/api/lingo/learn", {"word": "x" * 500}, TOKEN)[0] == 400
    assert post(server + "/api/lingo/rename", {"old": "Nobody", "new": "Somebody"}, TOKEN)[0] == 400
    assert post(server + "/api/lingo/forget", {"word": "Nobody"}, TOKEN)[0] == 400
    assert post(server + "/api/lingo/drop-tables", {}, TOKEN)[0] == 404


def test_idle_server_stops_itself(monkeypatch):
    stopped = threading.Event()

    class Fake:
        def shutdown(self):
            stopped.set()

    U.Handler.last_request = 0.0
    monkeypatch.setattr(U.time, "monotonic", lambda: 1000.0)
    t = threading.Thread(target=U._stop_when_idle, args=(Fake(), 60), daemon=True)
    t.start()
    assert stopped.wait(2)
