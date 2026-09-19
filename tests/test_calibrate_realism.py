"""Test the realism-measurement core (EVALUATION UPGRADE §5).

``calibrate_realism.measure_take`` is validated on synthetic renders whose knobs are known:
the vibrato it measures on a REALISTIC take (55-cent vibrato) must be clearly larger than on a
CLEAN take (8-cent wobble), and the measured rate must land in the vibrato band. This both
tests the tool and shows the measurement tracks the true knob, so a fit from real takes means
something.
"""

from __future__ import annotations

import math

from mouthtranscriber.config import Params
from mouthtranscriber.pipeline import transcribe_array
from tests.calibrate_realism import measure_take
from tests.make_synthetic import REALISTIC, build


def _measure(fixture, expr):
    y, sr, _ = build(fixture, expr=expr)
    p = Params(sr=sr)
    an = transcribe_array(y, p)
    return measure_take(an.frames, an.score.notes, bpm=100, hop_s=p.hop_s, y=y, sr=sr)


def test_measures_more_vibrato_on_realistic_than_clean():
    clean = _measure("mixed_rhythm", None)         # 8-cent wobble at most
    realistic = _measure("mixed_rhythm", REALISTIC)  # 55-cent vibrato
    assert realistic["n_held"] >= 1
    assert math.isfinite(realistic["vibrato_cents"])
    assert realistic["vibrato_cents"] > clean["vibrato_cents"] + 5.0, (
        f"clean={clean['vibrato_cents']:.1f} realistic={realistic['vibrato_cents']:.1f}"
    )


def test_measured_rate_in_vibrato_band_and_fields_present():
    m = _measure("mixed_rhythm", REALISTIC)
    if math.isfinite(m["vibrato_rate"]):
        assert 3.0 <= m["vibrato_rate"] <= 9.0
    # every knob key is present (nan allowed where a take is too short to measure)
    for key in ("vibrato_cents", "drift_cents", "timing_jitter_ms",
                "closure_depth_db", "closure_width_ms", "shimmer_db"):
        assert key in m


def test_no_audio_still_measures_pitch_only():
    y, sr, _ = build("mixed_rhythm", expr=REALISTIC)
    an = transcribe_array(y, Params(sr=sr))
    m = measure_take(an.frames, an.score.notes, bpm=100, hop_s=Params(sr=sr).hop_s)
    assert math.isnan(m["shimmer_db"])           # needs audio
    assert math.isfinite(m["vibrato_cents"])     # from frames only
