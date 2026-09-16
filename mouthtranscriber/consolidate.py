"""Backend-agnostic note consolidation (PLAN §5.5b).

Every note-production path over-segments a *sustained* hum, each in its own way:

  * the DSP segmenter (pYIN / CREPE) trips its energy-valley and pitch-step
    splitters on vibrato and breath, shattering one held note into a run of
    fragments;
  * basic-pitch ends a note early when frame salience dips, then starts another,
    so one held note comes back as several events.

In every case the fragments *nearly touch* in time (gap ~= 0) and sit within a
fraction of a semitone of each other. A genuine re-articulation (a fresh "da")
leaves a real devoiced gap, and a genuine melodic step moves the pitch past the
tolerance -- so both survive. We fuse greedily, re-deriving the merged pitch from
a duration-weighted mean of the fragments' raw (fractional) pitches, so a couple
of vibrato fragments that happened to round to neighbouring semitones collapse to
the one pitch the singer actually held.

This replaces the old segmenter-internal ``_merge_same_pitch`` (which only fused
*exactly*-equal semitones and never ran for the neural backend).
"""

from __future__ import annotations

import math

from . import grid as grid_mod
from .config import Params
from .model import NoteEvent


def _pitch(n: NoteEvent) -> float:
    """Continuous measured pitch if available, else the integer semitone."""
    return n.raw_midi if not math.isnan(n.raw_midi) else float(n.midi)


def consolidate_notes(
    notes: list[NoteEvent], params: Params, bpm: float | None = None
) -> list[NoteEvent]:
    """Fuse over-segmented fragments of one held note. Runs for every backend.

    A fragment carrying ``hard_onset`` (segment.py split there on a deep "d"-closure dip or
    a held pitch step) is a confident re-articulation and is NEVER fused into its neighbour.
    This is the robust guard: a soft "d" that never devoices leaves same-pitch fragments
    touching (gap ~= 0) and off the beat, so gap/pitch alone would merge them and the grid
    check below misses them when the hum drifts off the metronome. ``bpm``, when known, still
    adds a secondary grid guard for shallow on-beat re-articulations that were not tagged
    hard: two same-pitch fragments are not fused when the second begins on a beat a grid-step
    or more after the first's onset.
    """
    if not params.consolidate or len(notes) < 2:
        return notes

    gap_tol = params.consolidate_gap_s
    semi_tol = params.consolidate_semitones
    grid_s = grid_mod.step_s(bpm, params.quantize_subdiv) if bpm else None
    phase = (
        grid_mod.estimate_phase([n.start for n in notes], grid_s) if grid_s else 0.0
    )

    out = [notes[0]]
    for n in notes[1:]:
        prev = out[-1]
        gap = n.start - prev.end
        fuse = gap <= gap_tol and abs(_pitch(n) - _pitch(prev)) <= semi_tol
        if fuse and n.hard_onset:
            fuse = False  # a confident re-articulation from segment.py: keep it separate
        if fuse and grid_s is not None:
            onset_sep = n.start - prev.start
            if onset_sep >= grid_s * 0.75 and grid_mod.on_grid(
                n.start, phase, grid_s, params.grid_align_tol_s
            ):
                fuse = False  # a real onset on the grid: keep it a separate note

        if fuse:
            d1 = max(prev.duration, 1e-9)
            d2 = max(n.duration, 1e-9)
            raw = (_pitch(prev) * d1 + _pitch(n) * d2) / (d1 + d2)
            prev.end = n.end
            prev.raw_midi = raw
            prev.midi = int(round(raw))
            prev.cents_offset = (raw - prev.midi) * 100.0
            prev.velocity = max(prev.velocity, n.velocity)
        else:
            out.append(n)
    return out
