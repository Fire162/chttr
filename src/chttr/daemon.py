"""The chttr daemon: holds the speech model, owns the state machine, listens on a Unix socket."""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from . import config as C
from . import polish as P
from .config import Config, Mode
from .dictionary import Dictionary
from .feedback import Feedback
from .meeting import Session, launch_processing
from .output import deliver, focused_window, grab_selection
from .recorder import Recorder
from .transcriber import Transcriber, engine_key

log = logging.getLogger("chttr.daemon")

IDLE, RECORDING, TRANSCRIBING = "idle", "recording", "transcribing"
# What a recording is for. PTT and COMMAND end when the key is released;
# HANDSFREE ends on silence or a second press.
PTT, HANDSFREE, COMMAND = "dictate", "hands-free", "command"
OVERLAY_SCRIPT = Path(__file__).with_name("overlay.py")
SYSTEM_PYTHON = "/usr/bin/python3"      # has PyGObject + gtk4-layer-shell on Omarchy
# gtk4-layer-shell must be loaded before libwayland-client; with Python that means LD_PRELOAD.
LAYER_SHELL_LIBS = [Path("/usr/lib/libgtk4-layer-shell.so"), Path("/usr/lib64/libgtk4-layer-shell.so")]
QUIET_PEAK_DB = -45.0                   # below this the mic is probably not picking anything up
CAPTION_INTERVAL = 0.8                  # seconds between live-caption passes
CAPTION_WINDOW = 8.0                    # only the last this-many seconds are re-transcribed
CALL_TIMEOUT = 90                       # no word from the Meet tab for this long: the call is over


class Daemon:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.runtime = C.ensure_runtime_dir()
        self.state = IDLE
        self.lock = threading.Lock()
        self.feedback = Feedback(cfg.feedback, self.runtime)
        self.recorder = Recorder(cfg.audio.device, on_level=self._on_level)
        self.transcriber = Transcriber(cfg)
        self.polish_warned = False
        self.overlay: subprocess.Popen | None = None
        self.overlay_lock = threading.Lock()
        self.overlay_warned = False
        self.restore_mute = False
        # per-recording
        self.kind = PTT
        self.app = self.title = ""
        self.selection = ""
        self.heard_speech = False
        self.last_speech = 0.0
        self.captions_stop: threading.Event | None = None
        self.captions_thread: threading.Thread | None = None
        # meetings
        self.meeting: Session | None = None
        self.call: dict | None = None           # the Meet call the extension says we're in
        self.dictation_started = 0.0
        # A key press reads the focused window (and the selection, in command mode)
        # outside the lock; a release that arrives meanwhile is remembered here.
        self.starting = False
        self.release_requested = False
        # Dictations still being transcribed / cleaned up. A new recording may start
        # while earlier ones finish; their text is typed strictly in order.
        self.jobs = 0
        self.job_seq = 0
        self.next_delivery = 0
        self.delivery = threading.Condition()
        self._stop = threading.Event()
        C.MEETING_STATE_FILE.unlink(missing_ok=True)
        self._write_state(IDLE)
        self._tighten_permissions()

    def _tighten_permissions(self) -> None:
        """Files written by versions before 0.3 were created with the default umask."""
        C.ensure_data_dir()
        for path in (C.DATA_DIR, C.MEETINGS_DATA_DIR, C.HISTORY_FILE, C.VOICES_FILE):
            try:
                if path.exists() and C.too_open(path):
                    path.chmod(0o700 if path.is_dir() else 0o600)
            except OSError as exc:
                log.debug("could not tighten %s: %s", path, exc)

    # ---- state -------------------------------------------------------------
    def _refresh_state(self) -> None:
        """Recording beats transcribing beats idle."""
        if self.recorder.active:
            state = RECORDING
        else:
            state = TRANSCRIBING if self.jobs else IDLE
        if state != self.state:
            self._write_state(state)

    def _write_state(self, state: str) -> None:
        self.state = state
        tmp = C.STATE_FILE.with_suffix(".tmp")
        tmp.write_text(state)
        os.replace(tmp, C.STATE_FILE)

    # ---- overlay -----------------------------------------------------------
    def _overlay_open(self, label: str) -> None:
        if not self.cfg.feedback.overlay or self.overlay is not None:
            return
        env = dict(os.environ)
        for lib in LAYER_SHELL_LIBS:
            if lib.exists():
                env["LD_PRELOAD"] = str(lib)
                break
        cmd = [SYSTEM_PYTHON, "-I", str(OVERLAY_SCRIPT), "--label", label]
        if self.cfg.feedback.live_captions:
            cmd.append("--captions")
        try:
            self.overlay = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env,
            )
        except OSError as exc:
            self._overlay_fail(str(exc))

    def _overlay_fail(self, detail: str) -> None:
        self.overlay = None
        if not self.overlay_warned:
            self.overlay_warned = True
            log.warning("level overlay unavailable: %s", detail)

    def _overlay_send(self, line: str) -> None:
        proc = self.overlay
        if proc is None or proc.stdin is None:
            return
        try:
            with self.overlay_lock:
                proc.stdin.write((line.replace("\n", " ") + "\n").encode())
                proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            err = (proc.stderr.read() if proc.stderr else b"").decode(errors="replace").strip()
            self._overlay_fail(err.splitlines()[-1] if err else "overlay exited")

    def _on_level(self, rms: float, peak_db: float) -> None:
        rms_db = 20 * np.log10(max(rms, 1e-6))
        if rms_db > self.cfg.audio.speech_threshold_db:
            self.heard_speech = True
            self.last_speech = time.monotonic()
        self._overlay_send(f"{rms:.5f} {peak_db:.1f}")

    def _overlay_close(self) -> None:
        proc, self.overlay = self.overlay, None
        if proc is None:
            return
        try:
            with self.overlay_lock:
                if proc.stdin:
                    proc.stdin.close()
            proc.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()

    # ---- live captions -----------------------------------------------------
    def _captions_start(self) -> None:
        if not (self.cfg.feedback.live_captions and self.cfg.feedback.overlay):
            return
        self.captions_stop = threading.Event()
        self.captions_thread = threading.Thread(target=self._captions_loop, args=(self.captions_stop,), daemon=True)
        self.captions_thread.start()

    def _captions_loop(self, stop: threading.Event) -> None:
        last_len = 0
        while not stop.wait(CAPTION_INTERVAL):
            audio = self.recorder.snapshot(CAPTION_WINDOW)
            if len(audio) == last_len or not self.heard_speech:
                continue
            last_len = len(audio)
            try:
                text = self.transcriber.transcribe(audio)
            except Exception as exc:  # noqa: BLE001 - captions are cosmetic
                log.debug("caption pass failed: %s", exc)
                continue
            if not stop.is_set() and text:
                self._overlay_send("caption " + text)

    def _captions_detach(self) -> threading.Thread | None:
        """Tell this recording's caption loop to stop; returns its thread to join."""
        stop, thread = self.captions_stop, self.captions_thread
        self.captions_stop = self.captions_thread = None
        if stop:
            stop.set()
        return thread

    # ---- mic mute ----------------------------------------------------------
    def _unmute_if_needed(self) -> None:
        self.restore_mute = False
        if not self.cfg.audio.unmute:
            return
        try:
            out = subprocess.run(["pactl", "get-source-mute", "@DEFAULT_SOURCE@"], capture_output=True, text=True, timeout=2).stdout
            if "yes" in out:
                subprocess.run(["pactl", "set-source-mute", "@DEFAULT_SOURCE@", "0"], timeout=2, check=False)
                self.restore_mute = True
                log.info("mic was muted; unmuted for recording")
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("could not query mic mute: %s", exc)

    def _restore_mute_state(self) -> None:
        if self.restore_mute:
            self.restore_mute = False
            subprocess.run(["pactl", "set-source-mute", "@DEFAULT_SOURCE@", "1"], timeout=2, check=False)

    # ---- commands ----------------------------------------------------------
    def handle(self, line: str) -> dict:
        verb, _, arg = line.strip().partition(" ")
        cmd = verb.lower()
        if cmd in ("start", "command") or (cmd == "toggle" and not self.recorder.active and not self.starting):
            return self._press({"start": PTT, "command": COMMAND, "toggle": HANDSFREE}[cmd])
        with self.lock:
            if cmd == "meeting":
                return self._meeting_command(arg.strip().lower())
            if cmd == "meet":
                return self._meet_event(json.loads(arg))
            if cmd == "status":
                return {"ok": True, "state": self.state, "kind": self.kind if self.state == RECORDING else "",
                        "seconds": round(self.recorder.elapsed, 1)}
            if cmd == "toggle":
                return self._stop_and_transcribe()
            if cmd == "release":
                # Key-up of any Insert binding. Hands-free recordings ignore it, so
                # letting go of Super+Insert in either order doesn't end them.
                if self.starting:
                    if self.kind != HANDSFREE:
                        self.release_requested = True
                    return {"ok": True, "state": self.state, "message": "released while starting; will stop"}
                if self.state == RECORDING and self.kind == HANDSFREE:
                    return {"ok": True, "state": self.state, "message": "hands-free; ignoring key release"}
                return self._stop_and_transcribe()
            if cmd == "stop":
                return self._stop_and_transcribe()
            if cmd == "cancel":
                return self._cancel()
            if cmd == "reload":
                return self._reload()
            if cmd == "quit":
                self._stop.set()
                return {"ok": True, "state": self.state, "message": "bye"}
            return {"ok": False, "state": self.state, "error": f"unknown command {cmd!r}"}

    # ---- meetings ----------------------------------------------------------
    def _log_dictation(self) -> None:
        if self.meeting and self.dictation_started:
            self.meeting.log({"type": "dictation", "start": self.dictation_started, "end": time.time()})
        self.dictation_started = 0.0

    def _meeting_status(self) -> dict:
        m = self.meeting
        return {"ok": True, "state": self.state, "recording": m is not None,
                "meeting": {"title": m.title, "code": m.code, "seconds": round(m.elapsed), "dir": str(m.dir)} if m else None,
                "call": {k: self.call[k] for k in ("code", "title")} if self.call else None}

    def _meeting_command(self, arg: str) -> dict:
        if arg in ("", "status"):
            return self._meeting_status()
        if arg == "start":
            return self._meeting_start("manual")
        if arg == "stop":
            return self._meeting_stop()
        if arg == "toggle":
            return self._meeting_stop() if self.meeting else self._meeting_start("manual")
        if arg == "cancel":
            if not self.meeting:
                return {"ok": True, "state": self.state, "message": "no meeting is being recorded"}
            self.meeting.discard()
            self.meeting = None
            C.MEETING_STATE_FILE.unlink(missing_ok=True)
            self.feedback.play("error")
            self.feedback.notify("Meeting recording discarded", timeout_ms=3000)
            return {"ok": True, "state": self.state, "message": "discarded"}
        return {"ok": False, "state": self.state, "error": f"unknown meeting command {arg!r}"}

    def _meeting_start(self, source: str) -> dict:
        if self.meeting:
            return {**self._meeting_status(), "message": "already recording a meeting"}
        code = self.call["code"] if self.call else ""
        title = self.call.get("title", "") if self.call else ""
        session = Session(self.cfg, code, title, source if not self.call else "extension")
        try:
            session.start()
        except RuntimeError as exc:
            session.discard()
            self.feedback.play("error")
            self.feedback.notify("chttr: can't record the meeting", str(exc), urgency="critical", timeout_ms=8000)
            return {"ok": False, "state": self.state, "error": str(exc)}
        self.meeting = session
        C.MEETING_STATE_FILE.write_text(json.dumps({"title": title, "code": code, "started": session.recorder.started}))
        log.info("meeting recording started in %s", session.dir)
        self.feedback.play("start")
        self.feedback.notify("Recording meeting" + (f": {title}" if title else ""),
                             "Notes are written when the call ends." if code else "Stop with: chttr meeting stop",
                             timeout_ms=4000)
        return {**self._meeting_status(), "message": "recording"}

    def _meeting_stop(self) -> dict:
        session = self.meeting
        if not session:
            return {"ok": True, "state": self.state, "recording": False, "message": "no meeting is being recorded"}
        self.meeting = None
        C.MEETING_STATE_FILE.unlink(missing_ok=True)
        secs = session.stop()
        log.info("meeting recording stopped after %.0fs", secs)
        self.feedback.play("stop")
        if secs < 10:
            session.discard()
            self.feedback.notify("Meeting recording too short; discarded", timeout_ms=3000)
            return {"ok": True, "state": self.state, "recording": False, "message": "too short; discarded"}
        launch_processing(session.dir)
        self.feedback.notify("Meeting recorded", f"{secs / 60:.0f} min. Writing the transcript and notes…", timeout_ms=4000)
        return {"ok": True, "state": self.state, "recording": False, "message": f"recorded {secs:.0f}s; processing",
                "dir": str(session.dir)}

    def _meet_event(self, msg: dict) -> dict:
        """A message from the Meet extension (through `chttr native-host`)."""
        kind, code = msg.get("type", ""), msg.get("code", "")
        now = time.time()
        if kind == "joined" and code:
            fresh = not self.call or self.call["code"] != code
            self.call = {"code": code, "title": msg.get("title", ""), "seen": now}
            if self.meeting and not self.meeting.code:
                self.meeting.code, self.meeting.title = code, msg.get("title", "")    # started by hand, now we know the call
                self.meeting.save_meta(status="recording")
            elif fresh and not self.meeting:
                log.info("Meet call %s joined", code)
                if self.cfg.meeting.auto == "always":
                    self._meeting_start("extension")
                elif self.cfg.meeting.auto == "ask":
                    threading.Thread(target=self._ask_to_record, args=(code, msg.get("title", "")), daemon=True).start()
        elif kind == "left":
            if self.meeting and code and self.meeting.code == code:
                log.info("Meet call %s left", code)
                self._meeting_stop()
            if self.call and self.call["code"] == code:
                self.call = None
        elif code and self.call and self.call["code"] == code:
            self.call["seen"] = now
            if self.meeting and self.meeting.code == code:
                for e in msg.get("events", []):
                    e = dict(e)
                    e["t"] = float(e.get("t", now * 1000)) / 1000    # the page sends milliseconds
                    self.meeting.log(e)
        elif code and kind in ("heartbeat", "events") and not self.call:
            # daemon restarted mid-call: the page keeps talking, so pick the call back up
            self.call = {"code": code, "title": msg.get("title", ""), "seen": now}
        return {"ok": True, "recording": bool(self.meeting and self.meeting.code == code)}

    def _ask_to_record(self, code: str, title: str) -> None:
        from . import gcal

        label, body = title or "this Meet call", "chttr will save a transcript with speaker names."
        try:
            event = gcal.find_event(gcal.read_urls(self.cfg.meeting.calendar_file), code)
            if event:
                label = event.title
                people = [a.name or a.email for a in event.attendees if not a.is_me]
                if people:
                    body = ", ".join(people[:5]) + (f" +{len(people) - 5}" if len(people) > 5 else "")
                with self.lock:
                    if self.call and self.call["code"] == code and not self.call.get("title"):
                        self.call["title"] = event.title
        except Exception as exc:  # noqa: BLE001
            log.warning("calendar lookup failed: %s", exc)
        try:
            out = subprocess.run(["notify-send", "-a", "chttr", "-u", "normal", "-t", "120000",
                                  "-A", "record=Record", "-A", "skip=Not now", "--wait",
                                  f"Record \u201c{label}\u201d?", body],
                                 capture_output=True, text=True, timeout=180).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return
        if out == "record":
            with self.lock:
                if self.call and self.call["code"] == code and not self.meeting:
                    self._meeting_start("extension")

    def _reload(self) -> dict:
        cfg = C.load_config()
        message = "config reloaded"
        if engine_key(cfg) != self.transcriber.key:
            if self.state != IDLE:
                return {"ok": False, "state": self.state, "error": "busy; reload again when idle to switch engines"}
            self.transcriber = Transcriber(cfg)
            message += f"; {cfg.engine.name} loaded"
        self.cfg = cfg
        self.recorder.device = cfg.audio.device
        self.feedback.cfg = cfg.feedback
        return {"ok": True, "state": self.state, "message": message}

    def _press(self, kind: str) -> dict:
        """A key press. Reading the focused window and grabbing the selection talk to
        the compositor and the app, so they run without holding the lock."""
        with self.lock:
            if self.recorder.active or self.starting:
                return {"ok": True, "state": self.state, "message": "already recording"}
            self.starting, self.release_requested, self.kind = True, False, kind
        try:
            app, title = focused_window()
            selection = ""
            if kind == COMMAND:
                mode = self.cfg.mode_for(app)
                selection = grab_selection(terminal=bool(mode and mode.name == "terminal")).strip()
        except Exception:
            log.exception("reading the focused window failed")
            app, title, selection = "", "", ""
        with self.lock:
            self.starting = False
            reply = self._start(kind, app, title, selection)
            if self.release_requested and self.recorder.active:
                self.release_requested = False
                log.info("key released before recording started; stopping right away")
                return self._stop_and_transcribe()
            return reply

    def _start(self, kind: str, app: str, title: str, selection: str) -> dict:
        self.kind = kind
        self.app, self.title, self.selection = app, title, selection
        self.heard_speech = False
        self.last_speech = time.monotonic()
        self._unmute_if_needed()
        self._overlay_open(kind if kind != PTT else "")
        try:
            self.recorder.start()
        except RuntimeError as exc:
            self._overlay_close()
            self._restore_mute_state()
            self._refresh_state()
            self.feedback.play("error")
            self.feedback.notify("chttr: can't record", str(exc), urgency="critical", timeout_ms=6000)
            return {"ok": False, "state": self.state, "error": str(exc)}
        self._write_state(RECORDING)
        self.dictation_started = time.time()
        self._captions_start()
        self.feedback.play("start")
        if kind == COMMAND:
            what = f"{len(self.selection.split())} selected words" if self.selection else "no selection: new text"
            self.feedback.notify("Command mode", f"Say what to do ({what})", timeout_ms=2500)
        return {"ok": True, "state": self.state, "kind": kind}

    def _stop_and_transcribe(self) -> dict:
        if self.state != RECORDING:
            return {"ok": True, "state": self.state, "message": "not recording"}
        audio, secs = self.recorder.stop()
        peak_db = self.recorder.peak_db
        self._log_dictation()
        self.jobs += 1
        seq, self.job_seq = self.job_seq, self.job_seq + 1
        self._write_state(TRANSCRIBING)
        captions = self._captions_detach()
        self._overlay_close()
        self._restore_mute_state()
        self.feedback.play("stop")
        self.feedback.notify("Transcribing…", f"{secs:.1f}s of audio, peak {peak_db:.0f} dB", timeout_ms=0)
        job = (seq, audio, secs, peak_db, self.kind, self.app, self.title, self.selection, captions)
        threading.Thread(target=self._process, args=job, daemon=True).start()
        return {"ok": True, "state": self.state, "seconds": round(secs, 1), "peak_db": round(peak_db, 1)}

    def _cancel(self) -> dict:
        if self.state != RECORDING:
            return {"ok": True, "state": self.state, "message": "nothing to cancel"}
        self.recorder.cancel()
        self._log_dictation()
        self._overlay_close()
        self._restore_mute_state()
        self._refresh_state()
        self._captions_detach()
        self.feedback.play("error")
        self.feedback.notify("Dictation cancelled", timeout_ms=1500)
        return {"ok": True, "state": self.state}

    # ---- pipeline (worker thread) -----------------------------------------
    def _wait_turn(self, seq: int) -> None:
        """Block until every earlier dictation has been delivered (or given up)."""
        with self.delivery:
            self.delivery.wait_for(lambda: self.next_delivery >= seq, timeout=60)

    def _done_turn(self, seq: int) -> None:
        with self.delivery:
            self.next_delivery = max(self.next_delivery, seq + 1)
            self.delivery.notify_all()

    def _process(self, seq: int, audio: np.ndarray, secs: float, peak_db: float, kind: str,
                 app: str, title: str, selection: str, captions: threading.Thread | None) -> None:
        raw = text = ""
        method = ""
        mode: Mode | None = None
        try:
            if captions:
                captions.join(timeout=5)   # let an in-flight caption pass finish first
            t0 = time.monotonic()
            dictionary = Dictionary.load()
            raw = self.transcriber.transcribe(audio, dictionary.whisper_prompt())
            log.info("%s %.1fs audio (peak %.0f dB) -> %.2fs: %r",
                     self.transcriber.engine.name, secs, peak_db, time.monotonic() - t0, raw)
            if not raw:
                self._no_speech(secs, peak_db)
                return
            fixed = dictionary.apply(raw)
            if fixed != raw:
                log.info("dictionary: %r", fixed)
            mode = self.cfg.mode_for(app)
            if kind == COMMAND:
                text = self._run_command(fixed, selection, app, title, mode, dictionary)
                if not text:
                    return
            else:
                text = self._polish(fixed, app, title, mode, dictionary)
                text = dictionary.expand_snippets(text)
            self._wait_turn(seq)
            method = deliver(text, self.cfg.output,
                             mode=(mode.output if mode else ""),
                             trailing_space=False if kind == COMMAND else (mode.trailing_space if mode else None))
            label = "Replaced" if kind == COMMAND and selection else "Typed"
            if method == "clipboard":
                self.feedback.notify("Copied to clipboard", text[:200], timeout_ms=4000)
            else:
                self.feedback.notify(label, text[:200], timeout_ms=2500)
        except Exception as exc:
            log.exception("dictation failed")
            self.feedback.play("error")
            self.feedback.notify("chttr error", f"{type(exc).__name__}: {exc}", urgency="critical", timeout_ms=8000)
        finally:
            self._done_turn(seq)
            with self.lock:
                self.jobs -= 1
                self._refresh_state()
            if raw and self.cfg.history.enabled:
                self._append_history(raw, text, app, secs, method, kind, mode.name if mode else "")

    def _no_speech(self, secs: float, peak_db: float) -> None:
        if peak_db < QUIET_PEAK_DB:
            self.feedback.notify("No speech detected: mic is silent",
                                 f"Peak {peak_db:.0f} dB. Check the input device or mute (SUPER+CTRL+A → Audio).",
                                 urgency="normal", timeout_ms=6000)
        else:
            self.feedback.notify("No speech detected", f"{secs:.1f}s, peak {peak_db:.0f} dB", timeout_ms=2500)

    def _polish(self, text: str, app: str, title: str, mode: Mode | None, dictionary: Dictionary) -> str:
        if mode and not mode.polish:
            return text
        if P.backend(self.cfg.polish) == "none":
            return text
        try:
            t1 = time.monotonic()
            out = P.polish(text, self.cfg.polish, app, title, dictionary.vocabulary,
                           style=mode.style if mode else "", keep=list(dictionary.snippets))
            log.info("polish (%s) %.2fs: %r", mode.name if mode else "default", time.monotonic() - t1, out)
            return out
        except Exception as exc:  # noqa: BLE001 - any failure falls back to raw text
            log.warning("polish failed, using raw transcript: %s", exc)
            if not self.polish_warned:
                self.polish_warned = True
                self.feedback.notify("chttr: cleanup unavailable", f"{type(exc).__name__}: {str(exc)[:160]}",
                                     urgency="normal", timeout_ms=8000)
            return text

    def _run_command(self, instruction: str, selection: str, app: str, title: str,
                     mode: Mode | None, dictionary: Dictionary) -> str:
        if P.backend(self.cfg.polish) == "none":
            self.feedback.play("error")
            self.feedback.notify("Command mode needs a model",
                                 "Add an Anthropic key to ~/.config/chttr/anthropic-api-key, or install and start Ollama.",
                                 urgency="normal", timeout_ms=8000)
            return ""
        try:
            t1 = time.monotonic()
            out = P.command(instruction, selection, self.cfg.polish, app, title, dictionary.vocabulary,
                            style=mode.style if mode else "")
            log.info("command %.2fs: %r -> %r", time.monotonic() - t1, instruction, out[:200])
            return out
        except Exception as exc:  # noqa: BLE001
            log.warning("command failed: %s", exc)
            self.feedback.play("error")
            self.feedback.notify("Command failed", f"{type(exc).__name__}: {str(exc)[:160]}", urgency="normal", timeout_ms=8000)
            return ""

    def _append_history(self, raw: str, text: str, app: str, secs: float, method: str, kind: str, mode_name: str) -> None:
        try:
            C.ensure_data_dir()
            with C.open_private(C.HISTORY_FILE) as fh:
                fh.write(json.dumps({
                    "ts": datetime.now(UTC).isoformat(timespec="seconds"),
                    "seconds": round(secs, 1), "app": app, "kind": kind, "mode": mode_name,
                    "raw": raw, "text": text, "output": method,
                }) + "\n")
        except OSError as exc:
            log.warning("history write failed: %s", exc)

    # ---- server ------------------------------------------------------------
    def _watchdog(self) -> None:
        while not self._stop.wait(0.2):
            with self.lock:
                m = self.meeting
                if m and m.elapsed >= self.cfg.meeting.max_hours * 3600:
                    log.info("meeting max_hours reached; stopping")
                    self._meeting_stop()
                elif m and m.code and self.call and time.time() - self.call["seen"] > CALL_TIMEOUT:
                    log.info("no word from the Meet tab for %ds; treating the call as over", CALL_TIMEOUT)
                    self.call = None
                    self._meeting_stop()
                if self.state != RECORDING:
                    continue
                a = self.cfg.audio
                if self.recorder.elapsed >= a.max_seconds:
                    log.info("max_seconds reached; stopping")
                    self._stop_and_transcribe()
                elif self.kind == HANDSFREE:
                    quiet = time.monotonic() - self.last_speech
                    if self.heard_speech and quiet >= a.auto_stop_silence:
                        log.info("hands-free: %.1fs of silence; stopping", quiet)
                        self._stop_and_transcribe()
                    elif not self.heard_speech and self.recorder.elapsed >= a.no_speech_timeout:
                        log.info("hands-free: nothing heard in %.0fs; cancelling", a.no_speech_timeout)
                        self._cancel()

    def _serve_one(self, conn: socket.socket) -> None:
        with conn:
            try:
                conn.settimeout(2)
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                reply = self.handle(buf.decode(errors="replace"))
            except Exception as exc:
                log.exception("request failed")
                reply = {"ok": False, "state": self.state, "error": str(exc)}
            try:
                conn.sendall((json.dumps(reply) + "\n").encode())
            except OSError:
                pass

    def serve(self) -> None:
        sock_path = C.SOCKET_PATH
        if sock_path.exists():
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                probe.settimeout(1)
                probe.connect(str(sock_path))
                probe.sendall(b"status\n")
                raise SystemExit("chttr daemon is already running")
            except (ConnectionRefusedError, FileNotFoundError, TimeoutError):
                sock_path.unlink(missing_ok=True)
            finally:
                probe.close()

        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(sock_path))
        os.chmod(sock_path, 0o600)
        server.listen(8)
        server.settimeout(0.5)

        def on_signal(signum, _frame):
            log.info("signal %s; shutting down", signum)
            self._stop.set()

        signal.signal(signal.SIGTERM, on_signal)
        signal.signal(signal.SIGINT, on_signal)
        threading.Thread(target=self._watchdog, daemon=True).start()
        log.info("listening on %s (cleanup backend: %s)", sock_path, P.backend(self.cfg.polish))
        try:
            while not self._stop.is_set():
                try:
                    conn, _ = server.accept()
                except TimeoutError:
                    continue
                # One thread per request: a slow one (a meeting stopping, the calendar)
                # never delays a key release.
                threading.Thread(target=self._serve_one, args=(conn,), daemon=True).start()
        finally:
            with self.lock:
                if self.meeting:
                    self._meeting_stop()
                if self.recorder.active:
                    self.recorder.cancel()
                self._overlay_close()
                self._restore_mute_state()
                self._write_state(IDLE)
            server.close()
            sock_path.unlink(missing_ok=True)
            log.info("stopped")


def run(cfg: Config) -> None:
    Daemon(cfg).serve()
