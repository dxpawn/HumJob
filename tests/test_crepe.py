"""CREPE backend — the voice/humming-specialized neural pitch model.

basic-pitch is instrument-trained and can octave-jump on a bare hum; CREPE is a
CNN trained for the singing voice, so it tracks a sustained "da-da-da" cleanly.
Same worst-case input as the basic-pitch test (held tone + vibrato + tremolo);
here we assert CREPE recovers the scale as whole notes in the right octave.

Skipped automatically if torch / torchcrepe aren't installed.
"""

from __future__ import annotations

import pytest

pytest.importorskip("torch")
pytest.importorskip("torchcrepe")

from mouthtranscriber.config import Params
from mouthtranscriber.pipeline import transcribe_array
from tests.test_basicpitch import _sustained


def test_sustained_scale_stays_whole_crepe():
    midis = [60, 62, 64, 65, 67, 69, 71, 72]
    y, sr = _sustained(midis)
    score = transcribe_array(
        y, Params(sr=sr, backend="crepe"), tempo_bpm=76
    ).score
    assert [n.midi for n in score.notes] == midis, [n.name for n in score.notes]
    # No octave jumps, no fragmentation: each 2-beat half note stays whole.
    assert all(n.duration >= 1.0 for n in score.notes), [n.duration for n in score.notes]


def test_key_detected_through_crepe():
    y, sr = _sustained([60, 62, 64, 65, 67, 69, 71, 72])
    score = transcribe_array(
        y, Params(sr=sr, backend="crepe"), tempo_bpm=76
    ).score
    assert score.key == "C major"
