"""Spectral octave-error correction for the f0 contour (PLAN §5.3; backend-agnostic).

pYIN (and other autocorrelation trackers) can lock onto a SUBHARMONIC on a
continuously-voiced legato line: the Viterbi decoder's "stay put" transition prior
outweighs a real octave jump, so it reports f0 = true/2 for a whole note. The
``octave_leaps`` fixture is the worst case - C4 C5 C4 C5 C4 hummed with soft, voiced
"d" closures (no silence to reset the decoder) reads back as all C4, even though the
spectrum has essentially zero energy at the reported C4 and a strong peak at the true
C5. Because the octave error also erases the pitch step, segmentation then merges the
notes, so this is worth fixing upstream of segment.py rather than as a note relabel.

The tell is spectral and unambiguous. A subharmonic candidate f (= true / 2) has
energy only at its EVEN harmonics 2f, 4f, 6f - they coincide with the true
fundamental's harmonics - and NONE at its ODD harmonics f, 3f, 5f. A genuine
fundamental always keeps odd-harmonic energy, even a weak- or missing-fundamental
voice (h1 may be quiet, but 3f and 5f are there). So when a frame's odd-harmonic
salience collapses relative to its even-harmonic salience, the reported pitch is half
the true pitch and we double it.

This runs once, right after tracking and before voicing/segment, so the restored pitch
step lets segmentation recover the note it would otherwise merge. It only ever moves a
pitch UP by an octave, only when the doubled pitch is still inside the search range, and
only on strong spectral evidence, so clean takes (no odd/even collapse) are untouched.

Only the octave-DOWN (subharmonic) direction is corrected: it is the documented tracker
failure and the safe direction. An octave-UP correction would risk demoting a strong
second harmonic and is left out deliberately.
"""

from __future__ import annotations

import numpy as np

from .config import Params
from .model import Frame


def correct_octaves(frames: list[Frame], y: np.ndarray, params: Params) -> list[Frame]:
    """Double f0 on frames whose reported fundamental is a subharmonic.

    ``y`` is the same (preprocessed) signal the tracker saw; a single STFT at the
    pipeline hop supplies the harmonic evidence. Returns a new frame list (pure).
    """
    p = params
    if not frames or not p.octave_correct:
        return frames

    import librosa

    n_fft = p.frame_length
    S = np.abs(librosa.stft(y, n_fft=n_fft, hop_length=p.hop_length, center=True))
    bin_hz = p.sr / n_fft
    nyq = p.sr / 2.0
    n_cols = S.shape[1]

    def salience(col: np.ndarray, f: float) -> float:
        """Magnitude at frequency ``f``, taking the max over +-1 bin so a little
        vibrato/detune does not slip a harmonic between bins."""
        if f <= 0.0 or f >= nyq:
            return 0.0
        b = int(round(f / bin_hz))
        lo, hi = max(0, b - 1), min(len(col), b + 2)
        return float(col[lo:hi].max()) if hi > lo else 0.0

    out: list[Frame] = []
    for i, fr in enumerate(frames):
        f0 = fr.f0
        if np.isnan(f0) or f0 <= 0.0 or 2.0 * f0 > p.fmax:
            out.append(fr)
            continue
        col = S[:, i if i < n_cols else n_cols - 1]
        odd = salience(col, f0) + salience(col, 3.0 * f0) + salience(col, 5.0 * f0)
        even = salience(col, 2.0 * f0) + salience(col, 4.0 * f0) + salience(col, 6.0 * f0)
        if even > 0.0 and odd < p.octave_odd_even_ratio * even:
            out.append(Frame(t=fr.t, f0=2.0 * f0, confidence=fr.confidence, rms=fr.rms))
        else:
            out.append(fr)
    return out
