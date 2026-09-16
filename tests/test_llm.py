"""Shared LLM transport (mouthtranscriber.llm).

No real network: `_post_chat` and `load_env` are monkeypatched. Covers the env parser
(duplicated from test_coach so llm owns it now), parse_json_object's fence/prose tolerance,
and chat() (model override key, empty-content and finish_reason "length" handling).
"""

from __future__ import annotations

import httpx
import pytest

from mouthtranscriber import llm as llm_mod


# ---- load_env ----------------------------------------------------------------

def test_load_env_parses_and_ignores_noise(tmp_path, monkeypatch):
    for k in ("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "# a comment\n"
        "\n"
        "DEEPSEEK_API_KEY = \"sk-fromfile\"\n"
        "DEEPSEEK_MODEL='deepseek-flash'\n"
        "DEEPSEEK_EDIT_MODEL=fast-1\n"
        "NOT_A_PAIR\n",
        encoding="utf-8",
    )
    cfg = llm_mod.load_env(str(env))
    assert cfg["DEEPSEEK_API_KEY"] == "sk-fromfile"      # whitespace + quotes stripped
    assert cfg["DEEPSEEK_MODEL"] == "deepseek-flash"
    assert cfg["DEEPSEEK_EDIT_MODEL"] == "fast-1"        # any KEY=VALUE line is parsed
    assert "NOT_A_PAIR" not in cfg                        # a line without '=' is ignored


def test_load_env_real_environ_wins(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("DEEPSEEK_API_KEY=sk-fromfile\n", encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fromenv")
    cfg = llm_mod.load_env(str(env))
    assert cfg["DEEPSEEK_API_KEY"] == "sk-fromenv"


def test_load_env_missing_file_is_empty(tmp_path, monkeypatch):
    for k in ("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    cfg = llm_mod.load_env(str(tmp_path / "nope.env"))
    assert cfg == {}


# ---- parse_json_object -------------------------------------------------------

def test_parse_bare_json():
    assert llm_mod.parse_json_object('{"a": 1, "b": "x"}') == {"a": 1, "b": "x"}


def test_parse_fenced_json():
    text = "```json\n{\"ops\": [], \"summary\": \"ok\"}\n```"
    assert llm_mod.parse_json_object(text) == {"ops": [], "summary": "ok"}


def test_parse_json_inside_prose():
    text = "Sure, here is the edit script: {\"ops\": [1, 2]} - hope that helps!"
    assert llm_mod.parse_json_object(text) == {"ops": [1, 2]}


def test_parse_garbage_raises():
    for bad in ["no json here", "", "[1,2,3]", "{not valid}"]:
        with pytest.raises(llm_mod.LLMBadOutput):
            llm_mod.parse_json_object(bad)


# ---- chat --------------------------------------------------------------------

def test_chat_no_key_raises(monkeypatch):
    monkeypatch.setattr(llm_mod, "load_env", lambda *a, **k: {})
    with pytest.raises(llm_mod.LLMNotConfigured):
        llm_mod.chat([{"role": "user", "content": "hi"}])


def test_chat_happy_path_and_default_model(monkeypatch):
    monkeypatch.setattr(llm_mod, "load_env", lambda *a, **k: {"DEEPSEEK_API_KEY": "sk-test"})
    captured = {}

    def fake_post(base_url, api_key, model, messages, **kw):
        captured["model"] = model
        captured["kw"] = kw
        return {"choices": [{"message": {"content": "hello there"}}]}

    monkeypatch.setattr(llm_mod, "_post_chat", fake_post)
    out = llm_mod.chat([{"role": "user", "content": "hi"}], json_mode=True, temperature=0.2)
    assert out == {"content": "hello there", "model": llm_mod.DEFAULT_MODEL}
    assert captured["model"] == llm_mod.DEFAULT_MODEL
    assert captured["kw"]["json_mode"] is True
    assert captured["kw"]["temperature"] == 0.2


def test_chat_model_override_key_honoured(monkeypatch):
    # The feature reads its own override key; it wins over DEEPSEEK_MODEL.
    monkeypatch.setattr(
        llm_mod, "load_env",
        lambda *a, **k: {"DEEPSEEK_API_KEY": "sk", "DEEPSEEK_MODEL": "base", "DEEPSEEK_EDIT_MODEL": "fast"},
    )
    monkeypatch.setattr(
        llm_mod, "_post_chat",
        lambda *a, **k: {"choices": [{"message": {"content": "ok"}}]},
    )
    out = llm_mod.chat([], model_key="DEEPSEEK_EDIT_MODEL")
    assert out["model"] == "fast"


def test_chat_override_key_absent_falls_back_to_default_model(monkeypatch):
    monkeypatch.setattr(
        llm_mod, "load_env",
        lambda *a, **k: {"DEEPSEEK_API_KEY": "sk", "DEEPSEEK_MODEL": "base"},
    )
    monkeypatch.setattr(
        llm_mod, "_post_chat",
        lambda *a, **k: {"choices": [{"message": {"content": "ok"}}]},
    )
    out = llm_mod.chat([], model_key="DEEPSEEK_EDIT_MODEL")
    assert out["model"] == "base"      # override unset -> DEEPSEEK_MODEL


def test_chat_upstream_http_error(monkeypatch):
    monkeypatch.setattr(llm_mod, "load_env", lambda *a, **k: {"DEEPSEEK_API_KEY": "sk"})

    def boom(*a, **k):
        raise httpx.HTTPError("connection refused")

    monkeypatch.setattr(llm_mod, "_post_chat", boom)
    with pytest.raises(llm_mod.LLMUpstreamError):
        llm_mod.chat([])


def test_chat_length_finish_reason_maps_to_token_message(monkeypatch):
    monkeypatch.setattr(llm_mod, "load_env", lambda *a, **k: {"DEEPSEEK_API_KEY": "sk"})
    monkeypatch.setattr(
        llm_mod, "_post_chat",
        lambda *a, **k: {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]},
    )
    with pytest.raises(llm_mod.LLMUpstreamError) as ei:
        llm_mod.chat([])
    assert "token limit" in str(ei.value)


def test_chat_empty_content_without_length(monkeypatch):
    monkeypatch.setattr(llm_mod, "load_env", lambda *a, **k: {"DEEPSEEK_API_KEY": "sk"})
    monkeypatch.setattr(
        llm_mod, "_post_chat",
        lambda *a, **k: {"choices": [{"message": {"content": "   "}}]},
    )
    with pytest.raises(llm_mod.LLMUpstreamError) as ei:
        llm_mod.chat([])
    assert "empty" in str(ei.value)


def test_chat_unexpected_shape(monkeypatch):
    monkeypatch.setattr(llm_mod, "load_env", lambda *a, **k: {"DEEPSEEK_API_KEY": "sk"})
    monkeypatch.setattr(llm_mod, "_post_chat", lambda *a, **k: {"unexpected": True})
    with pytest.raises(llm_mod.LLMUpstreamError):
        llm_mod.chat([])
