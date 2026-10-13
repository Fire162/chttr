from __future__ import annotations

import json
import subprocess

from chttr import context as CX


def test_names_in_picks_mid_sentence_capitals_and_mixed_case():
    text = "Thanks for the notes. Can you send them to Priyanka and McAllister before the iPhone launch? Sure."
    assert CX.names_in(text) == ["Priyanka", "McAllister", "iPhone"]


def test_names_in_skips_sentence_starts_acronyms_and_common_words():
    text = "Meeting moved. Budget is fine.\nTomorrow works for NASA and the Deal team."
    assert CX.names_in(text) == []


def test_corrections_find_a_renamed_word():
    typed = "Please send the contract to Sorov by Friday."
    field = "Hi team, Please send the contract to Sohrab by Friday. Thanks"
    assert CX.corrections(typed, field) == [CX.Correction("Sorov", "Sohrab")]


def test_corrections_ignore_rewrites_and_ordinary_words():
    typed = "Please send the contract to Sorov by Friday."
    assert CX.corrections(typed, "Please send the deal to Sorov by Friday.") == []        # "deal" is ordinary
    assert CX.corrections(typed, "Completely different text now.") == []               # dictation gone
    assert CX.corrections(typed, "Please send the contract to Alexandra by Friday.") == []   # doesn't sound alike


def test_read_field_parses_helper_output(monkeypatch):
    class Out:
        stdout = json.dumps({"app": "gedit", "before": "Dear Dana,", "after": "", "caret": 10, "count": 10})

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Out())
    field = CX.read_field("/usr/bin/python3")
    assert field and field.app == "gedit" and field.text == "Dear Dana,"


def test_read_field_handles_nothing_focused_and_failures(monkeypatch):
    class Empty:
        stdout = "{}"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Empty())
    assert CX.read_field("python3") is None

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("python3", 1)

    monkeypatch.setattr(subprocess, "run", boom)
    assert CX.read_field("python3") is None
