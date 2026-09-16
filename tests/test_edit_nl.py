"""Manual-mode natural-language editing (mouthtranscriber.edit_nl + /api/edit-nl).

No real network: the shared transport (`llm._post_chat`) and config loader (`llm.load_env`)
are monkeypatched. Covers the pure prompt builder (listing, instruction, op vocabulary,
language, the word JSON) and the route's status-code mapping (400 empty/oversized, 503 no
key, 502 upstream + bad output, 200 happy path through parse_json_object).
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from mouthtranscriber import edit_nl as edit_mod
from mouthtranscriber import llm as llm_mod
from server.app import app

client = TestClient(app)

_LISTING = (
    "Key: C major. Time: 4/4. Tempo: 100 bpm. 1 beat = 1 quarter note = 4 ticks.\n"
    "5 notes. Selected: note 3.\n"
    "Bar 1: 1: C4 1 beat | 2: D4 0.5 beat | rest 0.5 beat | 3: E4 1 beat | 4: E4 1 beat\n"
    "Bar 2: 5: G4 2 beats"
)


# ---- build_messages (pure) ---------------------------------------------------

def test_build_messages_has_listing_instruction_vocab_and_json():
    msgs = edit_mod.build_messages(_LISTING, "merge notes 3 to 5", "en")
    assert [m["role"] for m in msgs] == ["system", "user"]
    blob = " ".join(m["content"] for m in msgs)
    assert "merge notes 3 to 5" in blob          # the instruction is included
    assert "Bar 2: 5: G4 2 beats" in blob        # the listing is included
    assert "JSON" in blob                        # some json-mode impls require the word
    for op in ("pitch", "setPitch", "merge", "split", "insertRest", "transpose"):
        assert op in blob                        # the op vocabulary is present
    assert "em dash" in blob.lower() or "en dash" in blob.lower()   # house style pinned


def test_build_messages_language_switch():
    en = " ".join(m["content"] for m in edit_mod.build_messages(_LISTING, "x", "en"))
    vi = " ".join(m["content"] for m in edit_mod.build_messages(_LISTING, "x", "vi"))
    assert "English" in en and "Vietnamese" not in en
    assert "Vietnamese" in vi


# ---- _validate_shape ---------------------------------------------------------

def test_validate_shape_accepts_and_rejects():
    ok = edit_mod._validate_shape({"ops": [{"op": "merge", "from": 3, "to": 5}], "summary": "ok"})
    assert ok["ops"][0]["op"] == "merge"
    # empty ops is valid (the model asking a question)
    assert edit_mod._validate_shape({"ops": [], "summary": "which note?"})["ops"] == []
    for bad in [
        {"ops": "nope"},                                  # ops not a list
        {"ops": [{"op": "frobnicate"}]},                  # unknown op
        {"ops": [{"note": 3}]},                            # missing op name
        {"ops": [{"op": "pitch", "note": {"x": 1}}]},     # nested field value
        {"ops": [{"op": "pitch", "note": True}]},         # bool field value
    ]:
        with pytest.raises(llm_mod.LLMBadOutput):
            edit_mod._validate_shape(bad)


# ---- edit_script + /api/edit-nl ---------------------------------------------

def _payload(instruction="merge notes 3 to 5", listing=_LISTING, language="en"):
    return {"listing": listing, "instruction": instruction, "language": language}


def test_route_empty_instruction_400():
    r = client.post("/api/edit-nl", json=_payload(instruction="   "))
    assert r.status_code == 400


def test_route_oversized_instruction_400():
    r = client.post("/api/edit-nl", json=_payload(instruction="x" * 501))
    assert r.status_code == 400


def test_route_oversized_listing_400():
    r = client.post("/api/edit-nl", json=_payload(listing="x" * 8001))
    assert r.status_code == 400


def test_route_no_key_503(monkeypatch):
    monkeypatch.setattr(llm_mod, "load_env", lambda *a, **k: {})
    r = client.post("/api/edit-nl", json=_payload())
    assert r.status_code == 503
    assert "DEEPSEEK_API_KEY" in r.json()["detail"]


def test_route_happy_path_through_fenced_json(monkeypatch):
    monkeypatch.setattr(llm_mod, "load_env", lambda *a, **k: {"DEEPSEEK_API_KEY": "sk-test"})
    captured = {}

    def fake_post(base_url, api_key, model, messages, **kw):
        captured["kw"] = kw
        captured["model"] = model
        # Fenced JSON with a preamble: proves parse_json_object is in the path.
        return {"choices": [{"message": {"content":
            "Sure, here is the script:\n```json\n"
            "{\"ops\": [{\"op\": \"merge\", \"from\": 3, \"to\": 5}], \"summary\": \"Merged notes 3 to 5.\"}\n```"}}]}

    monkeypatch.setattr(llm_mod, "_post_chat", fake_post)
    r = client.post("/api/edit-nl", json=_payload())
    assert r.status_code == 200
    body = r.json()
    assert body["script"]["ops"] == [{"op": "merge", "from": 3, "to": 5}]
    assert body["script"]["summary"] == "Merged notes 3 to 5."
    assert body["model"] == llm_mod.DEFAULT_MODEL
    assert captured["kw"]["json_mode"] is True        # json_mode requested


def test_route_edit_model_override(monkeypatch):
    monkeypatch.setattr(
        llm_mod, "load_env",
        lambda *a, **k: {"DEEPSEEK_API_KEY": "sk", "DEEPSEEK_MODEL": "base", "DEEPSEEK_EDIT_MODEL": "fast"},
    )
    monkeypatch.setattr(
        llm_mod, "_post_chat",
        lambda *a, **k: {"choices": [{"message": {"content": "{\"ops\": [], \"summary\": \"which note?\"}"}}]},
    )
    r = client.post("/api/edit-nl", json=_payload())
    assert r.status_code == 200
    assert r.json()["model"] == "fast"                # DEEPSEEK_EDIT_MODEL wins


def test_route_bad_output_502(monkeypatch):
    monkeypatch.setattr(llm_mod, "load_env", lambda *a, **k: {"DEEPSEEK_API_KEY": "sk"})
    monkeypatch.setattr(
        llm_mod, "_post_chat",
        lambda *a, **k: {"choices": [{"message": {"content": "I cannot help with that."}}]},
    )
    r = client.post("/api/edit-nl", json=_payload())
    assert r.status_code == 502
    assert "edit script" in r.json()["detail"]


def test_route_upstream_error_502(monkeypatch):
    monkeypatch.setattr(llm_mod, "load_env", lambda *a, **k: {"DEEPSEEK_API_KEY": "sk"})

    def boom(*a, **k):
        raise httpx.HTTPError("connection refused")

    monkeypatch.setattr(llm_mod, "_post_chat", boom)
    r = client.post("/api/edit-nl", json=_payload())
    assert r.status_code == 502
