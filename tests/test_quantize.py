"""Rhythm quantization tests (PLAN §5.7, milestone M4).

Assert the quantizer recovers the exact note values the fixtures were built with,
and that the notation exporter round-trips through MusicXML with correct durations,
rests, key signature, and enharmonic spelling.
"""

from __future__ import annotations

import pytest

from mouthtranscriber import export as export_mod
from mouthtranscriber.config import Params
from mouthtranscriber.pipeline import transcribe_array
from tests.make_synthetic import FIXTURES, build

# fixture -> expected (start_ql, dur_ql) per note
EXPECTED = {
    "c_major_scale": [(float(i), 1.0) for i in range(8)],
    "mixed_rhythm": [(0.0, 2.0), (2.0, 1.0), (3.0, 0.5), (3.5, 0.5), (4.0, 2.0)],
    "twinkle": [
        (0.0, 1.0), (1.0, 1.0), (2.0, 1.0), (3.0, 1.0), (4.0, 1.0), (5.0, 1.0),
        (6.0, 2.0), (8.0, 1.0), (9.0, 1.0), (10.0, 1.0), (11.0, 1.0),
        (12.0, 1.0), (13.0, 1.0), (14.0, 2.0),
    ],
    "with_silence": [(0.0, 1.0), (1.0, 1.0), (3.0, 1.0), (4.0, 1.0), (6.0, 2.0)],
}


def _transcribe(name):
    bpm = FIXTURES[name][0]
    y, sr, _ = build(name)
    return transcribe_array(y, Params(sr=sr), tempo_bpm=bpm).score


@pytest.mark.parametrize("name,expected", EXPECTED.items())
def test_quantized_durations(name, expected):
    score = _transcribe(name)
    got = [(round(n.start_ql, 3), round(n.dur_ql, 3)) for n in score.notes]
    assert got == expected, f"{name}: {got}"


def test_musicxml_roundtrip_twinkle():
    """The exported MusicXML must parse back to the same notes and durations."""
    from music21 import converter, note as m21note

    score = _transcribe("twinkle")
    xml = export_mod.to_musicxml_string(score)
    reparsed = converter.parse(xml)
    # Melody Notes only — chord symbols (M6) are Harmony objects, not notes.
    melody = reparsed.flatten().getElementsByClass(m21note.Note)
    notes = [(n.name, float(n.duration.quarterLength)) for n in melody]
    expected = (
        [("C", 1.0), ("C", 1.0), ("G", 1.0), ("G", 1.0), ("A", 1.0), ("A", 1.0),
         ("G", 2.0), ("F", 1.0), ("F", 1.0), ("E", 1.0), ("E", 1.0),
         ("D", 1.0), ("D", 1.0), ("C", 2.0)]
    )
    assert notes == expected


def test_flat_key_uses_flat_spelling():
    """A note in a flat key should be spelled with flats, not sharps."""
    from music21 import converter, note as m21note

    score = _transcribe("c_major_scale")
    # Force a flat key and a black-key note, then check the spelling.
    score.key = "F minor"
    score.notes[0].midi = 70  # A#4 / Bb4
    score.chords = []  # isolate the note spelling from chord-symbol spelling
    xml = export_mod.to_musicxml_string(score)
    reparsed = converter.parse(xml)
    first = next(iter(reparsed.flatten().getElementsByClass(m21note.Note)))
    assert first.pitch.accidental is not None
    assert first.pitch.accidental.name == "flat", first.pitch.nameWithOctave


def test_svg_sheet_is_generated(tmp_path):
    score = _transcribe("twinkle")
    out = tmp_path / "twinkle.svg"
    export_mod.render_sheet_svg(score, str(out))
    text = out.read_text(encoding="utf-8")
    assert text.lstrip().startswith("<svg") or "<svg" in text[:200]
    assert len(text) > 2000  # non-trivial engraving
