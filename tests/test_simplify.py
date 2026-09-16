"""LLM melody simplification (mouthtranscriber.simplify + /api/reorganize).

No real network: the shared transport (`llm._post_chat`) and config loader (`llm.load_env`)
are monkeypatched. Covers the pure prompt builder, local validation, the sequential->grid note
conversion, and the route's status-code mapping.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from mouthtranscriber import llm as llm_mod
from mouthtranscriber import simplify as simplify_mod
from mouthtranscriber.model import NoteEvent
from server.app import app

client = TestClient(app)


def _note(midi: int, start_ql: float, dur_ql: float = 1.0) -> NoteEvent:
    n = NoteEvent(start=start_ql * 0.5, end=(start_ql + dur_ql) * 0.5, midi=midi)
    n.start_ql = start_ql
    n.dur_ql = dur_ql
    return n


def _notes():
    # A messy little line: a tied sliver pair and an off-grid length.
    return [_note(60, 0, 0.5), _note(60, 0.5, 0.5), _note(64, 1, 0.9), _note(67, 2, 1.0)]


def _notes_json():
    return [{"midi": n.midi, "start_ql": n.start_ql, "dur_ql": n.dur_ql} for n in _notes()]


_GOOD_CONTENT = (
    '{"notes": [{"midi": 60, "beats": 1}, {"midi": 64, "beats": 1}, '
    '{"rest": true, "beats": 0.5}, {"midi": 67, "beats": 1.5}], '
    '"summary": "Merged the repeated C and snapped every length to a beat."}'
)


# ---- build_messages (pure) ---------------------------------------------------

def test_build_messages_has_json_values_and_listing():
    msgs = simplify_mod.build_messages(_notes(), "C major", (4, 4), "en")
    assert [m["role"] for m in msgs] == ["system", "user"]
    blob = " ".join(m["content"] for m in msgs)
    assert "JSON" in blob
    assert "C major" in blob and "4/4" in blob
    assert "C4 (60)" in blob                          # the listing shows note name + midi
    assert "0.5" in blob and "0.25" in blob           # the note-value set is spelled out
    assert "em dash" in blob.lower() or "en dash" in blob.lower()


def test_build_messages_language_switch():
    en = " ".join(m["content"] for m in simplify_mod.build_messages(_notes(), "C major", (4, 4), "en"))
    vi = " ".join(m["content"] for m in simplify_mod.build_messages(_notes(), "C major", (4, 4), "vi"))
    assert "English" in en and "Vietnamese" not in en
    assert "Vietnamese" in vi


# ---- validation --------------------------------------------------------------

def test_validate_accepts_notes_and_rests():
    out = simplify_mod._validate({"notes": [{"midi": 60, "beats": 1}, {"rest": True, "beats": 0.5}]})
    assert out == [{"midi": 60, "beats": 1.0}, {"rest": True, "beats": 0.5}]


def test_validate_rejects_empty():
    with pytest.raises(llm_mod.LLMBadOutput):
        simplify_mod._validate({"notes": []})


def test_validate_rejects_all_rests():
    with pytest.raises(llm_mod.LLMBadOutput):
        simplify_mod._validate({"notes": [{"rest": True, "beats": 1}]})


def test_validate_rejects_bad_beats():
    with pytest.raises(llm_mod.LLMBadOutput):
        simplify_mod._validate({"notes": [{"midi": 60, "beats": 0}]})
    with pytest.raises(llm_mod.LLMBadOutput):
        simplify_mod._validate({"notes": [{"midi": 60, "beats": "1"}]})


def test_validate_rejects_out_of_range_midi():
    with pytest.raises(llm_mod.LLMBadOutput):
        simplify_mod._validate({"notes": [{"midi": 200, "beats": 1}]})


def test_validate_rejects_missing_midi():
    with pytest.raises(llm_mod.LLMBadOutput):
        simplify_mod._validate({"notes": [{"beats": 1}]})


def test_validate_rejects_bool_midi():
    # bool is an int subclass; a JSON true must not pass as a midi number.
    with pytest.raises(llm_mod.LLMBadOutput):
        simplify_mod._validate({"notes": [{"midi": True, "beats": 1}]})


# ---- sequential -> grid conversion -------------------------------------------

def test_to_notes_running_onsets_with_rest_gap():
    items = [{"midi": 60, "beats": 1}, {"midi": 64, "beats": 1}, {"rest": True, "beats": 0.5}, {"midi": 67, "beats": 1.5}]
    notes = simplify_mod._to_notes(items)
    assert [n["midi"] for n in notes] == [60, 64, 67]
    assert [n["start_ql"] for n in notes] == [0.0, 1.0, 2.5]   # the rest opens a 0.5 gap
    assert [n["dur_ql"] for n in notes] == [1.0, 1.0, 1.5]
    assert notes[0]["name"] == "C4"


# ---- simplify (mock transport) ----------------------------------------------

def test_simplify_happy_path(monkeypatch):
    monkeypatch.setattr(llm_mod, "load_env", lambda *a, **k: {"DEEPSEEK_API_KEY": "sk"})
    monkeypatch.setattr(llm_mod, "_post_chat",
                        lambda *a, **k: {"choices": [{"message": {"content": _GOOD_CONTENT}}]})
    out = simplify_mod.simplify(_notes(), "C major", (4, 4), "en")
    assert [n["midi"] for n in out["notes"]] == [60, 64, 67]
    assert [n["start_ql"] for n in out["notes"]] == [0.0, 1.0, 2.5]
    assert out["summary"].startswith("Merged")


# ---- /api/reorganize ---------------------------------------------------------

def _payload(**over):
    p = {"notes": _notes_json(), "tempo": 120, "time_sig": [4, 4], "key": "C major", "language": "en"}
    p.update(over)
    return p


def test_route_no_notes_400():
    assert client.post("/api/reorganize", json=_payload(notes=[])).status_code == 400


def test_route_too_long_400():
    big = [{"midi": 60, "start_ql": i, "dur_ql": 1.0} for i in range(300)]
    assert client.post("/api/reorganize", json=_payload(notes=big)).status_code == 400


def test_route_no_key_503(monkeypatch):
    monkeypatch.setattr(llm_mod, "load_env", lambda *a, **k: {})
    r = client.post("/api/reorganize", json=_payload())
    assert r.status_code == 503
    assert "DEEPSEEK_API_KEY" in r.json()["detail"]


def test_route_happy_path(monkeypatch):
    monkeypatch.setattr(llm_mod, "load_env", lambda *a, **k: {"DEEPSEEK_API_KEY": "sk"})
    captured = {}

    def fake_post(base_url, api_key, model, messages, **kw):
        captured["kw"] = kw
        return {"choices": [{"message": {"content": _GOOD_CONTENT}}]}

    monkeypatch.setattr(llm_mod, "_post_chat", fake_post)
    r = client.post("/api/reorganize", json=_payload())
    assert r.status_code == 200
    body = r.json()
    assert [n["midi"] for n in body["notes"]] == [60, 64, 67]
    assert captured["kw"]["json_mode"] is True
    assert captured["kw"]["max_tokens"] == llm_mod._MAX_TOKENS   # inherits the generous shared budget


def test_route_bad_output_502(monkeypatch):
    monkeypatch.setattr(llm_mod, "load_env", lambda *a, **k: {"DEEPSEEK_API_KEY": "sk"})
    monkeypatch.setattr(llm_mod, "_post_chat",
                        lambda *a, **k: {"choices": [{"message": {"content": '{"notes": []}'}}]})
    r = client.post("/api/reorganize", json=_payload())
    assert r.status_code == 502


def test_route_upstream_error_502(monkeypatch):
    monkeypatch.setattr(llm_mod, "load_env", lambda *a, **k: {"DEEPSEEK_API_KEY": "sk"})

    def boom(*a, **k):
        raise httpx.HTTPError("connection refused")

    monkeypatch.setattr(llm_mod, "_post_chat", boom)
    assert client.post("/api/reorganize", json=_payload()).status_code == 502


def test_route_edit_model_override(monkeypatch):
    monkeypatch.setattr(
        llm_mod, "load_env",
        lambda *a, **k: {"DEEPSEEK_API_KEY": "sk", "DEEPSEEK_MODEL": "base", "DEEPSEEK_EDIT_MODEL": "fast"},
    )
    monkeypatch.setattr(llm_mod, "_post_chat", lambda *a, **k: {"choices": [{"message": {"content": _GOOD_CONTENT}}]})
    r = client.post("/api/reorganize", json=_payload())
    assert r.status_code == 200
    assert r.json()["model"] == "fast"
