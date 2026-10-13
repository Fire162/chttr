"""Global hotkeys on any Linux desktop.

Wayland doesn't let an application grab keys, so who catches them depends on
the desktop ([hotkeys] backend):

- compositor: Hyprland and Sway bind the keys themselves (`chttr setup` adds
  the bindings) and run `chttr start` / `chttr release`. Nothing to do here.
- portal: GNOME 48+ and KDE Plasma offer the XDG GlobalShortcuts portal. chttr
  registers its shortcuts once (the desktop asks you to confirm them and lets
  you change the keys in its settings) and gets press *and* release, which is
  what push-to-talk needs.
- evdev: everywhere else (niri, river, X11, ...), chttr reads the keyboard
  devices directly. That needs read access to /dev/input/event* (the `input`
  group). Keys are only watched, never swallowed.
"""

from __future__ import annotations

import logging
import os
import select
import struct
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("chttr.hotkeys")

ACTIONS = ("dictate", "command", "hands_free", "history", "meeting")
DESCRIPTIONS = {
    "dictate": "Dictate (hold, release to type)",
    "command": "Command mode on the selection (hold)",
    "hands_free": "Hands-free dictation (stops on a pause)",
    "history": "Dictation history",
    "meeting": "Start or stop recording a meeting",
}
HOLD = {"dictate", "command"}          # these act on release too

# Linux input event codes
MODIFIER_CODES = {29: "ctrl", 97: "ctrl", 42: "shift", 54: "shift", 56: "alt", 100: "alt", 125: "super", 126: "super"}
KEY_CODES = {"insert": 110, "pause": 119, "scrolllock": 70, "capslock": 58, "home": 102, "end": 107,
             "pageup": 104, "pagedown": 109, "menu": 127, "space": 57, "grave": 41,
             **{f"f{n}": c for n, c in zip(range(1, 13), [59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 87, 88], strict=True)},
             **{f"f{n}": 170 + n for n in range(13, 25)}}
MOD_ALIASES = {"ctrl": "ctrl", "control": "ctrl", "shift": "shift", "alt": "alt", "super": "super", "logo": "super",
               "meta": "super", "win": "super", "mod4": "super"}
EV_KEY = 1
EVENT = struct.Struct("llHHi")          # struct input_event on 64-bit Linux: sec, usec, type, code, value


@dataclass(frozen=True)
class Trigger:
    mods: frozenset[str]
    key: str

    @classmethod
    def parse(cls, text: str) -> Trigger:
        parts = [p.strip().lower() for p in text.replace(" ", "").split("+") if p.strip()]
        if not parts:
            raise ValueError("empty hotkey")
        mods = set()
        for p in parts[:-1]:
            if p not in MOD_ALIASES:
                raise ValueError(f"unknown modifier {p!r} in {text!r}")
            mods.add(MOD_ALIASES[p])
        key = parts[-1]
        if key not in KEY_CODES:
            raise ValueError(f"unsupported key {parts[-1]!r} in {text!r} (try Insert, Pause, F1-F24)")
        return cls(frozenset(mods), key)

    @property
    def code(self) -> int:
        return KEY_CODES[self.key]

    def portal(self) -> str:
        """The XDG shortcuts format: "LOGO+SHIFT+Insert"."""
        names = {"ctrl": "CTRL", "shift": "SHIFT", "alt": "ALT", "super": "LOGO"}
        key = {"insert": "Insert", "pause": "Pause", "scrolllock": "Scroll_Lock", "capslock": "Caps_Lock",
               "pageup": "Page_Up", "pagedown": "Page_Down", "space": "space", "grave": "grave",
               "home": "Home", "end": "End", "menu": "Menu"}.get(self.key, self.key.upper())
        order = ["ctrl", "alt", "shift", "super"]
        return "+".join([names[m] for m in order if m in self.mods] + [key])


def bindings(cfg) -> dict[str, Trigger]:
    out = {}
    for action in ACTIONS:
        text = getattr(cfg, action, "")
        if text:
            out[action] = Trigger.parse(text)
    return out


# ---- choosing a backend -----------------------------------------------------------

def compositor_has_bindings(compositor: str) -> bool:
    home = Path.home() / ".config"
    files = {"hyprland": [home / "hypr/bindings.lua", home / "hypr/bindings.conf", home / "hypr/hyprland.conf"],
             "sway": [home / "sway/config", *sorted((home / "sway/config.d").glob("*"))]}.get(compositor, [])
    for f in files:
        try:
            if "chttr" in f.read_text(encoding="utf-8", errors="ignore"):
                return True
        except OSError:
            continue
    return False


def readable_keyboards() -> list[Path]:
    """/dev/input/event* devices we can read that have the keys chttr binds."""
    out = []
    for dev in sorted(Path("/dev/input").glob("event*")):
        if os.access(dev, os.R_OK) and has_key(dev.name, KEY_CODES["insert"]):
            out.append(dev)
    return out


def has_key(event_name: str, code: int, sysfs: Path = Path("/sys/class/input")) -> bool:
    try:
        words = (sysfs / event_name / "device/capabilities/key").read_text().split()
    except OSError:
        return False
    return key_bit(words, code)


def key_bit(words: list[str], code: int, bits: int = 64) -> bool:
    """The kernel prints the key bitmap as hex longs, most significant first."""
    words = list(reversed(words))
    idx, bit = divmod(code, bits)
    return idx < len(words) and bool(int(words[idx], 16) >> bit & 1)


def portal_available() -> bool:
    try:
        from jeepney import DBusAddress, Properties
        from jeepney.io.blocking import open_dbus_connection
    except ImportError:
        return False
    try:
        conn = open_dbus_connection(bus="SESSION")
        try:
            addr = DBusAddress("/org/freedesktop/portal/desktop", bus_name="org.freedesktop.portal.Desktop",
                               interface="org.freedesktop.portal.GlobalShortcuts")
            reply = conn.send_and_get_reply(Properties(addr).get("version"), timeout=2)
            return reply.header.message_type.name == "method_return"
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - no session bus, no portal, no interface
        log.debug("GlobalShortcuts portal unavailable: %s", exc)
        return False


def resolve(backend: str, compositor: str) -> str:
    """The backend "auto" stands for on this desktop."""
    if backend != "auto":
        return backend
    if compositor in ("hyprland", "sway") and compositor_has_bindings(compositor):
        return "compositor"
    if compositor in ("kde", "gnome") and portal_available():
        return "portal"
    if readable_keyboards():
        return "evdev"
    return "compositor" if compositor in ("hyprland", "sway") else "none"


# ---- evdev ----------------------------------------------------------------------------

class KeyTracker:
    """Turns raw key events into (action, pressed) for the configured triggers.
    A trigger fires when its key goes down while exactly its modifiers are held."""

    def __init__(self, triggers: dict[str, Trigger]) -> None:
        self.triggers = triggers
        self.mods_down: dict[int, str] = {}
        self.active: dict[int, str] = {}           # key code -> action fired by its press

    def feed(self, code: int, value: int) -> tuple[str, bool] | None:
        if code in MODIFIER_CODES:
            if value == 1:
                self.mods_down[code] = MODIFIER_CODES[code]
            elif value == 0:
                self.mods_down.pop(code, None)
            return None
        if value == 2:                                 # autorepeat
            return None
        if value == 1:
            held = frozenset(self.mods_down.values())
            for action, trig in self.triggers.items():
                if trig.code == code and trig.mods == held:
                    self.active[code] = action
                    return action, True
            return None
        action = self.active.pop(code, None)
        return (action, False) if action else None


def decode(buf: bytes) -> Iterable[tuple[int, int, int]]:
    """(type, code, value) for every complete input_event in buf."""
    for off in range(0, len(buf) - EVENT.size + 1, EVENT.size):
        _sec, _usec, etype, code, value = EVENT.unpack_from(buf, off)
        yield etype, code, value


def run_evdev(triggers: dict[str, Trigger], on_event: Callable[[str, bool], None], stop: threading.Event,
              devices: list[Path] | None = None) -> None:
    devices = devices if devices is not None else readable_keyboards()
    fds = {}
    for dev in devices:
        try:
            fds[os.open(dev, os.O_RDONLY | os.O_NONBLOCK)] = dev
        except OSError as exc:
            log.debug("can't open %s: %s", dev, exc)
    if not fds:
        log.warning("hotkeys: no readable keyboard device (add yourself to the input group)")
        return
    log.info("hotkeys: watching %d keyboard device(s) for %s", len(fds),
             ", ".join(f"{a}={'+'.join(sorted(t.mods))}{'+' if t.mods else ''}{t.key}" for a, t in triggers.items()))
    trackers = {fd: KeyTracker(triggers) for fd in fds}
    try:
        while not stop.is_set():
            ready, _, _ = select.select(list(fds), [], [], 0.5)
            for fd in ready:
                try:
                    buf = os.read(fd, EVENT.size * 64)
                except BlockingIOError:
                    continue
                except OSError:                     # unplugged
                    os.close(fd)
                    del fds[fd]
                    continue
                for etype, code, value in decode(buf):
                    if etype == EV_KEY and (hit := trackers[fd].feed(code, value)):
                        on_event(*hit)
    finally:
        for fd in fds:
            os.close(fd)


# ---- portal ---------------------------------------------------------------------------

PORTAL_PATH = "/org/freedesktop/portal/desktop"
PORTAL_NAME = "org.freedesktop.portal.Desktop"
SHORTCUTS_IFACE = "org.freedesktop.portal.GlobalShortcuts"


def portal_shortcuts(triggers: dict[str, Trigger]) -> list[tuple[str, dict]]:
    """The a(sa{sv}) argument of BindShortcuts."""
    return [(action, {"description": ("s", "chttr: " + DESCRIPTIONS[action]),
                      "preferred_trigger": ("s", trig.portal())})
            for action, trig in triggers.items()]


def run_portal(triggers: dict[str, Trigger], on_event: Callable[[str, bool], None], stop: threading.Event) -> None:
    import secrets

    from jeepney import DBusAddress, MatchRule, message_bus, new_method_call
    from jeepney.io.blocking import Proxy, open_dbus_connection
    from jeepney.low_level import HeaderFields

    conn = open_dbus_connection(bus="SESSION")
    bus = Proxy(message_bus, conn)
    portal = DBusAddress(PORTAL_PATH, bus_name=PORTAL_NAME, interface=SHORTCUTS_IFACE)
    sender = conn.unique_name.lstrip(":").replace(".", "_")

    def request(method: str, signature: str, args: tuple, token: str) -> dict:
        path = f"{PORTAL_PATH}/request/{sender}/{token}"
        rule = MatchRule(type="signal", interface="org.freedesktop.portal.Request", member="Response", path=path)
        bus.AddMatch(rule)
        with conn.filter(rule) as responses:
            conn.send_and_get_reply(new_method_call(portal, method, signature, args), timeout=10)
            code, results = conn.recv_until_filtered(responses, timeout=120).body   # the user may be asked
        if code != 0:
            raise RuntimeError(f"{method} was declined (response {code})")
        return results

    signals = MatchRule(type="signal", interface=SHORTCUTS_IFACE, path=PORTAL_PATH)
    bus.AddMatch(signals)
    try:
        with conn.filter(signals, bufsize=256) as queue:
            t1 = "chttr" + secrets.token_hex(4)
            results = request("CreateSession", "a{sv}",
                              ({"handle_token": ("s", t1), "session_handle_token": ("s", "chttr" + secrets.token_hex(4))},),
                              t1)
            session = results["session_handle"][1]
            t2 = "chttr" + secrets.token_hex(4)
            bound = request("BindShortcuts", "oa(sa{sv})sa{sv}",
                            (session, portal_shortcuts(triggers), "", {"handle_token": ("s", t2)}), t2)
            got = {sid: props.get("trigger_description", ("s", ""))[1] for sid, props in bound.get("shortcuts", ("", []))[1]}
            log.info("hotkeys: portal bound %s", ", ".join(f"{k} ({v})" for k, v in got.items()) or "nothing")
            while not stop.is_set():
                try:
                    msg = conn.recv_until_filtered(queue, timeout=0.5)
                except TimeoutError:
                    continue
                member = msg.header.fields.get(HeaderFields.member)
                if member in ("Activated", "Deactivated") and msg.body[0] == session:
                    on_event(str(msg.body[1]), member == "Activated")
    finally:
        conn.close()


# ---- glue -------------------------------------------------------------------------------

class Hotkeys:
    """Runs the chosen backend on a thread and turns key events into daemon commands."""

    def __init__(self, backend: str, triggers: dict[str, Trigger], dispatch: Callable[[str, bool], None]) -> None:
        self.backend = backend
        self.triggers = triggers
        self.dispatch = dispatch
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> Hotkeys:
        target = {"evdev": run_evdev, "portal": run_portal}.get(self.backend)
        if target is None or not self.triggers:
            return self
        self.thread = threading.Thread(target=self._run, args=(target,), name=f"hotkeys-{self.backend}", daemon=True)
        self.thread.start()
        return self

    def _run(self, target) -> None:
        try:
            target(self.triggers, self._event, self.stop)
        except Exception:
            log.exception("hotkeys (%s) stopped", self.backend)

    def _event(self, action: str, pressed: bool) -> None:
        # In order, on this thread: the daemon marks a press as started before it
        # returns (the slow part runs elsewhere), so a quick release is never lost.
        try:
            self.dispatch(action, pressed)
        except Exception:
            log.exception("hotkey %s failed", action)
