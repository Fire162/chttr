"""Spoken code: identifier casing and file references, done locally before cleanup.

Anywhere:
    "camel case user id"        -> userId
    "snake case user id"        -> user_id
    "kebab case user id"        -> user-id
    "pascal case user id"       -> UserId
    "constant case max retries" -> MAX_RETRIES   (also "screaming snake case")

In code editors and terminals (and other apps whose mode sets `code = true`):
    "at main dot py"            -> @main.py     (Claude Code, Cursor, Aider file mentions)
    "at src slash app dot ts"   -> @src/app.ts
    "main dot py"               -> main.py      (known extensions only)

A cased phrase runs until punctuation, a filler or a short connecting word
("to", "and", "in", ...) or four words, whichever comes first.
"""

from __future__ import annotations

import re

CASES = {
    "camel case": "camel", "camelcase": "camel",
    "snake case": "snake", "snakecase": "snake",
    "kebab case": "kebab", "dash case": "kebab",
    "pascal case": "pascal", "title case": "pascal",
    "constant case": "constant", "screaming snake case": "constant", "all caps": "constant",
}
STOP_WORDS = {"to", "and", "or", "in", "on", "with", "from", "for", "then", "the", "a", "an", "into", "as", "of",
              "is", "equals", "plus", "minus"}
MAX_WORDS = 4
EXTENSIONS = {"py", "ts", "tsx", "js", "jsx", "json", "md", "toml", "yaml", "yml", "rs", "go", "rb", "java", "kt",
              "c", "h", "cpp", "hpp", "cs", "swift", "sh", "lua", "html", "css", "scss", "sql", "txt", "lock", "env",
              "conf", "qml", "vue", "svelte", "php", "ex", "exs", "zig", "nix", "dart"}

_CASE_RE = re.compile(r"\b(" + "|".join(sorted((re.escape(k) for k in CASES), key=len, reverse=True)) + r")\s+",
                      re.IGNORECASE)


def _join(words: list[str], case: str) -> str:
    words = [w.lower() for w in words]
    if case == "camel":
        return words[0] + "".join(w.capitalize() for w in words[1:])
    if case == "pascal":
        return "".join(w.capitalize() for w in words)
    if case == "snake":
        return "_".join(words)
    if case == "kebab":
        return "-".join(words)
    return "_".join(words).upper()


_HYPHENATED = re.compile(r"\b(camel|snake|kebab|pascal|constant|dash|title)[-\s]?case\b", re.IGNORECASE)


def apply_casing(text: str) -> str:
    """Replace "<case name> word word ..." with the cased identifier."""
    text = _HYPHENATED.sub(lambda m: m.group(1) + " case", text)   # the recognizer may write "camel-case"
    out, pos = [], 0
    for m in _CASE_RE.finditer(text):
        if m.start() < pos:
            continue
        case = CASES[m.group(1).lower()]
        words, end = [], m.end()
        for w in re.finditer(r"[A-Za-z0-9]+|[^\sA-Za-z0-9]", text[m.end():]):
            token = w.group(0)
            if not token[0].isalnum() or token.lower() in STOP_WORDS or len(words) == MAX_WORDS:
                break
            words.append(token)
            end = m.end() + w.end()
        if not words:
            continue
        out.append(text[pos:m.start()])
        out.append(_join(words, case))
        pos = end
    out.append(text[pos:])
    return "".join(out)


_PATH_WORD = r"[A-Za-z0-9_-]+"
_FILE_RE = re.compile(
    rf"\b(?P<at>at\s+)?(?P<path>{_PATH_WORD}(?:\s+slash\s+{_PATH_WORD})*)\s+dot\s+(?P<ext>[A-Za-z0-9]+)\b",
    re.IGNORECASE,
)


def apply_files(text: str) -> str:
    """ "at src slash app dot ts" -> "@src/app.ts"; "main dot py" -> "main.py"."""

    def sub(m: re.Match) -> str:
        ext = m.group("ext").lower()
        if ext not in EXTENSIONS:
            return m.group(0)
        path = re.sub(r"\s+slash\s+", "/", m.group("path"), flags=re.IGNORECASE)
        return ("@" if m.group("at") else "") + path + "." + ext

    text = _FILE_RE.sub(sub, text)
    # the recognizer sometimes writes the file name itself: "at main.py"
    return _WRITTEN_RE.sub(lambda m: "@" + m.group(1) if m.group(2).lower() in EXTENSIONS else m.group(0), text)


_WRITTEN_RE = re.compile(r"\bat\s+([\w/-]+\.([A-Za-z0-9]+))\b", re.IGNORECASE)


def transform(text: str, code: bool) -> str:
    text = apply_casing(text)
    return apply_files(text) if code else text


def keep_list(original: str, transformed: str) -> list[str]:
    """Identifiers and paths the transform produced, for the cleanup model to keep verbatim."""
    before = set(original.split())
    return [w.strip(".,;:!?") for w in transformed.split() if w not in before and re.search(r"[_@./-]|[a-z][A-Z]", w)]
