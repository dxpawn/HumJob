"""Unit tests for the metronome-grid helpers (mouthtranscriber/grid.py). Fast/pure."""

from __future__ import annotations

import numpy as np

from mouthtranscriber import grid


def test_step_s():
    assert grid.step_s(120, 4) == (0.5 / 4)      # 120 bpm -> 0.5 s beat -> 0.125 s /16th
    assert grid.step_s(60, 1) == 1.0
    assert grid.step_s(0, 4) is None             # unusable BPM
    assert grid.step_s(120, 0) is None


def test_estimate_phase_on_grid():
    g = 0.125
    onsets = [0.0, 0.125, 0.25, 0.5, 1.0]         # all exactly on grid lines
    assert abs(grid.estimate_phase(onsets, g)) < 1e-6


def test_estimate_phase_offset():
    g = 0.125
    off = 0.03
    onsets = [off + k * g for k in range(6)]       # every onset shifted by +30 ms
    assert abs(grid.estimate_phase(onsets, g) - off) < 1e-3


def test_estimate_phase_empty():
    assert grid.estimate_phase([], 0.125) == 0.0


def test_on_grid_and_dist():
    g, phase = 0.125, 0.0
    assert grid.on_grid(0.25, phase, g, tol_s=0.02)       # exactly on a line
    assert grid.on_grid(0.26, phase, g, tol_s=0.02)       # 10 ms off -> within tol
    assert not grid.on_grid(0.31, phase, g, tol_s=0.02)   # ~60 ms off -> off grid
    assert abs(grid.dist_to_grid(0.1875, phase, g) - 0.0625) < 1e-9  # midway between lines


def test_on_grid_respects_phase():
    g, phase = 0.125, 0.03
    assert grid.on_grid(0.03, phase, g, tol_s=0.015)      # phase shifts where lines fall
    assert grid.on_grid(0.155, phase, g, tol_s=0.015)     # 0.03 + 0.125
    assert not grid.on_grid(0.0, phase, g, tol_s=0.015)   # the unshifted origin is now off
