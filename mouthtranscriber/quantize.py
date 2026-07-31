"""Rhythm quantization to a known-BPM grid (PLAN §5.7).

Because the user hums to a metronome at a fixed BPM, this is snapping — not blind
tempo estimation. We:

  1. Convert onsets/offsets to quarter-note units (beats).
  2. Estimate a global grid PHASE (a circular-mean, like the tuning offset for
     pitch) so a lead-in / mic latency doesn't throw every note off the grid.
  3. Snap onsets to the grid and anchor the first note to beat 0.
  4. Choose each note's duration: hold to the next onset (legato — the "da" gaps
     are articulation, not rests), unless the real silence is long enough to be a
     genuine rest.

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

    # Snap (phase-removed) to integer grid steps, then anchor first note to 0.
    on_steps = np.round((onsets_ql - phase) / grid)
    off_steps = np.round((offsets_ql - phase) / grid)
    anchor = on_steps[0]
    q_on = (on_steps - anchor) * grid
    q_off = (off_steps - anchor) * grid

    n = len(notes)
    for i, note in enumerate(notes):
        start = float(q_on[i])
        sound_end = float(q_off[i])

        if i + 1 < n:
            next_on = float(q_on[i + 1])
            if next_on <= start:
                next_on = start + grid  # never let a note collapse to zero
            gap = next_on - sound_end
            if gap >= p.rest_threshold_ql:
                end = max(sound_end, start + grid)   # leave a real rest
            else:
                end = next_on                        # legato: hold to next onset
        else:
            end = max(sound_end, start + grid)       # last note keeps its length

        note.start_ql = start
        note.dur_ql = max(grid, end - start)

    return phase * spb
