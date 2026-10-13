# Roadmap

Where chttr is going, roughly in order. Issues welcome on any of it.

## Shipped

| Release | What |
|---|---|
| 0.4 | Tests and CI, pinned model downloads, privacy hardening, streaming recognition, Haiku cleanup, `chttr bench` |
| 0.5 | GNOME, KDE, Sway, niri and X11: typing, focused window, global-shortcuts portal and evdev hotkeys, one-line installer |
| 0.6 | Cleanup levels, developer dictation, names from the screen, learning from your corrections, double-tap, whisper mode |
| 0.7 | Bot-free call detection for Zoom, Teams, Slack, Discord, Webex and browser calls; people in the room |
| 0.8 | The meeting library: `chttr ask`, the MCP server, `chttr ui` |
| 0.9 | Slack, Notion, HubSpot, webhooks and a post-meeting hook |
| 1.0 rc | README, configuration reference, launch |

## Next

- **1.0**: confirmed on GNOME 48+ and KDE Plasma 6 hardware, then tagged.
- **A demo video** at the top of the README.
- **Speaker names from Teams and Zoom web captions** ([#9](https://github.com/robjobbed/chttr/issues/9)).
- **Firefox** for the Meet extension ([#10](https://github.com/robjobbed/chttr/issues/10)).
- **`chttr export`** to JSON and CSV ([#7](https://github.com/robjobbed/chttr/issues/7)).
- **Notifications in more languages** ([#8](https://github.com/robjobbed/chttr/issues/8)).

## Considered, not planned

- **Video recording and clips.** Fathom's video features need a bot or screen
  capture; chttr stays audio-only and bot-free.
- **Batched recognition for meetings.** Measured at 1.3× faster but it changed
  the transcript for some utterances; not worth the accuracy risk.
- **macOS and Windows.** chttr is a Linux tool; Handy and VoiceInk cover those.
