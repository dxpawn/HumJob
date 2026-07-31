"""Evaluation metrics for the transcription pipeline (PLAN §6).

Wraps mir_eval so we can put a number on "does it work". The headline metric is
note-level F1 (an estimated note counts as correct when its onset lands within a
tolerance of a reference note AND the pitch matches within 50 cents). Offsets are
ignored (``offset_ratio=None``) because melody transcription cares about the note,
not its exact release.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .model import NoteEvent, midi_to_hz


@dataclass
class NoteScores:
    precision: float
    recall: float
    f1: float
    n_ref: int
    n_est: int


def _intervals_pitches(notes) -> tuple[np.ndarray, np.ndarray]:
    if not notes:
        return np.zeros((0, 2)), np.zeros(0)
    intervals = np.array([[float(n.start), float(max(n.end, n.start + 1e-3))] for n in notes])
    pitches = np.array([midi_to_hz(int(n.midi)) for n in notes])
    return intervals, pitches


def note_scores(ref_notes, est_notes, onset_tolerance: float = 0.05) -> NoteScores:
    """Note precision/recall/F1 (onset within tolerance, pitch within 50 cents)."""
    import mir_eval

    ref_int, ref_p = _intervals_pitches(ref_notes)
    est_int, est_p = _intervals_pitches(est_notes)

    if len(ref_int) == 0 and len(est_int) == 0:
        return NoteScores(1.0, 1.0, 1.0, 0, 0)
    if len(ref_int) == 0 or len(est_int) == 0:
        return NoteScores(0.0, 0.0, 0.0, len(ref_int), len(est_int))

    p, r, f, _ = mir_eval.transcription.precision_recall_f1_overlap(
        ref_int,
        ref_p,
        est_int,
        est_p,
        onset_tolerance=onset_tolerance,
        pitch_tolerance=50.0,
        offset_ratio=None,
    )
    return NoteScores(float(p), float(r), float(f), len(ref_int), len(est_int))


def ref_notes_from_tuples(tuples) -> list[NoteEvent]:
    """Build reference NoteEvents from (start, end, midi) tuples."""
    return [NoteEvent(start=s, end=e, midi=int(m), raw_midi=float(m)) for (s, e, m) in tuples]
