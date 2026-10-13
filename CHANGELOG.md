# Changelog

All notable changes to chttr. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project uses [semantic versioning](https://semver.org/).

## [Unreleased]

## [0.3.0] - 2026-10-13

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

[Unreleased]: https://github.com/robjobbed/chttr/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/robjobbed/chttr/releases/tag/v0.3.0
[0.2.1]: https://github.com/robjobbed/chttr/commit/7953228
