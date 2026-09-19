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


@dataclass
class DiagnosticScores:
    """Failure-mode error-rates that the aggregate note F1 hides (EVALUATION UPGRADE §1).

    Note F1 collapses every kind of mistake into one number, and on our synthetic corpus
    it is pinned at 1.000, so it can no longer tell a better segmenter from a worse one.
    These break the output apart into the specific failures that map onto the real
    complaints, so a diversified corpus stays discriminative:

    * ``split_rate`` - signed (est - ref) / ref. Positive is over-splitting (a held note
      shattered into slivers), negative is under-splitting (repeats merged / onsets missed).
    * ``octave_error_rate`` - of the positionally aligned notes, the fraction off by exactly
      one or more octaves (``est - ref`` a non-zero multiple of 12). Mirrors the ``oct_off``
      flag in tests/diagnose_recorded.py. Inputs must share a register (the synthetic corpus
      has exact ground truth, so no global shift is removed here).
    * ``repeat_recall`` - of the reference's adjacent same-pitch pairs (the "da da" repeats),
      the fraction that survive as two distinct notes. This is the "merged repeats" bug:
      merging two equal notes into one removes an adjacent-equal pair, so recall drops. It is
      counted over the whole sequence (not by index) so one merge does not poison later
      repeats, and it is capped at the reference count so an unrelated over-split cannot push
      it above 1.0 (over-splitting is reported by ``split_rate``). 1.0 when the reference has
      no repeats.
    * ``off_grid_rate`` - fraction of estimated notes whose ``start_ql`` or ``dur_ql`` leaves
      the simple-note grid (a multiple of ``simple_ql``, an eighth note by default). A wrong
      BPM warps a clean quarter note into a value like 0.75 that needs a tie, so this is the
      "held note becomes tied slivers / off-grid onset" bug.

    Alignment is positional over the shorter list, the same style as ``rhythm_scores``; a
    count mismatch is surfaced via ``n_ref`` / ``n_est`` rather than hidden.
    """
    n_ref: int
    n_est: int
    count_delta: int            # est - ref (signed)
    split_rate: float           # count_delta / n_ref (signed; + over-split, - under-split)
    octave_error_rate: float    # aligned notes off by an exact octave
    repeat_recall: float        # ref same-pitch adjacencies preserved as two notes
    n_repeat_pairs: int         # adjacent equal-pitch pairs in the reference
    off_grid_rate: float        # est notes whose start_ql/dur_ql leave the simple grid
    aligned: int                # notes positionally compared (min of the two counts)


def _is_multiple(x: float, base: float, tol: float) -> bool:
    """Is ``x`` an integer multiple of ``base`` (within ``tol``)? NaN/None -> False."""
    if x is None or x != x or base <= 0:
        return False
    r = abs(x) / base
    return abs(r - round(r)) * base <= tol


def diagnostic_scores(
    ref_notes,
    est_notes,
    simple_ql: float = 0.5,
    tol_ql: float = 1e-3,
) -> DiagnosticScores:
    """Compute the failure-mode error-rates in ``DiagnosticScores`` (see its docstring).

    ``ref_notes`` need only ``.midi``; ``est_notes`` need ``.midi`` plus ``.start_ql`` /
    ``.dur_ql`` (a ``None``/NaN value counts as off-grid). ``simple_ql`` is the coarsest
    grid the intended rhythm lives on (0.5 = eighth note for our corpus, on which no
    fixture uses a finer value), so a note landing off it is a quantisation sliver.
    """
    ref_midis = [int(n.midi) for n in ref_notes]
    est_midis = [int(n.midi) for n in est_notes]
    n_ref, n_est = len(ref_midis), len(est_midis)
    m = min(n_ref, n_est)

    count_delta = n_est - n_ref
    split_rate = (count_delta / n_ref) if n_ref else 0.0

    oct_err = sum(
        1 for i in range(m)
        if est_midis[i] != ref_midis[i] and (est_midis[i] - ref_midis[i]) % 12 == 0
    )
    octave_error_rate = (oct_err / m) if m else 0.0

    n_repeat_pairs = sum(1 for i in range(n_ref - 1) if ref_midis[i] == ref_midis[i + 1])
    est_repeat_pairs = sum(1 for i in range(n_est - 1) if est_midis[i] == est_midis[i + 1])
    # Count-based (position-independent): how many adjacent same-pitch pairs survived,
    # capped at the reference count so an unrelated over-split cannot inflate it.
    repeat_recall = (min(est_repeat_pairs, n_repeat_pairs) / n_repeat_pairs
                     if n_repeat_pairs else 1.0)

    off = 0
    for n in est_notes:
        on_ok = _is_multiple(getattr(n, "start_ql", None), simple_ql, tol_ql)
        du_ok = _is_multiple(getattr(n, "dur_ql", None), simple_ql, tol_ql)
        if not (on_ok and du_ok):
            off += 1
    off_grid_rate = (off / n_est) if n_est else 0.0

    return DiagnosticScores(
        n_ref=n_ref,
        n_est=n_est,
        count_delta=count_delta,
        split_rate=split_rate,
        octave_error_rate=octave_error_rate,
        repeat_recall=repeat_recall,
        n_repeat_pairs=n_repeat_pairs,
        off_grid_rate=off_grid_rate,
        aligned=m,
    )


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
