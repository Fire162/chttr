#!/usr/bin/python3
"""Floating mic level overlay (GTK4 + wlr-layer-shell).

Runs under the *system* python (needs PyGObject and gtk4-layer-shell). Reads
lines on stdin: "<rms 0..1> <peak_db>" for levels, "caption <text>" for the live
transcript. Exits when stdin closes.

    overlay.py [--label TEXT] [--captions]
"""

import argparse
import math
import os
import signal
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import GLib, Gtk
from gi.repository import Gtk4LayerShell as LayerShell

BARS = 28
W, H = 320, 72
W_CAP, H_CAP = 460, 112     # with live captions
BG = (0.08, 0.08, 0.10, 0.92)
ACCENT = (0.69, 0.60, 0.93)        # lavender, matches the user's theme
ACCENT_HOT = (0.98, 0.55, 0.55)    # near clipping
DIM = (0.45, 0.45, 0.50)


class Overlay:
    def __init__(self, label: str = "", captions: bool = False) -> None:
        self.levels = [0.0] * BARS      # recent rms history, 0..1 (log-scaled)
        self.peak_db = -90.0
        self.label = label
        self.captions = captions
        self.caption = ""
        self.w, self.h = (W_CAP, H_CAP) if captions else (W, H)
        self.phase = 0.0
        self.closing = False

        self.app = Gtk.Application(application_id="dev.rab.chttr.overlay")
        self.app.connect("activate", self.on_activate)

    def on_activate(self, app: Gtk.Application) -> None:
        win = Gtk.ApplicationWindow(application=app)
        win.set_default_size(self.w, self.h)
        win.set_decorated(False)
        LayerShell.init_for_window(win)
        LayerShell.set_layer(win, LayerShell.Layer.OVERLAY)
        LayerShell.set_namespace(win, "chttr-overlay")
        LayerShell.set_anchor(win, LayerShell.Edge.BOTTOM, True)
        LayerShell.set_margin(win, LayerShell.Edge.BOTTOM, 96)
        LayerShell.set_keyboard_mode(win, LayerShell.KeyboardMode.NONE)
        LayerShell.set_exclusive_zone(win, -1)

        self.area = Gtk.DrawingArea()
        self.area.set_content_width(self.w)
        self.area.set_content_height(self.h)
        self.area.set_draw_func(self.draw)
        win.set_child(self.area)

        css = Gtk.CssProvider()
        css.load_from_string("window { background: transparent; }")
        Gtk.StyleContext.add_provider_for_display(win.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        win.present()

        GLib.io_add_watch(sys.stdin, GLib.PRIORITY_DEFAULT, GLib.IO_IN | GLib.IO_HUP, self.on_stdin)
        GLib.timeout_add(33, self.tick)

    # ---- input ----------------------------------------------------------
    def on_stdin(self, source, condition) -> bool:
        line = sys.stdin.readline()
        if not line:  # EOF: the daemon stopped recording
            self.app.quit()
            return False
        if line.startswith("caption "):
            self.caption = line[8:].strip()
            return True
        try:
            rms, peak_db = line.split()
            rms, peak_db = float(rms), float(peak_db)
        except ValueError:
            return True
        # Log scale so quiet speech is visible: -60 dB -> 0, 0 dB -> 1.
        db = 20 * math.log10(max(rms, 1e-6))
        v = min(1.0, max(0.0, (db + 60) / 60))
        self.levels.append(v)
        del self.levels[:-BARS]
        self.peak_db = peak_db
        return True

    def tick(self) -> bool:
        self.phase += 0.15
        self.area.queue_draw()
        return True

    # ---- drawing --------------------------------------------------------
    def draw(self, area, cr, width, height) -> None:
        r = 18
        cr.new_sub_path()
        cr.arc(width - r, r, r, -math.pi / 2, 0)
        cr.arc(width - r, height - r, r, 0, math.pi / 2)
        cr.arc(r, height - r, r, math.pi / 2, math.pi)
        cr.arc(r, r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()
        cr.set_source_rgba(*BG)
        cr.fill()

        band = H                    # the meter row; captions go below it
        mid = band / 2

        # Pulsing dot = recording (lavender in command mode)
        pulse = 0.65 + 0.35 * math.sin(self.phase)
        if self.label == "command":
            cr.set_source_rgba(*ACCENT, pulse)
        else:
            cr.set_source_rgba(0.95, 0.3, 0.3, pulse)
        cr.arc(24, mid, 6, 0, 2 * math.pi)
        cr.fill()

        # Mode label above the dot
        if self.label:
            cr.set_source_rgb(*DIM)
            cr.select_font_face("sans-serif")
            cr.set_font_size(9)
            cr.move_to(12, 15)
            cr.show_text(self.label)

        # Waveform bars (newest on the right)
        x0, x1 = 44, width - 70
        gap = (x1 - x0) / BARS
        bw = gap * 0.6
        for i, v in enumerate(self.levels):
            h = max(3.0, v * (band - 22))
            hot = v > 0.95
            c = ACCENT_HOT if hot else ACCENT
            alpha = 0.35 + 0.65 * (i + 1) / BARS
            cr.set_source_rgba(c[0], c[1], c[2], alpha)
            x = x0 + i * gap + (gap - bw) / 2
            cr.rectangle(x, mid - h / 2, bw, h)
            cr.fill()

        # Peak dB readout
        cr.set_source_rgb(*DIM)
        cr.select_font_face("monospace")
        cr.set_font_size(12)
        label = f"{self.peak_db:5.0f} dB" if self.peak_db > -89 else "  -- dB"
        cr.move_to(width - 66, mid + 4)
        cr.show_text(label)

        if self.captions:
            self.draw_caption(cr, width, band, height)

    def draw_caption(self, cr, width, top, height) -> None:
        """The tail of the live transcript, two lines at most, newest words kept."""
        cr.select_font_face("sans-serif")
        cr.set_font_size(13)
        maxw = width - 36
        words = self.caption.split() if self.caption else []
        lines: list[str] = []
        line = ""
        for word in reversed(words):           # fill from the end
            trial = f"{word} {line}".strip()
            if cr.text_extents(trial).x_advance > maxw and line:
                lines.insert(0, line)
                if len(lines) == 2:
                    break
                line = word
            else:
                line = trial
        else:
            if line:
                lines.insert(0, line)
        lines = lines[-2:]
        if not lines:
            cr.set_source_rgb(*DIM)
            lines = ["listening…"]
        else:
            cr.set_source_rgb(0.92, 0.92, 0.95)
        y = top + 6
        for text in lines:
            cr.move_to(18, y)
            cr.show_text(text)
            y += 18

    def run(self) -> None:
        signal.signal(signal.SIGTERM, lambda *_: self.app.quit())
        self.app.run([])


if __name__ == "__main__":
    os.environ.setdefault("GDK_BACKEND", "wayland")
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="")
    ap.add_argument("--captions", action="store_true")
    a = ap.parse_args()
    Overlay(a.label, a.captions).run()
