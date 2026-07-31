"""Voiced / silent decision (PLAN §5.4).

This is where other apps fail at silence. We gate on BOTH pitch confidence AND
energy, use hysteresis so the decision doesn't chatter at boundaries, and bridge
the sub-60 ms unvoiced blips that the "d" consonant punches into a held note.
Removing too-short notes is left to segmentation (which knows note boundaries).
"""

from __future__ import annotations

import numpy as np

from .config import Params
from .model import Frame


def decide_voicing(frames: list[Frame], params: Params) -> np.ndarray:
    """Return a boolean array (one per frame): True = voiced.

    A frame is voiced when confidence clears the hysteresis threshold AND the
    energy clears the dB-below-peak gate AND the tracker produced a pitch.
    """
    p = params
    n = len(frames)
    if n == 0:
        return np.zeros(0, dtype=bool)

    conf = np.array([f.confidence for f in frames])
    rms = np.array([f.rms for f in frames])
    has_pitch = np.array([not np.isnan(f.f0) for f in frames])

    peak = float(rms.max()) + 1e-12
    rms_db = 20.0 * np.log10(rms / peak + 1e-12)
    energy_ok = rms_db > p.rms_threshold_db

    voiced = np.zeros(n, dtype=bool)
    state = False
    for i in range(n):
        gate = energy_ok[i] and has_pitch[i]
        if state:
            state = gate and conf[i] > p.voiced_exit
        else:
            state = gate and conf[i] > p.voiced_enter
        voiced[i] = state

    return _fill_short_gaps(voiced, p.hop_s, p.max_gap_merge_s)


def _fill_short_gaps(voiced: np.ndarray, hop_s: float, max_gap_s: float) -> np.ndarray:
    """Bridge unvoiced runs shorter than ``max_gap_s`` that sit between voiced runs.

    This reconnects a held note that the consonant briefly interrupted, without
    merging genuinely separate notes (those are re-split by onsets in segment.py).
    """
    v = voiced.copy()
    n = len(v)
    max_frames = int(round(max_gap_s / hop_s))
    i = 0
    while i < n:
        if not v[i]:
            j = i
            while j < n and not v[j]:
                j += 1
            interior = i > 0 and j < n  # bounded by voiced on both sides
            if interior and (j - i) <= max_frames:
                v[i:j] = True
            i = j
        else:
            i += 1
    return v
