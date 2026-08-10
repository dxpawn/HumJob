"""Spectral octave-error correction (mouthtranscriber/octave.py).

The pure tests build a real tone and then hand the corrector frames that lie about
the pitch the way pYIN's Viterbi does on a legato line (reports the subharmonic, an
octave low). They need no pitch tracker, so they are fast. One slow end-to-end test
guards the octave_leaps fixture the correction was built for.
"""

from __future__ import annotations

import numpy as np

from mouthtranscriber.config import Params
from mouthtranscriber.model import Frame, hz_to_midi, midi_to_hz, midi_to_name
from mouthtranscriber import octave
from mouthtranscriber import preprocess as preprocess_mod
from mouthtranscriber.evaluate import note_scores, ref_notes_from_tuples
from mouthtranscriber.pipeline import transcribe_array
from tests.make_synthetic import FIXTURES, REALISTIC, Expr, _synth_note, build


def _frames_over(y: np.ndarray, p: Params, report_hz: float) -> list[Frame]:
    """One frame per pipeline hop, each falsely reporting ``report_hz`` as f0."""
    hop = p.hop_length
    n = len(y) // hop
    return [Frame(t=i * p.hop_s, f0=report_hz, confidence=0.9, rms=0.5) for i in range(n)]


def _median_corrected(y, p, report_hz):
    frames = octave.correct_octaves(_frames_over(y, p, report_hz), y, p)
    vals = [hz_to_midi(f.f0) for f in frames if not np.isnan(f.f0)]
    return float(np.median(vals))


def test_subharmonic_is_doubled():
    """A C5 tone reported an octave low (as C4) is corrected back up to C5."""
    p = Params()
    tone = _synth_note(72.0, 0.5, p.sr, Expr(), scoop=False, rng=np.random.default_rng(0))
    med = _median_corrected(tone, p, midi_to_hz(60))   # tracker lies: says C4
    assert round(med) == 72, midi_to_name(int(round(med)))


def test_true_fundamental_is_left_alone():
    """A genuine C4 tone reported correctly as C4 must NOT be pushed up an octave."""
    p = Params()
    tone = _synth_note(60.0, 0.5, p.sr, Expr(), scoop=False, rng=np.random.default_rng(0))
    med = _median_corrected(tone, p, midi_to_hz(60))
    assert round(med) == 60, midi_to_name(int(round(med)))


def test_missing_fundamental_is_left_alone():
    """A weak/missing h1 must not be mistaken for a subharmonic: odd energy at 3f/5f
    still marks C4 as the true fundamental, so it stays C4 (no false octave-up)."""
    p = Params()
    tone = _synth_note(60.0, 0.5, p.sr, Expr(), scoop=False, rng=np.random.default_rng(0))
    # Strip the fundamental: high-pass well above C4 (262 Hz) but below its 2nd harmonic.
    weakh1 = preprocess_mod.preprocess(tone, p.sr, highpass_hz=330.0)
    med = _median_corrected(weakh1, p, midi_to_hz(60))
    assert round(med) == 60, midi_to_name(int(round(med)))


def test_disabled_is_a_noop():
    p = Params(octave_correct=False)
    tone = _synth_note(72.0, 0.5, p.sr, Expr(), scoop=False, rng=np.random.default_rng(0))
    med = _median_corrected(tone, p, midi_to_hz(60))
    assert round(med) == 60   # untouched: still the (wrong) reported C4


def test_octave_leaps_realistic_recovered():
    """End-to-end: the fixture that motivated the fix. Without correction pYIN reads
    every C5 as C4 (F1 ~ 0.44); with it the five notes come back exactly."""
    y, sr, refs = build("octave_leaps", expr=REALISTIC)
    bpm = FIXTURES["octave_leaps"][0]
    score = transcribe_array(y, Params(sr=sr), tempo_bpm=bpm).score
    assert [midi_to_name(n.midi) for n in score.notes] == ["C4", "C5", "C4", "C5", "C4"]
    ref_notes = ref_notes_from_tuples([(r.start, r.end, r.midi) for r in refs])
    assert note_scores(ref_notes, score.notes).f1 == 1.0
