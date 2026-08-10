"""Guards on the synthetic generator itself (fast; no pipeline).

The realistic eval (tests/eval_report.py) is only meaningful if the REALISTIC
fixtures keep the properties that make segmentation hard: partial "d" closures
that never fully devoice, and wide vibrato that crosses semitone lines. These
tests pin those properties so a future edit to make_synthetic can't quietly turn
the hard cases back into easy ones. They also pin that the CLEAN take is
unchanged (it feeds the F1 = 1.0 regression gate in test_pipeline.py).
"""

from __future__ import annotations

import numpy as np

from tests.make_synthetic import REALISTIC, build


def _frame_rms(y: np.ndarray, sr: int, win_s: float = 0.02) -> np.ndarray:
    """Windowed RMS envelope (short window so a brief closure is not smeared away)."""
    w = max(1, int(win_s * sr))
    n = len(y) // w
    return np.array([np.sqrt(np.mean(y[i * w:(i + 1) * w] ** 2) + 1e-12) for i in range(n)])


def _min_over_span(y: np.ndarray, sr: int, start: float, end: float) -> float:
    """Smallest windowed RMS inside [start, end), relative to the take's peak RMS."""
    env = _frame_rms(y, sr)
    peak = float(env.max()) + 1e-12
    lo, hi = int(start / 0.02), int(end / 0.02)
    span = env[lo:hi]
    return float(span.min()) / peak if len(span) else 0.0


def test_clean_repeats_have_silent_closures():
    """The CLEAN take separates repeats with real silence — voicing splits them, so it
    scores F1 = 1.0. This pins that the clean path is untouched by the realism knobs."""
    y, sr, refs = build("repeated_notes")
    assert len(refs) == 5
    # Somewhere between the first and last note the signal drops to ~silence.
    assert _min_over_span(y, sr, refs[0].start, refs[-1].end) < 0.05


def test_realistic_closures_stay_voiced():
    """The REALISTIC take uses PARTIAL closures: the amplitude dips but never devoices,
    so voicing cannot split the repeats and the energy-valley splitter has to. This is
    the property that makes repeated_notes / twinkle hard; pin it so it can't regress."""
    y, sr, refs = build("repeated_notes", expr=REALISTIC)
    assert len(refs) == 5
    # The dips never reach silence: min RMS across the sung span stays well above the floor.
    assert _min_over_span(y, sr, refs[0].start, refs[-1].end) > 0.08


def test_realistic_differs_but_shares_timeline():
    """Realistic and clean renders occupy the same timeline (same reference onsets) but
    are different audio — the realism is added on top, not a different arrangement."""
    yc, _, rc = build("twinkle")
    yr, _, rr = build("twinkle", expr=REALISTIC)
    assert [r.midi for r in rc] == [r.midi for r in rr]
    assert yc.shape == yr.shape
    assert not np.allclose(yc, yr)


def test_realistic_vibrato_is_wide():
    """A held realistic note wobbles far more than the gentle clean take (a proxy for the
    wide-vibrato stress): its short-window RMS/pitch content is visibly modulated. We check
    the pitch swing indirectly via the configured depth being real-voice scale."""
    assert REALISTIC.vibrato_cents >= 40.0
    assert REALISTIC.closure_db is not None and REALISTIC.closure_db > -30.0
