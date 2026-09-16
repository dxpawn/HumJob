"""Chord-quality table (chords.QUALITIES) and its export path.

Pins the table to music21: every quality must construct a harmony.ChordSymbol whose pitch
classes equal the intervals we declare, and every quality must survive
export._add_chord_symbols into the MusicXML (which silently drops a kind music21 rejects).
This is the groundwork that lets a seventh chord spell, engrave, and export everywhere.
"""

from __future__ import annotations

import re

from mouthtranscriber.chords import QUALITIES
from mouthtranscriber.export import to_musicxml_string
from mouthtranscriber.model import Chord, NoteEvent, Score


def _note(start_ql: float, midi: int = 60) -> NoteEvent:
    n = NoteEvent(start=start_ql, end=start_ql + 4.0, midi=midi)
    n.start_ql = start_ql
    n.dur_ql = 4.0
    return n


def test_qualities_table_wellformed():
    for q, v in QUALITIES.items():
        assert set(v) >= {"intervals", "kind", "suffix", "roman"}, q
        assert isinstance(v["intervals"], tuple) and len(v["intervals"]) in (3, 4), q
        assert all(isinstance(i, int) for i in v["intervals"]), q
        assert isinstance(v["kind"], str) and v["kind"], q
        assert isinstance(v["suffix"], str), q
        assert isinstance(v["roman"], str), q


def test_every_kind_constructs_and_matches_intervals():
    from music21 import harmony

    for q, v in QUALITIES.items():
        cs = harmony.ChordSymbol(root="C", kind=v["kind"])   # C -> pc 0
        got = set(int(pc) for pc in cs.pitchClasses)
        want = set(iv % 12 for iv in v["intervals"])
        assert got == want, f"{q}: music21 pcs {got} != declared {want}"


def test_export_emits_every_kind_no_drops():
    # One whole note + one chord per measure, one measure per quality.
    quals = list(QUALITIES)
    notes = [_note(m * 4.0) for m in range(len(quals))]
    chords = [
        Chord(measure=m, start_ql=m * 4.0, root_pc=7, root_name="G", quality=q,
              symbol="G" + QUALITIES[q]["suffix"], roman="")
        for m, q in enumerate(quals)
    ]
    xml = to_musicxml_string(Score(notes=notes, key="C major", time_sig=(4, 4), chords=chords))
    kinds = re.findall(r"<kind[^>]*>([^<]*)</kind>", xml)
    assert len(kinds) == len(quals), f"expected {len(quals)} <harmony>, got {len(kinds)}"
    assert kinds == [QUALITIES[q]["kind"] for q in quals]


def test_triad_defaults_unchanged():
    # The three triads keep their exact prior intervals/kind/suffix (no behaviour change).
    assert QUALITIES["maj"]["intervals"] == (0, 4, 7)
    assert QUALITIES["min"]["kind"] == "minor"
    assert QUALITIES["dim"]["suffix"] == "dim"
    assert QUALITIES["maj"]["suffix"] == ""
