"""The bridge between the Meet extension and the daemon, and its installer.

Chromium starts `chttr native-host` (through a tiny wrapper script, because a
native messaging manifest can't pass arguments) when the extension connects,
and talks to it over stdin/stdout: each message is a 4-byte little-endian
length followed by JSON. Every message is forwarded to the daemon's socket as
`meet <json>`; the daemon's reply goes back to the extension.
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import struct
import sys
from pathlib import Path

from . import config as C
from .client import DaemonNotRunning, send

HOST_NAME = "com.chttr.meet"
EXTENSION_DIR = Path(__file__).with_name("extension")
BROWSERS = [               # (executable, config dir, flags file) of Chromium-family browsers
    ("chromium", Path.home() / ".config/chromium", Path.home() / ".config/chromium-flags.conf"),
    ("google-chrome-stable", Path.home() / ".config/google-chrome", Path.home() / ".config/chrome-flags.conf"),
    ("brave", Path.home() / ".config/BraveSoftware/Brave-Browser", Path.home() / ".config/brave-flags.conf"),
]
BROWSER_DIRS = [(d, f) for exe, d, f in BROWSERS if shutil.which(exe)]


def run() -> int:
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    while True:
        header = stdin.read(4)
        if len(header) < 4:
            return 0
        (length,) = struct.unpack("<I", header)
        msg: dict = {}
        try:
            msg = json.loads(stdin.read(length))
            reply = send("meet " + json.dumps(msg, separators=(",", ":")), timeout=10)
        except DaemonNotRunning as exc:
            reply = {"ok": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - never die on one bad message
            reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if isinstance(msg, dict) and "tab" in msg:
            reply["tab"] = msg["tab"]
        data = json.dumps(reply).encode()
        stdout.write(struct.pack("<I", len(data)) + data)
        stdout.flush()


def extension_id() -> str:
    """Chromium derives an unpacked extension's ID from the manifest's public key."""
    key = json.loads((EXTENSION_DIR / "manifest.json").read_text())["key"]
    digest = hashlib.sha256(base64.b64decode(key)).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in digest)


def _chttr_command() -> str:
    exe = shutil.which("chttr") or str(Path.home() / ".local/bin/chttr")
    return f'exec "{exe}" native-host' if Path(exe).exists() else f'exec "{sys.executable}" -m chttr native-host'


def _is_chttr_extension(path: Path) -> bool:
    try:
        return json.loads((path / "manifest.json").read_text()).get("name") == "chttr for Google Meet"
    except (OSError, ValueError):
        return path.name == "extension" and "chttr" in str(path)


def _add_load_extension(flags: Path, ext: str) -> str:
    """Add the extension to the browser's --load-extension flag (Omarchy loads its
    own extensions the same way). Returns what was done."""
    lines = flags.read_text().splitlines() if flags.exists() else []
    for i, line in enumerate(lines):
        if line.strip().startswith("--load-extension="):
            paths = line.strip().split("=", 1)[1].split(",")
            if ext in paths:
                return "already loaded"
            # another checkout of this extension (same key, same ID) would clash
            paths = [x for x in paths if not _is_chttr_extension(Path(x))]
            backup = flags.with_name(flags.name + ".bak-chttr")
            if not backup.exists():
                backup.write_text("\n".join(lines) + "\n")
            lines[i] = "--load-extension=" + ",".join(paths + [ext])
            flags.write_text("\n".join(lines) + "\n")
            return f"added to {flags}"
    lines.append(f"--load-extension={ext}")
    flags.write_text("\n".join(lines) + "\n")
    return f"added to {flags}"


def install() -> list[str]:
    done = []
    wrapper = C.DATA_DIR / "native-host"
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text(f"#!/bin/sh\n# Started by the browser for the chttr Meet extension.\n{_chttr_command()}\n")
    wrapper.chmod(0o755)
    manifest = {
        "name": HOST_NAME,
        "description": "chttr: Google Meet calls and captions to the chttr daemon",
        "path": str(wrapper),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{extension_id()}/"],
    }
    for config_dir, flags in BROWSER_DIRS:
        if not config_dir.exists():
            continue
        hosts = config_dir / "NativeMessagingHosts"
        hosts.mkdir(exist_ok=True)
        (hosts / f"{HOST_NAME}.json").write_text(json.dumps(manifest, indent=2) + "\n")
        done.append(f"native host registered for {config_dir.name}")
        done.append(f"extension {_add_load_extension(flags, str(EXTENSION_DIR))}")
    return done
