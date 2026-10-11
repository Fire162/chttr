"""Personal dictionary: words the recognizer should know, and mis-hearings to fix.

Stored in ~/.config/chttr/dictionary.toml:

    vocabulary = ["Sohrab", "Omarchy"]

    [replacements]
    "sorob" = "Sohrab"      # heard = typed, case-insensitive, whole words
    "so rob" = "Sohrab"

    [snippets]
    "my email" = "rob@example.com"   # say "my email" / "insert my email"

It is used four ways: the vocabulary is given to whisper as an initial prompt
(which biases recognition), replacements are applied to the raw transcript,
words that sound like a vocabulary word are corrected (below), and the
vocabulary is handed to Claude so the cleanup step keeps the spelling.

Sound-alikes: a name comes out of the recognizer a different way each time
("Sorob", "Sorov", "Suhrab" for Sohrab), more ways than anyone will list as
replacements. A word is changed to a vocabulary word when it reduces to the
same consonant skeleton (sound_key), the recognizer capitalized it (it took it
for a name), it isn't an English word, and it's spelled reasonably close.
Turn it off with `sound_alikes = false` at the top of dictionary.toml.
"""

from __future__ import annotations

import difflib
import logging
import re
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .config import CONFIG_DIR

DICTIONARY_FILE = CONFIG_DIR / "dictionary.toml"
WORD_LISTS = [Path("/usr/share/dict/words"), Path("/usr/share/dict/american-english"),
              Path("/usr/share/dict/british-english"), Path("/usr/share/dict/cracklib-small")]
log = logging.getLogger("chttr.dictionary")

# Letters that sound alike share a class; vowels, h, w and y are dropped after the first letter.
_SOUND = str.maketrans({**dict.fromkeys("bfpv", "B"), **dict.fromkeys("cgkq", "K"), **dict.fromkeys("sxz", "S"),
                        **dict.fromkeys("dt", "T"), **dict.fromkeys("mn", "N"), "l": "L", "r": "R", "j": "J"})


def sound_key(word: str) -> str:
    """Consonant skeleton: Sohrab, Sorob, Sorov, Suhrab, Zorab -> "SRB"; sorrow -> "SR"."""
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return ""
    first = "A" if w[0] in "aeiouy" else w[0].translate(_SOUND).upper()
    rest = re.sub(r"[aeiouhwy]", "", w[1:]).translate(_SOUND)
    key = first
    for ch in rest:
        if ch != key[-1]:
            key += ch
    return key


@lru_cache(maxsize=1)
def english_words() -> frozenset[str]:
    for path in WORD_LISTS:
        try:
            return frozenset(w.strip().lower() for w in path.read_text(errors="ignore").splitlines() if w.strip())
        except OSError:
            continue
    return frozenset()


@dataclass
class Dictionary:
    vocabulary: list[str] = field(default_factory=list)
    replacements: dict[str, str] = field(default_factory=dict)   # heard (lowercase) -> typed
    snippets: dict[str, str] = field(default_factory=dict)       # phrase (lowercase) -> expansion
    sound_alikes: bool = True
    _pattern: re.Pattern | None = field(default=None, repr=False)
    _snippet_pattern: re.Pattern | None = field(default=None, repr=False)

    # ---- use ---------------------------------------------------------------
    def whisper_prompt(self) -> str | None:
        return ", ".join(self.vocabulary) if self.vocabulary else None

    def apply(self, text: str, extra_names: list[str] | None = None) -> str:
        """Replacements, then sound-alikes of the vocabulary (plus `extra_names`, e.g. a
        meeting's attendees)."""
        if self.replacements:
            if self._pattern is None:
                keys = sorted(self.replacements, key=len, reverse=True)
                self._pattern = re.compile(r"\b(" + "|".join(re.escape(k) for k in keys) + r")\b", re.IGNORECASE)
            text = self._pattern.sub(lambda m: self.replacements[m.group(1).lower()], text)
        if self.sound_alikes:
            text = self.fix_sound_alikes(text, extra_names or [])
        return text

    def fix_sound_alikes(self, text: str, extra_names: list[str]) -> str:
        names = [w for w in [*self.vocabulary, *extra_names] if re.fullmatch(r"[^\W\d_]{3,}", w)]
        if not names:
            return text
        by_key: dict[str, set[str]] = {}
        for name in names:
            by_key.setdefault(sound_key(name), set()).add(name)
        known = {n.lower() for n in names}
        common = english_words()

        def sub(m: re.Match) -> str:
            word = m.group(0)
            low = word.lower()
            if low in known or not word[0].isupper() or low in common:
                return word
            candidates = by_key.get(sound_key(word), set())
            if len(candidates) != 1:
                return word             # no match, or two names sound alike: don't guess
            name = next(iter(candidates))
            close = difflib.SequenceMatcher(None, low.replace("h", ""), name.lower().replace("h", "")).ratio()
            if close < 0.5 or abs(len(word) - len(name)) > 3:
                return word
            log.info("sounds like %s: %r -> %r", name, word, name)
            return name

        return re.sub(r"\b[^\W\d_]{3,}\b", sub, text)

    def expand_snippets(self, text: str) -> str:
        """Replace snippet phrases ("my email", "insert my email") with their expansion."""
        if not self.snippets:
            return text
        if self._snippet_pattern is None:
            keys = sorted(self.snippets, key=len, reverse=True)
            alt = "|".join(re.escape(k).replace(r"\ ", r"\s+") for k in keys)
            self._snippet_pattern = re.compile(
                r"(?:\b(?:insert|paste|add|type)\s+)?\b(" + alt + r")\b", re.IGNORECASE)

        def sub(m: re.Match) -> str:
            key = " ".join(m.group(1).lower().split())
            return self.snippets.get(key, m.group(0))

        return self._snippet_pattern.sub(sub, text)

    # ---- edit --------------------------------------------------------------
    def learn(self, word: str, heard: list[str] | None = None) -> None:
        word = word.strip()
        if word and word not in self.vocabulary:
            self.vocabulary.append(word)
        for h in heard or []:
            h = " ".join(h.lower().split())
            if h and h != word.lower():
                self.replacements[h] = word
        self._pattern = None

    def add_snippet(self, phrase: str, expansion: str) -> None:
        self.snippets[" ".join(phrase.lower().split())] = expansion
        self._snippet_pattern = None

    def forget(self, word: str) -> bool:
        found = False
        key = " ".join(word.lower().split())
        if key in self.snippets:
            del self.snippets[key]
            self._snippet_pattern = None
            found = True
        if word in self.vocabulary:
            self.vocabulary.remove(word)
            found = True
        for heard in [k for k, v in self.replacements.items() if v == word or k == word.lower()]:
            del self.replacements[heard]
            found = True
        self._pattern = None
        return found

    # ---- io ----------------------------------------------------------------
    @classmethod
    def load(cls, path: Path = DICTIONARY_FILE) -> "Dictionary":
        if not path.exists():
            return cls()
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        vocab = [str(w) for w in data.get("vocabulary", [])]
        repl = {str(k).lower(): str(v) for k, v in (data.get("replacements") or {}).items()}
        snip = {" ".join(str(k).lower().split()): str(v) for k, v in (data.get("snippets") or {}).items()}
        return cls(vocab, repl, snip, bool(data.get("sound_alikes", True)))

    def save(self, path: Path = DICTIONARY_FILE) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# chttr personal dictionary. Edit by hand or with: chttr learn / chttr forget",
            "# vocabulary: words spelled exactly as you want them typed.",
            "vocabulary = [" + ", ".join(_q(w) for w in self.vocabulary) + "]",
            "# correct words that sound like a vocabulary word (Sorov -> Sohrab)",
            f"sound_alikes = {'true' if self.sound_alikes else 'false'}",
            "",
            "# replacements: what the recognizer hears = what should be typed",
            "[replacements]",
        ]
        for heard, typed in sorted(self.replacements.items()):
            lines.append(f"{_q(heard)} = {_q(typed)}")
        lines += ["", "# snippets: say the phrase (optionally after \"insert\") to type the expansion", "[snippets]"]
        for phrase, expansion in sorted(self.snippets.items()):
            lines.append(f"{_q(phrase)} = {_q(expansion)}")
        path.write_text("\n".join(lines) + "\n")


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
