"""Backend API test (PLAN §3, milestone M5).

Exercises the same /api/transcribe route the browser UI posts to: upload a
fixture WAV, get back key + notes + an engraved SVG + MIDI/MusicXML. Requires
ffmpeg on PATH (the endpoint decodes uploads through it).
"""

from __future__ import annotations

import base64
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


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_empty_upload_rejected():
    resp = client.post(
        "/api/transcribe",
        files={"audio": ("empty.wav", b"", "audio/wav")},
        data={"bpm": 100},
    )
    assert resp.status_code == 400
