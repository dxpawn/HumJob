"""Sing-Along LLM coaching (mouthtranscriber.coach + /api/coach).

No real network: the upstream HTTP call (`coach._post_chat`) and the config loader
(`coach.load_env`) are monkeypatched, so these tests are deterministic regardless of
whether a real `.env` exists. Covers the env parser, the pure prompt builder, and the
route's status-code mapping (503 no key, 502 upstream error, 200 happy path, 400 malformed).
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from mouthtranscriber import coach as coach_mod
from server.app import app

client = TestClient(app)


def _report():
    return {
        "summary": {"inTunePct": 0.62, "meanAbsCents": 41, "scoredNotes": 8, "notesTotal": 8},
        "signedBiasCents": -18,
        "drift": {"firstCents": -4, "lastCents": -31, "driftCents": -27},
        "octaveSlips": 1,
        "leaps": {"leap": {"n": 3, "hitPct": 0.33}, "step": {"n": 5, "hitPct": 0.8}, "missedLeapLandings": 2},
        "register": {"low": {"n": 2, "hitPct": 0.9}, "mid": {"n": 4, "hitPct": 0.7}, "high": {"n": 2, "hitPct": 0.2}, "weakest": "high"},
        "worstNotes": [{"name": "F4", "bar": 12, "hitPct": 0.2, "meanCents": -40}],
        "lowConfidence": False,
        "context": {"key": "C major", "tempo_bpm": 120, "time_sig": [4, 4], "band_cents": 50, "octave_mode": "agnostic"},
    }


# ---- load_env ----------------------------------------------------------------

def test_load_env_parses_and_ignores_noise(tmp_path, monkeypatch):
    for k in ("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "# a comment\n"
        "\n"
        "DEEPSEEK_API_KEY = \"sk-fromfile\"\n"
        "DEEPSEEK_MODEL='deepseek-v4-flash'\n"
        "NOT_A_PAIR\n",
        encoding="utf-8",
    )
    cfg = coach_mod.load_env(str(env))
    assert cfg["DEEPSEEK_API_KEY"] == "sk-fromfile"      # whitespace + quotes stripped
    assert cfg["DEEPSEEK_MODEL"] == "deepseek-v4-flash"
    assert "NOT_A_PAIR" not in cfg                        # a line without '=' is ignored


def test_load_env_real_environ_wins(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("DEEPSEEK_API_KEY=sk-fromfile\n", encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fromenv")
    cfg = coach_mod.load_env(str(env))
    assert cfg["DEEPSEEK_API_KEY"] == "sk-fromenv"


def test_load_env_missing_file_is_empty(tmp_path, monkeypatch):
    for k in ("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    cfg = coach_mod.load_env(str(tmp_path / "nope.env"))
    assert cfg == {}


# ---- build_messages (pure) ---------------------------------------------------

def test_build_messages_grounds_in_numbers_and_style():
    msgs = coach_mod.build_messages(_report(), "en")
    assert [m["role"] for m in msgs] == ["system", "user"]
    blob = " ".join(m["content"] for m in msgs)
    assert "-18" in blob                 # the signed bias number is present
    assert '"bar": 12' in blob           # the worst-note bar is present
    assert "markdown" in blob.lower()    # plain-text / no-markdown instruction present
    assert "em dash" in blob.lower()     # house style pinned


def test_build_messages_language_switch():
    en = " ".join(m["content"] for m in coach_mod.build_messages(_report(), "en"))
    vi = " ".join(m["content"] for m in coach_mod.build_messages(_report(), "vi"))
    assert "English" in en and "Vietnamese" not in en
    assert "Vietnamese" in vi
    other = " ".join(m["content"] for m in coach_mod.build_messages(_report(), "fr"))
    assert "English" in other            # anything unknown falls back to English


# ---- coach_feedback + /api/coach ---------------------------------------------

def test_coach_feedback_no_key_raises(monkeypatch):
    monkeypatch.setattr(coach_mod, "load_env", lambda *a, **k: {})
    with pytest.raises(coach_mod.CoachNotConfigured):
        coach_mod.coach_feedback(_report(), "en")


def test_route_no_key_returns_503(monkeypatch):
    monkeypatch.setattr(coach_mod, "load_env", lambda *a, **k: {})
    r = client.post("/api/coach", json={"report": _report(), "language": "en"})
    assert r.status_code == 503
    assert "DEEPSEEK_API_KEY" in r.json()["detail"]


def test_route_happy_path(monkeypatch):
    monkeypatch.setattr(coach_mod, "load_env", lambda *a, **k: {"DEEPSEEK_API_KEY": "sk-test"})
    captured = {}

    def fake_post(base_url, api_key, model, messages):
        captured["model"] = model
        captured["messages"] = messages
        return {"choices": [{"message": {"content": "Nice work. Steady the F4 in bar 12."}}]}

    monkeypatch.setattr(coach_mod, "_post_chat", fake_post)
    r = client.post("/api/coach", json={"report": _report(), "language": "en"})
    assert r.status_code == 200
    body = r.json()
    assert "F4 in bar 12" in body["feedback"]
    assert body["model"] == coach_mod.DEFAULT_MODEL     # default model when .env sets no override
    assert captured["model"] == coach_mod.DEFAULT_MODEL


def test_route_upstream_error_returns_502(monkeypatch):
    monkeypatch.setattr(coach_mod, "load_env", lambda *a, **k: {"DEEPSEEK_API_KEY": "sk-test"})

    def boom(*a, **k):
        raise httpx.HTTPError("connection refused")

    monkeypatch.setattr(coach_mod, "_post_chat", boom)
    r = client.post("/api/coach", json={"report": _report(), "language": "en"})
    assert r.status_code == 502


def test_route_unparseable_response_returns_502(monkeypatch):
    monkeypatch.setattr(coach_mod, "load_env", lambda *a, **k: {"DEEPSEEK_API_KEY": "sk-test"})
    monkeypatch.setattr(coach_mod, "_post_chat", lambda *a, **k: {"unexpected": True})
    r = client.post("/api/coach", json={"report": _report(), "language": "en"})
    assert r.status_code == 502


def test_route_malformed_payload_returns_400():
    r = client.post("/api/coach", json={"language": "en"})   # no report
    assert r.status_code == 400
