"""Global tuning correction (PLAN §5.6).

Fixes the classic failure: the user hums 40 cents flat, so every note rounds to
the wrong semitone. We estimate one global offset that best aligns all the notes'
fractional pitches to the semitone grid, then re-snap. Uses a circular mean so it
is robust to wraparound (a note at +0.45 and one at -0.45 are both ~half a
semitone off, not a whole one apart).
"""

from __future__ import annotations

import math

import numpy as np

from .model import NoteEvent


def estimate_offset_semitones(notes: list[NoteEvent]) -> float:
    """Return the global pitch offset (in semitones, roughly [-0.5, 0.5]).

    Positive means the humming was sharp; negative means flat.
    """
    if not notes:
        return 0.0
    fracs = np.array([n.raw_midi - round(n.raw_midi) for n in notes])
    fracs = fracs[~np.isnan(fracs)]
    if len(fracs) == 0:
        return 0.0
    # Circular mean of the fractional deviations mapped onto the unit circle.
    ang = np.angle(np.mean(np.exp(1j * 2 * math.pi * fracs)))
    return float(ang / (2 * math.pi))


def correct(notes: list[NoteEvent]) -> float:
    """Re-snap every note using the estimated global offset.

    Mutates the notes in place and returns the applied offset in cents.
    """
    delta = estimate_offset_semitones(notes)
    for n in notes:
        if math.isnan(n.raw_midi):
            continue
        shifted = n.raw_midi - delta
        n.midi = int(round(shifted))
        n.cents_offset = (shifted - n.midi) * 100.0
    return delta * 100.0
