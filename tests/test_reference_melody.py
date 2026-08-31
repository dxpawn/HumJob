"""Sing-Along melody reduction (Sing-Along tab, uploaded-file path).

Exercises the /api/reference-melody route the tab posts to: a possibly polyphonic score
comes back reduced to a single karaoke melody line (the skyline), with ties stripped so a
held note is one target. No audio / ffmpeg - scores are built in-memory.

Everything routes through the TestClient (verovio is engraved on the app's worker thread,
matching test_transpose.py) so the vendored verovio toolkit is only driven from one thread.
"""

from __future__ import annotations

import io
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from server.app import app

client = TestClient(app)


def _melody_over_bass():
    """A C-major score: melody C4 D4 E4 F4 (quarters) over a held C3 whole note.

    The bass sits below the melody the whole bar, so the skyline is exactly the melody;
    the held C3 must never appear in the reduced line.
    """
    from music21 import stream, note, meter, tempo, key as m21key

    sc = stream.Score()
    top = stream.Part()
    top.append(tempo.MetronomeMark(number=90))
    top.append(meter.TimeSignature("4/4"))
    top.append(m21key.Key("C"))
    for m in (60, 62, 64, 65):
        n = note.Note(m)
        n.quarterLength = 1
        top.append(n)
    low = stream.Part()
    bass = note.Note(48)
    bass.quarterLength = 4
    low.append(bass)
    sc.insert(0, top)
    sc.insert(0, low)
    return sc


def _tied_note():
    """A single C4 tied across a barline: two written notes, one sung target."""
    from music21 import stream, note, meter, tempo, tie

    p = stream.Part()
    p.append(tempo.MetronomeMark(number=120))
    p.append(meter.TimeSignature("4/4"))
    a = note.Note(60)
    a.quarterLength = 4
    b = note.Note(60)
    b.quarterLength = 4
    a.tie = tie.Tie("start")
    b.tie = tie.Tie("stop")
    p.append(a)
    p.append(b)
    return p


def _chord_score():
    """A bar of C-E-G triads (quarters). Skyline keeps only the top voice: G4 x4."""
    from music21 import stream, chord, meter, tempo

    p = stream.Part()
    p.append(tempo.MetronomeMark(number=100))
    p.append(meter.TimeSignature("4/4"))
    for _ in range(4):
        c = chord.Chord([60, 64, 67])
        c.quarterLength = 1
        p.append(c)
    return p


def _truncation_score():
    """A held E5 (2 ql) with a higher G5 (1 ql) entering at ql 1, in a second part.

    The skyline is E5 [0,1) then G5 [1,2): the higher note truncates the held one.
    Two parts (not one) keep the notes genuinely overlapping through a MusicXML roundtrip.
    """
    from music21 import stream, note, tempo, meter

    sc = stream.Score()
    top = stream.Part()
    top.append(tempo.MetronomeMark(number=100))
    top.append(meter.TimeSignature("4/4"))
    e = note.Note(76)   # E5
    e.quarterLength = 2
    top.append(e)
    high = stream.Part()
    high.insert(1.0, note.Note(79, quarterLength=1))  # G5 enters mid-way, above E5
    sc.insert(0, top)
    sc.insert(0, high)
    return sc


def _two_tempo_score():
    """Two tempo marks, so n_tempos == 2 (the client warns; v1 uses the first)."""
    from music21 import stream, note, tempo, meter

    p = stream.Part()
    p.append(tempo.MetronomeMark(number=80))
    p.append(meter.TimeSignature("4/4"))
    for m in (60, 62):
        n = note.Note(m)
        n.quarterLength = 1
        p.append(n)
    p.insert(2.0, tempo.MetronomeMark(number=140))
    for m in (64, 65):
        n = note.Note(m)
        n.quarterLength = 1
        p.append(n)
    return p


def _rest_only():
    from music21 import stream, note, meter

    p = stream.Part()
    p.append(meter.TimeSignature("4/4"))
    p.append(note.Rest(quarterLength=4))
    return p


def _bytes(obj, ext: str) -> bytes:
    fmt = "midi" if ext in (".mid", ".midi") else "musicxml"
    fd, path = tempfile.mkstemp(suffix=ext)
    os.close(fd)
    try:
        obj.write(fmt, fp=path)
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        os.unlink(path)


def _post(obj, ext: str):
    raw = _bytes(obj, ext)
    mime = "audio/midi" if ext in (".mid", ".midi") else "application/xml"
    return client.post(
        "/api/reference-melody",
        files={"file": ("sample" + ext, io.BytesIO(raw), mime)},
    )


@pytest.mark.parametrize("ext", [".mid", ".musicxml"])
def test_skyline_drops_bass(ext):
    resp = _post(_melody_over_bass(), ext)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["n_notes"] == 4
    assert [n["midi"] for n in body["melody"]] == [60, 62, 64, 65]
    # Non-overlapping, sorted by onset, quarter-note grid.
    starts = [n["start_ql"] for n in body["melody"]]
    assert starts == sorted(starts) == [0.0, 1.0, 2.0, 3.0]
    assert all(n["dur_ql"] == 1.0 for n in body["melody"])
    assert body["key"] == "C major"
    assert body["key_pc"] == 0 and body["key_mode"] == "major"
    assert body["tempo_bpm"] == 90.0
    assert body["n_tempos"] == 1
    assert body["time_sig"] == [4, 4]
    assert body["duration_ql"] == 4.0


@pytest.mark.parametrize("ext", [".mid", ".musicxml"])
def test_tie_is_one_target(ext):
    body = _post(_tied_note(), ext).json()
    # Two written notes tied -> a single 8-ql target, not two onsets.
    assert body["n_notes"] == 1
    assert body["melody"][0]["midi"] == 60
    assert body["melody"][0]["dur_ql"] == 8.0


def test_chord_keeps_top_voice():
    body = _post(_chord_score(), ".musicxml").json()
    assert body["n_notes"] == 4
    assert all(n["midi"] == 67 for n in body["melody"])  # G4, the chord top


def test_higher_note_truncates_held():
    body = _post(_truncation_score(), ".musicxml").json()
    midis = [n["midi"] for n in body["melody"]]
    assert midis == [76, 79]
    e, g = body["melody"]
    assert e["start_ql"] == 0.0 and e["dur_ql"] == 1.0   # E5 truncated at ql 1
    assert g["start_ql"] == 1.0 and g["dur_ql"] == 1.0


def test_multi_tempo_reported():
    body = _post(_two_tempo_score(), ".musicxml").json()
    assert body["n_tempos"] == 2
    assert body["tempo_bpm"] == 80.0   # v1 uses the first mark


def test_legato_overlap_keeps_lower_note():
    """A slight note-off overrun (legato / triplet-grid rounding) must NOT delete the next,
    lower note. Regression: a monophonic melody was losing notes to phantom overlaps."""
    from music21 import stream, note
    from mouthtranscriber import reference as reference_mod

    p = stream.Part()
    a = note.Note(72); a.quarterLength = 1.05   # C5, held slightly long
    p.insert(0.0, a)
    b = note.Note(71); b.quarterLength = 1.0    # B4 (lower) starts at 1.0, inside A's tail
    p.insert(1.0, b)
    mel = reference_mod.melody_notes(p)          # test the reducer directly (no roundtrip)
    assert [n["midi"] for n in mel] == [72, 71]  # both notes survive
    # the earlier note is clipped so the two do not overlap
    assert mel[0]["start_ql"] + mel[0]["dur_ql"] <= mel[1]["start_ql"] + 1e-6


def test_simultaneous_lower_voice_dropped():
    """A genuine simultaneous lower voice (same onset, large overlap) is masked, so the
    reduced line stays monophonic and accompaniment does not leak into the melody."""
    from music21 import stream, note
    from mouthtranscriber import reference as reference_mod

    p = stream.Part()
    hi = note.Note(72); hi.quarterLength = 2.0
    p.insert(0.0, hi)
    lo = note.Note(60); lo.quarterLength = 2.0   # same onset, a full 2-beat overlap
    p.insert(0.0, lo)
    mel = reference_mod.melody_notes(p)
    assert [n["midi"] for n in mel] == [72]      # only the top voice


def test_real_midi_loses_no_notes():
    """The reported file (CO HANG XOM.mid) is a monophonic melody whose notes were turning
    into rests. Every note must survive and the line must stay non-overlapping."""
    import os
    from music21 import converter
    from mouthtranscriber import reference as reference_mod

    path = os.path.join("testMaterials", "CO HANG XOM.mid")
    if not os.path.exists(path):
        import pytest as _pytest
        _pytest.skip("demo MIDI not present")
    score = converter.parse(path)
    flat = len(score.stripTies().flatten().notes)
    mel = reference_mod.melody_notes(score)
    assert len(mel) == flat, f"melody dropped {flat - len(mel)} of {flat} notes"
    for i in range(len(mel) - 1):
        assert mel[i]["start_ql"] + mel[i]["dur_ql"] <= mel[i + 1]["start_ql"] + 1e-6


def test_rest_only_is_400():
    resp = _post(_rest_only(), ".musicxml")
    assert resp.status_code == 400
    assert "no melody" in resp.json()["detail"].lower()


def test_empty_upload_is_400():
    resp = client.post(
        "/api/reference-melody",
        files={"file": ("empty.mid", io.BytesIO(b""), "audio/midi")},
    )
    assert resp.status_code == 400


def test_garbage_upload_is_400():
    resp = client.post(
        "/api/reference-melody",
        files={"file": ("junk.mid", io.BytesIO(b"not a real midi"), "audio/midi")},
    )
    assert resp.status_code == 400
