"""Hub progress coach (mouthtranscriber.progress_coach + /api/progress-coach).

No real network: the shared transport (`llm._post_chat`) and config loader (`llm.load_env`)
are monkeypatched, so these are deterministic regardless of a real `.env`. Covers the pure
prompt builder (drill names, lowConfidence instruction, language, house style) and the
route's status-code mapping (503 no key, 502 upstream, 400 malformed, 200 happy path).
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from mouthtranscriber import llm as llm_mod
from mouthtranscriber import progress_coach as progress_mod
from server.app import app

client = TestClient(app)


def _report():
    return {
        "window": {"days": 28, "takes": 12, "activeDays": 6, "streakDays": 3, "firstTakeDaysAgo": 20},
        "inTune": {"n": 12, "mean": 66, "first": 60, "last": 72, "delta": 12, "best": 80, "slopePerTake": 1.1},
        "steadiness": {"n": 12, "medianCents": 14, "first": 18, "last": 12},
        "sustain": {"bestS": 8.2, "first": 5.0, "last": 7.5},
        "vibrato": {"takesWithVibrato": 3, "rateHzMedian": 5.5, "depthCentsMedian": 30},
        "range": {"tests": 2, "first": {"lo": 45, "hiExt": 64, "extST": 19},
                  "last": {"lo": 43, "hiExt": 67, "extST": 24}, "deltaST": 5,
                  "voiceTypes": ["Baritone", "Tenor"], "tessitura": {"p25": 50, "p50": 55, "p75": 60}, "group": "male"},
        "singalong": {"takes": 4, "inTunePct": {"first": 55, "last": 70},
                      "signedBiasCents": {"mean": -14, "first": -8, "last": -18},
                      "weakestRegister": "high", "leapMinusStepPct": -30,
                      "recurringWorst": [{"name": "C5", "count": 3}], "octaveSlipsTotal": 2},
        "ear": {"interval": {"n": 40, "pct": 82, "recentPct": 88}, "chord": {"n": 30, "pct": 61, "recentPct": 60},
                "scale": {"n": 10, "pct": 70, "recentPct": 70}, "key": {"n": 8, "pct": 55, "recentPct": 55}},
        "lowConfidence": {"voice": False, "singalong": False, "ear": False, "range": False},
    }


# ---- build_messages (pure) ---------------------------------------------------

def test_build_messages_has_drills_confidence_and_style():
    msgs = progress_mod.build_messages(_report(), "en")
    assert [m["role"] for m in msgs] == ["system", "user"]
    blob = " ".join(m["content"] for m in msgs)
    # names the app's own drills, so the plan is actionable inside the app
    assert "Realtime > Match game" in blob
    assert "Realtime > Scale trainer" in blob
    assert "Realtime > Range test" in blob
    assert "Sing-Along at Strict difficulty" in blob
    assert "Ear Trainer" in blob
    # honesty + house style
    assert "lowConfidence" in blob
    assert "markdown" in blob.lower()
    assert "em dash" in blob.lower()
    # the actual numbers are in the payload
    assert '"delta": 12' in blob


def test_build_messages_language_switch():
    en = " ".join(m["content"] for m in progress_mod.build_messages(_report(), "en"))
    vi = " ".join(m["content"] for m in progress_mod.build_messages(_report(), "vi"))
    assert "English" in en and "Vietnamese" not in en
    assert "Vietnamese" in vi
    other = " ".join(m["content"] for m in progress_mod.build_messages(_report(), "fr"))
    assert "English" in other


# ---- progress_feedback + /api/progress-coach ---------------------------------

def test_route_no_key_returns_503(monkeypatch):
    monkeypatch.setattr(llm_mod, "load_env", lambda *a, **k: {})
    r = client.post("/api/progress-coach", json={"report": _report(), "language": "en"})
    assert r.status_code == 503
    assert "DEEPSEEK_API_KEY" in r.json()["detail"]


def test_route_happy_path(monkeypatch):
    monkeypatch.setattr(llm_mod, "load_env", lambda *a, **k: {"DEEPSEEK_API_KEY": "sk-test"})
    captured = {}

    def fake_post(base_url, api_key, model, messages, **kw):
        captured["model"] = model
        captured["messages"] = messages
        return {"choices": [{"message": {"content": "Great progress. Book Realtime > Scale trainer daily."}}]}

    monkeypatch.setattr(llm_mod, "_post_chat", fake_post)
    r = client.post("/api/progress-coach", json={"report": _report(), "language": "en"})
    assert r.status_code == 200
    body = r.json()
    assert "Scale trainer" in body["feedback"]
    assert body["model"] == llm_mod.DEFAULT_MODEL
    assert captured["model"] == llm_mod.DEFAULT_MODEL


def test_route_upstream_error_returns_502(monkeypatch):
    monkeypatch.setattr(llm_mod, "load_env", lambda *a, **k: {"DEEPSEEK_API_KEY": "sk-test"})

    def boom(*a, **k):
        raise httpx.HTTPError("connection refused")

    monkeypatch.setattr(llm_mod, "_post_chat", boom)
    r = client.post("/api/progress-coach", json={"report": _report(), "language": "en"})
    assert r.status_code == 502


def test_route_unparseable_response_returns_502(monkeypatch):
    monkeypatch.setattr(llm_mod, "load_env", lambda *a, **k: {"DEEPSEEK_API_KEY": "sk-test"})
    monkeypatch.setattr(llm_mod, "_post_chat", lambda *a, **k: {"unexpected": True})
    r = client.post("/api/progress-coach", json={"report": _report(), "language": "en"})
    assert r.status_code == 502


def test_route_malformed_payload_returns_400():
    r = client.post("/api/progress-coach", json={"language": "en"})   # no report
    assert r.status_code == 400
