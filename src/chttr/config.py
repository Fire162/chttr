"""Configuration and well-known paths."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

APP = "chttr"

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP
CONFIG_FILE = Path(os.environ["CHTTR_CONFIG"]).expanduser() if os.environ.get("CHTTR_CONFIG") else CONFIG_DIR / "config.toml"
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/{APP}-{os.getuid()}")) / APP
SOCKET_PATH = RUNTIME_DIR / "chttr.sock"
STATE_FILE = RUNTIME_DIR / "state"
HISTORY_FILE = DATA_DIR / "history.jsonl"
MEETINGS_DATA_DIR = DATA_DIR / "meetings"        # per-meeting audio, events, intermediate results
MEETING_STATE_FILE = RUNTIME_DIR / "meeting"     # present while a meeting is being recorded
MODELS_DIR = DATA_DIR / "models"
VOICES_FILE = DATA_DIR / "voices.json"

# Directories searched for ggml whisper models, in order. voxtype ships with
# Omarchy and usually has already downloaded ggml-base.en.bin, so reuse it.
MODEL_SEARCH_DIRS = [
    Path.home() / ".local/share/voxtype/models",
    Path.home() / ".local/share/pywhispercpp/models",
]
PARAKEET_SEARCH_DIRS = [
    Path.home() / ".local/share/voxtype/models/parakeet-tdt-0.6b-v3-int8",
    DATA_DIR / "models/parakeet-tdt-0.6b-v3-int8",
]

DEFAULT_CONFIG = """\
# chttr configuration. Every key is optional; these are the defaults.
# Apply changes with: chttr reload   (switching engines reloads the model)

[engine]
# "parakeet": NVIDIA Parakeet TDT 0.6B v3, int8 ONNX. Fast and accurate on CPU,
#             reuses the model Omarchy's voxtype downloads. Multilingual.
# "whisper":  whisper.cpp with the [whisper] settings below.
name = "parakeet"
# Directory with encoder-model.int8.onnx / decoder_joint-model.int8.onnx / vocab.txt.
# "" = look in voxtype's model dir, then ~/.local/share/chttr/models.
parakeet_dir = ""
# 0 = auto (half the logical CPUs)
threads = 0

[whisper]
# ggml model name (tiny, base, base.en, small, small.en, medium, large-v3-turbo, ...)
# or an absolute path to a ggml-*.bin file. Looked up in the voxtype and
# pywhispercpp model directories and downloaded if missing.
model = "base.en"
language = "en"

[audio]
# PipeWire source name or serial (pactl list sources short); "" = default input
device = ""
# Safety limit for a single recording
max_seconds = 180
# Unmute the default source while recording (Omarchy's mic-mute toggle is
# easy to leave on); the previous mute state is restored afterwards.
unmute = true
# Hands-free mode (chttr toggle): stop automatically after this much silence
# once you have started speaking, and give up after no_speech_timeout seconds
# if nothing is heard at all.
auto_stop_silence = 1.5
no_speech_timeout = 10
# Levels above this (dBFS, per 20 ms chunk) count as speech
speech_threshold_db = -38

[output]
# "type"      - type into the focused window with wtype
# "paste"     - copy to clipboard and send Ctrl+V to the focused window
# "clipboard" - only copy to the clipboard
mode = "type"
trailing_space = true
fallback_to_clipboard = true

[polish]
# Wispr Flow-style cleanup: fillers removed, punctuation fixed, self-corrections
# applied, formatting adapted to the focused app. Also powers command mode.
# backend: "auto"   - Claude if a key is available, else Ollama if it is
#                     running, else no cleanup
#          "claude" | "ollama" | "none"
backend = "auto"
model = "claude-opus-5-5"
effort = "low"
timeout_seconds = 20
# Optional: file containing an Anthropic API key (chmod 600). Used when
# ANTHROPIC_API_KEY is not in the daemon's environment.
api_key_file = "~/.config/chttr/anthropic-api-key"
# Local alternative (fully offline): https://ollama.com, e.g. `ollama pull llama3.2`
ollama_model = "llama3.2"
ollama_url = "http://127.0.0.1:11434"
# Tell the model which app is focused so it can adapt tone/formatting
use_app_context = true

[feedback]
sounds = true
notifications = true
# Floating waveform + dB meter while recording (GTK4 layer-shell overlay)
overlay = true
# Show a rolling transcript in the overlay while you speak
live_captions = true

[history]
# Append every dictation to ~/.local/share/chttr/history.jsonl
enabled = true

[meeting]
# Meeting transcripts (chttr meeting start / stop, or automatic with the Meet
# extension: chttr meeting install). Your mic and the speaker output are
# recorded as separate channels; the other side is split into speakers and
# named from Meet's captions, your calendar and remembered voices.
# Where the Markdown notes go:
dir = "~/Documents/Meetings"
# When the Meet extension reports that you joined a call:
#   "ask"    - notification with a Record button
#   "always" - start recording straight away
#   "never"  - only record with chttr meeting start
auto = "ask"
# Keep the audio (stereo Opus: you left, them right) next to the notes
keep_audio = false
# Summary, decisions and action items with the [polish] Claude model (needs a key)
summary = true
summary_effort = "medium"
# Your name in transcripts; "" = from your calendar, else "Me"
me = ""
# File with secret iCal addresses, one per line (Google Calendar > Settings >
# your calendar > "Secret address in iCal format"). Add one with: chttr meeting calendar URL
calendar_file = "~/.config/chttr/calendar-urls"
# Safety limit
max_hours = 4

# Per-app modes. The first mode whose `match` (regex, case-insensitive) matches
# the focused window's class wins. `style` is extra guidance for the cleanup
# step; `polish = false` types the raw transcript; `output` and
# `trailing_space` override the [output] section.
[[modes]]
name = "terminal"
match = ["alacritty", "kitty", "foot", "wezterm", "ghostty", "xterm", "konsole", "gnome-terminal"]
style = "This is a terminal. Output a single line of plain text with no trailing period; keep commands, paths and flags exactly as spoken."
trailing_space = false

[[modes]]
name = "code"
match = ["^code", "cursor", "zed", "neovide", "jetbrains", "idea", "pycharm", "webstorm", "sublime"]
style = "This is a code editor. Keep identifiers, file names and symbols as spoken; use snake_case or camelCase if the user spells them that way. No trailing period unless it is a sentence in a comment."
trailing_space = false

[[modes]]
match = ["slack", "discord", "whatsapp", "signal", "telegram", "element", "messages", "beeper"]
name = "chat"
style = "This is a chat app. Keep it casual and concise; one short message; no sign-off."

[[modes]]
name = "email"
match = ["thunderbird", "mail", "gmail", "outlook", "proton", "hey"]
style = "This is an email client. Use complete, well-punctuated sentences and paragraphs where the user pauses between thoughts."

[[modes]]
name = "docs"
match = ["docs.google", "notion", "obsidian", "libreoffice", "writer", "typora", "logseq"]
style = "This is a document editor. Use complete sentences and paragraphs; honour dictated headings and lists."
"""


@dataclass
class EngineCfg:
    name: str = "parakeet"
    parakeet_dir: str = ""
    threads: int = 0


@dataclass
class WhisperCfg:
    model: str = "base.en"
    language: str = "en"
    threads: int = 0


@dataclass
class AudioCfg:
    device: str = ""
    max_seconds: int = 180
    unmute: bool = True
    auto_stop_silence: float = 1.5
    no_speech_timeout: float = 10
    speech_threshold_db: float = -38


@dataclass
class OutputCfg:
    mode: str = "type"
    trailing_space: bool = True
    fallback_to_clipboard: bool = True


@dataclass
class PolishCfg:
    backend: str = "auto"
    model: str = "claude-opus-5-5"
    effort: str = "low"
    timeout_seconds: float = 20
    api_key_file: str = "~/.config/chttr/anthropic-api-key"
    ollama_model: str = "llama3.2"
    ollama_url: str = "http://127.0.0.1:11434"
    use_app_context: bool = True
    # legacy key, maps to backend = "none" when false
    enabled: bool = True


@dataclass
class FeedbackCfg:
    sounds: bool = True
    notifications: bool = True
    overlay: bool = True
    live_captions: bool = True


@dataclass
class HistoryCfg:
    enabled: bool = True


@dataclass
class MeetingCfg:
    dir: str = "~/Documents/Meetings"
    auto: str = "ask"
    keep_audio: bool = False
    summary: bool = True
    summary_effort: str = "medium"
    me: str = ""
    calendar_file: str = "~/.config/chttr/calendar-urls"
    max_hours: float = 4


@dataclass
class Mode:
    name: str = ""
    match: list[str] = field(default_factory=list)
    style: str = ""
    polish: bool = True
    output: str = ""              # "" = use [output].mode
    trailing_space: bool | None = None

    def matches(self, window_class: str) -> bool:
        return any(re.search(p, window_class, re.IGNORECASE) for p in self.match)


@dataclass
class Config:
    engine: EngineCfg = field(default_factory=EngineCfg)
    whisper: WhisperCfg = field(default_factory=WhisperCfg)
    audio: AudioCfg = field(default_factory=AudioCfg)
    output: OutputCfg = field(default_factory=OutputCfg)
    polish: PolishCfg = field(default_factory=PolishCfg)
    feedback: FeedbackCfg = field(default_factory=FeedbackCfg)
    history: HistoryCfg = field(default_factory=HistoryCfg)
    meeting: MeetingCfg = field(default_factory=MeetingCfg)
    modes: list[Mode] = field(default_factory=list)

    def mode_for(self, window_class: str) -> Mode | None:
        for m in self.modes:
            if window_class and m.matches(window_class):
                return m
        return None


def _fill(obj, data: dict) -> None:
    for key, value in data.items():
        if hasattr(obj, key):
            setattr(obj, key, value)


def _default_modes() -> list[Mode]:
    data = tomllib.loads(DEFAULT_CONFIG)
    return [_mode_from(d) for d in data.get("modes", [])]


def _mode_from(d: dict) -> Mode:
    m = Mode()
    _fill(m, d)
    if isinstance(m.match, str):
        m.match = [m.match]
    return m


def load_config(path: Path = CONFIG_FILE) -> Config:
    cfg = Config(modes=_default_modes())
    if not path.exists():
        return cfg
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    for section in ("engine", "whisper", "audio", "output", "polish", "feedback", "history", "meeting"):
        if isinstance(data.get(section), dict):
            _fill(getattr(cfg, section), data[section])
    if isinstance(data.get("modes"), list):
        cfg.modes = [_mode_from(d) for d in data["modes"] if isinstance(d, dict)]
    if not cfg.polish.enabled:
        cfg.polish.backend = "none"
    if cfg.output.mode not in ("type", "paste", "clipboard"):
        raise ValueError(f"output.mode must be type, paste or clipboard (got {cfg.output.mode!r})")
    if cfg.engine.name not in ("parakeet", "whisper"):
        raise ValueError(f"engine.name must be parakeet or whisper (got {cfg.engine.name!r})")
    if cfg.meeting.auto not in ("ask", "always", "never"):
        raise ValueError(f"meeting.auto must be ask, always or never (got {cfg.meeting.auto!r})")
    if cfg.polish.backend not in ("auto", "claude", "ollama", "none"):
        raise ValueError(f"polish.backend must be auto, claude, ollama or none (got {cfg.polish.backend!r})")
    return cfg


def write_default_config(path: Path = CONFIG_FILE) -> bool:
    """Create the default config file if missing. Returns True if written."""
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_CONFIG)
    return True


def ensure_runtime_dir() -> Path:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    return RUNTIME_DIR
