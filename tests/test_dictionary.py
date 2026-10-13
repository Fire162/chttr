from __future__ import annotations

import pytest

from chttr.dictionary import Dictionary, sound_key


@pytest.mark.parametrize("word", ["Sohrab", "Sorob", "Sorov", "Suhrab", "Zorab"])
def test_sound_key_groups_spellings_of_a_name(word):
    assert sound_key(word) == "SRB"


def test_sound_key_keeps_different_words_apart():
    assert sound_key("sorrow") == "SR"
    assert sound_key("") == ""
    assert sound_key("Priya") != sound_key("Sohrab")


def test_replacements_are_whole_word_and_case_insensitive():
    d = Dictionary(replacements={"so rob": "Sohrab", "omar chee": "Omarchy"})
    assert d.apply("ask So Rob about omar chee") == "ask Sohrab about Omarchy"
    assert d.apply("also robust") == "also robust"


def test_longest_replacement_wins():
    d = Dictionary(replacements={"new york": "NYC", "new york times": "NYT"}, sound_alikes=False)
    assert d.apply("read the new york times") == "read the NYT"


def test_sound_alikes_fix_capitalized_non_words():
    d = Dictionary(vocabulary=["Sohrab"])
    assert d.apply("send it to Sorov tomorrow") == "send it to Sohrab tomorrow"


def test_sound_alikes_leave_real_words_and_lowercase_alone():
    d = Dictionary(vocabulary=["Sohrab"])
    assert d.apply("Sorry about that") == "Sorry about that"     # an English word
    assert d.apply("sorov said") == "sorov said"                 # not capitalized: not taken for a name


def test_sound_alikes_refuse_to_guess_between_two_names():
    d = Dictionary(vocabulary=["Sohrab", "Zorab"])
    assert d.apply("call Sorov") == "call Sorov"


def test_sound_alikes_can_be_turned_off():
    d = Dictionary(vocabulary=["Sohrab"], sound_alikes=False)
    assert d.apply("call Sorov") == "call Sorov"


def test_extra_names_are_corrected_like_vocabulary():
    d = Dictionary()
    assert d.apply("thanks Priyanka", extra_names=["Priyanca"]) == "thanks Priyanca"


def test_snippets_expand_with_or_without_a_verb():
    d = Dictionary(snippets={"my email": "rob@example.com"})
    assert d.expand_snippets("reach me at my email") == "reach me at rob@example.com"
    assert d.expand_snippets("insert my  email please") == "rob@example.com please"
    assert d.expand_snippets("nothing here") == "nothing here"


def test_learn_and_forget_round_trip(tmp_path):
    path = tmp_path / "dictionary.toml"
    d = Dictionary()
    d.learn("Sohrab", heard=["sorob", "So Rob"])
    d.add_snippet("My Address", "1 Main St\nSpringfield")
    d.save(path)

    loaded = Dictionary.load(path)
    assert loaded.vocabulary == ["Sohrab"]
    assert loaded.replacements == {"sorob": "Sohrab", "so rob": "Sohrab"}
    assert loaded.snippets == {"my address": "1 Main St\nSpringfield"}

    assert loaded.forget("Sohrab")
    assert loaded.vocabulary == [] and loaded.replacements == {}
    assert not loaded.forget("nobody")


def test_save_escapes_quotes(tmp_path):
    path = tmp_path / "d.toml"
    d = Dictionary(snippets={"sig": 'say "hi" \\ bye'})
    d.save(path)
    assert Dictionary.load(path).snippets["sig"] == 'say "hi" \\ bye'


def test_missing_file_is_an_empty_dictionary(tmp_path):
    d = Dictionary.load(tmp_path / "nope.toml")
    assert d.vocabulary == [] and d.whisper_prompt() is None
