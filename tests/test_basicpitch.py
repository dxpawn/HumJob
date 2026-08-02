"""Neural backend (basic-pitch) transcription — the sustained-singing win.

The DSP segmenter shatters a held, vibrato'd note into 16th/32nd fragments
(see tests/test_segment.py). The basic-pitch backend maps audio straight to note
events and is immune to that. Same worst-case input (sustained tone + vibrato +
tremolo); here we assert it comes back as whole notes.

Skipped automatically if basic-pitch / onnxruntime aren't installed.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("basic_pitch")
pytest.importorskip("onnxruntime")

from mouthtranscriber.config import Params
from mouthtranscriber.pipeline import transcribe_array


def _sustained(midis, bpm=76, sr=22050, beats=2, vib=0.3, trem_db=7.0):
    """Held tones with ~5.5 Hz vibrato and 4 Hz amplitude tremolo (worst case)."""
    spb = 60.0 / bpm

    def tone(m):
        n = int(beats * spb * sr)
        t = np.arange(n) / sr
        f = 440 * 2 ** ((m - 69) / 12)
        inst = f * 2 ** ((vib * np.sin(2 * np.pi * 5.5 * t)) / 12)
        phase = 2 * np.pi * np.cumsum(inst) / sr
        amp = 10 ** ((-trem_db * (0.5 - 0.5 * np.cos(2 * np.pi * 4 * t))) / 20)
        env = np.ones(n)
        a = int(0.02 * sr)
        env[:a] = np.linspace(0, 1, a)
        env[-a:] = np.linspace(1, 0, a)
        return (0.5 * amp * env * np.sin(phase)).astype(np.float32)

    return np.concatenate([tone(m) for m in midis]), sr


def test_sustained_scale_stays_whole():
    midis = [60, 62, 64, 65, 67, 69, 71, 72]
    y, sr = _sustained(midis)
    score = transcribe_array(
        y, Params(sr=sr, backend="basic_pitch"), tempo_bpm=76
    ).score
    assert [n.midi for n in score.notes] == midis, [n.name for n in score.notes]
    # Each was a 2-beat half note (~1.58 s at 76 bpm): one note, not a pile.
    assert all(n.duration >= 1.0 for n in score.notes), [n.duration for n in score.notes]


def test_key_detected_through_neural_backend():
    y, sr = _sustained([60, 62, 64, 65, 67, 69, 71, 72])
    score = transcribe_array(
        y, Params(sr=sr, backend="basic_pitch"), tempo_bpm=76
    ).score
    assert score.key == "C major"
