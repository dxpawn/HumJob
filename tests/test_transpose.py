"""Full-score file transposition (Transposer tab, uploaded-file path).

Exercises the /api/transpose-file route the tab posts to: a polyphonic score transposed
by N semitones comes back with every voice and the key signature shifted, an engraved
SVG, transposed MusicXML + MIDI, and a flat note list for playback. No audio / ffmpeg -
scores are built in-memory.

Everything routes through the TestClient (verovio is engraved on the app's worker thread,
matching test_server.py) rather than calling the module directly, so the vendored verovio
toolkit is only ever driven from one thread within the test process.
"""

from __future__ import annotations

import base64
import io
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from server.app import app

client = TestClient(app)


def _sample_score():
    """A tiny 2-part C-major score: melody C4 D4 E4 F4 over a C3 D3 E3 F3 lower voice."""
    from music21 import stream, note, meter, tempo, key as m21key

    sc = stream.Score()
    top = stream.Part()
    top.append(tempo.MetronomeMark(number=100))
    top.append(meter.TimeSignature("4/4"))
    top.append(m21key.Key("C"))
    for m in (60, 62, 64, 65):
        n = note.Note(m)
        n.quarterLength = 1
        top.append(n)
    low = stream.Part()
    for m in (48, 50, 52, 53):
        n = note.Note(m)
        n.quarterLength = 1
        low.append(n)
    sc.insert(0, top)
    sc.insert(0, low)
    return sc


def _score_bytes(ext: str) -> bytes:
    fmt = "midi" if ext in (".mid", ".midi") else "musicxml"
    fd, path = tempfile.mkstemp(suffix=ext)
    os.close(fd)
    try:
        _sample_score().write(fmt, fp=path)
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        os.unlink(path)


def _post(ext: str, semitones: int):
    raw = _score_bytes(ext)
    mime = "audio/midi" if ext in (".mid", ".midi") else "application/xml"
    resp = client.post(
        "/api/transpose-file",
        files={"file": ("sample" + ext, io.BytesIO(raw), mime)},
        data={"semitones": semitones},
    )
    return resp


@pytest.mark.parametrize("ext", [".mid", ".musicxml"])
def test_transpose_up_whole_tone(ext):
    resp = _post(ext, 2)  # C major -> D major
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["key"] == "D major"
    assert body["key_pc"] == 2 and body["key_mode"] == "major"
    assert body["tempo_bpm"] == 100.0
    assert body["time_sig"] == [4, 4]
    assert body["semitones"] == 2

    # Both voices present (polyphony preserved), every pitch up two semitones.
    assert body["n_notes"] == 8
    midis = sorted(n["midi"] for n in body["notes"])
    assert midis == [50, 52, 54, 55, 62, 64, 66, 67]

    assert "<svg" in body["svg"][:200]
    assert "<score" in body["musicxml"]
    assert base64.b64decode(body["midi_b64"])[:4] == b"MThd"


def test_transpose_identity():
    """semitones=0 is a faithful pass-through (the initial upload preview)."""
    body = _post(".mid", 0).json()
    assert body["key"] == "C major"
    assert sorted(n["midi"] for n in body["notes"]) == [48, 50, 52, 53, 60, 62, 64, 65]


def test_transpose_down_minor_third():
    body = _post(".musicxml", -3).json()  # C major -> A major
    assert body["key"] == "A major"
    assert body["semitones"] == -3
    assert body["n_notes"] == 8


def test_transpose_bad_upload():
    resp = client.post(
        "/api/transpose-file",
        files={"file": ("junk.mid", io.BytesIO(b"not a real midi"), "audio/midi")},
        data={"semitones": 0},
    )
    assert resp.status_code == 400
