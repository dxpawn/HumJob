"""Unit tests for the diagnostic error-rates (EVALUATION UPGRADE §1).

These pin the failure-mode metrics that keep a diversified corpus discriminative once the
aggregate note F1 saturates: signed over/under-split rate, octave-error rate, same-pitch
repeat recall, and the tied-sliver / off-grid rate. They use tiny hand-built note stubs so
they are fast and need no pipeline run.
"""

from __future__ import annotations

from mouthtranscriber.evaluate import diagnostic_scores, ref_notes_from_tuples


class _Est:
    """Minimal stand-in for a quantised NoteEvent (midi + grid position)."""

    def __init__(self, midi, start_ql=0.0, dur_ql=1.0):
        self.midi = midi
        self.start_ql = start_ql
        self.dur_ql = dur_ql


def _ref(midis):
    # Reference notes: pitch is all the diagnostics read from the reference.
    return ref_notes_from_tuples([(float(i), float(i) + 1.0, m) for i, m in enumerate(midis)])


def _est(midis, starts=None, durs=None):
    starts = starts if starts is not None else [float(i) for i in range(len(midis))]
    durs = durs if durs is not None else [1.0] * len(midis)
    return [_Est(m, s, d) for m, s, d in zip(midis, starts, durs)]


def test_perfect_transcription_is_all_clean():
    ref = _ref([60, 62, 64, 65])
    est = _est([60, 62, 64, 65])
    d = diagnostic_scores(ref, est)
    assert d.count_delta == 0
    assert d.split_rate == 0.0
    assert d.octave_error_rate == 0.0
    assert d.off_grid_rate == 0.0
    assert d.repeat_recall == 1.0  # no repeats -> vacuously perfect
    assert d.n_repeat_pairs == 0


def test_over_split_is_positive_rate():
    ref = _ref([60, 62, 64, 65])          # 4 intended
    est = _est([60, 60, 62, 64, 65])      # a held note shattered -> 5
    d = diagnostic_scores(ref, est)
    assert d.count_delta == 1
    assert d.split_rate == 0.25           # +1 / 4
    assert d.aligned == 4


def test_under_split_is_negative_rate():
    ref = _ref([60, 62, 64, 65])          # 4 intended
    est = _est([60, 64, 65])              # two merged -> 3
    d = diagnostic_scores(ref, est)
    assert d.count_delta == -1
    assert d.split_rate == -0.25          # -1 / 4


def test_octave_error_rate_counts_exact_octaves_only():
    ref = _ref([60, 62, 64, 65])
    est = _est([72, 62, 64, 66])          # note 0 up an octave; note 3 off by 1 (not octave)
    d = diagnostic_scores(ref, est)
    assert d.octave_error_rate == 0.25    # 1 of 4 aligned is an exact octave
    # a plain wrong pitch (66 vs 65, +1) is NOT counted as an octave error.


def test_repeat_recall_full_when_repeats_survive():
    ref = _ref([60, 60, 67, 67, 69])      # two same-pitch pairs: (60,60) and (67,67)
    est = _est([60, 60, 67, 67, 69])
    d = diagnostic_scores(ref, est)
    assert d.n_repeat_pairs == 2
    assert d.repeat_recall == 1.0


def test_repeat_recall_drops_when_a_repeat_merges():
    ref = _ref([60, 60, 67, 67, 69])      # two repeat pairs
    est = _est([60, 67, 67, 69])          # first pair merged into ONE C4
    d = diagnostic_scores(ref, est)
    assert d.n_repeat_pairs == 2
    assert d.repeat_recall == 0.5         # the (67,67) pair still reads as two notes; (60,60) gone


def test_repeat_recall_counts_are_position_independent():
    # A merge early in the phrase must not poison a repeat that survives later on.
    ref = _ref([60, 62, 64, 64])          # one repeat pair, at the end
    est = _est([60, 64, 64])              # notes 62/64 mis-segmented earlier, repeat intact
    d = diagnostic_scores(ref, est)
    assert d.n_repeat_pairs == 1
    assert d.repeat_recall == 1.0         # the surviving (64,64) pair is credited


def test_repeat_recall_capped_at_one_under_over_split():
    ref = _ref([60, 60, 67])              # one repeat pair
    est = _est([60, 60, 60, 67])          # over-split adds a spurious equal-pair
    d = diagnostic_scores(ref, est)
    assert d.repeat_recall == 1.0         # capped; the over-split shows up in split_rate
    assert d.split_rate > 0


def test_off_grid_rate_flags_tied_slivers():
    ref = _ref([60, 62, 64])
    # note 1's duration 0.75 and note 2's onset 2.25 are off the eighth grid (slivers).
    est = _est([60, 62, 64], starts=[0.0, 1.0, 2.25], durs=[1.0, 0.75, 1.0])
    d = diagnostic_scores(ref, est)
    assert abs(d.off_grid_rate - (2 / 3)) < 1e-9


def test_off_grid_rate_allows_eighths_and_none_is_off_grid():
    ref = _ref([60, 62])
    est = _est([60, 62], starts=[0.0, 1.5], durs=[0.5, 1.0])  # both on the eighth grid
    assert diagnostic_scores(ref, est).off_grid_rate == 0.0
    # a note with no grid position (None) counts as off-grid.
    bad = _est([60, 62])
    bad[0].dur_ql = None
    assert diagnostic_scores(ref, bad).off_grid_rate == 0.5


def test_empty_inputs_do_not_crash():
    d = diagnostic_scores(_ref([]), _est([]))
    assert d.n_ref == 0 and d.n_est == 0
    assert d.repeat_recall == 1.0
    assert d.off_grid_rate == 0.0
