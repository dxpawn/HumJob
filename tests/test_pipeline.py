"""Regression tests + the M2 "works excellently" gate (PLAN §6).

Runs the full pipeline over every synthetic fixture and asserts:
  - note-level F1 >= 0.95 (the headline bar)
  - the exact note sequence matches
  - key detection is correct
  - tuning correction rescues a deliberately-flat take
  - silence produces no phantom notes

These run on synthetic audio so they are deterministic and CI-friendly. Real
recordings get added under tests/data/recorded/ and evaluated the same way.
"""

from __future__ import annotations

import numpy as np
import pytest

from mouthtranscriber.config import Params
from mouthtranscriber.evaluate import note_scores, ref_notes_from_tuples
from mouthtranscriber.model import midi_to_name
from mouthtranscriber.pipeline import transcribe_array
from tests.make_synthetic import build

F1_GATE = 0.95

# (fixture, build-kwargs, expected note names, expected key prefix)
CASES = [
    ("c_major_scale", {}, "C4 D4 E4 F4 G4 A4 B4 C5", "C major"),
    ("a_minor_scale", {}, "A4 G4 F4 E4 D4 C4 B3 A3", "A minor"),
    ("arpeggio", {}, "C4 E4 G4 C5 G4 E4 C4", "C major"),
    ("repeated_notes", {}, "C4 C4 C4 C4 C4", None),
    ("with_silence", {}, "C4 D4 E4 F4 G4", "C major"),
    ("octave_leaps", {}, "C4 C5 C4 C5 C4", None),
    ("twinkle", {}, "C4 C4 G4 G4 A4 A4 G4 F4 F4 E4 E4 D4 D4 C4", "C major"),
    ("mixed_rhythm", {}, "C4 D4 E4 F4 G4", "C major"),
    ("c_major_scale", {"detune_semitones": -0.4}, "C4 D4 E4 F4 G4 A4 B4 C5", "C major"),
    ("twinkle", {"vibrato": True, "scoop": True},
     "C4 C4 G4 G4 A4 A4 G4 F4 F4 E4 E4 D4 D4 C4", "C major"),
]


_CACHE: dict = {}


def _run(fixture, kwargs):
    # pYIN is slow; each fixture is analyzed once and reused across test functions.
    cache_key = (fixture, tuple(sorted(kwargs.items())))
    if cache_key not in _CACHE:
        y, sr, refs = build(fixture, **kwargs)
        params = Params(sr=sr)
        analysis = transcribe_array(y, params)
        ref_notes = ref_notes_from_tuples([(r.start, r.end, r.midi) for r in refs])
        _CACHE[cache_key] = (analysis, ref_notes)
    return _CACHE[cache_key]


@pytest.mark.parametrize("fixture,kwargs,expected,key", CASES)
def test_note_f1_gate(fixture, kwargs, expected, key):
    analysis, ref_notes = _run(fixture, kwargs)
    scores = note_scores(ref_notes, analysis.score.notes)
    assert scores.f1 >= F1_GATE, (
        f"{fixture}{kwargs}: F1={scores.f1:.3f} "
        f"(P={scores.precision:.3f} R={scores.recall:.3f}, "
        f"ref={scores.n_ref} est={scores.n_est})"
    )


@pytest.mark.parametrize("fixture,kwargs,expected,key", CASES)
def test_exact_note_sequence(fixture, kwargs, expected, key):
    analysis, _ = _run(fixture, kwargs)
    got = " ".join(midi_to_name(n.midi) for n in analysis.score.notes)
    assert got == expected, f"{fixture}{kwargs}: got '{got}'"


@pytest.mark.parametrize("fixture,kwargs,expected,key", CASES)
def test_key_detection(fixture, kwargs, expected, key):
    if key is None:
        pytest.skip("ambiguous key by design")
    analysis, _ = _run(fixture, kwargs)
    assert analysis.score.key == key, (
        f"{fixture}{kwargs}: got key {analysis.score.key}, "
        f"candidates={analysis.score.key_candidates}"
    )


def test_flat_humming_is_tuned():
    """A take hummed ~40 cents flat still transcribes to the correct pitches."""
    analysis, ref_notes = _run("c_major_scale", {"detune_semitones": -0.4})
    assert analysis.score.tuning_offset_cents < -20  # detected the flatness
    scores = note_scores(ref_notes, analysis.score.notes)
    assert scores.f1 >= F1_GATE


def test_silence_makes_no_phantom_notes():
    """Pure noise / near-silence must not produce notes (PLAN §5.4)."""
    rng = np.random.default_rng(0)
    y = rng.normal(0, 10 ** (-48 / 20.0), 22050 * 2).astype(np.float32)
    analysis = transcribe_array(y, Params())
    assert len(analysis.score.notes) == 0, (
        f"phantom notes in silence: {[midi_to_name(n.midi) for n in analysis.score.notes]}"
    )
