"""LLM reharmonization (mouthtranscriber.reharm + /api/reharmonize) and the deterministic
/api/chord-alternatives route.

No real network: the shared transport (`llm._post_chat`) and config loader (`llm.load_env`)
are monkeypatched. Covers the pure prompt builder, local validation + chord building (roman,
fit, svg), and both routes' status-code mapping.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from mouthtranscriber import llm as llm_mod
from mouthtranscriber import reharm as reharm_mod
from mouthtranscriber.model import NoteEvent
from server.app import app

client = TestClient(app)


def _note(midi: int, start_ql: float, dur_ql: float = 1.0) -> NoteEvent:
    n = NoteEvent(start=start_ql * 0.5, end=(start_ql + dur_ql) * 0.5, midi=midi)
    n.start_ql = start_ql
    n.dur_ql = dur_ql
    return n


# bar 0: C E G, bar 1: G B D F  -> two 4/4 measures
def _notes():
    return [_note(60, 0), _note(64, 1), _note(67, 2, 2),
            _note(67, 4), _note(71, 5), _note(62, 6), _note(65, 7)]


def _notes_json():
    return [{"midi": n.midi, "start_ql": n.start_ql, "dur_ql": n.dur_ql} for n in _notes()]


def _profiles():
    from mouthtranscriber import chords
    return chords.measure_profiles(_notes(), (4, 4))


_GOOD_CONTENT = (
    '{"chords": [{"measure": 1, "root": "C", "quality": "maj7"}, '
    '{"measure": 2, "root": "G", "quality": "dom7"}], "summary": "Cmaj7 to G7."}'
)


# ---- build_messages (pure) ---------------------------------------------------

def test_build_messages_has_whitelist_profiles_style_and_json():
    msgs = reharm_mod.build_messages(_profiles(), "C major", (4, 4), [], "jazzier", "en")
    assert [m["role"] for m in msgs] == ["system", "user"]
    blob = " ".join(m["content"] for m in msgs)
    assert "JSON" in blob
    assert "jazzier" in blob                         # the style is included
    for q in ("maj7", "dom7", "min7b5", "sus4"):     # the quality whitelist is listed
        assert q in blob
    assert '"melody_by_measure"' in blob             # per-bar profiles are in the payload
    assert "em dash" in blob.lower() or "en dash" in blob.lower()


def test_build_messages_language_switch():
    en = " ".join(m["content"] for m in reharm_mod.build_messages(_profiles(), "C major", (4, 4), [], "x", "en"))
    vi = " ".join(m["content"] for m in reharm_mod.build_messages(_profiles(), "C major", (4, 4), [], "x", "vi"))
    assert "English" in en and "Vietnamese" not in en
    assert "Vietnamese" in vi


# ---- validation + build ------------------------------------------------------

def test_validate_rejects_wrong_measure_count():
    with pytest.raises(llm_mod.LLMBadOutput):
        reharm_mod._validate({"chords": [{"measure": 1, "root": "C", "quality": "maj"}]}, 2)


def test_validate_rejects_unknown_quality():
    bad = {"chords": [{"measure": 1, "root": "C", "quality": "frob"}, {"measure": 2, "root": "G", "quality": "maj"}]}
    with pytest.raises(llm_mod.LLMBadOutput):
        reharm_mod._validate(bad, 2)


def test_validate_rejects_out_of_order_measures():
    bad = {"chords": [{"measure": 2, "root": "C", "quality": "maj"}, {"measure": 1, "root": "G", "quality": "maj"}]}
    with pytest.raises(llm_mod.LLMBadOutput):
        reharm_mod._validate(bad, 2)


def test_build_chords_spells_and_labels():
    proposed = [{"measure": 1, "root": "C", "quality": "maj7"}, {"measure": 2, "root": "G", "quality": "dom7"}]
    built = reharm_mod._build_chords(proposed, "C major", (4, 4))
    assert [c.symbol for c in built] == ["Cmaj7", "G7"]
    assert built[0].root_pc == 0 and built[1].root_pc == 7
    assert [c.roman for c in built] == ["Imaj7", "V7"]          # diatonic romans via music21
    assert [c.start_ql for c in built] == [0.0, 4.0]


def test_build_chords_bad_root_raises():
    with pytest.raises(llm_mod.LLMBadOutput):
        reharm_mod._build_chords([{"measure": 1, "root": "H#x", "quality": "maj"}], "C major", (4, 4))


def test_reharmonize_happy_path(monkeypatch):
    monkeypatch.setattr(llm_mod, "load_env", lambda *a, **k: {"DEEPSEEK_API_KEY": "sk"})
    monkeypatch.setattr(llm_mod, "_post_chat",
                        lambda *a, **k: {"choices": [{"message": {"content": _GOOD_CONTENT}}]})
    out = reharm_mod.reharmonize(_notes(), "C major", (4, 4), [], "jazzier", "en")
    assert [c["symbol"] for c in out["chords"]] == ["Cmaj7", "G7"]
    assert out["fit"] == [1.0, 1.0]                              # both chords cover their bar fully
    assert out["summary"] == "Cmaj7 to G7."
    assert out["svg"].lstrip().startswith("<svg")


# ---- /api/reharmonize --------------------------------------------------------

def _payload(**over):
    p = {"notes": _notes_json(), "tempo": 120, "time_sig": [4, 4], "key": "C major",
         "chords": [], "style": "jazzier", "language": "en"}
    p.update(over)
    return p


def test_route_no_notes_400():
    assert client.post("/api/reharmonize", json=_payload(notes=[])).status_code == 400


def test_route_no_style_400():
    assert client.post("/api/reharmonize", json=_payload(style="  ")).status_code == 400


def test_route_no_key_503(monkeypatch):
    monkeypatch.setattr(llm_mod, "load_env", lambda *a, **k: {})
    r = client.post("/api/reharmonize", json=_payload())
    assert r.status_code == 503
    assert "DEEPSEEK_API_KEY" in r.json()["detail"]


def test_route_happy_path(monkeypatch):
    monkeypatch.setattr(llm_mod, "load_env", lambda *a, **k: {"DEEPSEEK_API_KEY": "sk"})
    captured = {}

    def fake_post(base_url, api_key, model, messages, **kw):
        captured["kw"] = kw
        return {"choices": [{"message": {"content": _GOOD_CONTENT}}]}

    monkeypatch.setattr(llm_mod, "_post_chat", fake_post)
    r = client.post("/api/reharmonize", json=_payload())
    assert r.status_code == 200
    body = r.json()
    assert [c["symbol"] for c in body["chords"]] == ["Cmaj7", "G7"]
    assert body["fit"] == [1.0, 1.0]
    assert body["svg"].lstrip().startswith("<svg")
    assert captured["kw"]["json_mode"] is True


def test_route_bad_output_502_with_reason(monkeypatch):
    monkeypatch.setattr(llm_mod, "load_env", lambda *a, **k: {"DEEPSEEK_API_KEY": "sk"})
    # only one chord for a two-measure melody -> validation reason surfaces
    bad = '{"chords": [{"measure": 1, "root": "C", "quality": "maj"}], "summary": "x"}'
    monkeypatch.setattr(llm_mod, "_post_chat", lambda *a, **k: {"choices": [{"message": {"content": bad}}]})
    r = client.post("/api/reharmonize", json=_payload())
    assert r.status_code == 502
    assert "expected 2 chords" in r.json()["detail"]


def test_route_upstream_error_502(monkeypatch):
    monkeypatch.setattr(llm_mod, "load_env", lambda *a, **k: {"DEEPSEEK_API_KEY": "sk"})

    def boom(*a, **k):
        raise httpx.HTTPError("connection refused")

    monkeypatch.setattr(llm_mod, "_post_chat", boom)
    assert client.post("/api/reharmonize", json=_payload()).status_code == 502


def test_route_chord_model_override(monkeypatch):
    monkeypatch.setattr(
        llm_mod, "load_env",
        lambda *a, **k: {"DEEPSEEK_API_KEY": "sk", "DEEPSEEK_MODEL": "base", "DEEPSEEK_CHORD_MODEL": "jazz"},
    )
    monkeypatch.setattr(llm_mod, "_post_chat", lambda *a, **k: {"choices": [{"message": {"content": _GOOD_CONTENT}}]})
    r = client.post("/api/reharmonize", json=_payload())
    assert r.status_code == 200
    assert r.json()["model"] == "jazz"


# ---- /api/chord-alternatives (deterministic, no LLM) -------------------------

def test_alternatives_route_returns_options():
    r = client.post("/api/chord-alternatives", json=_payload(style="sevenths"))
    assert r.status_code == 200
    body = r.json()
    assert len(body["measures"]) == 2
    for m in body["measures"]:
        assert 1 <= len(m["options"]) <= 3
        assert all("fit" in o and "symbol" in o for o in m["options"])


def test_alternatives_route_no_notes_400():
    assert client.post("/api/chord-alternatives", json=_payload(notes=[])).status_code == 400
