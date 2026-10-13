# Security

chttr handles your microphone, your clipboard, an API key and recordings of
other people, so security reports are very welcome.

**Report privately** through GitHub's *Report a vulnerability* button on the
Security tab of this repository. Please don't open a public issue for a
vulnerability. Expect a first answer within a few days.

## What chttr does to keep things local

- Speech recognition, voice activity detection and speaker embeddings run on
  your CPU. Audio never leaves the machine.
- Model files are downloaded from a pinned Hugging Face commit and verified
  against a SHA-256 before use (`src/chttr/models.py`; re-check any time with
  `chttr doctor --verify`).
- The daemon listens on a Unix socket in `$XDG_RUNTIME_DIR` with mode 0600.
- The browser extension asks only for `nativeMessaging` and access to
  `meet.google.com`; the native host only accepts that extension's ID.
- History, meeting data and voiceprints live in `~/.local/share/chttr` (0700).
- Text sent to a model (Claude or a local Ollama) is wrapped as data and the
  prompts tell the model never to follow instructions inside it.

See the *Privacy* section of the README for exactly what is sent where.
