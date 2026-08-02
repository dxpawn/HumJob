"""Tempo detection from free humming (the 'find my tempo' flow).

A mismatched BPM is what turns clean held notes into strings of tied slivers
(see the pipeline: told-120-but-hummed-90 gives durations 1.5/1.25/2.75/...).
detect_bpm lets the user hum first so the metronome matches how they actually
phrase. These synthesize 'da-da-da' at a known tempo and assert we recover it.
"""

from __future__ import annotations

import numpy as np
import pytest

from mouthtranscriber.config import Params
from mouthtranscriber.tempo import _fold, detect_bpm

SR = 22050


def _hum(seq, bpm, attack=0.006, gap_s=0.06, vib=0.2):
    """seq = [(midi, beats)]. Gap carved out of the beat so onset-to-onset == beat."""
    spb = 60.0 / bpm
    out = []
    for m, beats in seq:
        n = max(1, int((beats * spb - gap_s) * SR))
        t = np.arange(n) / SR
        f = 440 * 2 ** ((m - 69) / 12)
        inst = f * 2 ** ((vib * np.sin(2 * np.pi * 5.5 * t)) / 12)
        phase = 2 * np.pi * np.cumsum(inst) / SR
        env = np.exp(-t / (beats * spb * 0.6))
        a = int(attack * SR)
        env[:a] *= np.linspace(0, 1, a)
        out.append((0.5 * env * np.sin(phase)).astype(np.float32))
        out.append(np.zeros(int(gap_s * SR), np.float32))
    return np.concatenate(out)


@pytest.mark.parametrize("bpm", [76, 90, 100, 120])
def test_recovers_steady_tempo(bpm):
    y = _hum([(60, 1)] * 8, bpm)
    got = detect_bpm(y, SR, Params(sr=SR))
    assert abs(got - bpm) <= 3, f"wanted ~{bpm}, got {got}"


def test_recovers_tempo_from_mixed_rhythm():
    seq = [(60, 1), (62, 1), (64, 2), (65, 1), (67, 1), (69, 2), (67, 1), (65, 1), (64, 4)]
    got = detect_bpm(y=_hum(seq, 100), sr=SR, params=Params(sr=SR))
    assert abs(got - 100) <= 4, got


def test_fold_only_shifts_out_of_range():
    assert _fold(144, 50, 180, 100) == 144   # in range: keep librosa's octave
    assert _fold(40, 50, 180, 100) == 80     # too slow -> double
    assert _fold(200, 50, 180, 100) == 100   # too fast -> halve into range


def test_silence_returns_prior():
    y = np.zeros(SR, dtype=np.float32)
    assert detect_bpm(y, SR, Params(sr=SR), prior_bpm=100) == 100
