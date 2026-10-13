from __future__ import annotations

import pytest

from chttr import devmode as DM


@pytest.mark.parametrize(("spoken", "typed"), [
    ("rename it to camel case user id", "rename it to userId"),
    ("set snake case max retries to five", "set max_retries to five"),
    ("the kebab case main nav bar component", "the main-nav-bar-component"),
    ("class pascal case http client", "class HttpClient"),
    ("constant case api base url", "API_BASE_URL"),
    ("use camel case user id and snake case user name", "use userId and user_name"),
    ("camel case one two three four five", "oneTwoThreeFour five"),
    ("camel case user id, then save", "userId, then save"),
    ("nothing to change here", "nothing to change here"),
    ("say camel case.", "say camel case."),
    ("Rename it to camel-case user ID.", "Rename it to userId."),
])
def test_casing(spoken, typed):
    assert DM.apply_casing(spoken) == typed


@pytest.mark.parametrize(("spoken", "typed"), [
    ("look at main dot py", "look @main.py"),
    ("open at src slash app dot ts and fix it", "open @src/app.ts and fix it"),
    ("edit config dot toml", "edit config.toml"),
    ("the meeting is at noon dot com", "the meeting is at noon dot com"),     # not a code extension
    ("run it at five", "run it at five"),
    ("Look at main.py and at README.md.", "Look @main.py and @README.md."),
    ("meet at example.com", "meet at example.com"),
])
def test_files(spoken, typed):
    assert DM.apply_files(spoken) == typed


def test_file_references_only_in_code_apps():
    assert DM.transform("check at main dot py", code=True) == "check @main.py"
    assert DM.transform("check at main dot py", code=False) == "check at main dot py"
    assert DM.transform("camel case user id", code=False) == "userId"


def test_keep_list_collects_identifiers_for_the_model():
    original = "rename camel case user id in at main dot py"
    out = DM.transform(original, code=True)
    assert DM.keep_list(original, out) == ["userId", "@main.py"]
