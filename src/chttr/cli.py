"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
from pathlib import Path

from . import __version__
from . import config as C
from .client import DaemonNotRunning, read_state, send

ICONS = {"idle": "", "recording": "󰍬", "transcribing": "󰔟", "meeting": "󰑊"}


def _status_json(state: str) -> str:
    if state == "idle" and C.MEETING_STATE_FILE.exists():
        state = "meeting"       # dictation idle while a meeting is being recorded
    tooltip = {"idle": "chttr: idle", "recording": "chttr: recording", "transcribing": "chttr: transcribing",
               "meeting": "chttr: recording a meeting"}[state]
    return json.dumps({"text": ICONS[state], "alt": state, "class": state, "tooltip": tooltip})


def cmd_daemon(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    C.write_default_config()
    cfg = C.load_config()
    from .daemon import run

    run(cfg)
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    try:
        reply = send(args.command)
    except DaemonNotRunning as exc:
        print(exc, file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(reply))
    elif not reply.get("ok"):
        print(reply.get("error", "error"), file=sys.stderr)
        return 1
    elif reply.get("message"):
        print(reply["message"])
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    if not args.follow:
        state = read_state() if not C.SOCKET_PATH.exists() else send("status").get("state", "idle")
        print(_status_json(state) if args.json else state)
        return 0
    last = None
    try:
        while True:
            state = read_state()
            if state not in ICONS:
                state = "idle"
            if state != last:
                print(_status_json(state) if args.json else state, flush=True)
                last = state
            time.sleep(0.15)
    except KeyboardInterrupt:
        return 0


def cmd_transcribe(args: argparse.Namespace) -> int:
    """Offline: run the pipeline on a WAV file without the daemon."""
    logging.basicConfig(level=logging.WARNING)
    cfg = C.load_config()
    if args.engine:
        cfg.engine.name = args.engine
    from .dictionary import Dictionary
    from .polish import backend, polish
    from .transcriber import Transcriber

    d = Dictionary.load()
    t0 = time.monotonic()
    raw = Transcriber(cfg).transcribe(Path(args.file), d.whisper_prompt())
    if args.verbose:
        print(f"[{cfg.engine.name} {time.monotonic() - t0:.2f}s incl. load]", file=sys.stderr)
    text = d.apply(raw)
    if raw and not args.raw and backend(cfg.polish) != "none":
        text = polish(text, cfg.polish, vocabulary=d.vocabulary, keep=list(d.snippets))
    print(d.expand_snippets(text))
    return 0


def cmd_polish(args: argparse.Namespace) -> int:
    """Read raw text on stdin, print cleaned text (usable as a post-processor for other tools)."""
    cfg = C.load_config()
    text = sys.stdin.read().strip()
    if not text:
        return 0
    from .polish import polish

    print(polish(text, cfg.polish, args.app or "", ""))
    return 0


def cmd_learn(args: argparse.Namespace) -> int:
    from .dictionary import DICTIONARY_FILE, Dictionary

    d = Dictionary.load()
    d.learn(args.word, args.heard)
    d.save()
    heard = ", ".join(f"{h!r}" for h in (args.heard or []))
    print(f"learned {args.word!r}" + (f" (heard as {heard})" if heard else "") + f" -> {DICTIONARY_FILE}")
    return 0


def cmd_forget(args: argparse.Namespace) -> int:
    from .dictionary import Dictionary

    d = Dictionary.load()
    if d.forget(args.word):
        d.save()
        print(f"forgot {args.word!r}")
        return 0
    print(f"{args.word!r} is not in the dictionary", file=sys.stderr)
    return 1


def cmd_dictionary(args: argparse.Namespace) -> int:
    from .dictionary import DICTIONARY_FILE, Dictionary

    d = Dictionary.load()
    print(f"# {DICTIONARY_FILE}")
    print("vocabulary:", ", ".join(d.vocabulary) or "(none)")
    for heard, typed in sorted(d.replacements.items()):
        print(f"  {heard!r:20} -> {typed}")
    if d.snippets:
        print("snippets:")
        for phrase, expansion in sorted(d.snippets.items()):
            print(f"  {phrase!r:20} -> {expansion[:60]!r}")
    return 0


def cmd_snippet(args: argparse.Namespace) -> int:
    from .dictionary import Dictionary

    d = Dictionary.load()
    expansion = args.expansion.replace("\\n", "\n")
    d.add_snippet(args.phrase, expansion)
    d.save()
    print(f"say {args.phrase!r} (or \"insert {args.phrase}\") to type {expansion!r}")
    return 0


def cmd_fix(args: argparse.Namespace) -> int:
    """Learn from the last dictation: pick what came out wrong, type what you meant."""
    from .dictionary import Dictionary
    from .history import load

    d = Dictionary.load()
    if args.wrong and args.right:
        d.learn(args.right, [args.wrong])
        d.save()
        print(f"learned: {args.wrong!r} -> {args.right!r}")
        return 0
    entries = load()
    if not entries:
        print("no dictations in history yet", file=sys.stderr)
        return 1
    last = entries[-1]
    print(f"heard:  {last.get('raw', '')}")
    print(f"typed:  {last.get('text', '')}\n")
    try:
        wrong = (args.wrong or input("Which word or phrase came out wrong? (as typed or heard) ")).strip()
        if not wrong:
            return 1
        right = input(f"What should {wrong!r} be? ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return 130
    if not right:
        return 1
    d.learn(right, [wrong])
    d.save()
    print(f"\nlearned: {wrong!r} -> {right!r}. chttr will type {right!r} from now on.")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    from .history import _local, load, pick

    entries = load()
    if not entries:
        print("no dictations in history yet", file=sys.stderr)
        return 1
    if args.print:
        for e in entries[-args.print:]:
            when = _local(e.get("ts", ""))
            print(f"{when.strftime('%m-%d %H:%M') if when else '?':11}  {e.get('app', '') or '-':12.12}  {e.get('text', '')}")
        return 0
    text = pick(entries)
    if text is None:
        return 130
    from .output import deliver

    cfg = C.load_config()
    time.sleep(0.25)            # let focus return to the window the menu covered
    method = deliver(text, cfg.output)
    if method == "clipboard":
        print("copied to clipboard")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    from .history import load, stats

    print(stats(load(), days=args.days))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    cfg = C.load_config()
    from .polish import backend
    from .transcriber import resolve_model, resolve_parakeet

    ok = True
    print(f"config:      {C.CONFIG_FILE}")
    if cfg.engine.name == "parakeet":
        try:
            print(f"engine:      parakeet ({resolve_parakeet(cfg.engine.parakeet_dir)})")
            import onnx_asr  # noqa: F401
        except (FileNotFoundError, ImportError) as exc:
            ok = False
            print(f"engine:      parakeet: PROBLEM: {exc}")
    else:
        print(f"engine:      whisper ({resolve_model(cfg.whisper.model)})")
    b = backend(cfg.polish)
    hint = {"none": "  (raw text; add ~/.config/chttr/anthropic-api-key or run Ollama for cleanup + command mode)"}
    print(f"cleanup:     {b}{hint.get(b, '')}")
    try:
        print(f"daemon:      {send('status').get('state')}")
    except DaemonNotRunning:
        ok = False
        print("daemon:      not running")
    print(f"modes:       {', '.join(m.name for m in cfg.modes) or '(none)'}")
    from .gcal import read_urls
    from .native_host import BROWSER_DIRS, HOST_NAME

    print(f"meetings:    notes to {cfg.meeting.dir}; auto = {cfg.meeting.auto}")
    print(f"calendar:    {len(read_urls(cfg.meeting.calendar_file))} iCal address(es)"
          + ("" if read_urls(cfg.meeting.calendar_file) else "  (chttr meeting calendar URL)"))
    hosts = [d.name for d, _ in BROWSER_DIRS if (d / "NativeMessagingHosts" / f"{HOST_NAME}.json").exists()]
    print(f"meet ext:    {'registered for ' + ', '.join(hosts) if hosts else 'not installed (chttr meeting install)'}")
    return 0 if ok else 1


def _send_or_exit(command: str) -> dict:
    try:
        return send(command)
    except DaemonNotRunning as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(2)


def cmd_meeting_control(args: argparse.Namespace) -> int:
    reply = _send_or_exit(f"meeting {args.action}")
    if args.json:
        print(json.dumps(reply))
        return 0 if reply.get("ok") else 1
    if not reply.get("ok"):
        print(reply.get("error", "error"), file=sys.stderr)
        return 1
    m, call = reply.get("meeting"), reply.get("call")
    if args.action == "status":
        if m:
            print(f"recording {m['title'] or m['code'] or 'a meeting'} ({m['seconds'] // 60} min) -> {m['dir']}")
        else:
            print("not recording" + (f"; in Meet call {call['code']}" if call else ""))
    else:
        print(reply.get("message", ""))
    return 0


def cmd_meeting_process(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    from .meeting import notify_done, process, resolve, summary_line, update_meta

    directory = resolve(args.meeting)
    cfg = C.load_config()
    try:
        md = process(directory, cfg, summary=False if args.no_summary else None)
    except Exception as exc:
        logging.exception("processing failed")
        update_meta(directory, status="failed", error=f"{type(exc).__name__}: {exc}")
        if args.notify:
            import subprocess

            subprocess.run(["notify-send", "-a", "chttr", "-u", "critical", "Meeting transcript failed",
                            f"{type(exc).__name__}: {str(exc)[:200]}\nRetry: chttr meeting process {directory.name}"],
                           check=False)
        return 1
    print(md)
    if args.notify:
        notify_done(md, summary_line(md, directory))
    return 0


def cmd_meeting_list(args: argparse.Namespace) -> int:
    from datetime import datetime

    from .meeting import meeting_dirs, read_json

    dirs = meeting_dirs()
    if not dirs:
        print("no meetings recorded yet")
        return 0
    for d in dirs[-args.n:]:
        meta = read_json(d / "meta.json", {}) or {}
        when = datetime.fromtimestamp(float(meta.get("started", 0))).strftime("%a %b %d %H:%M")
        mins = round(float(meta.get("duration", 0)) / 60)
        what = Path(meta["markdown"]).name if meta.get("markdown") else (meta.get("title") or meta.get("code") or "")
        status = meta.get("status", "?") + (f": {meta['error']}" if meta.get("error") else "")
        print(f"{d.name:36} {when}  {mins:3} min  {status:10}  {what}")
    return 0


def cmd_meeting_rename(args: argparse.Namespace) -> int:
    from .meeting import rename, resolve

    md = rename(resolve(args.meeting), args.old, args.new, args.email or "", C.load_config())
    print(f"{args.old} -> {args.new} in {md}; voice remembered")
    return 0


def cmd_meeting_calendar(args: argparse.Namespace) -> int:
    from . import gcal

    cfg = C.load_config()
    if args.url:
        if "calendar.google.com" not in args.url or not args.url.endswith(".ics"):
            print("that doesn't look like a secret iCal address (https://calendar.google.com/calendar/ical/…/basic.ics)",
                  file=sys.stderr)
            return 1
        print(f"saved to {gcal.add_url(cfg.meeting.calendar_file, args.url)} (chmod 600)")
    urls = gcal.read_urls(cfg.meeting.calendar_file)
    if not urls:
        print("no calendar yet. In Google Calendar: Settings > (your calendar) > Integrate calendar >\n"
              "\"Secret address in iCal format\", then: chttr meeting calendar 'https://calendar.google.com/…/basic.ics'")
        return 1
    events = gcal.upcoming(urls, hours=48)
    print(f"{len(urls)} calendar(s); next 48 h:")
    for e in events[:15]:
        who = ", ".join(a.name or a.email for a in e.attendees if not a.is_me)
        print(f"  {e.start:%a %H:%M}  {e.title[:40]:40}  {'Meet ' + e.meet_code if e.meet_code else '':18}  {who[:60]}")
    if not events:
        print("  (nothing)")
    return 0


def cmd_meeting_voices(args: argparse.Namespace) -> int:
    from .diarize import load_voices, save_voices

    voices = load_voices()
    if args.forget:
        key = args.forget.lower()
        if key not in voices:
            print(f"no voice {args.forget!r}", file=sys.stderr)
            return 1
        del voices[key]
        save_voices(voices)
        print(f"forgot {args.forget}")
        return 0
    if not voices:
        print("no voices remembered yet")
    for v in voices.values():
        print(f"  {v.name or '-':24} {v.email or '':32} {v.count} meeting(s), last {v.updated[:10]}")
    return 0


def cmd_meeting_install(args: argparse.Namespace) -> int:
    from .native_host import EXTENSION_DIR, extension_id, install

    for line in install():
        print(line)
    print(f"\nextension: {EXTENSION_DIR} (id {extension_id()})")
    print("Restart Chromium to load it. If you'd rather load it by hand: chrome://extensions >"
          " Developer mode > Load unpacked > the folder above.")
    return 0


def cmd_native_host(args: argparse.Namespace) -> int:
    from .native_host import run

    return run()


def cmd_setup(args: argparse.Namespace) -> int:
    from .install import run

    return run(do_models=not args.no_models, do_service=not args.no_service,
               do_bindings=not args.no_keybindings, do_meet=not args.no_meet)


def _terminal(command: str) -> None:
    import subprocess

    launcher = shutil.which("omarchy-launch-floating-terminal-with-presentation")
    cmd = [launcher, command] if launcher else ["xdg-terminal-exec", "bash", "-c", command + "; read -rp 'Press Enter'"]
    subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def cmd_menu(args: argparse.Namespace) -> int:
    """The bar widget's menu (Omarchy's menu picker)."""
    import subprocess

    from .history import MENU
    from .meeting import meeting_dirs, read_json

    if not shutil.which(MENU):
        print(f"{MENU} not found", file=sys.stderr)
        return 1
    try:
        status = send("meeting status")
    except DaemonNotRunning:
        status = None
    cfg = C.load_config()
    notes = [Path(m["markdown"]) for d in reversed(meeting_dirs())
             if (m := read_json(d / "meta.json", {}) or {}).get("markdown") and Path(m["markdown"]).exists()]
    items: list[tuple[str, callable]] = []
    if status is None:
        items.append(("󰑓  Start the chttr daemon", lambda: subprocess.run(["systemctl", "--user", "restart", "chttr"])))
    elif status.get("recording"):
        items.append(("󰓛  Stop recording the meeting", lambda: send("meeting stop")))
    else:
        items.append(("󰑊  Record a meeting", lambda: send("meeting start")))
    if notes:
        items.append((f"󰈙  Open the last meeting notes ({notes[0].stem[:40]})",
                      lambda: subprocess.Popen(["xdg-open", str(notes[0])], start_new_session=True)))
    meetings_dir = Path(cfg.meeting.dir).expanduser()
    items += [
        ("󰉋  Meeting notes folder", lambda: (meetings_dir.mkdir(parents=True, exist_ok=True),
                                            subprocess.Popen(["xdg-open", str(meetings_dir)], start_new_session=True))),
        ("󰋚  Dictation history", lambda: subprocess.Popen([sys.argv[0], "history"], start_new_session=True)),
        ("󰄨  Stats", lambda: _terminal("chttr stats")),
        ("󰒓  Edit settings", lambda: subprocess.Popen(["omarchy-launch-editor", str(C.CONFIG_FILE)], start_new_session=True)
            if shutil.which("omarchy-launch-editor") else _terminal(f"${{EDITOR:-nano}} {C.CONFIG_FILE}")),
        ("󰓦  Check setup (doctor)", lambda: _terminal("chttr doctor")),
        ("󰜉  Restart the daemon", lambda: subprocess.run(["systemctl", "--user", "restart", "chttr"])),
    ]
    out = subprocess.run([MENU, "chttr", *[label for label, _ in items]], capture_output=True, text=True)
    choice = out.stdout.strip()
    for label, action in items:
        if label == choice:
            action()
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    created = C.write_default_config()
    if args.path:
        print(C.CONFIG_FILE)
    else:
        print(("created " if created else "") + str(C.CONFIG_FILE))
        print(C.CONFIG_FILE.read_text())
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="chttr", description="Wispr Flow-style voice dictation for Omarchy.")
    p.add_argument("--version", action="version", version=f"chttr {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("daemon", help="run the dictation daemon (loads the whisper model once)")
    d.add_argument("-v", "--verbose", action="store_true")
    d.set_defaults(func=cmd_daemon)

    for name, help_ in (
        ("toggle", "hands-free: start recording (stops by itself on silence), or stop now"),
        ("start", "start recording (push-to-talk press)"),
        ("command", "start a command-mode recording on the selected text (key press)"),
        ("release", "push-to-talk key release: stop and transcribe unless hands-free"),
        ("stop", "stop any recording and transcribe"),
        ("cancel", "discard the current recording"),
        ("reload", "reload config.toml in the running daemon"),
        ("quit", "stop the daemon"),
    ):
        s = sub.add_parser(name, help=help_)
        s.add_argument("--json", action="store_true", help="print the daemon reply as JSON")
        s.set_defaults(func=cmd_send, command=name)

    st = sub.add_parser("status", help="print the daemon state (idle/recording/transcribing)")
    st.add_argument("--follow", action="store_true", help="stream state changes until interrupted")
    st.add_argument("--json", action="store_true", help="bar-friendly JSON (text/alt/class/tooltip)")
    st.set_defaults(func=cmd_status)

    t = sub.add_parser("transcribe", help="transcribe a 16 kHz mono WAV file (no daemon needed)")
    t.add_argument("file")
    t.add_argument("--raw", action="store_true", help="skip the cleanup step")
    t.add_argument("--engine", choices=["parakeet", "whisper"], help="override [engine] name")
    t.add_argument("-v", "--verbose", action="store_true", help="print timing to stderr")
    t.set_defaults(func=cmd_transcribe)

    po = sub.add_parser("polish", help="clean up dictated text from stdin with Claude")
    po.add_argument("--app", help="name of the focused app, for tone/formatting")
    po.set_defaults(func=cmd_polish)

    le = sub.add_parser("learn", help="teach chttr a word: chttr learn Sohrab --heard sorob --heard 'so rob'")
    le.add_argument("word", help="the correct spelling")
    le.add_argument("--heard", action="append", metavar="TEXT", help="what the recognizer produced instead (repeatable)")
    le.set_defaults(func=cmd_learn)

    fo = sub.add_parser("forget", help="remove a word and its replacements from the dictionary")
    fo.add_argument("word")
    fo.set_defaults(func=cmd_forget)

    di = sub.add_parser("dictionary", help="show the personal dictionary")
    di.set_defaults(func=cmd_dictionary)

    sn = sub.add_parser("snippet", help="add a voice snippet: chttr snippet 'my email' rob@example.com")
    sn.add_argument("phrase", help="what you say")
    sn.add_argument("expansion", help="what gets typed (\\n for a new line)")
    sn.set_defaults(func=cmd_snippet)

    fx = sub.add_parser("fix", help="correct a word from the last dictation so it sticks")
    fx.add_argument("wrong", nargs="?", help="what came out (skip to be asked)")
    fx.add_argument("right", nargs="?", help="what it should be")
    fx.set_defaults(func=cmd_fix)

    hi = sub.add_parser("history", help="pick a past dictation and type it again")
    hi.add_argument("--print", type=int, nargs="?", const=20, metavar="N", help="print the last N instead")
    hi.set_defaults(func=cmd_history)

    sa = sub.add_parser("stats", help="words dictated, speaking speed, time saved")
    sa.add_argument("--days", type=int, default=7)
    sa.set_defaults(func=cmd_stats)

    dr = sub.add_parser("doctor", help="check the engine, cleanup backend and daemon")
    dr.set_defaults(func=cmd_doctor)

    me = sub.add_parser("meeting", help="record meetings: transcripts with speaker names, summary and action items")
    msub = me.add_subparsers(dest="meeting_cmd", required=True)
    for name, help_ in (
        ("start", "start recording a meeting (mic + speaker output)"),
        ("stop", "stop recording and write the notes"),
        ("toggle", "start or stop"),
        ("cancel", "stop and throw the recording away"),
        ("status", "is a meeting being recorded?"),
    ):
        s = msub.add_parser(name, help=help_)
        s.add_argument("--json", action="store_true")
        s.set_defaults(func=cmd_meeting_control, action=name)
    mp = msub.add_parser("process", help="(re)write the notes for a recorded meeting")
    mp.add_argument("meeting", nargs="?", help="meeting id or directory (default: the latest)")
    mp.add_argument("--no-summary", action="store_true", help="transcript only, no Claude notes")
    mp.add_argument("--notify", action="store_true", help=argparse.SUPPRESS)
    mp.set_defaults(func=cmd_meeting_process)
    ml = msub.add_parser("list", help="recorded meetings")
    ml.add_argument("-n", type=int, default=20)
    ml.set_defaults(func=cmd_meeting_list)
    mr = msub.add_parser("rename", help="fix a speaker's name (and remember their voice)")
    mr.add_argument("old", help='label in the transcript, e.g. "Speaker 2"')
    mr.add_argument("new", help="their name")
    mr.add_argument("--email")
    mr.add_argument("--meeting", help="meeting id (default: the latest)")
    mr.set_defaults(func=cmd_meeting_rename)
    mc = msub.add_parser("calendar", help="add a Google Calendar secret iCal address / show upcoming meetings")
    mc.add_argument("url", nargs="?")
    mc.set_defaults(func=cmd_meeting_calendar)
    mv = msub.add_parser("voices", help="list remembered voices")
    mv.add_argument("--forget", metavar="EMAIL_OR_NAME")
    mv.set_defaults(func=cmd_meeting_voices)
    mi = msub.add_parser("install", help="register the Google Meet extension and its native host")
    mi.set_defaults(func=cmd_meeting_install)

    se = sub.add_parser("setup", help="download models, install the service, keybindings and Meet extension")
    se.add_argument("--no-models", action="store_true")
    se.add_argument("--no-service", action="store_true", help="don't install the systemd user service")
    se.add_argument("--no-keybindings", action="store_true")
    se.add_argument("--no-meet", action="store_true", help="skip the Google Meet extension")
    se.set_defaults(func=cmd_setup)

    mn = sub.add_parser("menu", help="the bar widget's menu")
    mn.set_defaults(func=cmd_menu)

    nh = sub.add_parser("native-host", help=argparse.SUPPRESS)
    nh.set_defaults(func=cmd_native_host)

    c = sub.add_parser("config", help="create (if missing) and show the config file")
    c.add_argument("--path", action="store_true", help="only print the config path")
    c.set_defaults(func=cmd_config)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    sys.exit(args.func(args))
