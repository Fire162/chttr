# Changelog

All notable changes to chttr. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project uses [semantic versioning](https://semver.org/).

## [Unreleased]

## [0.4.0] - 2026-10-13

Speed: text arrives about as fast as you let go of the key. This release also
contains everything listed under 0.3.0 (tests, CI, pinned models, privacy
hardening), which was not released on its own.

### Added
- Streaming recognition (`[engine] streaming = true`, the default): once you
  have said a few seconds' worth, the next pause closes a segment that is
  transcribed in the background while you keep talking, so after release only
  the last stretch is left to recognize. Segments keep at least 4 seconds of
  speech, so accuracy matches a single pass over the whole recording. Live captions come from the same
  passes instead of re-transcribing the last 8 seconds every 0.8 seconds.
- `[polish] cleanup_model`, default `claude-haiku-5-5`: dictation cleanup and
  command mode run on Haiku with thinking off, which answers in a few hundred
  milliseconds. Meeting notes stay on `[polish] model` (Opus).
- Quick path: short phrases with no fillers, self-corrections or spoken
  punctuation ("git status", "sounds good") skip the model round trip
  (`[polish] quick_max_words`, default 4).
- `chttr bench FILE.wav…`: full-pass vs. after-release recognition time,
  cleanup time, real-time factor and word error rate. CI runs it on every
  change to `main` and publishes the numbers.
- `[output] paste_over`: dictations longer than 400 characters are pasted
  instead of typed character by character; your clipboard is restored.

### Changed
- One Anthropic client for the daemon's lifetime (no TLS handshake per
  dictation), the cleanup system prompt is marked for prompt caching, and the
  daemon opens the API connection at startup.
- Every file chttr reads or writes (notes, config, dictionary, history) is UTF-8
  whatever the system locale.
- End-to-end tests with the real models (`uv run pytest -m model`): the whole
  meeting pipeline on a synthesized two-voice call, and streaming vs. full-pass
  accuracy. CI runs them with the benchmark.

## [0.3.0] - 2026-10-13 (not released separately; shipped in 0.4.0)

Foundations: a test suite, CI, pinned model downloads, privacy hardening and a
more responsive daemon. No change to how you use chttr.

### Added
- Test suite (pytest) covering the dictionary, config, silence trimming, the
  meeting pipeline (echo removal, caption votes, speaker naming, rendering,
  renames), diarization and voice memory, note styles and schemas, the cleanup
  prompts, the calendar, the browser bridge, model downloads and the daemon's
  state machine.
- GitHub Actions CI: ruff, pyright and pytest on Python 3.12, 3.13 and 3.14,
  plus Arch Linux.
- `chttr doctor --verify` hashes every model file against its pinned checksum.
- `[polish] send_window_title`: send the focused app to the cleanup model but
  keep the window title (email subjects, document names) on your machine.
- Release workflow: pushing a `v*` tag publishes a GitHub release with the
  changelog entry.

### Changed
- A new dictation can start while the previous one is still being cleaned up;
  results are typed in the order you spoke them.
- The daemon serves each request on its own thread and reads the focused window
  (and, in command mode, the selection) without holding its lock, so a key
  release is never stuck behind a slow request. Releasing the key while
  recording is still starting stops it right away.
- Model downloads are pinned to a Hugging Face commit and verified against a
  SHA-256 before use.
- `meeting.py` is now the `chttr.meeting` package: `store`, `session`,
  `recorder`, `analysis`, `naming`, `pipeline`, `styles`, `render`, `edit`.
- Python 3.12+ (was 3.14+). whisper.cpp moved to an optional extra
  (`chttr[whisper]`); `bin/setup` keeps it installed if your config uses it.
- One version number for the package, the plugin and the Meet extension.

### Fixed
- A snippet with a line break (`chttr snippet sig "Thanks\nRob"`) corrupted
  `dictionary.toml`; strings are now escaped properly.
- With two invitees sharing a first name, a caption reading only that first
  name was matched to one of them by email; it is now left unmatched.
- Meeting filenames no longer contain runs of spaces where punctuation was
  removed.

### Security
- Dictation history, meeting data and the voiceprint store are created
  readable only by you, and files written by earlier versions are tightened on
  daemon start. `chttr doctor` warns when the API key file is readable by
  other users.

## [0.2.1] - 2026-10-11

First public release: push-to-talk, hands-free and command-mode dictation with
local Parakeet speech-to-text and Claude or Ollama cleanup, per-app modes, the
personal dictionary with sound-alike name correction, snippets, history and
stats, and Google Meet transcripts with speaker names and 18 note styles, as an
Omarchy plugin.

[Unreleased]: https://github.com/robjobbed/chttr/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/robjobbed/chttr/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/robjobbed/chttr/compare/v0.2.1...v0.4.0
[0.2.1]: https://github.com/robjobbed/chttr/commit/7953228
