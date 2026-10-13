"""Notice calls in any app, without a bot: watch which programs hold the microphone.

PipeWire lists every audio stream with the program behind it (`pw-dump`). When a
call app (Zoom, Teams, Slack, Discord, Webex, ...) or a browser starts capturing
the mic and keeps it for a few seconds, chttr offers to record, just as it does
for Google Meet. When the app lets go of the mic, the recording stops.

Google Meet itself is handled by the browser extension (it also knows who is
speaking), so a browser call is only offered here when the extension isn't
already reporting one.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass

log = logging.getLogger("chttr.calls")

# process binary / application name (lowercase) -> label shown to you
CALL_APPS = {
    "zoom": "Zoom", "zoom.real": "Zoom", "zoomwebinar": "Zoom",
    "teams": "Microsoft Teams", "teams-for-linux": "Microsoft Teams", "ms-teams": "Microsoft Teams",
    "slack": "Slack", "discord": "Discord", "vesktop": "Discord", "webcord": "Discord",
    "webex": "Webex", "ciscocollabhost": "Webex", "skypeforlinux": "Skype", "skype": "Skype",
    "signal-desktop": "Signal", "signal": "Signal", "telegram-desktop": "Telegram", "telegram": "Telegram",
    "element-desktop": "Element", "jitsi-meet": "Jitsi Meet", "whatsapp": "WhatsApp", "mumble": "Mumble",
}
BROWSERS = {"chrome": "Chrome", "chromium": "Chromium", "brave": "Brave", "firefox": "Firefox",
            "vivaldi": "Vivaldi", "msedge": "Edge", "microsoft-edge": "Edge", "opera": "Opera", "zen": "Zen",
            "librewolf": "LibreWolf", "thorium": "Thorium"}
OURS = {"pw-record", "pw-cat", "chttr", "python3", "python"}       # our own recorders


@dataclass(frozen=True)
class MicUser:
    binary: str
    app: str
    node: str

    @property
    def key(self) -> str:
        return (self.binary or self.app).lower()


def mic_users(dump: list[dict]) -> list[MicUser]:
    """Programs with a running capture stream from a microphone (not a monitor of
    the speakers), from `pw-dump` output."""
    users = []
    for obj in dump:
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        info = obj.get("info") or {}
        props = info.get("props") or {}
        if props.get("media.class") != "Stream/Input/Audio" or info.get("state") != "running":
            continue
        if str(props.get("stream.capture.sink", "")).lower() == "true":
            continue                                  # recording the speakers, not the mic
        binary = str(props.get("application.process.binary", "")).strip()
        app = str(props.get("application.name", "")).strip()
        if binary.lower() in OURS and "chttr" in (app.lower() + str(props.get("node.name", "")).lower() + binary):
            continue
        if binary.lower() in ("pw-record", "pw-cat"):
            continue
        users.append(MicUser(binary, app, str(props.get("node.name", ""))))
    return users


def classify(user: MicUser) -> tuple[str, bool] | None:
    """(label, is_browser) for a call app or browser, else None."""
    for name in (user.binary.lower(), user.app.lower()):
        base = name.rsplit("/", 1)[-1]
        for key, label in CALL_APPS.items():
            if base == key or base.startswith(key + "-") or base.startswith(key + " "):
                return label, False
        for key, label in BROWSERS.items():
            if base.startswith(key):
                return label, True
    return None


def current_call(users: list[MicUser], meet_call_active: bool) -> str | None:
    """The app a call is probably happening in, or None. A dedicated call app wins;
    a browser counts unless the Meet extension already reports its call."""
    found = [c for u in users if (c := classify(u))]
    apps = [label for label, browser in found if not browser]
    if apps:
        return apps[0]
    browsers = [label for label, browser in found if browser]
    if browsers and not meet_call_active:
        return browsers[0]
    return None


def snapshot(timeout: float = 3.0) -> list[MicUser]:
    try:
        out = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=timeout)
        return mic_users(json.loads(out.stdout or "[]"))
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        log.debug("pw-dump failed: %s", exc)
        return []


class CallWatcher:
    """Turns mic-holder snapshots into "a call started in X" / "it ended" decisions."""

    def __init__(self, start_after: float = 5.0, end_after: float = 20.0) -> None:
        self.start_after = start_after
        self.end_after = end_after
        self.app: str | None = None         # app seen holding the mic
        self.since = 0.0                     # ... since this time
        self.last_seen = 0.0
        self.offered: str | None = None      # app we already asked about, until it lets go

    def update(self, app: str | None, now: float) -> tuple[str, str] | None:
        """Feed the current call app (or None); returns ("start", app) once a call has
        lasted start_after seconds, ("end", app) once it has been gone end_after
        seconds, else None."""
        if app:
            if app != self.app:
                self.app, self.since = app, now
            self.last_seen = now
            if self.offered != app and now - self.since >= self.start_after:
                self.offered = app
                return "start", app
            return None
        if self.app and now - self.last_seen >= self.end_after:
            ended, self.app, self.offered = self.app, None, None
            return "end", ended
        return None
