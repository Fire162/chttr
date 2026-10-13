from __future__ import annotations

import hashlib
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from chttr import config as C
from chttr import integrations as I
from chttr import vault

MEETING = {
    "id": "2026-10-01-140000", "title": "Pricing review", "when": "2026-10-01T14:00-04:00", "minutes": 30,
    "speakers": {"Dana Lee": {}, "Ali Khan": {}},
    "notes": {"sections": [{"title": "Summary", "items": ["Budget set at $50k.", ""]},
                           {"title": "Risks", "items": []}],
              "action_items": [{"owner": "Ali Khan", "task": "Send the <revised> contract", "due": "Friday"}]},
    "event": {"attendees": [{"name": "Dana Lee", "email": "Dana@Acme.com", "is_me": False},
                            {"name": "Rob", "email": "rob@example.com", "is_me": True}]},
    "markdown": "/notes/x.md",
}


@pytest.fixture(autouse=True)
def file_vault(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(vault, "keyring_available", lambda: False)


def test_summary_drops_empty_sections_and_me():
    s = I.summary(MEETING)
    assert s["sections"] == [("Summary", ["Budget set at $50k."])]
    assert [a["email"] for a in s["attendees"]] == ["Dana@Acme.com"]


def test_slack_payload():
    p = I.slack_payload(I.summary(MEETING))
    assert p["text"] == "Meeting notes: Pricing review"
    assert p["blocks"][0] == {"type": "header", "text": {"type": "plain_text", "text": "Pricing review"}}
    text = json.dumps(p)
    assert "Budget set at $50k." in text and "*Ali Khan*: Send the <revised> contract" in text


def test_notion_blocks_have_headings_bullets_and_todos():
    blocks = I.notion_blocks(I.summary(MEETING))
    kinds = [b["type"] for b in blocks]
    assert kinds == ["paragraph", "heading_2", "bulleted_list_item", "heading_2", "to_do"]
    assert blocks[-1]["to_do"]["checked"] is False


def test_hubspot_html_is_escaped():
    html = I.hubspot_note_html(I.summary(MEETING))
    assert "&lt;revised&gt;" in html and "<revised>" not in html


def test_hubspot_flow_finds_contacts_then_logs_note_and_tasks(monkeypatch):
    vault.store("hubspot", "pat-test")
    calls = []

    def fake(url, payload=None, headers=None, method="POST", timeout=20):
        calls.append((url.removeprefix(I.HUBSPOT), payload, headers))
        return {"results": [{"id": "501"}]} if url.endswith("/search") else {"id": "1"}

    monkeypatch.setattr(I, "_request", fake)
    result = I.send("hubspot", MEETING)
    assert result == "logged in HubSpot on 1 contact(s), 1 task(s)"
    search, note, task = calls
    assert search[1]["filterGroups"][0]["filters"][0]["values"] == ["dana@acme.com"]
    assert search[2]["Authorization"] == "Bearer pat-test"
    assert note[0] == "/crm/v3/objects/notes"
    assert note[1]["associations"][0]["types"][0]["associationTypeId"] == I.NOTE_TO_CONTACT
    assert task[1]["properties"]["hs_task_subject"] == "Send the <revised> contract"


def test_hubspot_refuses_when_no_attendee_is_a_contact(monkeypatch):
    vault.store("hubspot", "pat-test")
    monkeypatch.setattr(I, "_request", lambda *a, **k: {"results": []})
    with pytest.raises(I.SendError, match="none of the attendees"):
        I.send("hubspot", MEETING)


def test_not_connected_errors():
    for d in ("slack", "notion", "hubspot", "webhook"):
        with pytest.raises(I.SendError):
            I.send(d, MEETING)


def test_webhook_posts_signed_json():
    got = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            got["body"] = self.rfile.read(int(self.headers["Content-Length"]))
            got["sig"] = self.headers["X-Chttr-Signature"]
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        vault.store("webhook", json.dumps({"url": f"http://127.0.0.1:{srv.server_address[1]}/hook", "secret": "s3"}))
        assert I.send("webhook", MEETING) == "sent to the webhook"
    finally:
        srv.shutdown()
        srv.server_close()
    assert json.loads(got["body"])["meeting"]["title"] == "Pricing review"
    assert got["sig"] == "sha256=" + hmac.new(b"s3", got["body"], hashlib.sha256).hexdigest()


def test_vault_file_fallback_is_private(tmp_path):
    import os

    assert vault.store("slack", "https://hooks.slack.com/services/x") == str(tmp_path / "secrets.json")
    assert vault.load("slack") == "https://hooks.slack.com/services/x"
    if os.name == "posix":
        assert (tmp_path / "secrets.json").stat().st_mode & 0o077 == 0
    assert I.connected() == ["slack"]
    assert vault.remove("slack") and vault.load("slack") == ""


def test_auto_destinations_follow_send_and_title_rules():
    vault.store("slack", "https://hooks.slack.com/services/x")
    cfg = C.Config()
    assert I.auto_destinations(cfg, "Pricing review") == []                  # "ask" by default
    cfg.integrations.send = "always"
    assert I.auto_destinations(cfg, "Pricing review") == ["slack"]
    cfg.integrations.auto_titles = r"\bcustomer\b"
    assert I.auto_destinations(cfg, "Pricing review") == []
    assert I.auto_destinations(cfg, "Customer call: Acme") == ["slack"]


def test_hook_gets_the_markdown_path_and_json(tmp_path):
    import os
    import shutil

    if os.name != "posix" or not shutil.which("sh"):
        pytest.skip("needs sh")
    out = tmp_path / "out"
    I.run_hook(f'cat > {out}.json; echo "$1" > {out}.path', MEETING)
    assert json.loads((tmp_path / "out.json").read_text())["title"] == "Pricing review"
    assert (tmp_path / "out.path").read_text().strip() == "/notes/x.md"
