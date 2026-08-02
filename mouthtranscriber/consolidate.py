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

from .config import Params
from .model import NoteEvent


def _pitch(n: NoteEvent) -> float:
    """Continuous measured pitch if available, else the integer semitone."""
    return n.raw_midi if not math.isnan(n.raw_midi) else float(n.midi)


def consolidate_notes(notes: list[NoteEvent], params: Params) -> list[NoteEvent]:
    """Fuse over-segmented fragments of one held note. Runs for every backend."""
    if not params.consolidate or len(notes) < 2:
        return notes

    gap_tol = params.consolidate_gap_s
    semi_tol = params.consolidate_semitones

    out = [notes[0]]
    for n in notes[1:]:
        prev = out[-1]
        gap = n.start - prev.end
        if gap <= gap_tol and abs(_pitch(n) - _pitch(prev)) <= semi_tol:
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
