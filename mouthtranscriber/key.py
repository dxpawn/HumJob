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


def detect_key(notes: list[NoteEvent], top_k: int = 3) -> list[tuple[float, str]]:
    """Return up to ``top_k`` ``(correlation, "F minor")`` candidates, best first."""
    hist = np.zeros(12)
    for n in notes:
        hist[n.midi % 12] += max(n.duration, 1e-6)
    if hist.sum() == 0:
        return []

    results: list[tuple[float, str]] = []
    for tonic in range(12):
        for profile, mode in ((_MAJOR, "major"), (_MINOR, "minor")):
            rotated = np.roll(profile, tonic)
            corr = float(np.corrcoef(hist, rotated)[0, 1])
            results.append((corr, f"{_NAMES[tonic]} {mode}"))

    results.sort(key=lambda x: x[0], reverse=True)
    return results[:top_k]
