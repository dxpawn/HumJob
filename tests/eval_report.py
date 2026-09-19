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

import glob
import json
import os
import shutil
import time
from dataclasses import replace

from mouthtranscriber.config import Params
from mouthtranscriber.evaluate import (
    diagnostic_scores,
    note_scores,
    ref_notes_from_tuples,
    rhythm_scores,
)
from mouthtranscriber.pipeline import transcribe_array
from mouthtranscriber.roundtrip import round_trip
from tests import corpus
from tests.make_synthetic import (
    FIXTURES,
    HARD,
    REALISTIC,
    build,
    grid_of_sequence,
    intended_grid,
    render_sequence,
)

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


def _diagnostic_pass(profile_name: str, expr) -> dict:
    """Diagnostic error-rates over the diversified corpus at one Expr profile (EVAL UPGRADE §1/§6).

    Once note F1 saturates it cannot separate a better segmenter from a worse one; these
    failure-mode rates (over/under-split, octave, repeat-recall, tied-sliver) stay
    discriminative. Printed as a per-profile summary so CLEAN / REALISTIC / HARD can be
    compared - they should NOT all be perfect any more.
    """
    full = corpus.build_corpus(include_fixtures=True)
    f1s, splits, absplits, octs, offs, reps = [], [], [], [], [], []
    collapsed = 0
    for _name, (bpm, seq) in full.items():
        y, sr, refs = render_sequence(seq, bpm, expr=expr)
        an = transcribe_array(y, Params(sr=sr), tempo_bpm=bpm)
        ref_notes = ref_notes_from_tuples([(r.start, r.end, r.midi) for r in refs])
        d = diagnostic_scores(ref_notes, an.score.notes)
        f1s.append(note_scores(ref_notes, an.score.notes).f1)
        splits.append(d.split_rate)
        absplits.append(abs(d.split_rate))
        octs.append(d.octave_error_rate)
        offs.append(d.off_grid_rate)
        if d.n_repeat_pairs:
            reps.append(d.repeat_recall)
        if d.n_est == 0:
            collapsed += 1

    def mean(xs):
        return sum(xs) / len(xs) if xs else float("nan")

    row = {
        "f1": mean(f1s), "split": mean(splits), "absplit": mean(absplits),
        "oct": mean(octs), "off": mean(offs), "rep": mean(reps),
        "collapsed": collapsed, "n": len(full),
    }
    print(f"{profile_name:<12} {row['f1']:5.3f} {row['split']:+6.2f} {row['absplit']:6.2f} "
          f"{row['oct']:6.2f} {row['rep']:6.2f} {row['off']:6.2f} {collapsed:>3}/{len(full)}")
    return row


def _diagnostics() -> None:
    print("\n=== DIAGNOSTIC error-rates over the diversified corpus (CLEAN/REALISTIC/HARD) ===")
    print("Note F1 saturates; these break the output into the failure modes it hides.")
    print("split<0 = merged/dropped (under-split), >0 = over-split. rep = same-pitch repeat")
    print("recall. off = tied-sliver/off-grid rate. collapsed = melodies transcribed to 0 notes.")
    print(f"{'profile':<12} {'F1':>5} {'split':>6} {'|spl|':>6} {'oct':>6} {'rep':>6} {'off':>6} {'coll':>7}")
    print("-" * 66)
    _diagnostic_pass("CLEAN", None)
    _diagnostic_pass("REALISTIC", REALISTIC)
    _diagnostic_pass("HARD", HARD)
    print("(HARD degrades by merging/dropping notes, not over-splitting: this pipeline's "
          "\n smoothing+consolidate defences hold - see make_synthetic.HARD.)")


def _metamorphic_summary() -> None:
    """Compact PASS/FAIL of the ground-truth-free invariances on one fixture (EVAL UPGRADE §2/§6)."""
    print("\n=== METAMORPHIC properties (ground-truth-free; c_major_scale) ===")
    fx = "c_major_scale"
    bpm = FIXTURES[fx][0]
    y0, sr, _ = build(fx)
    base = [int(n.midi) for n in transcribe_array(y0, Params(sr=sr), tempo_bpm=bpm).score.notes]

    def vals(notes):
        return [(int(n.midi), round(float(n.start_ql), 4), round(float(n.dur_ql), 4)) for n in notes]

    base_vals = vals(transcribe_array(y0, Params(sr=sr), tempo_bpm=bpm).score.notes)
    checks = []

    yk, _, _ = build(fx, detune_semitones=2.0)
    shifted = [int(n.midi) for n in transcribe_array(yk, Params(sr=sr)).score.notes]
    checks.append(("transpose +2", len(shifted) == len(base) and shifted == [m + 2 for m in base]))

    scaled = vals(transcribe_array((y0 * 3.0).astype(y0.dtype), Params(sr=sr), tempo_bpm=bpm).score.notes)
    checks.append(("gain x3", scaled == base_vals))

    ys, _, _ = build(fx, bpm=bpm * 1.5)
    tv = vals(transcribe_array(ys, Params(sr=sr), tempo_bpm=bpm * 1.5).score.notes)
    checks.append(("tempo x1.5", tv == base_vals))

    import numpy as np
    pad = np.zeros(int(0.5 * sr), dtype=y0.dtype)
    padded = transcribe_array(np.concatenate([pad, y0, pad]), Params(sr=sr), tempo_bpm=bpm).score.notes
    checks.append(("silence pad", [int(n.midi) for n in padded] == base))

    for label, ok in checks:
        print(f"  {label:<14} {'PASS' if ok else 'FAIL'}")


def _roundtrip_pass() -> None:
    """Reconstruction round-trip over the real .webm takes (EVAL UPGRADE §3/§6)."""
    here = os.path.dirname(os.path.abspath(__file__))
    jsons = sorted(glob.glob(os.path.join(here, "data", "recorded", "*.json")))
    print("\n=== RECONSTRUCTION round-trip on real hums (unsupervised; higher = better) ===")
    if not jsons:
        print("  (no recorded takes in tests/data/recorded/)")
        return
    if shutil.which("ffmpeg") is None:
        print("  (ffmpeg not on PATH - cannot decode the .webm takes)")
        return
    from tests.diagnose_recorded import decode

    print(f"  {'take':<20} {'explains':>9} {'pitch<50c':>10} {'onsetF1':>8} {'medcents':>9}")
    scores = []
    for jp in jsons:
        with open(jp, encoding="utf-8") as f:
            gt = json.load(f)
        base = os.path.splitext(jp)[0]
        audio = gt.get("audio")
        audio = os.path.join(os.path.dirname(jp), audio) if audio else base + ".webm"
        if not os.path.exists(audio):
            continue
        p = Params(backend=gt.get("backend", "pesto"), quantize_subdiv=int(gt.get("subdiv", 4)))
        y = decode(audio, p.sr)
        notes = transcribe_array(y, p, tempo_bpm=float(gt.get("bpm", 100))).score.notes
        rt = round_trip(y, notes, p)
        scores.append(rt.explains_audio)
        mc = "  n/a" if rt.median_cents_err != rt.median_cents_err else f"{rt.median_cents_err:7.1f}"
        print(f"  {os.path.basename(base):<20} {rt.explains_audio:9.2f} {rt.pitch_agree_50c:10.2f} "
              f"{rt.onset_f1:8.2f} {mc:>9}")
    if scores:
        print(f"  mean explains_audio = {sum(scores)/len(scores):.2f}  "
              f"(sanity signal; catches gross pitch/onset/segmentation errors, not tuning)")


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

    # EVALUATION UPGRADE: the four richer evidence types that stay useful once F1 saturates.
    _diagnostics()
    _metamorphic_summary()
    _roundtrip_pass()


if __name__ == "__main__":
    main()
