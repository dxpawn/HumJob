"""Turn the continuous f0 contour into discrete notes (PLAN §5.5).

The make-or-break stage. We do NOT round each frame to a pitch. Instead we find
note *boundaries* and take a representative pitch per segment. Boundaries come
from three cues tailored to "da-da-da" humming:

  1. silence        -- full "d" closures fully devoice, so voicing already splits
                       the contour into separate runs (handled upstream).
  2. energy valleys -- a partial "d" closure dips the amplitude without fully
                       devoicing; a prominent RMS trough marks it. This is what
                       separates two repeats of the SAME pitch.
  3. pitch steps    -- a legato slide/step to a new semitone that holds.

Flux-based onset detectors (librosa.onset) were tried and rejected: tuned for
percussive music, they double-fire around each hummed note. Energy prominence is
far more reliable for voiced humming.

Representative pitch is the median over the stable middle of the note, so scoops
and releases don't drag it off (PLAN §5.6).
"""

from __future__ import annotations

import numpy as np
from scipy.signal import find_peaks

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
) -> list[NoteEvent]:
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

    min_frames = max(1, int(round(p.min_note_s / p.hop_s)))
    hop = p.hop_s

    notes: list[NoteEvent] = []

    for s, e in _voiced_runs(voiced):
        # (2) energy valleys: prominent RMS troughs = partial "d" closures.
        cand: set[int] = set()
        seg_db = rms_db[s:e]
        if len(seg_db) >= 3:
            valleys, _ = find_peaks(
                -seg_db, prominence=p.valley_prominence_db, distance=min_frames
            )
            cand.update(int(s + v) for v in valleys)

        # (3) pitch steps.
        cand.update(_pitch_step_bounds(rounded, s, e, p))

        # Assemble boundaries, keep only interior ones far enough from the edges,
        # then enforce minimum spacing so we never emit a sub-min_note sliver.
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

    if p.merge_same_pitch:
        notes = _merge_same_pitch(notes, p)
    return notes


def _merge_same_pitch(notes: list[NoteEvent], p: Params) -> list[NoteEvent]:
    """Fuse consecutive same-pitch notes that are essentially touching.

    Vibrato and amplitude tremolo make the valley/pitch-step splitters shatter one
    held note into a run of fragments — all at the same pitch and back-to-back
    (gap ~= 0). A genuine re-articulation of the same pitch (a real "da") leaves a
    devoiced gap well above ``same_pitch_gap_s``, so it survives. Different pitches
    never merge.
    """
    if not notes:
        return notes
    out = [notes[0]]
    for n in notes[1:]:
        prev = out[-1]
        if n.midi == prev.midi and (n.start - prev.end) < p.same_pitch_gap_s:
            d1, d2 = prev.duration, n.duration
            tot = d1 + d2
            raw = (prev.raw_midi * d1 + n.raw_midi * d2) / tot if tot > 0 else prev.raw_midi
            prev.end = n.end
            prev.raw_midi = raw
            prev.cents_offset = (raw - prev.midi) * 100.0
            prev.velocity = max(prev.velocity, n.velocity)
        else:
            out.append(n)
    return out


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
