"""FCNF0++ backend (penn) — a precision peer to PESTO.

FCNF0++ (Morrison 2023, the `penn` library) is a fully-convolutional f0 + periodicity
model that matches/beats CREPE. Same worst-case input as the CREPE/PESTO tests (held tone
+ vibrato + tremolo); here we assert FCNF0++ recovers the scale as whole notes in the
right octave.

Skipped automatically if torch / penn aren't installed. NOTE: penn downloads its
checkpoint from the HuggingFace Hub on first run, so the first invocation needs network.
"""

from __future__ import annotations

import pytest

pytest.importorskip("torch")

# penn imports the `torbi` Viterbi extension at load, which has no wheel for some torch
# builds; install the same stub PennTracker uses so `import penn` (and this test) works.
from mouthtranscriber.pitch import ensure_penn_importable

ensure_penn_importable()
pytest.importorskip("penn")

from mouthtranscriber.config import Params
from mouthtranscriber.pipeline import transcribe_array
from tests.test_basicpitch import _sustained


def test_sustained_scale_stays_whole_penn():
    midis = [60, 62, 64, 65, 67, 69, 71, 72]
    y, sr = _sustained(midis)
    score = transcribe_array(
        y, Params(sr=sr, backend="fcnf0"), tempo_bpm=76
    ).score
    assert [n.midi for n in score.notes] == midis, [n.name for n in score.notes]
    # No octave jumps, no fragmentation: each 2-beat half note stays whole.
    assert all(n.duration >= 1.0 for n in score.notes), [n.duration for n in score.notes]


def test_key_detected_through_penn():
    y, sr = _sustained([60, 62, 64, 65, 67, 69, 71, 72])
    score = transcribe_array(
        y, Params(sr=sr, backend="fcnf0"), tempo_bpm=76
    ).score
    assert score.key == "C major"
