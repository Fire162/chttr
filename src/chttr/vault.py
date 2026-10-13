"""Secrets for integrations (Slack webhook, Notion and HubSpot tokens).

Stored in the desktop keyring through libsecret's `secret-tool` (GNOME Keyring,
KWallet's Secret Service, KeePassXC). Without it, they go to
~/.config/chttr/secrets.json, readable only by you, and `chttr doctor` says so.
Never in config.toml.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess

from . import config as C

log = logging.getLogger("chttr.vault")
SERVICE = "chttr"


def _file():
    return C.CONFIG_DIR / "secrets.json"


def keyring_available() -> bool:
    return shutil.which("secret-tool") is not None and bool(os.environ.get("DBUS_SESSION_BUS_ADDRESS"))


def _read_file() -> dict:
    try:
        return json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_file(data: dict) -> None:
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)


def store(name: str, value: str) -> str:
    """Save a secret; returns where it went ("keyring" or the file path)."""
    if keyring_available():
        out = subprocess.run(["secret-tool", "store", f"--label=chttr {name}", "service", SERVICE, "account", name],
                             input=value, text=True, capture_output=True, timeout=30)
        if out.returncode == 0:
            data = _read_file()
            if data.pop(name, None) is not None:
                _write_file(data)
            return "keyring"
        log.warning("secret-tool store failed (%s); using %s", out.stderr.strip(), _file())
    data = _read_file()
    data[name] = value
    _write_file(data)
    return str(_file())


def load(name: str) -> str:
    if keyring_available():
        try:
            out = subprocess.run(["secret-tool", "lookup", "service", SERVICE, "account", name],
                                 capture_output=True, text=True, timeout=10)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    return str(_read_file().get(name, ""))


def remove(name: str) -> bool:
    found = False
    if keyring_available():
        found = subprocess.run(["secret-tool", "clear", "service", SERVICE, "account", name],
                               capture_output=True, timeout=10).returncode == 0
    data = _read_file()
    if data.pop(name, None) is not None:
        _write_file(data)
        found = True
    return found
