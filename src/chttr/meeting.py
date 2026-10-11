"""Meeting transcripts: recording sessions, the post-call pipeline, and the Markdown notes.

A meeting lives in ~/.local/share/chttr/meetings/<id>/ while it is recorded and
processed:

    meta.json      when, Meet code, title, status
    mic.raw        your microphone (s16le, 16 kHz)
    them.raw       the speaker output, i.e. everyone else
    events.jsonl   from the Meet extension: who the captions say is speaking,
                   mute changes; from the daemon: dictations made during the call
    result.json    utterances, speakers and notes after processing
    speakers.npz   voice embeddings per speaker, for `chttr meeting rename`

The notes go to [meeting] dir as "<date> <time> <title>.md". The raw audio is
deleted once the notes are written (or kept as Opus with keep_audio = true).

Who said what:
- Your words are whatever your microphone picked up (minus the moments Meet had
  you muted, dictations you made with chttr, and the other side's voices leaking
  from your speakers).
- The other side is split into voices by speaker embeddings. Each voice is named
  by, in order: Meet's captions (the extension reports the captioned speaker
  over time), a remembered voice, the calendar (a 1:1 has only one other
  person), or Claude reading the conversation. Whatever is left stays
  "Speaker N"; `chttr meeting rename "Speaker 2" "Name"` fixes it and teaches
  chttr that voice.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from . import config as C
from . import diarize as D
from .config import Config

log = logging.getLogger("chttr.meeting")

SAMPLE_RATE = 16000
# How Meet labels your own captions, in the languages it's most often used in
SELF_CAPTION_NAMES = {"you", "du", "vous", "tú", "tu", "você", "jij", "ty", "あなた", "你"}
CAPTION_LAG = (0.1, 1.8)         # caption text appears this long after the words are spoken
ECHO_OVERLAP = 0.6               # share of mic words also heard on the speakers => echo, not you


# ---- data -----------------------------------------------------------------

@dataclass
class Utterance:
    start: float            # seconds from the start of the recording
    end: float
    channel: str            # "mic" | "them"
    text: str = ""
    cluster: int = -1       # voice cluster on the "them" channel
    speaker: str = ""       # label in the transcript


@dataclass
class Speaker:
    label: str
    name: str = ""
    email: str = ""
    source: str = ""        # me | captions | voice | calendar | claude | unknown
    evidence: str = ""
    clusters: list[int] = field(default_factory=list)
    seconds: float = 0.0


def meeting_dirs() -> list[Path]:
    if not C.MEETINGS_DATA_DIR.exists():
        return []
    return sorted((d for d in C.MEETINGS_DATA_DIR.iterdir() if (d / "meta.json").exists()), key=lambda d: d.name)


def resolve(ref: str | None) -> Path:
    """A meeting directory from an id, a path, or None for the latest one."""
    if ref:
        p = Path(ref).expanduser()
        if (p / "meta.json").exists():
            return p
        matches = [d for d in meeting_dirs() if d.name == ref or d.name.startswith(ref)]
        if len(matches) == 1:
            return matches[0]
        raise SystemExit(f"no single meeting matches {ref!r} (see: chttr meeting list)")
    dirs = meeting_dirs()
    if not dirs:
        raise SystemExit("no meetings recorded yet")
    return dirs[-1]


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    tmp.replace(path)


def update_meta(directory: Path, **changes) -> dict:
    meta = read_json(directory / "meta.json", {}) or {}
    meta.update(changes)
    write_json(directory / "meta.json", meta)
    return meta


def new_meeting_dir(started: float, code: str) -> Path:
    stamp = datetime.fromtimestamp(started).strftime("%Y-%m-%d-%H%M%S")
    d = C.MEETINGS_DATA_DIR / (stamp + (f"-{code}" if code else ""))
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def launch_processing(directory: Path) -> None:
    """Run `chttr meeting process` in its own session at low priority, so the
    daemon stays responsive and a daemon restart doesn't kill it."""
    log_file = (directory / "process.log").open("ab")
    cmd = ["nice", "-n", "10", sys.executable, "-m", "chttr", "meeting", "process", "--notify", str(directory)]
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True)


class Session:
    """One meeting being recorded (owned by the daemon)."""

    def __init__(self, cfg: Config, code: str = "", title: str = "", source: str = "manual") -> None:
        from .meeting_recorder import MeetingRecorder

        self.code, self.title, self.source = code, title, source
        self.dir = new_meeting_dir(time.time(), code)
        self.recorder = MeetingRecorder(self.dir, cfg.audio.device)
        self._events = None

    @property
    def elapsed(self) -> float:
        return self.recorder.elapsed

    def start(self) -> None:
        self.recorder.start()
        self._events = (self.dir / "events.jsonl").open("a")
        self.save_meta(status="recording")

    def save_meta(self, **extra) -> None:
        update_meta(self.dir, id=self.dir.name, started=self.recorder.started, code=self.code,
                    title=self.title, source=self.source, **extra)

    def log(self, event: dict) -> None:
        if self._events:
            self._events.write(json.dumps(event, ensure_ascii=False) + "\n")
            self._events.flush()

    def stop(self) -> float:
        secs = self.recorder.stop()
        if self._events:
            self._events.close()
            self._events = None
        self.save_meta(status="recorded", duration=round(secs, 1))
        return secs

    def discard(self) -> None:
        self.recorder.stop()
        if self._events:
            self._events.close()
            self._events = None
        shutil.rmtree(self.dir, ignore_errors=True)


# ---- pipeline -------------------------------------------------------------

def _load_channel(directory: Path, name: str) -> np.ndarray:
    path = directory / f"{name}.raw"
    if not path.exists() or path.stat().st_size < SAMPLE_RATE:
        return np.zeros(0, dtype=np.int16)
    return np.memmap(path, dtype=np.int16, mode="r")


def _intervals(events: list[dict], started: float) -> list[tuple[float, float]]:
    """Stretches of the recording your mic should not count: muted in Meet, or dictating."""
    out = []
    muted_since = None
    for e in events:
        t = float(e.get("t", 0)) - started
        if e.get("type") == "mute":
            if e.get("muted") and muted_since is None:
                muted_since = t
            elif not e.get("muted") and muted_since is not None:
                out.append((muted_since, t))
                muted_since = None
        elif e.get("type") == "dictation":
            out.append((float(e["start"]) - started, float(e["end"]) - started))
    if muted_since is not None:
        out.append((muted_since, float("inf")))
    return out


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _words(text: str) -> list[str]:
    return re.findall(r"[\w']+", text.lower())


def _is_echo(mic: Utterance, them: list[Utterance]) -> bool:
    """Your speakers' sound reaching your mic transcribes to the same words as the
    them channel at the same moment."""
    near = [u for u in them if _overlap(mic.start - 1, mic.end + 1, u.start, u.end) > 0]
    if not near:
        return False
    mw = _words(mic.text)
    if len(mw) < 2:
        return True     # a word or two of mic sound while they talk: crosstalk, not a turn
    tw = _words(" ".join(u.text for u in near))
    matched = sum(b.size for b in difflib.SequenceMatcher(None, mw, tw, autojunk=False).get_matching_blocks())
    return matched / len(mw) >= ECHO_OVERLAP


def _transcribe(utts: list[Utterance], audio: dict[str, np.ndarray], cfg: Config) -> None:
    from .dictionary import Dictionary
    from .transcriber import Transcriber, _clean

    engine = Transcriber(cfg).engine
    dictionary = Dictionary.load()
    prompt = dictionary.whisper_prompt()
    t0 = time.monotonic()
    for i, u in enumerate(utts):
        seg = D.as_float(audio[u.channel][int(u.start * SAMPLE_RATE):int(u.end * SAMPLE_RATE)])
        text = re.sub(r"\s+", " ", engine.recognize(seg, prompt)).strip()
        if engine.name == "whisper":
            text = _clean(text)
        u.text = dictionary.apply(text) if text else ""
        if i and i % 50 == 0:
            log.info("transcribed %d/%d utterances (%.0fs)", i, len(utts), time.monotonic() - t0)


def _diarize(them: list[Utterance], audio: np.ndarray, threads: int) -> dict[int, np.ndarray]:
    """Sets .cluster on every them-utterance; returns {cluster: centroid}."""
    if not them:
        return {}
    embed = D.Embedder(threads)
    vecs: dict[int, np.ndarray] = {}
    for i, u in enumerate(them):
        if u.end - u.start >= D.MIN_EMBED_SECONDS:
            vecs[i] = embed(D.as_float(audio[int(u.start * SAMPLE_RATE):int(u.end * SAMPLE_RATE)]))
    if not vecs:
        for u in them:
            u.cluster = 0
        return {}
    idx = list(vecs)
    labels = D.cluster(np.stack([vecs[i] for i in idx]))
    for i, label in zip(idx, labels):
        them[i].cluster = int(label)
    centroids = {}
    for c in set(int(x) for x in labels):
        rows = [vecs[i] for i, label in zip(idx, labels) if label == c]
        weights = [them[i].end - them[i].start for i, label in zip(idx, labels) if label == c]
        centroids[c] = D.centroid(np.stack(rows), np.array(weights))
    # Short utterances: nearest voice if it is close, else whoever spoke just before.
    for i, u in enumerate(them):
        if u.cluster >= 0:
            continue
        seg = D.as_float(audio[int(u.start * SAMPLE_RATE):int(u.end * SAMPLE_RATE)])
        best, score = -1, -1.0
        if len(seg) >= SAMPLE_RATE // 2:
            v = embed(seg)
            best, score = max(((c, float(v @ m)) for c, m in centroids.items()), key=lambda x: x[1])
        prev = next((them[j] for j in range(i - 1, -1, -1) if them[j].cluster >= 0), None)
        if score >= 0.3 or prev is None or u.start - prev.end > 3:
            u.cluster = best if best >= 0 else (prev.cluster if prev else 0)
        else:
            u.cluster = prev.cluster
    return centroids


def _caption_votes(them: list[Utterance], events: list[dict], started: float,
                   self_names: set[str]) -> tuple[dict[int, Counter], list[Counter]]:
    speaking = sorted((float(e["t"]) - started, str(e["name"]).strip())
                      for e in events if e.get("type") == "speaking" and e.get("name"))
    speaking = [(t, n) for t, n in speaking if n.lower() not in self_names]
    times = np.array([t for t, _ in speaking])
    per_utt: list[Counter] = []
    per_cluster: dict[int, Counter] = defaultdict(Counter)
    for u in them:
        votes: Counter = Counter()
        if len(times):
            lo, hi = np.searchsorted(times, [u.start + CAPTION_LAG[0], u.end + CAPTION_LAG[1]])
            votes.update(n for _, n in speaking[lo:hi])
        per_utt.append(votes)
        per_cluster[u.cluster].update(votes)
    return per_cluster, per_utt


def _norm(name: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", name.lower()).split())


def _match_attendee(name: str, attendees: list[dict]) -> dict | None:
    """The invitee a Meet display name belongs to: same name, else a unique first-name
    match, else a unique email local-part match."""
    n = _norm(name)
    if not n:
        return None
    for a in attendees:
        if a["name"] and _norm(a["name"]) == n:
            return a
    first = n.split()[0]
    by_first = [a for a in attendees if a["name"] and _norm(a["name"]).split()[0] == first]
    if len(by_first) == 1:
        return by_first[0]
    by_email = [a for a in attendees if first in re.sub(r"[^a-z]", " ", a["email"].split("@")[0].lower()).split()
                or a["email"].split("@")[0].lower().startswith(first)]
    return by_email[0] if len(by_email) == 1 else None


def _fmt_ts(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _turns(utts: list[Utterance], gap: float = 3.0) -> list[tuple[str, float, str]]:
    """Consecutive utterances by the same speaker joined into turns: (label, start, text)."""
    turns: list[list] = []      # [label, start, text, end]
    for u in sorted(utts, key=lambda u: u.start):
        if not u.text:
            continue
        if turns and turns[-1][0] == u.speaker and u.start - turns[-1][3] < gap:
            turns[-1][2] += " " + u.text
            turns[-1][3] = max(turns[-1][3], u.end)
        else:
            turns.append([u.speaker, u.start, u.text, u.end])
    return [(label, start, text) for label, start, text, _ in turns]


def process(directory: Path, cfg: Config, summary: bool | None = None) -> Path:
    """Turn a recorded meeting into notes. Returns the Markdown file."""
    from . import gcal
    from . import notes as N

    meta = update_meta(directory, status="processing", error="")
    started = float(meta["started"])
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines() if line.strip()] \
        if (directory / "events.jsonl").exists() else []
    threads = cfg.engine.threads
    D.ensure_models()

    audio = {"mic": _load_channel(directory, "mic"), "them": _load_channel(directory, "them")}
    utts: list[Utterance] = []
    with D.timed("voice activity"):
        excluded = _intervals(events, started)
        for channel, data in audio.items():
            for s, e in D.speech_segments(data, threads):
                if channel == "mic" and sum(_overlap(s, e, a, b) for a, b in excluded) > 0.5 * (e - s):
                    continue
                utts.append(Utterance(s, e, channel))
    log.info("%d utterances (%d mic, %d them)", len(utts), sum(u.channel == "mic" for u in utts),
             sum(u.channel == "them" for u in utts))

    with D.timed("transcription"):
        _transcribe(utts, audio, cfg)
    utts = [u for u in utts if u.text]
    them = sorted((u for u in utts if u.channel == "them"), key=lambda u: u.start)
    mine = [u for u in utts if u.channel == "mic" and not _is_echo(u, them)]
    log.info("dropped %d mic utterances as echo", sum(u.channel == "mic" for u in utts) - len(mine))

    with D.timed("speaker embeddings"):
        centroids = _diarize(them, audio["them"], threads)

    # ---- who is who --------------------------------------------------------
    urls = gcal.read_urls(cfg.meeting.calendar_file)
    event = gcal.find_event(urls, meta.get("code", ""), datetime.fromtimestamp(started).astimezone()) if urls else None
    attendees = [a.to_dict() for a in event.attendees] if event else []
    me_att = next((a for a in attendees if a["is_me"]), None)
    me_name = cfg.meeting.me or (me_att["name"] if me_att and me_att["name"] else "") or "Me"
    others = [a for a in attendees if not a["is_me"]]
    invited = {a["email"] for a in others}

    speakers: dict[str, Speaker] = {}
    if mine:
        speakers[me_name] = Speaker(me_name, me_name, me_att["email"] if me_att else "", "me")
        for u in mine:
            u.speaker = me_name

    self_names = SELF_CAPTION_NAMES | {_norm(me_name)} | ({_norm(me_att["name"])} if me_att and me_att["name"] else set())
    per_cluster, per_utt = _caption_votes(them, events, started, self_names)
    cluster_label: dict[int, str] = {}
    cluster_seconds = Counter()
    for u in them:
        cluster_seconds[u.cluster] += u.end - u.start

    def add_speaker(label: str, cluster: int, source: str, name: str = "", email: str = "", evidence: str = "") -> None:
        sp = speakers.get(label)
        if sp is None:
            sp = speakers[label] = Speaker(label, name or label, email, source, evidence)
        if cluster not in sp.clusters:
            sp.clusters.append(cluster)
        cluster_label[cluster] = label

    for c, votes in per_cluster.items():
        total = sum(votes.values())
        if not total:
            continue
        name, n = votes.most_common(1)[0]
        if n >= 3 and n / total >= 0.6:
            att = _match_attendee(name, others)
            add_speaker(name, c, "captions", name, att["email"] if att else "",
                        f"Meet captions named {name} for {n} of {total} caption updates")

    voices = D.load_voices()
    taken = {sp.email or sp.label.lower() for sp in speakers.values()}
    for c in sorted(centroids, key=lambda c: -cluster_seconds[c]):
        if c in cluster_label:
            continue
        v, score = D.match_voice({k: x for k, x in voices.items() if k not in taken}, centroids[c], invited)
        if v:
            att = next((a for a in others if a["email"] == v.email), None)
            label = (att["name"] if att and att["name"] else v.name) or v.email
            add_speaker(label, c, "voice", label, v.email, f"voice matches the one remembered for {label} ({score:.2f})")
            taken.add(v.key)

    unnamed = [c for c in cluster_seconds if c not in cluster_label]
    them_total = sum(cluster_seconds.values()) or 1
    left = [a for a in others if (a["email"] not in taken) and a["status"] != "DECLINED"]
    if len(unnamed) == 1 and len(left) == 1 and len(cluster_label) == 0 \
            and cluster_seconds[unnamed[0]] / them_total > 0.8:
        a = left[0]
        label = a["name"] or a["email"]
        add_speaker(label, unnamed[0], "calendar", label, a["email"], "the only other person invited")
        unnamed = []

    for n, c in enumerate(sorted(unnamed, key=lambda c: min(u.start for u in them if u.cluster == c)), 1):
        add_speaker(f"Speaker {n}", c, "unknown")

    for u, votes in zip(them, per_utt):
        u.speaker = cluster_label.get(u.cluster, "")
        total = sum(votes.values())
        if total >= 4:
            name, n = votes.most_common(1)[0]
            if n / total >= 0.85 and name in speakers and name != u.speaker:
                u.speaker = name        # captions clearly disagree with the voice clustering here
    for sp in speakers.values():
        sp.seconds = sum(u.end - u.start for u in utts if u.speaker == sp.label and u.text)
    all_utts = sorted(mine + them, key=lambda u: u.start)

    # ---- notes -------------------------------------------------------------
    notes: dict = {}
    duration = max((len(a) for a in audio.values()), default=0) / SAMPLE_RATE
    want_summary = cfg.meeting.summary if summary is None else summary
    if want_summary and all_utts and N.available(cfg.polish):
        transcript = "\n".join(f"[{_fmt_ts(s)}] {label}: {text}" for label, s, text in _turns(all_utts))
        when = datetime.fromtimestamp(started).astimezone()
        meeting_desc = "\n".join(x for x in (
            f"Calendar title: {event.title}" if event else "",
            f"Started: {when:%A %Y-%m-%d %H:%M} ({duration / 60:.0f} min)",
            f"Recorded by: {me_name} (their own microphone; their lines are labelled {me_name})",
            f"Calendar description: {event.description[:1500]}" if event and event.description.strip() else "",
        ) if x)
        invited_desc = "\n".join(f"- {a['name'] or '(no name)'} <{a['email']}>" + (" (organizer)" if a["organizer"] else "")
                                 + (" (the user)" if a["is_me"] else "") + (" (declined)" if a["status"] == "DECLINED" else "")
                                 for a in attendees)
        speakers_desc = "\n".join(f"- {sp.label}: " + {"me": "the user who recorded the meeting",
                                                        "captions": "named by Meet's captions",
                                                        "voice": "recognised by voice",
                                                        "calendar": "the only other invitee",
                                                        "unknown": "not identified"}[sp.source]
                                  for sp in speakers.values() if sp.seconds)
        unidentified = [sp.label for sp in speakers.values() if sp.source == "unknown" and sp.seconds]
        try:
            with D.timed("notes"):
                notes = N.write_notes(cfg.polish, cfg.meeting.summary_effort, meeting_desc, invited_desc,
                                      speakers_desc, unidentified, transcript)
        except Exception as exc:  # noqa: BLE001 - the transcript is still worth saving
            log.warning("notes failed: %s", exc)
            notes = {"error": f"{type(exc).__name__}: {exc}"}
        for guess in notes.get("speakers", []):
            sp = speakers.get(guess.get("label", ""))
            if sp and sp.source == "unknown" and guess.get("name") and guess.get("confidence") in ("high", "medium"):
                _relabel(speakers, all_utts, notes, sp.label, guess["name"], guess.get("email", ""),
                         "claude", f"{guess['confidence']} confidence: {guess.get('evidence', '')}")

    # ---- remember voices we are sure of ------------------------------------
    for sp in speakers.values():
        vecs = [centroids[c] for c in sp.clusters if c in centroids]
        secs = sum(cluster_seconds[c] for c in sp.clusters)
        if sp.source in ("captions", "calendar", "voice") and vecs and secs >= 10:
            D.remember(voices, sp.name, sp.email, D.centroid(np.stack(vecs)), secs)
    D.save_voices(voices)
    np.savez(directory / "speakers.npz", **{str(c): v for c, v in centroids.items()})

    result = {
        "meta": meta, "duration": duration,
        "event": event.to_dict() if event else None,
        "speakers": {k: asdict(v) for k, v in speakers.items()},
        "utterances": [asdict(u) for u in all_utts],
        "notes": notes,
    }
    md = render(directory, result, cfg)
    result["markdown"] = str(md)
    write_json(directory / "result.json", result)

    _finish_audio(directory, md, cfg)
    update_meta(directory, status="done", markdown=str(md))
    return md


def _relabel(speakers: dict[str, Speaker], utts: list[Utterance], notes: dict, old: str, name: str,
             email: str, source: str, evidence: str) -> str:
    """Rename a speaker everywhere (merging into an existing speaker of that name)."""
    sp = speakers.pop(old)
    target = speakers.get(name)
    if target:
        target.clusters += [c for c in sp.clusters if c not in target.clusters]
        target.seconds += sp.seconds
        target.email = target.email or email
    else:
        sp.label, sp.name, sp.email, sp.source, sp.evidence = name, name, email or sp.email, source, evidence
        speakers[name] = sp
    for u in utts:
        if u.speaker == old:
            u.speaker = name
    pattern = re.compile(r"\b" + re.escape(old) + r"\b")
    for key in ("summary", "decisions", "open_questions"):
        notes[key] = [pattern.sub(name, s) for s in notes.get(key, [])]
    for item in notes.get("action_items", []):
        item["owner"] = name if item.get("owner") == old else item.get("owner", "")
        item["task"] = pattern.sub(name, item.get("task", ""))
    return name


def _finish_audio(directory: Path, md: Path, cfg: Config) -> None:
    raws = [directory / "mic.raw", directory / "them.raw"]
    if cfg.meeting.keep_audio and all(p.exists() for p in raws) and shutil.which("ffmpeg"):
        out = md.with_suffix(".opus")
        cmd = ["ffmpeg", "-loglevel", "error", "-y",
               "-f", "s16le", "-ar", "16000", "-ac", "1", "-i", str(raws[0]),
               "-f", "s16le", "-ar", "16000", "-ac", "1", "-i", str(raws[1]),
               "-filter_complex", "[0:a][1:a]join=inputs=2:channel_layout=stereo[a]", "-map", "[a]",
               "-c:a", "libopus", "-b:a", "48k", str(out)]
        if subprocess.run(cmd, check=False).returncode != 0:
            log.warning("could not encode the audio; keeping the raw files in %s", directory)
            return
    for p in raws:
        p.unlink(missing_ok=True)


# ---- rendering ------------------------------------------------------------

def _q(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def _filename(title: str, when: datetime) -> str:
    safe = re.sub(r"[/\\:*?\"<>|\n\r\t]+", " ", title).strip()[:80] or "Meeting"
    return f"{when:%Y-%m-%d %H%M} {safe}"


def render(directory: Path, result: dict, cfg: Config) -> Path:
    meta, notes, event = result["meta"], result.get("notes") or {}, result.get("event")
    speakers = {k: Speaker(**v) for k, v in result["speakers"].items()}
    utts = [Utterance(**u) for u in result["utterances"]]
    started = datetime.fromtimestamp(float(meta["started"])).astimezone()
    ended = started + timedelta(seconds=result["duration"])
    title = notes.get("title") or (event or {}).get("title") or meta.get("title") or "Meeting"
    attendees = (event or {}).get("attendees", [])
    spoke = {sp.email for sp in speakers.values() if sp.email and sp.seconds} | \
            {sp.name.lower() for sp in speakers.values() if sp.seconds}

    lines = ["---", f"title: {_q(title)}", f"date: {started:%Y-%m-%d}", f"start: {_q(f'{started:%H:%M}')}",
             f"end: {_q(f'{ended:%H:%M}')}", f"duration_minutes: {round(result['duration'] / 60)}"]
    if meta.get("code"):
        lines.append(f"meet: {_q('https://meet.google.com/' + meta['code'])}")
    if event:
        lines.append(f"calendar_event: {_q(event['title'])}")
    if attendees:
        lines.append("attendees:")
        for a in attendees:
            lines += [f"  - name: {_q(a['name'])}", f"    email: {_q(a['email'])}",
                      f"    spoke: {'true' if a['email'] in spoke or (a['name'] and a['name'].lower() in spoke) else 'false'}"]
    lines.append("speakers: [" + ", ".join(_q(s.label) for s in speakers.values() if s.seconds) + "]")
    lines += ["tags: [meeting]", "---", "", f"# {title}", ""]
    when = f"{started:%a %b %-d, %Y} · {started:%H:%M}–{ended:%H:%M} ({round(result['duration'] / 60)} min)"
    lines.append(f"**{when}**" + (f" · Google Meet `{meta['code']}`" if meta.get("code") else ""))
    if attendees:
        names = [(a["name"] or a["email"]) + (" (organizer)" if a["organizer"] else "") for a in attendees
                 if a["status"] != "DECLINED"]
        lines += ["", "**Invited:** " + ", ".join(names)]
    extra = [s.label for s in speakers.values() if s.seconds and s.source != "me"
             and not any(s.email and s.email == a["email"] for a in attendees)
             and not any(a["name"] and a["name"].lower() == s.name.lower() for a in attendees)]
    if attendees and extra:
        lines.append("**Also speaking:** " + ", ".join(extra))
    lines.append("")

    def section(heading: str, items: list[str]) -> None:
        if items:
            lines.extend([f"## {heading}", ""] + [f"- {x}" for x in items] + [""])

    if notes.get("error"):
        lines += [f"> Notes could not be written: {notes['error']}", ""]
    section("Summary", notes.get("summary", []))
    section("Decisions", notes.get("decisions", []))
    actions = [f"[ ] " + (f"**{a['owner']}**: " if a.get("owner") else "") + a["task"]
               + (f" *(due {a['due']})*" if a.get("due") else "") for a in notes.get("action_items", [])]
    section("Action items", actions)
    section("Open questions", notes.get("open_questions", []))

    lines += ["## Transcript", ""]
    for label, start, text in _turns(utts):
        lines += [f"**{label}** · {_fmt_ts(start)}  ", text, ""]

    how = {"me": "your microphone", "captions": "Meet captions", "voice": "remembered voice",
           "calendar": "calendar (only other invitee)", "claude": "inferred by Claude", "corrected": "corrected by you",
           "unknown": "not identified"}
    lines += ["## Speakers", ""]
    for sp in speakers.values():
        if not sp.seconds:
            continue
        who = sp.label + (f" <{sp.email}>" if sp.email else "")
        detail = f" ({sp.evidence})" if sp.evidence and sp.source in ("claude", "voice") else ""
        lines.append(f"- {who}: {how.get(sp.source, sp.source)}{detail}, {sp.seconds / 60:.1f} min")
    unsure = [sp.label for sp in speakers.values() if sp.source in ("unknown", "claude") and sp.seconds]
    if unsure:
        lines += ["", f"Wrong or missing name? `chttr meeting rename \"{unsure[0]}\" \"Their Name\" --meeting {directory.name}`"]
    lines.append("")

    out_dir = Path(cfg.meeting.dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    previous = result.get("markdown") or meta.get("markdown")
    if previous and Path(previous).parent == out_dir and Path(previous).exists():
        path = Path(previous)          # re-render in place (rename, reprocess)
    else:
        base = _filename(title, started)
        path = out_dir / f"{base}.md"
        n = 2
        while path.exists():
            path = out_dir / f"{base} ({n}).md"
            n += 1
    path.write_text("\n".join(lines))
    return path


# ---- corrections ----------------------------------------------------------

def rename(directory: Path, old: str, name: str, email: str, cfg: Config) -> Path:
    """Fix a speaker's name in a processed meeting and remember their voice."""
    result = read_json(directory / "result.json")
    if not result:
        raise SystemExit(f"{directory.name} has not been processed yet")
    speakers = {k: Speaker(**v) for k, v in result["speakers"].items()}
    if old not in speakers:
        raise SystemExit(f"no speaker {old!r} in this meeting; speakers: {', '.join(speakers)}")
    if speakers[old].source == "me":
        raise SystemExit("that's you; set [meeting] me = \"Your Name\" in the config instead")
    clusters = list(speakers[old].clusters)
    utts = [Utterance(**u) for u in result["utterances"]]
    notes = result.get("notes") or {}
    _relabel(speakers, utts, notes, old, name, email, "corrected", "corrected by hand")
    result.update(speakers={k: asdict(v) for k, v in speakers.items()}, utterances=[asdict(u) for u in utts], notes=notes)
    md = render(directory, result, cfg)
    result["markdown"] = str(md)
    write_json(directory / "result.json", result)
    npz = directory / "speakers.npz"
    if npz.exists() and clusters:
        stored = np.load(npz)
        vecs = [stored[str(c)] for c in clusters if str(c) in stored]
        secs = sum(u.end - u.start for u in utts if u.speaker == name and u.channel == "them")
        if vecs:
            voices = D.load_voices()
            D.remember(voices, name, email, D.centroid(np.stack(vecs)), max(secs, 60))
            D.save_voices(voices)
    return md


# ---- notification ---------------------------------------------------------

def notify_done(md: Path, summary_line: str) -> None:
    """Notification with an Open button; waits for the click in this (background) process."""
    try:
        out = subprocess.run(["notify-send", "-a", "chttr", "-A", "open=Open", "-t", "60000", "--wait",
                              f"Meeting notes saved: {md.stem}", summary_line],
                             capture_output=True, text=True, timeout=120)
        if out.stdout.strip() == "open":
            subprocess.Popen(["xdg-open", str(md)], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        pass


def summary_line(result_md: Path, directory: Path) -> str:
    result = read_json(directory / "result.json", {}) or {}
    names = [k for k, v in result.get("speakers", {}).items() if v.get("seconds")]
    mins = round(result.get("duration", 0) / 60)
    return f"{mins} min · " + ", ".join(names[:6]) + ("…" if len(names) > 6 else "")
