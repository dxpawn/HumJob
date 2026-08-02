"""Backend-agnostic note consolidation (mouthtranscriber/consolidate.py).

The unit under test is the pure function that fuses over-segmented fragments of one
held note — the failure the user hit on EVERY pitch engine (one note -> many short,
wrong-pitch notes). These tests craft NoteEvent lists directly (no audio) so they're
fast and engine-independent, and pin the exact merge/keep boundaries.
"""

from __future__ import annotations

from mouthtranscriber.config import Params
from mouthtranscriber.consolidate import consolidate_notes
from mouthtranscriber.model import NoteEvent


def _n(start, end, raw):
    return NoteEvent(start=start, end=end, midi=round(raw), raw_midi=raw)


def test_vibrato_fragments_at_neighbouring_semitones_merge():
    # One held note whose vibrato made the segmenter round fragments to 60/61/60.
    # The exact-pitch merge could never fuse these; the tolerant pass does.
    notes = [_n(0.0, 0.2, 60.3), _n(0.2, 0.4, 60.6), _n(0.4, 0.7, 60.2)]
    out = consolidate_notes(notes, Params())
    assert len(out) == 1
    assert out[0].start == 0.0 and out[0].end == 0.7
    assert out[0].midi == 60  # duration-weighted mean, not the last fragment


def test_basic_pitch_same_pitch_fragments_merge():
    # basic-pitch style: identical integer pitch, touching, salience-dip splits.
    notes = [_n(0.0, 0.25, 67.0), _n(0.25, 0.5, 67.0), _n(0.5, 0.9, 67.0)]
    out = consolidate_notes(notes, Params())
    assert len(out) == 1
    assert out[0].end == 0.9


def test_real_rearticulation_survives():
    # Same pitch but a real devoiced "da" gap (80 ms > gap tol) -> two notes.
    notes = [_n(0.0, 0.4, 60.0), _n(0.48, 0.9, 60.0)]
    out = consolidate_notes(notes, Params())
    assert len(out) == 2


def test_melodic_step_survives():
    # Touching, but a whole tone apart -> a real step, past the pitch tolerance.
    notes = [_n(0.0, 0.4, 60.0), _n(0.4, 0.8, 62.0)]
    out = consolidate_notes(notes, Params())
    assert len(out) == 2


def test_octave_error_not_merged():
    notes = [_n(0.0, 0.3, 60.0), _n(0.3, 0.6, 72.0)]
    out = consolidate_notes(notes, Params())
    assert len(out) == 2


def test_disabled_is_a_noop():
    notes = [_n(0.0, 0.2, 60.0), _n(0.2, 0.4, 60.0)]
    out = consolidate_notes(notes, Params(consolidate=False))
    assert len(out) == 2
