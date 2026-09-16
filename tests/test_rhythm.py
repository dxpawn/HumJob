"""Rhythm quantization metric + guard (Session 38).

Note F1 ignores durations and passes any onset within 50 ms, so rhythm was entirely
unmeasured - the eval read 1.0 while real hums came back with the wrong rhythm. These
lock the new rhythm metric (`evaluate.rhythm_scores`) and its ground truth
(`make_synthetic.intended_grid`). Most are pure/fast; one end-to-end guard keeps the
quantizer recovering the grid from an expressive on-grid take.
"""

from __future__ import annotations

from mouthtranscriber.config import Params
from mouthtranscriber.evaluate import rhythm_scores
from mouthtranscriber.model import NoteEvent
from mouthtranscriber.pipeline import transcribe_array
from mouthtranscriber.quantize import _snap_onsets
from tests.make_synthetic import FIXTURES, REALISTIC, build, intended_grid


def _note(start_ql: float, dur_ql: float) -> NoteEvent:
    return NoteEvent(start=0.0, end=0.0, midi=60, start_ql=start_ql, dur_ql=dur_ql)


# ---- onset snapping: the note-value-prior DP (quantize._snap_onsets) ----------------------
# These lock in the fix for the FIRST real recording (tests/data/recorded/scale_1): a hummed
# scale whose onsets jitter up to ~0.3 of a beat off. A per-note nearest-grid round snapped
# them to the wrong 1/16 (0.7 of a beat -> the 0.75 sixteenth), giving off-grid onsets and
# non-integer, tied-sliver durations. The DP trades a bounded timing deviation against a
# note-value complexity penalty, so a lone jittered note lands back on the beat.

def test_snap_onsets_recovers_beats_from_real_scale_jitter():
    # The actual onset positions of scale_1.webm, in grid steps relative to the first note
    # (start_seconds / (60/100 bpm) * quantize_subdiv). Every note aimed at a beat.
    x = [0.0, 2.79, 6.97, 11.53, 15.71, 20.35, 24.38, 28.56]
    steps = _snap_onsets(x, sub=4, p=Params())
    assert steps == [0, 4, 8, 12, 16, 20, 24, 28]  # all on beats, no stray sixteenths


def test_snap_onsets_preserves_genuine_eighths():
    # mixed_rhythm's onsets in grid steps (beats 0, 2, 3, 3.5, 4): the two eighth notes at
    # 3.0 and 3.5 must survive the beat prior, not collapse onto beats.
    steps = _snap_onsets([0.0, 8.0, 12.0, 14.0, 16.0], sub=4, p=Params())
    assert steps == [0, 8, 12, 14, 16]


def test_snap_onsets_keeps_a_fast_run_fast():
    # A genuine run of four sixteenths (steps 0,1,2,3): the IOI penalty is contextual, so the
    # run stays fast - all four onsets survive, strictly increasing and compact - rather than
    # collapsing onto beats ([0,4,8,12]). It may end on a beat (the run resolves to [0,1,2,4]),
    # which is fine; what must not happen is losing notes or spreading to whole beats.
    steps = _snap_onsets([0.0, 1.0, 2.0, 3.0], sub=4, p=Params())
    assert len(steps) == 4
    assert all(b > a for a, b in zip(steps, steps[1:]))  # strictly increasing, no merges
    assert steps[-1] <= 6  # stayed compact (a fast run), not spread to [0,4,8,12]


def test_intended_grid_mixed_rhythm():
    starts, durs = intended_grid("mixed_rhythm")
    assert starts == [0.0, 2.0, 3.0, 3.5, 4.0]
    assert durs == [2.0, 1.0, 0.5, 0.5, 2.0]


def test_intended_grid_skips_rests():
    # with_silence: (60,1)(62,1)(rest,1)(64,1)(65,1)(rest,1)(67,2); rests emit no note
    # but still advance the clock, so the notes after them keep their true positions.
    starts, durs = intended_grid("with_silence")
    assert starts == [0.0, 1.0, 3.0, 4.0, 6.0]
    assert durs == [1.0, 1.0, 1.0, 1.0, 2.0]


def test_rhythm_scores_perfect():
    starts, durs = [0.0, 1.0, 2.0], [1.0, 1.0, 1.0]
    est = [_note(s, d) for s, d in zip(starts, durs)]
    rs = rhythm_scores(starts, durs, est)
    assert rs.onset_acc == 1.0 and rs.dur_acc == 1.0 and rs.both_acc == 1.0
    assert rs.mean_onset_err_ql == 0.0


def test_rhythm_scores_detects_onset_and_dur_errors():
    starts, durs = [0.0, 1.0, 2.0], [1.0, 1.0, 1.0]
    # note 2 onset late (1.25), note 3 duration too long (2.0): each wrong on one axis.
    est = [_note(0.0, 1.0), _note(1.25, 1.0), _note(2.0, 2.0)]
    rs = rhythm_scores(starts, durs, est)
    assert rs.onset_acc == 2 / 3
    assert rs.dur_acc == 2 / 3
    assert rs.both_acc == 1 / 3   # only the first note is right on both axes
    assert abs(rs.mean_onset_err_ql - 0.25 / 3) < 1e-9


def test_rhythm_scores_count_mismatch_aligns_min():
    starts, durs = [0.0, 1.0, 2.0], [1.0, 1.0, 1.0]
    est = [_note(0.0, 1.0), _note(1.0, 1.0)]   # segmentation dropped the third note
    rs = rhythm_scores(starts, durs, est)
    assert rs.n_ref == 3 and rs.n_est == 2 and rs.aligned == 2
    assert rs.both_acc == 1.0   # the two that survived are correct


def test_quantize_recovers_grid_on_expressive_take():
    """End-to-end guard (slow, pYIN): an expressive but on-grid REALISTIC take must
    still quantize to the exact intended grid, so both_acc == 1.0. This is the number
    the wrong-BPM / jitter passes in eval_report degrade away from."""
    for fixture in ["mixed_rhythm", "twinkle"]:
        y, sr, _ = build(fixture, expr=REALISTIC)
        bpm = FIXTURES[fixture][0]
        score = transcribe_array(y, Params(sr=sr), tempo_bpm=bpm).score
        starts, durs = intended_grid(fixture)
        rs = rhythm_scores(starts, durs, score.notes)
        assert rs.both_acc == 1.0, (fixture, rs)
