# Contributing to chttr

Thanks for helping. chttr is small on purpose: one daemon, plain files, no
services. Changes that keep it that way are the easiest to merge.

## Set up

```bash
git clone https://github.com/robjobbed/chttr && cd chttr
uv sync --group dev          # Python 3.12+; installs pytest, ruff, pyright
uv run pytest                # fast tests: no models, no audio devices, no network
uv run ruff check .
uv run pyright
```

To run your checkout as your real dictation tool: `bin/setup` (editable install,
so edits take effect after `systemctl --user restart chttr`). Logs:
`journalctl --user -u chttr -f`.

## Where things live

| Path | What |
|---|---|
| `src/chttr/daemon.py` | State machine, socket server, the dictation pipeline |
| `src/chttr/recorder.py`, `transcriber.py` | Microphone capture, Parakeet / whisper.cpp |
| `src/chttr/polish.py` | Cleanup and command-mode prompts (Claude, Ollama) |
| `src/chttr/output.py` | Focused window, selection, typing |
| `src/chttr/dictionary.py` | Vocabulary, replacements, sound-alikes, snippets |
| `src/chttr/meeting/` | Recording, pipeline, speaker naming, notes, Markdown |
| `src/chttr/diarize.py` | VAD, speaker embeddings, clustering, voice memory |
| `src/chttr/notes.py`, `note_styles.toml` | Meeting notes with Claude, style definitions |
| `src/chttr/extension/`, `native_host.py` | The Google Meet extension and its bridge |
| `tests/` | pytest suite; `tests/fixtures/` holds sample data |

## Guidelines

- **Tests.** New behaviour comes with a test. Most of chttr is pure functions
  around subprocesses; fake the subprocess, test the logic (see
  `tests/test_daemon.py` for the pattern).
- **Privacy is a feature.** Nothing new may leave the machine by default. If a
  change sends data anywhere, it needs a config switch, a line in the README's
  privacy table and a changelog entry under *Security* or *Changed*.
- **Fail soft.** Dictation must still produce text when cleanup, the overlay,
  the calendar or the network fail. Log it, notify once, carry on.
- **Model files** are pinned in `src/chttr/models.py` (commit + SHA-256). Never
  download from a moving `main`.
- **Changelog.** Add a line under `## [Unreleased]` in `CHANGELOG.md`.
- **Commits.** Small and descriptive (`daemon: stop recording when the key is
  released during start`). Conventional-commit prefixes are welcome, not required.

## Releasing (maintainers)

1. Move the `Unreleased` notes under a new version heading in `CHANGELOG.md`.
2. Bump the version in `pyproject.toml`, `src/chttr/__init__.py`,
   `manifest.json` and `src/chttr/extension/manifest.json`
   (`tests/test_versions.py` checks they agree).
3. Merge to `main`, then `git tag vX.Y.Z && git push --tags`. The release
   workflow runs the tests, builds and publishes the GitHub release.
