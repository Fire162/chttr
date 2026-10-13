"""Deliver text to the focused window."""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time

from .config import OutputCfg

log = logging.getLogger("chttr.output")


def focused_window() -> tuple[str, str]:
    """(class, title) of the active Hyprland window, or empty strings."""
    try:
        out = subprocess.run(["hyprctl", "activewindow", "-j"], capture_output=True, text=True, timeout=2)
        data = json.loads(out.stdout or "{}")
        return str(data.get("class") or data.get("initialClass") or ""), str(data.get("title") or "")
    except Exception:  # noqa: BLE001 - best effort only
        return "", ""


def _paste(primary: bool = False) -> str:
    cmd = ["wl-paste", "--no-newline"] + (["--primary"] if primary else [])
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout.decode(errors="replace") if out.returncode == 0 else ""


def _clipboard_has_text() -> bool:
    try:
        types = subprocess.run(["wl-paste", "--list-types"], capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return any(t.startswith("text/") or t in ("UTF8_STRING", "STRING", "TEXT") for t in types.split())


def grab_selection(terminal: bool) -> str:
    """The text selected in the focused window, or "".

    Terminals: the primary selection (Ctrl+C would interrupt the shell). Elsewhere:
    copy with Ctrl+C into an emptied clipboard and read it back, so a stale
    selection is never mistaken for a current one. A text clipboard is restored
    afterwards; an image on the clipboard is lost.
    """
    if terminal:
        return _paste(primary=True)
    saved = _paste() if _clipboard_has_text() else None
    subprocess.run(["wl-copy", "--clear"], timeout=2, check=False)
    try:
        subprocess.run(["hyprctl", "dispatch", "sendshortcut", "CTRL, C,"], capture_output=True, timeout=2, check=False)
        selection = ""
        for _ in range(8):           # the app answers asynchronously
            time.sleep(0.05)
            selection = _paste()
            if selection:
                break
    finally:
        if saved:
            copy_to_clipboard(saved)
    return selection


def copy_to_clipboard(text: str) -> None:
    subprocess.run(["wl-copy", "--", text], check=True, timeout=5)


def type_text(text: str) -> None:
    subprocess.run(["wtype", "--", text], check=True, capture_output=True, timeout=60)


def paste_shortcut() -> None:
    subprocess.run(["hyprctl", "dispatch", "sendshortcut", "CTRL, V,"], check=True, capture_output=True, timeout=5)


def paste_preserving_clipboard(text: str, restore_after: float = 0.6) -> None:
    """Paste `text` with Ctrl+V, then put the clipboard back the way it was."""
    saved = _paste() if _clipboard_has_text() else None
    copy_to_clipboard(text)
    paste_shortcut()
    if saved is not None:
        timer = threading.Timer(restore_after, copy_to_clipboard, args=(saved,))
        timer.daemon = True
        timer.start()


def deliver(text: str, cfg: OutputCfg, mode: str = "", trailing_space: bool | None = None,
            allow_paste: bool = True) -> str:
    """Send text to the user. `mode`/`trailing_space` override cfg (per-app modes).
    Returns the method actually used."""
    mode = mode or cfg.mode
    if (cfg.trailing_space if trailing_space is None else trailing_space) and not text.endswith(("\n", " ")):
        text += " "
    if mode == "type" and allow_paste and cfg.paste_over and len(text) > cfg.paste_over:
        try:
            paste_preserving_clipboard(text)
            return "paste"
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
            log.warning("pasting failed (%s); typing instead", exc)
    if mode == "clipboard":
        copy_to_clipboard(text)
        return "clipboard"
    if mode == "paste":
        copy_to_clipboard(text)
        paste_shortcut()
        return "paste"
    try:
        type_text(text)
        return "type"
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        detail = getattr(exc, "stderr", b"") or b""
        log.warning("wtype failed (%s); %s", detail.decode(errors="replace").strip() or exc, "copying to clipboard" if cfg.fallback_to_clipboard else "giving up")
        if not cfg.fallback_to_clipboard:
            raise
        copy_to_clipboard(text)
        return "clipboard"
