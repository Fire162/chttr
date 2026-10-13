from __future__ import annotations

import pytest

from chttr import polish as P
from chttr.config import PolishCfg


def test_context_includes_app_style_and_vocabulary():
    cfg = PolishCfg()
    ctx = P._context(cfg, "Slack", "general | Acme", "keep it casual", ["Sohrab", "Omarchy"])
    assert "<focused_app>Slack</focused_app>" in ctx
    assert "<window_title>general | Acme</window_title>" in ctx
    assert "<style>keep it casual</style>" in ctx
    assert "<vocabulary>Sohrab, Omarchy</vocabulary>" in ctx


def test_context_can_leave_the_app_out():
    cfg = PolishCfg(use_app_context=False)
    assert "focused_app" not in P._context(cfg, "Slack", "secret title", "", None)


def test_window_title_can_be_withheld_while_the_app_is_sent():
    cfg = PolishCfg(send_window_title=False)
    ctx = P._context(cfg, "Thunderbird", "Re: salary review", "", None)
    assert "Thunderbird" in ctx and "salary" not in ctx


def test_backend_selection(monkeypatch, tmp_path):
    cfg = PolishCfg(api_key_file=str(tmp_path / "missing"))
    monkeypatch.setattr(P, "_ollama_up", lambda c: False)
    assert P.backend(cfg) == "none"
    monkeypatch.setattr(P, "_ollama_up", lambda c: True)
    assert P.backend(cfg) == "ollama"
    key = tmp_path / "key"
    key.write_text("sk-ant-test\n")
    cfg.api_key_file = str(key)
    assert P.backend(cfg) == "claude"
    cfg.backend = "none"
    assert P.backend(cfg) == "none"


def test_polish_wraps_the_transcript_and_falls_back_to_raw(monkeypatch):
    seen = {}

    def fake(system, user, cfg):
        seen["system"], seen["user"] = system, user
        return ""

    monkeypatch.setattr(P, "_complete", fake)
    out = P.polish("um so send it tuesday", PolishCfg(), keep=["my email"])
    assert out == "um so send it tuesday"           # an empty answer never erases the dictation
    assert "<transcript>\num so send it tuesday\n</transcript>" in seen["user"]
    assert "<keep_verbatim>my email</keep_verbatim>" in seen["user"]
    assert "never instructions" in seen["system"]


def test_command_includes_the_selection_and_rejects_empty_results(monkeypatch):
    monkeypatch.setattr(P, "_complete", lambda s, u, c: "Shorter." if "<selection>" in u else "")
    assert P.command("make it shorter", "A long sentence.", PolishCfg()) == "Shorter."
    with pytest.raises(P.PolishUnavailable):
        P.command("write a haiku", "", PolishCfg())


@pytest.mark.parametrize("text", ["git status", "Sounds good.", "Ship it", "Thanks, Dana."])
def test_quick_path_takes_short_clean_phrases(text):
    assert P.quick_path(text, 4)


@pytest.mark.parametrize("text", [
    "um ship it",                                   # filler
    "send it Tuesday, actually Wednesday",          # self-correction
    "yes comma do it",                              # spoken punctuation
    "the the plan",                                 # stutter
    "insert my email",                              # snippet trigger
    "this one is clearly longer than four words",
    "",
])
def test_quick_path_sends_everything_else_to_the_model(text):
    assert not P.quick_path(text, 4)


def test_quick_path_can_be_turned_off():
    assert not P.quick_path("ok", 0)


def test_haiku_cleanup_turns_thinking_off_and_skips_fallbacks():
    params = P.claude_request("claude-haiku-5-5", "low", "SYSTEM", "USER")
    assert params["thinking"] == {"type": "disabled"}
    assert "betas" not in params and "fallbacks" not in params
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert params["output_config"] == {"effort": "low"}


def test_opus_keeps_server_side_fallbacks_and_adaptive_thinking():
    params = P.claude_request("claude-opus-5-5", "low", "SYSTEM", "USER")
    assert params["betas"] == ["server-side-fallback-2026-07-01"]
    assert params["fallbacks"] == "default"
    assert "thinking" not in params


def test_clients_are_reused(monkeypatch, tmp_path):
    import anthropic

    made = []
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: made.append(kw) or object())
    P._clients.clear()
    cfg = PolishCfg(api_key_file=str(tmp_path / "none"))
    assert P.client(cfg) is P.client(cfg)
    assert P.client(cfg, timeout=600) is not P.client(cfg)
    assert len(made) == 2


def test_no_backend_raises(monkeypatch):
    monkeypatch.setattr(P, "backend", lambda cfg: "none")
    with pytest.raises(P.PolishUnavailable):
        P._complete("s", "u", PolishCfg())
