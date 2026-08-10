"""Turn the continuous f0 contour into discrete notes (PLAN §5.5).

The make-or-break stage. We do NOT round each frame to a pitch. Instead we find
note *boundaries* and take a representative pitch per segment. Boundaries come
from three cues tailored to "da-da-da" humming:

  1. silence     -- full "d" closures fully devoice, so voicing already splits the
                    contour into separate runs (handled upstream).
  2. pitch steps -- a legato slide/step to a new semitone that holds.
  3. energy dips -- a partial "d" closure dips the amplitude without fully devoicing;
                    this is what separates two repeats of the SAME pitch. We detect it
                    on a FINE energy envelope (Params.onset_frame_length, passed in) and
                    keep only NARROW dips (a consonant is sharp, ~40 ms; smooth tremolo
                    troughs are ~2x wider and rejected by a width gate). A narrow dip is
                    a boundary when it is deep on its own, or shallower but sitting on a
                    beat -- because the user hums to a known metronome, so re-articulations
                    land on the grid (see mouthtranscriber/grid.py). This fine + width +
                    grid detector replaced a coarse-RMS valley splitter that both missed
                    short closures (the per-frame RMS window smears them) and false-fired
                    on wide tremolo.

Flux-based onset detectors (librosa.onset) were tried and rejected: tuned for
percussive music, they double-fire around each hummed note. Energy prominence is
far more reliable for voiced humming.

Representative pitch is the median over the stable middle of the note, so scoops
and releases don't drag it off (PLAN §5.6).
"""

from __future__ import annotations

import numpy as np
from scipy.signal import find_peaks

from . import grid as grid_mod
from .config import Params
from .model import Frame, NoteEvent, hz_to_midi


def _smooth_midi(midi: np.ndarray, size: int) -> np.ndarray:
    """NaN-aware running median over the pitch contour."""
    n = len(midi)
    out = np.full(n, np.nan)
    half = size // 2
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        w = midi[lo:hi]
        w = w[~np.isnan(w)]
        if len(w):
            out[i] = np.median(w)
    return out


def _voiced_runs(voiced: np.ndarray) -> list[tuple[int, int]]:
    """Return [start, end) index pairs for each maximal run of True."""
    runs = []
    n = len(voiced)
    i = 0
    while i < n:
        if voiced[i]:
            j = i
            while j < n and voiced[j]:
                j += 1
            runs.append((i, j))
            i = j
        else:
            i += 1
    return runs


def _pitch_step_bounds(rounded: np.ndarray, s: int, e: int, params: Params) -> list[int]:
    """Indices in [s, e) where the pitch steps to a new value that holds."""
    bounds: list[int] = []
    current = rounded[s]
    for i in range(s + 1, e):
        r = rounded[i]
        if np.isnan(r) or np.isnan(current):
            continue
        if abs(r - current) >= params.pitch_split_semitones:
            look = rounded[i : min(i + params.split_stable_frames, e)]
            look = look[~np.isnan(look)]
            if len(look) and np.all(np.abs(look - r) < 0.5):
                bounds.append(i)
                current = r
    return bounds


def segment_notes(
    frames: list[Frame],
    voiced: np.ndarray,
    params: Params,
    bpm: float | None = None,
    energy_db: np.ndarray | None = None,
) -> list[NoteEvent]:
    """Split the voiced contour into notes.

    ``energy_db`` is an optional FINE-resolution energy envelope (dB below peak, one per
    frame; see Params.onset_frame_length) that resolves short "d" closures the coarse
    per-frame RMS smears away. ``bpm``, when known, turns the metronome into a prior:
    a shallow dip on the fine envelope becomes a note boundary if it lands on a beat.
    Both are optional — with neither, this is the old grid-blind behaviour.
    """
    p = params
    if not frames:
        return []

    times = np.array([f.t for f in frames])
    f0 = np.array([f.f0 for f in frames])
    rms = np.array([f.rms for f in frames])
    midi = np.array([hz_to_midi(v) for v in f0])
    midi_s = _smooth_midi(midi, p.smooth_frames)
    rounded = np.round(midi_s)

    peak = float(rms.max()) + 1e-12
    rms_db = 20.0 * np.log10(rms / peak + 1e-12)

    # Fine energy for onset dips; fall back to the coarse RMS when not supplied.
    if energy_db is not None and len(energy_db) >= len(frames):
        fine_db = np.asarray(energy_db[: len(frames)], dtype=float)
    else:
        fine_db = rms_db

    min_frames = max(1, int(round(p.min_note_s / p.hop_s)))
    max_width = p.onset_max_width_s / p.hop_s  # a "d" dip is sharp; tremolo is wide
    hop = p.hop_s
    grid_s = grid_mod.step_s(bpm, p.quantize_subdiv) if bpm else None

    # --- pass A: gather each run's candidates + the confident onsets, to fix the grid phase.
    runs_data: list[tuple[int, int, set[int], list[tuple[int, float]]]] = []
    confident: list[float] = []
    for s, e in _voiced_runs(voiced):
        strong: set[int] = set()
        # (2) pitch steps.
        strong.update(_pitch_step_bounds(rounded, s, e, p))

        # (3) fine-envelope dips (idx, prominence) — only the NARROW ones (a consonant
        # closure; tremolo troughs are ~2x wider and rejected here, which is what lets a
        # sustained tremolo note stay whole). A deep narrow dip is a boundary anywhere; a
        # shallower narrow dip only if it lands on a beat (decided in pass B). This
        # replaces the old coarse-RMS valley splitter, which fired on wide tremolo troughs.
        fine_dips: list[tuple[int, float]] = []
        seg_fine = fine_db[s:e]
        if len(seg_fine) >= 3:
            fv, props = find_peaks(
                -seg_fine, prominence=p.grid_valley_prominence_db,
                distance=min_frames, width=0,
            )
            fine_dips = [
                (int(s + v), float(pr))
                for v, pr, wd in zip(fv, props["prominences"], props["widths"])
                if wd <= max_width
            ]

        runs_data.append((s, e, strong, fine_dips))
        confident.append(float(times[s]))
        confident.extend(float(times[b]) for b in strong)
        confident.extend(float(times[i]) for i, pr in fine_dips if pr >= p.onset_prominence_db)

    phase = grid_mod.estimate_phase(confident, grid_s) if grid_s else 0.0

    # --- pass B: assemble boundaries, promoting grid-aligned shallow dips.
    notes: list[NoteEvent] = []
    for s, e, strong, fine_dips in runs_data:
        cand: set[int] = set(strong)
        for idx, pr in fine_dips:
            deep = pr >= p.onset_prominence_db
            on_beat = grid_s is not None and grid_mod.on_grid(
                float(times[idx]), phase, grid_s, p.grid_align_tol_s
            )
            if deep or on_beat:
                cand.add(idx)

        # Keep only interior boundaries far enough from the edges, then enforce minimum
        # spacing so we never emit a sub-min_note sliver.
        interior = sorted(
            c for c in cand if (c - s) >= min_frames and (e - c) >= min_frames
        )
        bounds = [s]
        for c in interior:
            if c - bounds[-1] >= min_frames:
                bounds.append(c)
        bounds.append(e)

        for b0, b1 in zip(bounds[:-1], bounds[1:]):
            note = _build_note(midi_s, times, b0, b1, hop, p)
            if note is not None:
                notes.append(note)

    # Fragments left by vibrato/breath are fused back downstream, in the
    # backend-agnostic consolidate stage (mouthtranscriber/consolidate.py).
    return notes


def _build_note(midi_s, times, b0, b1, hop, p) -> NoteEvent | None:
    start = float(times[b0])
    end = float(times[b1 - 1] + hop)
    if end - start < p.min_note_s:
        return None

    seg = midi_s[b0:b1]
    seg = seg[~np.isnan(seg)]
    if len(seg) == 0:
        return None

    lo = int(len(seg) * p.note_core_lo)
    hi = int(len(seg) * p.note_core_hi)
    core = seg[lo:hi] if hi > lo else seg
    raw = float(np.median(core))

    midi_int = int(round(raw))
    return NoteEvent(
        start=start,
        end=end,
        midi=midi_int,
        raw_midi=raw,
        cents_offset=(raw - midi_int) * 100.0,
    )
