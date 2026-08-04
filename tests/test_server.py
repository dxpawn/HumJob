"""Backend API test (PLAN §3, milestone M5).

Exercises the same /api/transcribe route the browser UI posts to: upload a
fixture WAV, get back key + notes + an engraved SVG + MIDI/MusicXML. Requires
ffmpeg on PATH (the endpoint decodes uploads through it).
"""

from __future__ import annotations

import base64
import io
import os
import shutil

import pytest
from fastapi.testclient import TestClient

from server.app import app

client = TestClient(app)

FIXTURE = os.path.join(
    os.path.dirname(__file__), "data", "generated", "c_major_scale.wav"
)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")
def test_transcribe_endpoint():
    assert os.path.exists(FIXTURE), "run tests/make_synthetic.py first"
    with open(FIXTURE, "rb") as fh:
        resp = client.post(
            "/api/transcribe",
            files={"audio": ("c_major_scale.wav", fh, "audio/wav")},
            data={"bpm": 100, "beats": 4, "beat_unit": 4, "subdiv": 4},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["key"] == "C major"
    assert body["n_notes"] == 8
    assert [n["name"] for n in body["notes"]] == [
        "C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5"
    ]
    assert "<svg" in body["svg"][:200]
    assert "<score-partwise" in body["musicxml"] or "<score" in body["musicxml"]
    # MIDI header 'MThd'
    assert base64.b64decode(body["midi_b64"])[:4] == b"MThd"

    # Chord suggestions (M6): the 8-note C-major scale spans two 4/4 bars and
    # should harmonize starting on the tonic.
    chords = body["chords"]
    assert len(chords) == 2
    assert chords[0]["symbol"] == "C" and chords[0]["roman"] == "I"
    assert all("symbol" in c and "roman" in c for c in chords)
    # Pitch data the browser playback needs to voice the triads.
    assert chords[0]["root_pc"] == 0 and chords[0]["quality"] == "maj"
    assert all("root_pc" in c and "quality" in c for c in chords)
    # Manual mode additions: chord spelling for the edited export, the per-hop
    # contour for the reference strip, and the grid resolution for the editor.
    assert all("root_name" in c for c in chords)
    assert body["subdiv"] == 4
    frames = body["frames"]
    assert isinstance(frames, list) and len(frames) > 0  # CREPE default produces frames
    assert all({"t", "f0", "conf"} <= set(fr) for fr in frames)


# ---- Manual mode: edited export + rescore (no audio, so no ffmpeg needed) -----

EDITED_SCALE = [
    {"midi": 60 + s, "start_ql": float(i), "dur_ql": 1.0}
    for i, s in enumerate([0, 2, 4, 5, 7, 9, 11, 12])
]


def test_export_edited_endpoint():
    import pretty_midi

    chords = [
        {"measure": 0, "start_ql": 0.0, "root_pc": 0, "root_name": "C",
         "quality": "maj", "symbol": "C", "roman": "I"},
    ]
    resp = client.post(
        "/api/export-edited",
        json={"notes": EDITED_SCALE, "tempo": 120.0, "time_sig": [4, 4],
              "key": "C major", "chords": chords},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "<score-partwise" in body["musicxml"] or "<score" in body["musicxml"]
    assert "<harmony" in body["musicxml"]  # posted chord round-trips into the sheet

    midi = base64.b64decode(body["midi_b64"])
    assert midi[:4] == b"MThd"
    pm = pretty_midi.PrettyMIDI(io.BytesIO(midi))
    notes = sorted(pm.instruments[0].notes, key=lambda n: n.start)
    assert [n.pitch for n in notes] == [60, 62, 64, 65, 67, 69, 71, 72]
    # Seconds are synthesized from the grid: start == start_ql * 60/bpm (0.5 s/beat).
    for i, n in enumerate(notes):
        assert n.start == pytest.approx(i * 0.5, abs=1e-6)


def test_rescore_endpoint():
    resp = client.post(
        "/api/rescore",
        json={"notes": EDITED_SCALE, "tempo": 120.0, "time_sig": [4, 4]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["key"] == "C major"
    assert len(body["chords"]) == 2  # 8 quarters span two 4/4 bars
    assert body["chords"][0]["symbol"] == "C" and body["chords"][0]["roman"] == "I"
    assert all("root_name" in c for c in body["chords"])
    assert body["key_candidates"] and body["key_candidates"][0]["name"] == "C major"


def test_export_edited_empty_rejected():
    assert client.post("/api/export-edited", json={"notes": []}).status_code == 400


def test_rescore_empty_rejected():
    assert client.post("/api/rescore", json={"notes": []}).status_code == 400


def test_manual_golden_in_sync():
    """The committed builder golden must match a fresh music21 generation, so the
    node builder test can trust it. Guards against silent music21 drift."""
    import json

    from tests.gen_manual_golden import MELODIES, OUT, _structural

    with open(OUT, encoding="utf-8") as fh:
        committed = json.load(fh)
    fresh = [_structural(m) for m in MELODIES]
    assert committed == fresh, "run tests/gen_manual_golden.py to refresh the golden"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")
def test_detect_tempo_endpoint():
    # The C-major-scale fixture is a steady stream of equal notes; the endpoint
    # should return a plausible BPM in range (not the default/garbage).
    with open(FIXTURE, "rb") as fh:
        resp = client.post(
            "/api/detect-tempo",
            files={"audio": ("c_major_scale.wav", fh, "audio/wav")},
        )
    assert resp.status_code == 200, resp.text
    bpm = resp.json()["bpm"]
    assert isinstance(bpm, int)
    assert 50 <= bpm <= 180


def test_detect_tempo_empty_rejected():
    resp = client.post(
        "/api/detect-tempo",
        files={"audio": ("empty.wav", b"", "audio/wav")},
    )
    assert resp.status_code == 400


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")
def test_analyze_endpoint():
    # Pitch Finder: the C-major-scale fixture should analyze to C major (8B) with a
    # full comprehensive-stats block.
    with open(FIXTURE, "rb") as fh:
        resp = client.post(
            "/api/analyze",
            files={"audio": ("c_major_scale.wav", fh, "audio/wav")},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["key"] == "C major"
    assert body["camelot"] == "8B"
    assert len(body["camelot_neighbors"]) == 3
    assert body["bpm"] > 0
    assert "advanced" in body and "pitch_class_distribution" in body["advanced"]
    assert len(body["advanced"]["pitch_class_distribution"]) == 12


def test_analyze_empty_rejected():
    resp = client.post(
        "/api/analyze",
        files={"audio": ("empty.wav", b"", "audio/wav")},
    )
    assert resp.status_code == 400


def test_key_endpoint():
    # Realtime voice monitor: a pitch-class histogram weighted toward the C-major
    # scale (tonic/dominant emphasized) should resolve to C major / 8B.
    hist = [0.0] * 12
    for pc, w in {0: 6, 2: 2, 4: 4, 5: 2, 7: 5, 9: 3, 11: 2}.items():
        hist[pc] = float(w)
    resp = client.post("/api/key", json={"histogram": hist})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["key"] == "C major", body["key"]
    assert body["camelot"] == "8B"
    assert len(body["candidates"]) >= 1
    assert all("key" in c and "camelot" in c and "score" in c for c in body["candidates"])


def test_key_endpoint_rejects_bad_input():
    # Wrong length and an all-zero histogram are both 400.
    assert client.post("/api/key", json={"histogram": [1, 2, 3]}).status_code == 400
    assert client.post("/api/key", json={"histogram": [0.0] * 12}).status_code == 400


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_empty_upload_rejected():
    resp = client.post(
        "/api/transcribe",
        files={"audio": ("empty.wav", b"", "audio/wav")},
        data={"bpm": 100},
    )
    assert resp.status_code == 400
