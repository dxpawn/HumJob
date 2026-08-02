"""Segmentation robustness on *sustained* humming (regression for the M6 follow-up).

The segmenter's energy-valley and pitch-step splitters were tuned for staccato
"da-da-da". On a held note, vibrato and amplitude tremolo trip them and shatter one
note into a run of 16th/32nd fragments (which also makes downstream tools misread
the tempo). The backend-agnostic consolidate pass (mouthtranscriber/consolidate.py)
fuses those fragments back together. These tests synthesize a sustained tone *with*
tremolo + vibrato (the worst case) and assert it stays whole.
"""

from __future__ import annotations

import numpy as np

from mouthtranscriber.config import Params
from mouthtranscriber.pipeline import transcribe_array


def _sustained(midis, bpm=76, sr=22050, beats=2, vib=0.3, trem_db=7.0):
    """Concatenate held tones, each with ~5.5 Hz vibrato and 4 Hz amplitude tremolo."""
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
        return (amp * env * np.sin(phase)).astype(np.float32)

    return np.concatenate([tone(m) for m in midis]), sr


def test_sustained_notes_do_not_fragment():
    y, sr = _sustained([60, 64, 67, 64])
    score = transcribe_array(y, Params(sr=sr), tempo_bpm=76).score
    assert len(score.notes) == 4, [n.name for n in score.notes]
    assert [n.midi for n in score.notes] == [60, 64, 67, 64]
    # Each was a 2-beat half note; it must land as one, not a pile of slivers.
    assert all(n.dur_ql >= 1.5 for n in score.notes), [n.dur_ql for n in score.notes]


def test_wide_vibrato_stays_one_note():
    """A single held note with WIDE (+-0.9 semitone) vibrato crosses semitone lines,
    so its fragments land on DIFFERENT pitches (C4/C#4) — the real-voice failure the
    user hit. Adequate contour smoothing keeps it whole and at the centre pitch."""
    y, sr = _sustained([60], vib=0.9, trem_db=9.0)
    score = transcribe_array(y, Params(sr=sr), tempo_bpm=76).score
    assert len(score.notes) == 1, [n.name for n in score.notes]
    assert score.notes[0].midi == 60


def test_short_smoothing_reproduces_the_over_split():
    """Documents the cause: with the old 5-frame window, wide vibrato slips past the
    smoother and the pitch-step splitter shatters the held note — and consolidation
    can't rescue it, because the fragments sit on opposite extremes of the wobble."""
    y, sr = _sustained([60], vib=0.9, trem_db=9.0)
    score = transcribe_array(y, Params(sr=sr, smooth_frames=5), tempo_bpm=76).score
    assert len(score.notes) > 1
