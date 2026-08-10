"""Regression for grid-aware segmentation on the REALISTIC take (Session 35/36).

These are the cases the hardened eval exposed and grid-aware segmentation fixed: a
soft, voiced "d" closure (no silence) between two SAME-pitch notes used to merge them
into one. The fine-envelope + width + grid onset detector now splits them. Guards the
win against regressions. Slow (pYIN), so kept to the two decisive fixtures.
"""

from __future__ import annotations

from mouthtranscriber.config import Params
from mouthtranscriber.evaluate import note_scores, ref_notes_from_tuples
from mouthtranscriber.model import midi_to_name
from mouthtranscriber.pipeline import transcribe_array
from tests.make_synthetic import FIXTURES, REALISTIC, build


def _run(fixture):
    y, sr, refs = build(fixture, expr=REALISTIC)
    bpm = FIXTURES[fixture][0]
    score = transcribe_array(y, Params(sr=sr), tempo_bpm=bpm).score
    ref_notes = ref_notes_from_tuples([(r.start, r.end, r.midi) for r in refs])
    return score, ref_notes


def test_realistic_repeated_notes_split():
    """Five same-pitch "da"s with partial closures must come back as five C4s, not one."""
    score, refs = _run("repeated_notes")
    assert [midi_to_name(n.midi) for n in score.notes] == ["C4"] * 5
    assert note_scores(refs, score.notes).f1 == 1.0


def test_realistic_twinkle_repeats_split():
    """Twinkle's repeated pairs (C4 C4, G4 G4, ...) survive the expressive take intact."""
    score, refs = _run("twinkle")
    got = " ".join(midi_to_name(n.midi) for n in score.notes)
    assert got == "C4 C4 G4 G4 A4 A4 G4 F4 F4 E4 E4 D4 D4 C4", got
    assert note_scores(refs, score.notes).f1 >= 0.95
