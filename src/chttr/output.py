"""Deliver text to the focused window (through the desktop layer in desktop.py)."""

from __future__ import annotations

import functools
import logging
import subprocess
import threading
import time

from . import desktop as DT
from .config import OutputCfg

log = logging.getLogger("chttr.output")

_FAILURES = (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired, OSError)


@functools.cache
def current_desktop() -> DT.Desktop:
    desk = DT.detect()
    log.info("desktop: %s", desk.describe())
    return desk


def focused_window() -> tuple[str, str]:
    """(class, title) of the focused window, or empty strings."""
    return DT.focused_window(current_desktop())


def grab_selection(terminal: bool) -> str:
    """The text selected in the focused window, or "".

    Terminals: the primary selection (Ctrl+C would interrupt the shell). Elsewhere:
    copy with Ctrl+C into an emptied clipboard and read it back, so a stale
    selection is never mistaken for a current one. A text clipboard is restored
    afterwards; an image on the clipboard is lost.
    """
    desk = current_desktop()
    if terminal:
        return DT.clipboard_get(desk, primary=True)
    saved = DT.clipboard_get(desk) if DT.clipboard_has_text(desk) else None
    DT.clipboard_clear(desk)
    selection = ""
    try:
        DT.send_shortcut("ctrl+c", desk)
        for _ in range(8):           # the app answers asynchronously
            time.sleep(0.05)
            selection = DT.clipboard_get(desk)
            if selection:
                break
    except _FAILURES as exc:
        log.warning("could not copy the selection: %s", exc)
    finally:
        if saved:
            copy_to_clipboard(saved)
    return selection


def copy_to_clipboard(text: str) -> None:
    DT.clipboard_set(text, current_desktop())


def paste_shortcut() -> None:
    DT.send_shortcut("ctrl+v", current_desktop())


def paste_preserving_clipboard(text: str, restore_after: float = 0.6) -> None:
    """Paste `text` with Ctrl+V, then put the clipboard back the way it was."""
    desk = current_desktop()
    saved = DT.clipboard_get(desk) if DT.clipboard_has_text(desk) else None
    copy_to_clipboard(text)
    paste_shortcut()
    if saved is not None:
        timer = threading.Timer(restore_after, copy_to_clipboard, args=(saved,))
        timer.daemon = True
        timer.start()


def deliver(text: str, cfg: OutputCfg, mode: str = "", trailing_space: bool | None = None,
            allow_paste: bool = True) -> str:
    """Send text to the user. `mode`/`trailing_space` override cfg (per-app modes).
    Returns the method actually used: "type", "paste" or "clipboard"."""
    mode = mode or cfg.mode
    if (cfg.trailing_space if trailing_space is None else trailing_space) and not text.endswith(("\n", " ")):
        text += " "
    if mode == "type" and allow_paste and cfg.paste_over and len(text) > cfg.paste_over:
        try:
            paste_preserving_clipboard(text)
            return "paste"
        except _FAILURES as exc:
            log.warning("pasting failed (%s); typing instead", exc)
    if mode == "clipboard":
        copy_to_clipboard(text)
        return "clipboard"
    if mode == "paste":
        copy_to_clipboard(text)
        paste_shortcut()
        return "paste"
    try:
        DT.type_text(text, current_desktop(), cfg.typer)
        return "type"
    except _FAILURES as exc:
        detail = (getattr(exc, "stderr", b"") or b"")
        detail = detail.decode(errors="replace") if isinstance(detail, bytes) else str(detail)
        log.warning("typing failed (%s)", detail.strip() or exc)
        if allow_paste:
            try:
                paste_preserving_clipboard(text)
                return "paste"
            except _FAILURES as exc2:
                log.warning("pasting failed too (%s)", exc2)
        if not cfg.fallback_to_clipboard:
            raise
        copy_to_clipboard(text)
        return "clipboard"
