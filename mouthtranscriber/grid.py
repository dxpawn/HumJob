"""Metronome-grid helpers shared by segmentation and consolidation (PLAN §5.5/5.7).

The user hums to a known BPM, so note onsets cluster on the subdivision grid
(one beat / ``quantize_subdiv``). Today that fact is used only in the final
``quantize`` stage; these helpers let ``segment`` and ``consolidate`` use it as a
PRIOR: estimate the grid PHASE from the confident onsets, then ask whether a given
time lands on a grid line. A weak energy dip that sits on a beat is far more likely
a real re-articulation than the same dip mid-beat, and two fragments split by a
grid onset should not be fused back together.

Phase is estimated the same way ``quantize`` estimates it (a circular mean of the
onset fractions), but in seconds rather than quarter-note units, because these
stages run before onsets are converted to beats.
"""

from __future__ import annotations

import math

import numpy as np


def step_s(bpm: float, subdiv: int) -> float | None:
    """Seconds per grid step (one subdivision), or None when BPM is unusable."""
    if not bpm or bpm <= 0 or subdiv <= 0:
        return None
    return (60.0 / bpm) / subdiv


def estimate_phase(onsets_s, grid_s: float) -> float:
    """Best grid phase in [-grid/2, grid/2): where the grid lines actually fall (seconds)."""
    onsets = np.asarray(list(onsets_s), dtype=float)
    if len(onsets) == 0 or grid_s <= 0:
        return 0.0
    frac = (onsets / grid_s) % 1.0
    ang = np.angle(np.mean(np.exp(1j * 2 * math.pi * frac)))
    return (ang / (2 * math.pi)) * grid_s


def dist_to_grid(t: float, phase: float, grid_s: float) -> float:
    """Distance (seconds) from time ``t`` to the nearest grid line."""
    if grid_s <= 0:
        return math.inf
    r = (t - phase) / grid_s
    return abs(r - round(r)) * grid_s


def on_grid(t: float, phase: float, grid_s: float, tol_s: float) -> bool:
    """True when ``t`` lands within ``tol_s`` of a grid line."""
    return dist_to_grid(t, phase, grid_s) <= tol_s
