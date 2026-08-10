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
from tests.make_synthetic import FIXTURES, REALISTIC, build, intended_grid


def _note(start_ql: float, dur_ql: float) -> NoteEvent:
    return NoteEvent(start=0.0, end=0.0, midi=60, start_ql=start_ql, dur_ql=dur_ql)


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
