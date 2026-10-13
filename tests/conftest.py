"""Point every chttr path at a throwaway directory *before* chttr is imported:
config.py resolves its paths at import time."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_ROOT = Path(tempfile.mkdtemp(prefix="chttr-tests-"))
for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR"):
    os.environ[var] = str(_ROOT / var.lower())
    Path(os.environ[var]).mkdir(parents=True, exist_ok=True)
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("CHTTR_CONFIG", None)

import pytest  # noqa: E402

from chttr import dictionary  # noqa: E402

FIXTURES = Path(__file__).with_name("fixtures")


@pytest.fixture(autouse=True)
def _no_system_word_list(monkeypatch):
    """Sound-alike correction consults /usr/share/dict; make it the same everywhere."""
    words = frozenset({"sorry", "sorrow", "serbia", "rob", "robe", "so", "the", "and", "send", "it", "to",
                       "call", "with", "about", "meeting", "tomorrow", "budget", "price", "deal"})
    monkeypatch.setattr(dictionary, "english_words", lambda: words)


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES
