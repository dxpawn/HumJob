"""Estimate a tempo (BPM) from free humming — for the "find my tempo" flow.

The user hums a few beats however they like; we estimate their natural tempo and
hand it back, so they can then hum the *real* take to a click at that tempo. That
is what makes rhythm quantization clean: when the metronome matches how the person
actually phrases, note durations land on whole beats instead of ugly fractions
(1.25, 2.75, ...) that notation software renders as strings of tied slivers.

Method: onset-strength autocorrelation via librosa, with a prior around a
comfortable humming tempo and octave folding so we don't return half/double time.
"""

from __future__ import annotations

import numpy as np

from .config import Params


def detect_bpm(
    y: np.ndarray,
    sr: int,
    params: Params | None = None,
    lo: float = 50.0,
    hi: float = 180.0,
    prior_bpm: float = 100.0,
) -> float:
    """Estimate BPM from a free hum. Returns a rounded value in ``[lo, hi]``."""
    import librosa

    p = params or Params()
    hop = p.hop_length
    oenv = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    if not np.any(oenv):
        return round(prior_bpm)

    try:  # librosa >= 0.10
        from librosa.feature.rhythm import tempo as _tempo
    except ImportError:  # older librosa
        from librosa.beat import tempo as _tempo
    cand = _tempo(onset_envelope=oenv, sr=sr, hop_length=hop, start_bpm=prior_bpm)

    bpm = float(np.ravel(cand)[0])
    return round(_fold(bpm, lo, hi, prior_bpm))


def _fold(bpm: float, lo: float, hi: float, prior: float) -> float:
    """Octave-fold an estimate *into* [lo, hi]. We trust librosa's own tempo prior
    for the octave and only shift when the value falls outside the range, so an
    in-range estimate (even a fast one like 144) is kept as-is."""
    if bpm <= 0:
        return prior
    while bpm < lo:
        bpm *= 2
    while bpm > hi:
        bpm /= 2
    return bpm
