"""Print a precision/recall/F1 table over all synthetic fixtures (PLAN §6).

Run:  python tests/eval_report.py
This is the standard "where do we stand" report for iterating on the DSP. It runs
the pipeline once per fixture and reports note metrics, detected key, tuning
offset, and per-clip latency.

Note passes (pitch + onset F1):
  * CLEAN     — the gentle take the regression gate expects at F1 = 1.0.
  * REALISTIC — an expressive take (wide vibrato, tremolo, pitch drift, off-grid
    onsets, partial "d" closures). This is where the segmenter actually struggles;
    its mean F1 is the number to drive up. See tests/make_synthetic.py:REALISTIC.

Rhythm passes (quantize vs the intended grid):
  Note F1 ignores durations and passes any onset within 50 ms, so it says nothing
  about rhythm. These score the quantized start_ql/dur_ql against the intended grid
  (evaluate.rhythm_scores + make_synthetic.intended_grid) under the two things that
  actually break real hums: human timing jitter, and a wrong BPM (the tied-sliver
  cause). ``both_acc`` (right beat AND right printed length) is the headline.
"""

from __future__ import annotations

import time
from dataclasses import replace

from mouthtranscriber.config import Params
from mouthtranscriber.evaluate import note_scores, ref_notes_from_tuples, rhythm_scores
from mouthtranscriber.pipeline import transcribe_array
from tests.make_synthetic import FIXTURES, REALISTIC, build, intended_grid

MELODIES = [
    "c_major_scale",
    "a_minor_scale",
    "arpeggio",
    "repeated_notes",
    "with_silence",
    "octave_leaps",
    "twinkle",
    "mixed_rhythm",
]


def _row(fixture: str, kwargs: dict) -> float:
    y, sr, refs = build(fixture, **kwargs)
    ref_notes = ref_notes_from_tuples([(r.start, r.end, r.midi) for r in refs])
    bpm = FIXTURES[fixture][0]

    t0 = time.perf_counter()
    analysis = transcribe_array(y, Params(sr=sr), tempo_bpm=bpm)
    ms = (time.perf_counter() - t0) * 1000

    s = note_scores(ref_notes, analysis.score.notes)
    label = fixture + ("*" if kwargs else "")
    key = (analysis.score.key or "-")[:10]
    print(
        f"{label:<28} {s.precision:5.2f} {s.recall:5.2f} {s.f1:5.2f} "
        f"{key:<10} {analysis.score.tuning_offset_cents:+5.0f}c "
        f"{s.n_ref:>3}/{s.n_est:<3} {ms:6.0f}"
    )
    return s.f1


def _pass(title: str, cases: list[tuple[str, dict]]) -> float:
    print(f"\n=== {title} ===")
    print(f"{'fixture':<28} {'P':>5} {'R':>5} {'F1':>5} {'key':<10} {'tune':>6} {'ref/est':>7} {'ms':>6}")
    print("-" * 84)
    f1s = [_row(fx, kw) for fx, kw in cases]
    print("-" * 84)
    mean = sum(f1s) / len(f1s)
    print(f"mean F1 = {mean:.3f}")
    return mean


def _rhythm_row(fixture: str, expr, bpm_factor: float) -> float:
    """Score one fixture's quantized rhythm against the intended grid.

    ``bpm_factor`` != 1.0 tells the pipeline a wrong tempo (a BPM detection/entry
    error) while the audio is rendered at the true BPM, so the metric measures the
    resulting drift against the true grid.
    """
    y, sr, _refs = build(fixture, expr=expr)
    true_bpm = FIXTURES[fixture][0]
    score = transcribe_array(y, Params(sr=sr), tempo_bpm=true_bpm * bpm_factor).score
    starts, durs = intended_grid(fixture)
    rs = rhythm_scores(starts, durs, score.notes)
    err_ms = rs.mean_onset_err_ql * (60000.0 / true_bpm)
    print(
        f"{fixture:<20} {rs.onset_acc:5.2f} {rs.dur_acc:5.2f} {rs.both_acc:6.2f} "
        f"{err_ms:8.0f} {rs.n_ref:>3}/{rs.n_est:<3}"
    )
    return rs.both_acc


def _rhythm_pass(title: str, expr, bpm_factor: float = 1.0) -> float:
    print(f"\n=== RHYTHM: {title} ===")
    print(f"{'fixture':<20} {'on':>5} {'dur':>5} {'both':>6} {'err(ms)':>8} {'ref/est':>7}")
    print("-" * 54)
    accs = [_rhythm_row(m, expr, bpm_factor) for m in MELODIES]
    print("-" * 54)
    mean = sum(accs) / len(accs)
    print(f"mean both_acc = {mean:.3f}")
    return mean


def main() -> None:
    clean = _pass("CLEAN (gate: 0.95)", [(m, {}) for m in MELODIES])
    realistic = _pass(
        "REALISTIC (wide vibrato, tremolo, drift, off-grid, partial closures)",
        [(m, {"expr": REALISTIC}) for m in MELODIES],
    )
    print(f"\nclean mean F1 = {clean:.3f}   realistic mean F1 = {realistic:.3f}"
          f"   (gap = {clean - realistic:.3f})")

    r_jitter = _rhythm_pass(
        "human timing jitter (+-30 ms, correct BPM)",
        replace(REALISTIC, timing_jitter_s=0.03),
    )
    r_bpm = _rhythm_pass(
        "BPM mismatch (+5%, on-grid timing)", REALISTIC, bpm_factor=1.05
    )
    print(f"\nrhythm both_acc - jitter = {r_jitter:.3f}   wrong-BPM = {r_bpm:.3f}")


if __name__ == "__main__":
    main()
