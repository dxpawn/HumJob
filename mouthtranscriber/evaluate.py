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


@dataclass
class RhythmScores:
    """How well quantization recovered the intended grid (PLAN §5.7).

    Note F1 (above) ignores durations and passes any onset within 50 ms, so it says
    nothing about rhythm. These score the quantized ``start_ql`` / ``dur_ql`` against
    the intended grid positions: did each note land on the right beat, with the right
    printed length. ``both_acc`` (correct onset AND duration) is the headline - it is
    what makes a hum notate as the rhythm the user actually performed.
    """
    onset_acc: float           # fraction of aligned notes whose onset snapped correctly
    dur_acc: float             # fraction whose duration snapped correctly
    both_acc: float            # fraction correct on BOTH (headline)
    mean_onset_err_ql: float   # mean |onset error| in quarter-note units
    n_ref: int
    n_est: int
    aligned: int               # notes actually compared (min of the two counts)


def rhythm_scores(
    ref_starts_ql: list[float],
    ref_durs_ql: list[float],
    est_notes,
    tol_ql: float = 1e-3,
) -> RhythmScores:
    """Compare quantized onsets/durations to the intended grid, aligned by order.

    ``ref_starts_ql`` / ``ref_durs_ql`` are the intended positions of the *sounded*
    notes, anchored so the first is 0 (see make_synthetic.intended_grid) - the same
    anchor the quantizer uses. Alignment is positional over the shorter of the two
    lists; a count mismatch (segmentation added/dropped a note) is surfaced via
    ``n_ref``/``n_est``/``aligned`` rather than hidden, since it makes the alignment
    itself unreliable.
    """
    n_ref, n_est = len(ref_starts_ql), len(est_notes)
    m = min(n_ref, n_est)
    if m == 0:
        return RhythmScores(0.0, 0.0, 0.0, float("nan"), n_ref, n_est, 0)

    on_ok = dur_ok = both_ok = 0
    err_sum = 0.0
    for i in range(m):
        est = est_notes[i]
        on = abs(float(est.start_ql) - ref_starts_ql[i]) <= tol_ql
        du = abs(float(est.dur_ql) - ref_durs_ql[i]) <= tol_ql
        on_ok += on
        dur_ok += du
        both_ok += on and du
        err_sum += abs(float(est.start_ql) - ref_starts_ql[i])
    return RhythmScores(on_ok / m, dur_ok / m, both_ok / m, err_sum / m, n_ref, n_est, m)


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
