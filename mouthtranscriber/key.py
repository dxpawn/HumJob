"""Key / scale detection via Krumhansl-Schmuckler (PLAN §5.8).

Build a duration-weighted pitch-class histogram, correlate it against the 24
major/minor key profiles, and return the top matches with confidence. Run this
AFTER tuning correction so out-of-tune humming isn't mistaken for out-of-key.
Implemented directly (no music21 import) to keep it fast and dependency-light.
"""

from __future__ import annotations

import numpy as np

from .model import NoteEvent

# Krumhansl & Kessler probe-tone profiles.
_MAJOR = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
)
_MINOR = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
)
_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def score_keys(hist12: np.ndarray) -> list[tuple[float, int, str]]:
    """Correlate a 12-bin pitch-class vector against all 24 key profiles.

    ``hist12`` is any pitch-class weighting -- a duration histogram (from notes) or a
    time-averaged chroma vector (from raw audio). Returns every ``(correlation, tonic,
    mode)`` sorted best-first, so callers can take the top match or the full ranking.
    Shared by :func:`detect_key` (notes) and the Pitch Finder's audio analyzer.
    """
    hist = np.asarray(hist12, dtype=float)
    results: list[tuple[float, int, str]] = []
    for tonic in range(12):
        for profile, mode in ((_MAJOR, "major"), (_MINOR, "minor")):
            rotated = np.roll(profile, tonic)
            corr = float(np.corrcoef(hist, rotated)[0, 1])
            if np.isnan(corr):
                corr = -1.0  # flat/empty input correlates with nothing
            results.append((corr, tonic, mode))
    results.sort(key=lambda x: x[0], reverse=True)
    return results


def detect_key(notes: list[NoteEvent], top_k: int = 3) -> list[tuple[float, str]]:
    """Return up to ``top_k`` ``(correlation, "F minor")`` candidates, best first."""
    hist = np.zeros(12)
    for n in notes:
        hist[n.midi % 12] += max(n.duration, 1e-6)
    if hist.sum() == 0:
        return []

    ranked = score_keys(hist)
    return [(corr, f"{_NAMES[tonic]} {mode}") for corr, tonic, mode in ranked[:top_k]]
