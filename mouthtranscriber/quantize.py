"""Rhythm quantization to a known-BPM grid (PLAN §5.7).

Because the user hums to a metronome at a fixed BPM, this is snapping - not blind
tempo estimation. We:

  1. Convert onsets/offsets to quarter-note units (beats).
  2. Estimate a global grid PHASE (a circular-mean, like the tuning offset for
     pitch) so a lead-in / mic latency doesn't throw every note off the grid.
  3. Snap each onset to the grid and anchor the first note to beat 0.
  4. Give each note a duration from its OWN sounded length, not from the spacing
     to the next note. The "da" consonant stop clips a small silent gap off the
     end of every note; that gap is articulation, not a rest, so we fold the
     typical clip (the median short inter-note gap) back into each length before
     snapping. Any gap noticeably larger than that typical articulation is a
     genuine rest and simply surfaces as the space between a note's end and the
     next onset.

Why own-length and not "hold to the next onset" (the old legato rule): tying a
note's printed duration to the spacing of the *next* note made identical hums
render as different durations. Two hums of the same note and length would differ
whenever their spacing wobbled across a grid line, and the final note - which has
no next onset - always fell back to its bare, clipped length and so read short.
Deriving the duration from the note's own length (plus the shared articulation
allowance) makes equal notes quantize equally, independent of spacing, and needs
no special case for the last note.

Sets ``start_ql`` and ``dur_ql`` (quarter-note units) on each note for the
notation exporter. Returns the estimated phase in seconds (for debugging).
"""

from __future__ import annotations

import math

import numpy as np

from .config import Params
from .model import NoteEvent


def _estimate_phase(onsets_ql: np.ndarray, grid: float) -> float:
    """Best grid phase in [-grid/2, grid/2): where the grid lines actually fall."""
    frac = (onsets_ql / grid) % 1.0
    ang = np.angle(np.mean(np.exp(1j * 2 * math.pi * frac)))
    return (ang / (2 * math.pi)) * grid


def quantize(notes: list[NoteEvent], bpm: float, params: Params) -> float:
    """Fill ``start_ql``/``dur_ql`` on each note. Returns phase offset (seconds)."""
    if not notes:
        return 0.0

    p = params
    spb = 60.0 / bpm                 # seconds per quarter note (beat)
    grid = 1.0 / p.quantize_subdiv   # grid step in quarter-note units

    onsets_ql = np.array([n.start / spb for n in notes])
    offsets_ql = np.array([n.end / spb for n in notes])

    phase = _estimate_phase(onsets_ql, grid)

    # Grid-snapped onset positions, in integer grid steps, first note anchored to 0.
    on_steps = np.round((onsets_ql - phase) / grid)
    on_steps = (on_steps - on_steps[0]).astype(int)

    # The typical "da" articulation clip: the median of the short inter-note gaps.
    # Gaps at/above rest_threshold_ql are real rests and are excluded so they don't
    # inflate the allowance. This shared value (not each note's own next-gap) is what
    # keeps identical notes identical regardless of how evenly they were spaced.
    lengths_ql = offsets_ql - onsets_ql
    if len(notes) > 1:
        gaps = onsets_ql[1:] - offsets_ql[:-1]
        art_gaps = gaps[gaps < p.rest_threshold_ql]
        art = float(np.median(art_gaps)) if len(art_gaps) else 0.0
    else:
        art = 0.0
    art = max(0.0, art)

    n = len(notes)
    for i, note in enumerate(notes):
        s = int(on_steps[i])
        dur_steps = max(1, int(round((lengths_ql[i] + art) / grid)))
        end = s + dur_steps
        if i + 1 < n:
            next_on = max(int(on_steps[i + 1]), s + 1)  # next onset, never behind us
            end = min(end, next_on)                     # never overrun the next note
        note.start_ql = float(s * grid)
        note.dur_ql = float(max(1, end - s) * grid)

    return phase * spb
