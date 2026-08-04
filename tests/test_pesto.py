"""PESTO backend — the most precise pitch tracker (self-supervised, 2023).

PESTO (pesto-pitch) matches/beats CREPE on singing voice while being lighter and
faster; its default ``mir-1k_g7`` model is trained on singing voice, so it suits
humming. Same worst-case input as the CREPE/basic-pitch tests (held tone + vibrato
+ tremolo); here we assert PESTO recovers the scale as whole notes in the right octave.

Skipped automatically if torch / pesto aren't installed.
"""

from __future__ import annotations

import pytest

pytest.importorskip("torch")
pytest.importorskip("pesto")

from mouthtranscriber.config import Params
from mouthtranscriber.pipeline import transcribe_array
from tests.test_basicpitch import _sustained


def test_sustained_scale_stays_whole_pesto():
    midis = [60, 62, 64, 65, 67, 69, 71, 72]
    y, sr = _sustained(midis)
    score = transcribe_array(
        y, Params(sr=sr, backend="pesto"), tempo_bpm=76
    ).score
    assert [n.midi for n in score.notes] == midis, [n.name for n in score.notes]
    # No octave jumps, no fragmentation: each 2-beat half note stays whole.
    assert all(n.duration >= 1.0 for n in score.notes), [n.duration for n in score.notes]


def test_key_detected_through_pesto():
    y, sr = _sustained([60, 62, 64, 65, 67, 69, 71, 72])
    score = transcribe_array(
        y, Params(sr=sr, backend="pesto"), tempo_bpm=76
    ).score
    assert score.key == "C major"
