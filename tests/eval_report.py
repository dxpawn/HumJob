"""Print a precision/recall/F1 table over all synthetic fixtures (PLAN §6).

Run:  python tests/eval_report.py
This is the standard "where do we stand" report for iterating on the DSP. It runs
the pipeline once per fixture and reports note metrics, detected key, tuning
offset, and per-clip latency.
"""

from __future__ import annotations

import time

from mouthtranscriber.config import Params
from mouthtranscriber.evaluate import note_scores, ref_notes_from_tuples
from mouthtranscriber.pipeline import transcribe_array
from tests.make_synthetic import build

CASES = [
    ("c_major_scale", {}),
    ("a_minor_scale", {}),
    ("arpeggio", {}),
    ("repeated_notes", {}),
    ("with_silence", {}),
    ("octave_leaps", {}),
    ("twinkle", {}),
    ("mixed_rhythm", {}),
    ("c_major_scale", {"detune_semitones": -0.4}),
    ("twinkle", {"vibrato": True, "scoop": True}),
]


def main() -> None:
    print(f"{'fixture':<28} {'P':>5} {'R':>5} {'F1':>5} {'key':<10} {'tune':>6} {'ms':>6}")
    print("-" * 74)
    f1s = []
    for fixture, kwargs in CASES:
        y, sr, refs = build(fixture, **kwargs)
        ref_notes = ref_notes_from_tuples([(r.start, r.end, r.midi) for r in refs])

        t0 = time.perf_counter()
        analysis = transcribe_array(y, Params(sr=sr))
        ms = (time.perf_counter() - t0) * 1000

        s = note_scores(ref_notes, analysis.score.notes)
        f1s.append(s.f1)
        label = fixture + ("*" if kwargs else "")
        key = (analysis.score.key or "-")[:10]
        print(
            f"{label:<28} {s.precision:5.2f} {s.recall:5.2f} {s.f1:5.2f} "
            f"{key:<10} {analysis.score.tuning_offset_cents:+5.0f}c {ms:6.0f}"
        )
    print("-" * 74)
    print(f"mean F1 = {sum(f1s) / len(f1s):.3f}   (gate: 0.95)")


if __name__ == "__main__":
    main()
