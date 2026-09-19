"""Tests for the reconstruction round-trip metric (EVALUATION UPGRADE §3).

The round-trip is an unsupervised sanity signal: resynthesise a transcription and compare it
to the original audio. These pin that (a) a correct transcription of a clean take explains its
own audio well, and (b) a deliberately corrupted transcription (every note an octave off) is
caught as pitch disagreement - so the metric discriminates rather than always passing.
"""

from __future__ import annotations

from dataclasses import replace

from mouthtranscriber.config import Params
from mouthtranscriber.pipeline import transcribe_array
from mouthtranscriber.roundtrip import resynthesize, round_trip
from tests.make_synthetic import build


def test_correct_transcription_explains_its_audio():
    y, sr, _ = build("c_major_scale")
    p = Params(sr=sr)
    notes = transcribe_array(y, p).score.notes
    rt = round_trip(y, notes, p)
    assert rt.n_joint_frames > 0
    assert rt.pitch_agree_50c > 0.9, f"pitch agreement too low: {rt.pitch_agree_50c}"
    assert rt.explains_audio > 0.7, f"explains_audio too low: {rt.explains_audio}"


def test_octave_corrupted_transcription_is_caught():
    y, sr, _ = build("c_major_scale")
    p = Params(sr=sr)
    notes = transcribe_array(y, p).score.notes
    good = round_trip(y, notes, p).pitch_agree_50c
    # Shift every transcribed note up an octave: the resynthesis no longer matches the audio.
    wrong = [replace(n, midi=int(n.midi) + 12) for n in notes]
    bad = round_trip(y, wrong, p).pitch_agree_50c
    assert bad < 0.5, f"octave-corrupted pitch agreement not low enough: {bad}"
    assert good - bad > 0.4, f"metric did not discriminate (good={good}, bad={bad})"


def test_resynthesize_shapes_and_empty():
    y, sr, _ = build("arpeggio")
    notes = transcribe_array(y, Params(sr=sr)).score.notes
    resyn = resynthesize(notes, sr)
    assert len(resyn) > 0
    assert resynthesize([], sr).shape == (0,)
