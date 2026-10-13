from __future__ import annotations

import pytest

from chttr import notes as N


def test_builtin_styles_all_parse_with_sections():
    styles = N.load_styles()
    assert len(styles) >= 18
    assert "general" in styles
    for s in styles.values():
        assert s.sections, s.id
        assert len({sec.key for sec in s.sections}) == len(s.sections), f"{s.id}: duplicate section keys"
        for sec in s.sections:
            assert sec.kind in ("bullets", "paragraph", "quotes"), (s.id, sec.title)


@pytest.mark.parametrize("style_id", sorted(N.load_styles()))
def test_schema_requires_every_section_of_the_style(style_id):
    style = N.load_styles()[style_id]
    schema = N._schema(style)
    sections = schema["properties"]["sections"]
    assert sections["required"] == [s.key for s in style.sections]
    assert sections["additionalProperties"] is False
    assert set(schema["required"]) == {"title", "sections", "action_items", "speakers"}


def test_custom_style_schema_lets_the_model_choose_sections():
    schema = N._schema(N.custom_style("focus on risks"))
    assert schema["properties"]["sections"]["type"] == "array"


def test_style_prompt_lists_sections_and_extra_request():
    style = N.load_styles()["bant"]
    prompt = N._style_prompt(style, "mention pricing twice")
    assert "Budget" in prompt and "Timeline" in prompt
    assert "<additional_request>\nmention pricing twice" in prompt


def test_user_styles_override_builtins(tmp_path, monkeypatch):
    user = tmp_path / "note-styles.toml"
    user.write_text('[[style]]\nid = "general"\nname = "Mine"\nsections = [{ title = "Only" }]\n'
                    '[[style]]\nid = "broken"\n')
    monkeypatch.setattr(N, "USER_STYLES", user)
    styles = N.load_styles()
    assert styles["general"].name == "Mine" and not styles["general"].builtin
    assert "broken" not in styles


def test_write_notes_maps_sections_back_in_order(monkeypatch):
    style = N.load_styles()["general"]
    raw = {"title": "T", "sections": {s.key: [f"item for {s.title}"] for s in reversed(style.sections)},
           "action_items": [{"owner": "Rob", "task": "x", "due": ""}], "speakers": []}
    captured = {}

    def fake_call(cfg, effort, schema, content, **kw):
        captured["content"] = content
        return raw

    monkeypatch.setattr(N, "_call", fake_call)
    out = N.write_notes(None, "medium", style, "meeting", "invited", "speakers", ["Speaker 1"], "transcript")
    assert [s["title"] for s in out["sections"]] == [s.title for s in style.sections]
    assert out["action_items"][0]["owner"] == "Rob"
    assert captured["content"][0]["cache_control"] == {"type": "ephemeral"}     # the transcript block is cached
    assert "<unidentified>Speaker 1</unidentified>" in captured["content"][1]["text"]
