"""Personal dictionary: words the recognizer should know, and mis-hearings to fix.

Stored in ~/.config/chttr/dictionary.toml:

    vocabulary = ["Sohrab", "Omarchy"]

    [replacements]
    "sorob" = "Sohrab"      # heard = typed, case-insensitive, whole words
    "so rob" = "Sohrab"

    [snippets]
    "my email" = "rob@example.com"   # say "my email" / "insert my email"

It is used three ways: the vocabulary is given to whisper as an initial prompt
(which biases recognition), replacements are applied to the raw transcript, and
the vocabulary is handed to Claude so the cleanup step keeps the spelling.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .config import CONFIG_DIR

DICTIONARY_FILE = CONFIG_DIR / "dictionary.toml"


@dataclass
class Dictionary:
    vocabulary: list[str] = field(default_factory=list)
    replacements: dict[str, str] = field(default_factory=dict)   # heard (lowercase) -> typed
    snippets: dict[str, str] = field(default_factory=dict)       # phrase (lowercase) -> expansion
    _pattern: re.Pattern | None = field(default=None, repr=False)
    _snippet_pattern: re.Pattern | None = field(default=None, repr=False)

    # ---- use ---------------------------------------------------------------
    def whisper_prompt(self) -> str | None:
        return ", ".join(self.vocabulary) if self.vocabulary else None

    def apply(self, text: str) -> str:
        if not self.replacements:
            return text
        if self._pattern is None:
            keys = sorted(self.replacements, key=len, reverse=True)
            self._pattern = re.compile(r"\b(" + "|".join(re.escape(k) for k in keys) + r")\b", re.IGNORECASE)
        return self._pattern.sub(lambda m: self.replacements[m.group(1).lower()], text)

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
        return cls(vocab, repl, snip)

    def save(self, path: Path = DICTIONARY_FILE) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# chttr personal dictionary. Edit by hand or with: chttr learn / chttr forget",
            "# vocabulary: words spelled exactly as you want them typed.",
            "vocabulary = [" + ", ".join(_q(w) for w in self.vocabulary) + "]",
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
