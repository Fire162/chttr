# chttr

[![CI](https://github.com/robjobbed/chttr/actions/workflows/ci.yml/badge.svg)](https://github.com/robjobbed/chttr/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)

Voice dictation and meeting transcripts for Linux: Hyprland and
[Omarchy](https://omarchy.org) first, and GNOME, KDE Plasma, Sway, niri and X11
too. Hold **Insert**, talk, let go: the words land in whatever window has focus,
cleaned up like you typed them. Join a Google Meet call and chttr can write the
meeting notes, with names next to every line.

Speech recognition runs on your machine. Claude (optional) tidies dictation
and writes meeting summaries.

> **Early release (0.9).** Built and used daily on an Omarchy 4 laptop. The
> logic is covered by a test suite that runs in CI on every change; the Google
> Meet extension has been used in few real calls so far. Issues and fixes
> welcome: see [CONTRIBUTING.md](CONTRIBUTING.md).

- **Local speech-to-text** with NVIDIA Parakeet TDT 0.6B v3 (int8 ONNX, the
  model Omarchy's voxtype downloads), about 3× faster than whisper base.en on
  CPU and more accurate. whisper.cpp is still available (`[engine] name = "whisper"`).
- **Streaming recognition**: what you said before a pause is transcribed while
  you keep talking, so letting go of the key leaves only the last sentence or
  two to recognize: a 20-second dictation is ready in about half the time of a
  full pass (0.3 s instead of 0.7 s on a desktop CPU), with the same accuracy (`chttr bench`
  measures it on your machine).
- **Wispr-style cleanup** with Claude Haiku (a few hundred milliseconds), or a
  local Ollama model: filler words removed, punctuation and casing fixed,
  self-corrections applied ("Tuesday, no, Wednesday"), spoken formatting
  honoured. Four levels, from `light` (your exact words, punctuated) to `high`
  (edited like a careful editor). Short, clean phrases skip the round trip
  entirely. Falls back to the raw transcript if no model is available.
- **Developer dictation**: "camel case user id" types `userId` (snake, kebab,
  Pascal and constant case too), and in editors and terminals "at main dot
  py" types `@main.py` for Claude Code, Cursor and Aider.
- **Names from the screen**: words near the cursor (read locally through the
  accessibility bus) fix sound-alike names, so "Priyanca" comes out as the
  "Priyanka" in the email you're replying to. Nothing extra leaves your machine
  unless you ask it to (`[polish] screen_context`).
- **Learns from your corrections**: change a name chttr got wrong and it offers
  to remember the spelling.
- **Per-app modes**: terminals get one plain line with no trailing period, code
  editors keep identifiers, chat apps stay casual, email and docs get full
  sentences. A config table matched against the focused window's class.
- **Command mode**: select text, hold Alt+Insert, say "make this shorter" or
  "turn this into bullet points", let go: the selection is replaced.
- **Hands-free**: Super+Insert, or double-tap Insert, starts a recording that
  stops by itself after a pause (1.5 s by default) or on the next tap.
- **Whisper mode**: dictate quietly; chttr lowers its speech threshold and
  boosts the audio before recognition.
- **25 languages**: Parakeet recognizes English and 24 European languages and
  picks the language by itself; the cleanup keeps whatever language you spoke.
- **Live captions**: the floating waveform pill shows the words as you speak.
- **Personal dictionary and snippets**: names that stick forever, and phrases
  like "insert my email" that expand to saved text. `chttr fix` learns from the
  last dictation.
- **Meeting transcripts** for Google Meet, Zoom, Teams, Slack and any other call
  app, with no bot in the call: records your mic and the other side as separate
  channels, splits the other side into voices, and names them from Google
  Meet's captions, your Google Calendar and voices it has heard before. Writes
  Markdown notes in the style you choose (general, BANT, MEDDPICC, 1:1,
  standup, interview, retro and more, or your own), switchable after the call.
  Everything but the notes runs locally.
- **History and stats**: a picker to re-type any past dictation, optional audio
  replay, words per day, speaking speed and time saved. `chttr doctor --mic`
  checks your microphone end to end.
- Auto-unmute of the mic, sounds, notifications, a state file for the bar.

## Install

**Omarchy**, as a plugin:

```bash
omarchy plugin add https://github.com/robjobbed/chttr.git --enable
```

Then click the microphone in the bar; that runs the setup in a terminal. Update
later with `omarchy plugin update robjobbed.chttr`.

**Any other Linux desktop:**

```bash
curl -fsSL https://raw.githubusercontent.com/robjobbed/chttr/main/install.sh | bash
```

The installer never uses sudo: if a system tool is missing it prints the one
command for your distribution (pacman, apt, dnf or zypper). Run it again to
update. **Arch** users can also build `packaging/aur/chttr-git/PKGBUILD`.

Either way, setup:

1. installs the `chttr` command with [uv](https://docs.astral.sh/uv/) (Python
   3.12+) from a git checkout, so updates are a `git pull` away,
2. downloads the speech model (Parakeet, ~670 MB, skipped if Omarchy's voxtype
   already has it) and the meeting models (~29 MB), checksum-verified,
3. sets up the hotkeys for your desktop (below),
4. registers the Google Meet extension with Chromium, Chrome, Brave, Vivaldi or Edge,
5. starts the daemon as a systemd user service (`chttr.service`).

Run it again any time (`bin/setup --no-keybindings --no-meet --no-service` skips
parts), and check everything with:

```bash
chttr doctor                     # desktop, typing, hotkeys, engine, cleanup, daemon, calendar, Meet
```

### Desktop support

| Desktop | Hotkeys | Typing | Focused app (per-app modes) | Overlay |
|---|---|---|---|---|
| Hyprland / Omarchy | compositor bindings (added by setup) | wtype | hyprctl | yes |
| Sway | compositor bindings (added by setup) | wtype | swaymsg | yes |
| niri, river, other wlroots | keyboard devices¹ | wtype | niri msg (niri) | yes |
| KDE Plasma 6 (Wayland) | global-shortcuts portal | ydotool² | kdotool (optional) | yes |
| GNOME 48+ (Wayland) | global-shortcuts portal | ydotool² | [Window Calls](https://github.com/ickyicky/window-calls) extension (optional) | notifications |
| X11 (any) | keyboard devices¹ | xdotool | xdotool | notifications |

¹ chttr reads `/dev/input` (it watches the keys, never blocks them); add yourself
to the `input` group once: `sudo usermod -aG input $USER`, then log out and in.
² `ydotool` needs its daemon: `systemctl --user enable --now ydotool`.

On GNOME and KDE the desktop asks you once to confirm chttr's shortcuts; you can
change them later in its keyboard settings. Elsewhere the keys come from
`[hotkeys]` in the config. Without a typing tool, text is pasted, and without
that it lands on the clipboard with a notification.

| Key | What it does |
|---|---|
| Hold **Insert** | Dictate; release to type |
| Hold **Alt+Insert** | Command mode: say what to do with the selected text |
| **Super+Insert** | Hands-free: stops by itself after a pause |
| **Super+Shift+Insert** | Dictation history |
| **Super+Alt+Insert** | Start / stop recording a meeting |

The bar widget: left click opens chttr's menu (record a meeting, last notes,
history, stats, settings), right click starts or stops a meeting recording.

### Claude cleanup

Claude gives the best cleanup and is what writes meeting notes. Put an
Anthropic API key in a file (read on each use):

```bash
install -m 600 /dev/null ~/.config/chttr/anthropic-api-key
echo 'sk-ant-...' > ~/.config/chttr/anthropic-api-key
```

Fully offline: install [Ollama](https://ollama.com), `ollama pull llama3.2`, and
leave `polish.backend = "auto"`; chttr uses Claude when a key is present, else
Ollama when it is running, else no cleanup. Command mode needs one of the two;
meeting summaries need Claude.

System tools used: `pw-record`/`pw-play` (PipeWire) and `notify-send` everywhere;
`wl-copy`/`wl-paste` on Wayland or `xclip`/`xsel` on X11; a typing tool from the
table above; a menu (`omarchy-menu-select`, or `fuzzel`, `wofi`, `rofi`, `bemenu`).
All of them ship with Omarchy.

## Personal dictionary and snippets

```bash
chttr learn Sohrab --heard sorob --heard "so rob"   # correct spelling + what the recognizer hears
chttr fix                                            # show the last dictation, ask what was wrong
chttr fix "so rob" Sohrab                            # same, in one line
chttr snippet "my email" rob@example.com             # say "insert my email"
chttr dictionary                                     # show it all
chttr forget sorob                                   # remove a word, mis-hearing or snippet
```

Names come out of the recognizer a different way each time ("Sorob", "Sorov",
"Suhrab" for Sohrab), so chttr also corrects words that *sound like* a
vocabulary word: same consonant skeleton, capitalized by the recognizer (it
took it for a name), not an English word, and spelled reasonably close. Real
words like "sorry" or "Serbia" are left alone. In meetings, everyone on the
invite and every name Meet's captions showed is corrected the same way. Turn it
off with `sound_alikes = false` at the top of `dictionary.toml`.

`~/.config/chttr/dictionary.toml` is re-read on every dictation. The `--heard`
variants are replaced in the raw transcript (whole words, case-insensitive);
the vocabulary is given to whisper as a prompt and to the cleanup model as
spellings to keep. Snippets are expanded after cleanup, and the model is told
to leave their phrases alone. Snippet phrases match anywhere, so pick ones you
wouldn't otherwise say.

## Meetings

```bash
chttr meeting calendar 'https://calendar.google.com/calendar/ical/…/basic.ics'
```

Setup registers the Meet extension; restart Chromium once to load it. Then
join a call and a notification asks whether to record it (`[meeting] auto =
"ask"`; `"always"` or `"never"` also work):

- **Google Meet**, through the extension, which also tells chttr who is speaking.
- **Zoom, Microsoft Teams, Slack, Discord, Webex, Signal** and other call apps,
  and calls in any browser tab: chttr notices which app holds your microphone
  (PipeWire) and offers to record after a few seconds; it stops when the app
  lets go of the mic. No bot joins the call (`[meeting] detect_calls`).
- Anything else: press **Super+Alt+Insert** (`chttr meeting toggle`).
When the call ends, the notes appear in `~/Documents/Meetings/` as
`2026-10-11 1400 Weekly sync.md`: frontmatter with the attendees, summary,
decisions, action items, open questions, the transcript with timestamps, and
how each speaker was identified.

How it works:

- **Two channels.** `pw-record` captures your microphone and the speaker
  output (the default sink's monitor, so headphones work and switching output
  mid-call is followed) into separate files. Your lines are what your mic
  heard, minus moments Meet had you muted, chttr dictations made during the
  call, and the other side leaking out of your speakers into the mic (detected
  by matching the words on both channels).
- **Voices.** Silero VAD cuts the speaker channel into utterances, WeSpeaker
  ResNet34 embeds each one, and average-linkage clustering groups them into
  voices. The models (~29 MB) download to `~/.local/share/chttr/models` on
  first use. Parakeet transcribes every utterance.
- **Names**, in order of trust:
  1. The **Meet extension**, while chttr is recording the call, turns on Meet's
     captions (hidden again if you hadn't turned them on yourself, and turned
     off when the recording ends) and reports whose name the captions show
     while each voice is talking. A red "chttr is recording" badge stays on the
     page meanwhile. The extension also reports the Meet code, your Meet mute
     button, and when you leave.
  2. **Remembered voices** (`~/.local/share/chttr/voices.json`): every voice
     named by captions, the calendar or you is saved under their email and
     recognised in later meetings, even without captions.
  3. The **calendar**: in a 1:1, the only other invitee.
  4. **Claude** reads the conversation ("thanks, Sarah") against the invite list.
  5. Anything left is `Speaker N`. `chttr meeting rename "Speaker 2" "Sarah Chen" --email sarah@…`
     fixes the notes and teaches chttr the voice.
- **People in the room.** Your microphone's side is split into voices too: the
  one that matches your remembered voice (or talks most) is you, and anyone else
  with real speaking time becomes `In the room 1`, ... (rename them the same
  way). Zoom and Teams desktop calls get names from remembered voices, the
  calendar and Claude, since there are no captions to read
  (`[meeting] room_speakers`).
- **Calendar.** Google Calendar > Settings > your calendar > Integrate calendar >
  *Secret address in iCal format*. No OAuth app to set up; the URL is stored in
  `~/.config/chttr/calendar-urls` (mode 600). The event is found by Meet code,
  else by time; attendees' names and emails go into the notes and help match
  caption names to people.
- **Processing** runs after the call in a separate, niced process
  (`chttr meeting process`): recognition runs at about 7% of real time, so an
  hour of meeting takes 5 to 10 minutes in all. A notification with an Open button appears when the notes are
  written. Audio is deleted afterwards unless `keep_audio = true` (then it's
  kept as stereo Opus next to the notes: you left, them right).

### Ask your meetings

Every meeting (and dictation) goes into a local full-text index
(`~/.local/share/chttr/library.sqlite`), so you can ask across all of them:

```bash
chttr ask "what did Dana commit to last month?"
chttr ask -v "when did we last talk about the Acme pricing?"   # -v shows the searches
```

Claude searches the index with tools (several phrasings, then reads the
transcript around the hits) and answers with citations like
`[Pricing review, 2026-10-01, 00:12:40]`. Only the snippets it asks for are
sent, only when you ask.

**In Claude Code or Claude Desktop**, the same tools are an MCP server:

```bash
claude mcp add chttr -- chttr mcp
```

Then ask Claude Code things like "check my meetings for what we decided about
the API rate limits" while you work. Read-only: `search_meetings`,
`read_transcript`, `get_notes`, `list_meetings`, `list_action_items`.

**In the browser**, `chttr ui` opens the library: search with highlighted
matches, notes and transcripts with clickable timestamps (synced to the audio
when `keep_audio` kept it), click a speaker to rename them, switch the notes
style, and every action item across meetings in one table. It listens on
127.0.0.1 only, with a one-time token in the link.

### Send notes where your team works

```bash
chttr connect slack        # an incoming-webhook URL for the channel
chttr connect notion       # an integration secret + the page that holds your notes
chttr connect hubspot      # a private-app token (contacts read/write)
chttr connect webhook      # any URL; signed with X-Chttr-Signature if you give a secret
chttr connect              # what's connected
```

After each meeting the "notes saved" notification gets a **Send to Slack /
Notion / HubSpot** button per connected destination; nothing leaves without
that click. Slack gets a formatted summary with the action items, Notion a page
with headings and to-dos, HubSpot a note (and a task per action item) on the
attendees' contacts, matched by email. `chttr send [MEETING] [--to slack]`
does it by hand. To send without asking, set `[integrations] send = "always"`,
optionally only for meetings whose calendar title matches `auto_titles`
(say, `'\b(customer|demo)\b'`). Tokens are stored in your desktop keyring
(libsecret), never in the config file.

For anything else, `[integrations] on_meeting_done` runs your own script after
every meeting with the Markdown path as `$1` and the whole meeting as JSON on
stdin.

### Note styles

Notes come in 18 styles, and a meeting can switch style at any time, even
weeks after the call, without re-transcribing:

| Style | For |
|---|---|
| `general` | Summary, decisions, open questions (fits most meetings) |
| `brief` | Executive TL;DR |
| `bant` | Sales qualification: Budget, Authority, Need, Timeline, objections, competitors |
| `meddpicc` | Enterprise sales: Metrics, Economic buyer, Decision criteria/process, Paper process, Pain, Champion, Competition |
| `spin` | Sales discovery: Situation, Problem, Implication, Need-payoff, best quotes |
| `customer-success` | Account health, goals, wins, issues, churn risks, expansion |
| `one-on-one` | Updates, wins, blockers, feedback, growth |
| `standup` | Progress, plans and blockers per person |
| `interview` | Hiring scorecard: evidence by topic, strengths, gaps (no hire/no-hire verdicts) |
| `user-research` | Participant context, goals, pain points, verbatim quotes, insights |
| `support` | Issue, impact, troubleshooting, resolution, root cause |
| `kickoff` | Goals, scope, out of scope, milestones, owners, risks |
| `retro` | Went well, didn't, ideas, changes |
| `brainstorm` | Ideas by theme, most promising, parking lot |
| `decision` | Context, options, decision, rationale, dissent |
| `minutes` | Formal minutes: agenda items, motions and votes |
| `investor` | Metrics, highlights, lowlights, investor feedback, asks |
| `lecture` | Concepts, examples, takeaways, follow-up questions |

Which style a meeting gets, first match wins:

1. what you picked for it: `chttr meeting style bant` (during the call, or
   from the bar menu's *Notes style for this meeting*),
2. a calendar-title rule in `[meeting] style_rules` (1:1s, standups, retros
   and interviews are set up already; add `{ match = "demo|discovery", style = "bant" }`),
3. `[meeting] style`: `"auto"` (the default) lets Claude pick from the
   conversation, or name one style for everything.

After the call:

```bash
chttr meeting style meddpicc                       # rewrite the last meeting's notes as MEDDPICC
chttr meeting style bant --meeting 2026-10-11-1400 # a specific meeting (chttr meeting list)
chttr meeting style --prompt "every number and who said it, plus security concerns"
chttr meeting style bant --prompt "they're also evaluating Gong"   # a style plus extra instructions
chttr meeting styles                               # list them
```

or the bar menu's *Change the last meeting's notes style…*, which includes
*Custom instructions…*. Every version is kept: switching back to a style you
already had is instant and free (`--fresh` writes it again). The transcript is
sent with prompt caching, so trying a few styles in a row costs little.

Your own styles go in `~/.config/chttr/note-styles.toml`, in the same format
as the built-in [`note_styles.toml`](src/chttr/note_styles.toml) (reuse an id to
replace a built-in one):

```toml
[[style]]
id = "podcast"
name = "Podcast episode"
description = "Show notes for a podcast recording."
actions = ""                      # no action items
sections = [
  { title = "Episode summary", guide = "Two paragraphs.", kind = "paragraph" },
  { title = "Timestamps", guide = "Topic changes as mm:ss - topic." },
  { title = "Quotable", guide = "Short verbatim quotes.", kind = "quotes" },
]
```

Note styles need Claude (an API key); without one you get the transcript with
speaker names.

The extension lives in `src/chttr/extension/` and is loaded through Omarchy's
`--load-extension` line in `~/.config/chromium-flags.conf`. Meet's page
structure is undocumented; if names stop appearing after a Meet update, the
caption selectors at the top of `content.js` are the place to look. It reads
English UI labels first, with icon names and Meet's `jsname` attributes as
fallbacks.

Recording laws differ: in many places everyone on a call has to agree to being
recorded. Tell people.

## Commands

| Command | What it does |
|---|---|
| `chttr daemon` | Run the daemon (loads the model once; ~5 s for Parakeet) |
| `chttr start` / `chttr release` | Push-to-talk press / release |
| `chttr command` | Command-mode press: grabs the selection, then records an instruction |
| `chttr toggle` | Hands-free: start (auto-stops on silence), or stop now |
| `chttr stop` / `chttr cancel` | Stop any recording and transcribe / discard it |
| `chttr history [--print [N]]` | Pick a past dictation and type it again |
| `chttr stats [--days N]` | Words, speaking speed, time saved, per-day chart |
| `chttr fix [WRONG RIGHT]` | Learn a correction from the last dictation |
| `chttr learn` / `forget` / `snippet` / `dictionary` | Edit the dictionary |
| `chttr status [--follow] [--json]` | `idle` / `recording` / `transcribing`; `--json` matches the Omarchy bar indicator format |
| `chttr transcribe file.wav [--raw] [--engine E] [-v]` | One-off transcription without the daemon |
| `chttr bench FILE.wav… [--cleanup] [--format FORMAT]` | Time recognition (full pass vs. after release), cleanup, real-time factor; word error rate when FILE.txt exists; FORMAT is text, json or markdown |
| `chttr polish [--app NAME]` | Clean up text from stdin |
| `chttr doctor [--verify]` | Check engine, cleanup backend, daemon and key permissions; `--verify` hashes the models |
| `chttr reload` | Re-read config.toml (switching engines reloads the model) |
| `chttr config` | Create and print the config file |
| `chttr setup` | Models, service, keybindings, Meet extension (what `bin/setup` runs) |
| `chttr menu` | The bar widget's menu |
| `chttr meeting start` / `stop` / `toggle` / `cancel` / `status` | Record a meeting by hand |
| `chttr meeting list` | Recorded meetings and their status |
| `chttr meeting process [ID] [--no-summary]` | (Re)write the notes for a meeting |
| `chttr meeting rename OLD NEW [--email E] [--meeting ID]` | Fix a speaker's name and remember their voice |
| `chttr meeting calendar [URL]` | Add a secret iCal address; show the next 48 hours |
| `chttr meeting style [STYLE] [--prompt TEXT] [--meeting ID]` | Set or switch a meeting's note style |
| `chttr meeting styles` | List note styles |
| `chttr meeting voices [--forget KEY]` | Remembered voices |
| `chttr meeting install` | Register the Meet extension and native host |
| `chttr connect [slack\|notion\|hubspot\|webhook] [--remove]` | Connect a destination for meeting notes (no argument: list them) |
| `chttr send [MEETING] [--to DEST] [-y]` | Send a meeting's notes to your connected destinations |
| `chttr ask [-v] QUESTION` | Answer a question from your meetings, with citations |
| `chttr mcp` | Your meetings as an MCP server for Claude Code / Claude Desktop |
| `chttr ui [--port N] [--no-browser]` | The meeting library in your browser |

## Config

See `chttr config` for every key with comments: engine, whisper model, audio
device, hands-free timings and speech threshold, output mode (`type`, `paste`,
`clipboard`), cleanup backend and models, overlay and live captions, history,
and the `[[modes]]` table.

## How it works

`daemon.py` owns a small state machine (`idle` → `recording` → `transcribing`),
listens on `$XDG_RUNTIME_DIR/chttr/chttr.sock`, and tracks what a recording is
for (dictate, hands-free, command). `recorder.py` runs `pw-record` at 16 kHz
mono and meters every 20 ms chunk; the daemon uses the levels for hands-free
auto-stop and pipes them to `overlay.py`, a GTK4 layer-shell window run under
the system Python (PyGObject and gtk4-layer-shell, preloaded via `LD_PRELOAD`).
While recording, `streaming.py` watches the levels for pauses and transcribes
each finished stretch in the background (cutting in the middle of the pause),
which also feeds the overlay's live captions; after release only the audio
since the last pause is recognized. A new dictation can start while the
previous one is still being cleaned up, and results are typed in order.
`transcriber.py` wraps Parakeet (onnx-asr) and whisper.cpp,
trims silence and filters whisper's silence hallucinations. `polish.py` holds
the cleanup and command prompts and talks to Claude or Ollama. `output.py`
reads the selection (Ctrl+C into an emptied clipboard, restored afterwards;
the primary selection in terminals) and delivers text. `history.py` is the
picker and stats. The state file at `$XDG_RUNTIME_DIR/chttr/state` is what
`status --follow` watches.

The `meeting/` package is everything after the call: `recorder.py` captures
the two channels, `pipeline.py` runs VAD, transcription, echo removal and
clustering as its own niced process, `naming.py` decides who is who,
`styles.py` and `render.py` write the notes and the Markdown, and `edit.py`
handles renames. `diarize.py` holds the VAD, embeddings, clustering and voice
memory, `gcal.py` the calendar, `notes.py` the Claude notes prompt, `models.py`
the pinned model downloads, and `native_host.py` plus `extension/` the Meet
side. `BarWidget.qml` and `manifest.json` make the repo an Omarchy plugin.

## Development

```bash
uv sync --group dev && uv run pytest     # 120+ tests, no models or audio devices needed
uv run ruff check . && uv run pyright
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the layout and guidelines, and
[CHANGELOG.md](CHANGELOG.md) for what changed in each release.

## Privacy

Audio never leaves your computer. Everything below is all that does, and only
when you turn it on:

| What | Where it goes | When | Turn it off |
|---|---|---|---|
| Dictated text (the transcript) | Anthropic, or your local Ollama | You added an API key / run Ollama | `[polish] backend = "none"` |
| Focused app's window class (e.g. `Slack`) | Same, with the dictation | Cleanup is on | `[polish] use_app_context = false` |
| Focused window's title | Same, with the dictation | Cleanup is on | `[polish] send_window_title = false` |
| Selected text in command mode | Same | You use command mode | Don't use command mode |
| Names near the cursor | Same, as spellings to keep | `[polish] screen_context = "names"` or `"full"` (default `"local"`: used on this machine only) | `"local"` or `"off"` |
| Up to 600 characters before the cursor | Same, as context | `[polish] screen_context = "full"` | Any other value |
| Meeting transcript, invitees' names and emails, calendar title and description | Anthropic | You added an API key | `[meeting] summary = false` |
| Snippets of your meetings that Claude searches for | Anthropic | You run `chttr ask` (or an MCP client asks) | Don't use them |
| A meeting's notes, action items and attendees' emails | Slack / Notion / HubSpot / your webhook | You click "Send to …" (or set `send = "always"`) | Don't connect them |
| Calendar feed request | Google | You added a secret iCal address | Remove it from `calendar-urls` |
| Model downloads (once, checksum-verified) | Hugging Face | `chttr setup` | Copy the models in by hand |

Reading the text field (for names on screen and to notice your corrections)
happens on this machine through AT-SPI and is never stored. Meeting audio is
deleted once the notes are written unless you set `keep_audio = true`, and
dictation audio is only kept with `[history] keep_audio_days`. Dictation history, meeting data and remembered voices stay
in `~/.local/share/chttr`, readable only by you. The Meet extension talks only
to the local daemon. Your calendar's secret iCal URL is stored with mode 600.
To report a security problem, see [SECURITY.md](SECURITY.md).

## License

MIT
