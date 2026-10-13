"""Send a meeting's notes where your team works: Slack, Notion, HubSpot, a webhook,
or your own script.

Connect once with `chttr connect slack|notion|hubspot|webhook` (secrets go to the
keyring, see vault.py). After each meeting the "notes saved" notification offers
a button per connected destination ([integrations] send = "ask", the default);
nothing is sent without that click unless you set send = "always" (optionally
only for calendar titles matching auto_titles). `chttr send` does it by hand.

All requests use the standard library; nothing here runs unless you connect it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from . import vault

log = logging.getLogger("chttr.integrations")

DESTINATIONS = ("slack", "notion", "hubspot", "webhook")
NOTION_VERSION = "2022-06-28"
HUBSPOT = "https://api.hubapi.com"
NOTE_TO_CONTACT, TASK_TO_CONTACT = 202, 204            # HubSpot-defined association types


class SendError(RuntimeError):
    pass


def _request(url: str, payload: dict | None = None, headers: dict | None = None, method: str = "POST",
             timeout: float = 20) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"Content-Type": "application/json", "User-Agent": "chttr", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode(errors="replace")
        raise SendError(f"{exc.code} from {urllib.parse.urlparse(url).netloc}: {detail}") from None
    except OSError as exc:
        raise SendError(f"couldn't reach {urllib.parse.urlparse(url).netloc}: {exc}") from None
    try:
        return json.loads(raw or b"{}")
    except ValueError:
        return {"raw": raw[:200].decode(errors="replace")}


def connected() -> list[str]:
    return [d for d in DESTINATIONS if vault.load(d)]


# ---- what a meeting looks like to the outside ------------------------------------

def summary(meeting: dict) -> dict:
    """The parts every destination uses, from library.meeting()."""
    notes = meeting.get("notes") or {}
    sections = [(s.get("title", ""), [str(x) for x in s.get("items", []) if str(x).strip()])
                for s in notes.get("sections", [])]
    return {
        "title": meeting.get("title") or "Meeting",
        "when": meeting.get("when", ""),
        "minutes": meeting.get("minutes", 0),
        "speakers": list((meeting.get("speakers") or {}).keys()),
        "sections": [(t, items) for t, items in sections if items],
        "actions": notes.get("action_items", []),
        "attendees": [a for a in ((meeting.get("event") or {}).get("attendees") or []) if not a.get("is_me")],
        "markdown": meeting.get("markdown", ""),
    }


def _when_human(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%a %b %d, %H:%M")
    except ValueError:
        return iso


# ---- Slack -------------------------------------------------------------------------

def slack_payload(s: dict) -> dict:
    def cut(text: str, n: int = 2900) -> str:
        return text if len(text) <= n else text[: n - 1] + "…"

    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": cut(s["title"], 150)}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": cut(
            f"{_when_human(s['when'])} · {s['minutes']} min · {', '.join(s['speakers'][:8])}", 300)}]},
    ]
    for title, items in s["sections"][:8]:
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
                                                   "text": cut(f"*{title}*\n" + "\n".join(f"• {x}" for x in items))}})
    if s["actions"]:
        lines = [f"☐ {'*' + a['owner'] + '*: ' if a.get('owner') else ''}{a.get('task', '')}"
                 + (f" _(due {a['due']})_" if a.get("due") else "") for a in s["actions"]]
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": cut("*Action items*\n" + "\n".join(lines))}})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": "Notes by chttr"}]})
    return {"text": f"Meeting notes: {s['title']}", "blocks": blocks[:50]}


def send_slack(s: dict) -> str:
    url = vault.load("slack")
    if not url.startswith("https://hooks.slack.com/"):
        raise SendError("Slack isn't connected (chttr connect slack)")
    _request(url, slack_payload(s))
    return "posted to Slack"


# ---- Notion ------------------------------------------------------------------------

def _rich(text: str) -> list[dict]:
    return [{"type": "text", "text": {"content": text[i:i + 1900]}} for i in range(0, max(len(text), 1), 1900)][:10]


def notion_blocks(s: dict) -> list[dict]:
    blocks = [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": _rich(
        f"{_when_human(s['when'])} · {s['minutes']} min · {', '.join(s['speakers'])}")}}]
    for title, items in s["sections"]:
        blocks.append({"object": "block", "type": "heading_2", "heading_2": {"rich_text": _rich(title)}})
        blocks += [{"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": _rich(x)}}
                   for x in items]
    if s["actions"]:
        blocks.append({"object": "block", "type": "heading_2", "heading_2": {"rich_text": _rich("Action items")}})
        blocks += [{"object": "block", "type": "to_do", "to_do": {"checked": False, "rich_text": _rich(
            (f"{a['owner']}: " if a.get("owner") else "") + a.get("task", "") + (f" (due {a['due']})" if a.get("due") else ""))}}
            for a in s["actions"]]
    return blocks[:100]


def send_notion(s: dict) -> str:
    cfg = json.loads(vault.load("notion") or "{}")
    token, parent = cfg.get("token", ""), cfg.get("parent", "")
    if not token or not parent:
        raise SendError("Notion isn't connected (chttr connect notion)")
    page = _request("https://api.notion.com/v1/pages", {
        "parent": {"page_id": parent},
        "properties": {"title": {"title": _rich(s["title"])}},
        "children": notion_blocks(s),
    }, {"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION})
    return f"saved to Notion{': ' + page['url'] if page.get('url') else ''}"


# ---- HubSpot ---------------------------------------------------------------------------

def hubspot_note_html(s: dict) -> str:
    def esc(x: str) -> str:
        return x.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    parts = [f"<p><strong>{esc(s['title'])}</strong> · {esc(_when_human(s['when']))} · {s['minutes']} min</p>"]
    for title, items in s["sections"]:
        parts.append(f"<p><strong>{esc(title)}</strong></p><ul>" + "".join(f"<li>{esc(x)}</li>" for x in items) + "</ul>")
    if s["actions"]:
        parts.append("<p><strong>Action items</strong></p><ul>" + "".join(
            f"<li>{esc((a.get('owner', '') + ': ') if a.get('owner') else '')}{esc(a.get('task', ''))}</li>"
            for a in s["actions"]) + "</ul>")
    return "".join(parts)


def send_hubspot(s: dict, tasks: bool = True) -> str:
    token = vault.load("hubspot")
    if not token:
        raise SendError("HubSpot isn't connected (chttr connect hubspot)")
    auth = {"Authorization": f"Bearer {token}"}
    emails = sorted({a["email"].lower() for a in s["attendees"] if a.get("email")})
    contacts: list[str] = []
    if emails:
        found = _request(f"{HUBSPOT}/crm/v3/objects/contacts/search", {
            "filterGroups": [{"filters": [{"propertyName": "email", "operator": "IN", "values": emails}]}],
            "properties": ["email"], "limit": 100}, auth)
        contacts = [r["id"] for r in found.get("results", [])]
    if not contacts:
        raise SendError("none of the attendees are HubSpot contacts; nothing to attach the notes to")
    when = s["when"] or datetime.now().astimezone().isoformat()
    _request(f"{HUBSPOT}/crm/v3/objects/notes", {
        "properties": {"hs_timestamp": when, "hs_note_body": hubspot_note_html(s)},
        "associations": [{"to": {"id": c}, "types": [{"associationCategory": "HUBSPOT_DEFINED",
                                                      "associationTypeId": NOTE_TO_CONTACT}]} for c in contacts]}, auth)
    made = 0
    for a in s["actions"] if tasks else []:
        _request(f"{HUBSPOT}/crm/v3/objects/tasks", {
            "properties": {"hs_timestamp": when, "hs_task_subject": a.get("task", "")[:250],
                           "hs_task_body": (f"Owner in the meeting: {a['owner']}. " if a.get("owner") else "")
                                           + (f"Due: {a['due']}. " if a.get("due") else "") + f"From: {s['title']}",
                           "hs_task_status": "NOT_STARTED"},
            "associations": [{"to": {"id": c}, "types": [{"associationCategory": "HUBSPOT_DEFINED",
                                                          "associationTypeId": TASK_TO_CONTACT}]} for c in contacts]},
            auth)
        made += 1
    return f"logged in HubSpot on {len(contacts)} contact(s)" + (f", {made} task(s)" if made else "")


# ---- webhook and script ----------------------------------------------------------------

def send_webhook(meeting: dict) -> str:
    cfg = json.loads(vault.load("webhook") or "{}")
    url, secret = cfg.get("url", ""), cfg.get("secret", "")
    if not url:
        raise SendError("no webhook connected (chttr connect webhook)")
    body = json.dumps({"event": "meeting.notes", "meeting": meeting}, ensure_ascii=False).encode()
    headers = {"X-Chttr-Event": "meeting.notes"}
    if secret:
        headers["X-Chttr-Signature"] = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "chttr", **headers})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read()
    except (urllib.error.HTTPError, OSError) as exc:
        raise SendError(f"webhook failed: {exc}") from None
    return "sent to the webhook"


def run_hook(command: str, meeting: dict) -> None:
    """[integrations] on_meeting_done: your own script, given the Markdown path as $1
    and the meeting as JSON on stdin. Runs for every meeting, without asking."""
    if not command:
        return
    md = meeting.get("markdown", "")
    try:
        subprocess.run(["sh", "-c", command + ' "$1"', "chttr-hook", md], input=json.dumps(meeting).encode(),
                       timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("on_meeting_done hook failed: %s", exc)


# ---- glue ----------------------------------------------------------------------------------

def send(destination: str, meeting: dict, hubspot_tasks: bool = True) -> str:
    s = summary(meeting)
    if destination == "slack":
        return send_slack(s)
    if destination == "notion":
        return send_notion(s)
    if destination == "hubspot":
        return send_hubspot(s, tasks=hubspot_tasks)
    if destination == "webhook":
        return send_webhook(meeting)
    raise SendError(f"unknown destination {destination!r}")


def auto_destinations(cfg, title: str) -> list[str]:
    """Where to send without asking: everything connected when send = "always" and the
    title matches auto_titles (an empty pattern matches every meeting)."""
    ic = cfg.integrations
    if ic.send != "always":
        return []
    try:
        if ic.auto_titles and not re.search(ic.auto_titles, title or "", re.IGNORECASE):
            return []
    except re.error:
        return []
    return connected()


def label(destination: str) -> str:
    return {"slack": "Slack", "notion": "Notion", "hubspot": "HubSpot", "webhook": "webhook"}[destination]


def markdown_path(meeting: dict) -> Path | None:
    md = meeting.get("markdown")
    return Path(md) if md else None
