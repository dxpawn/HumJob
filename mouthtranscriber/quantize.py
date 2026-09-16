"""Rhythm quantization to a known-BPM grid (PLAN §5.7).

Because the user hums to a metronome at a fixed BPM, this is snapping - not blind
tempo estimation. We:

  1. Convert onsets/offsets to quarter-note units (beats).
  2. Anchor the first note to beat 0 (its onset absorbs the lead-in / mic latency).
  3. Snap each onset to the grid with a NOTE-VALUE PRIOR, not a per-note nearest-grid
     round. A real hum jitters off the beat by more than half a subdivision (a note
     aimed at beat 2 lands at ~0.7 of a beat, closer to the 1/16 at 0.75 than to the
     beat), so a nearest-grid round lands it on the wrong subdivision - the source of
     off-grid onsets and tied slivers. Instead a small DP over the sequence trades
     timing deviation against a complexity penalty on each inter-onset interval, so a
     lone jittered note snaps to the beat while a genuine run of fast notes stays fast
     (``_snap_onsets`` / ``_ioi_penalty``; penalties in Params).
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
notation exporter. Returns the mean onset residual in seconds (for debugging).
"""

from __future__ import annotations

import math

import numpy as np

from .config import Params
from .model import NoteEvent


def _ioi_penalty(delta: int, sub: int, p) -> float:
    """Complexity of an inter-onset interval of ``delta`` grid steps.

    The metrical denominator of the interval is ``sub / gcd(delta, sub)``: 1 is a whole-beat
    multiple (quarter/half/whole notes and their rests), 2 is the half-beat level (eighth,
    dotted-quarter), 4 is the quarter-beat level (sixteenth), finer beyond that. Simpler
    intervals are cheaper, so the snapper only "spends" timing accuracy on a fine value when
    a run of close onsets makes it clearly intended - a lone note that merely jittered off a
    beat is pulled back to the beat instead.
    """
    if delta <= 0:
        return 0.0
    denom = sub // math.gcd(delta, sub)
    if denom <= 1:
        return 0.0
    if denom == 2:
        return p.quantize_eighth_penalty
    if denom == 4:
        return p.quantize_sixteenth_penalty
    return p.quantize_fine_penalty


def _snap_onsets(x: np.ndarray, sub: int, p) -> list[int]:
    """Assign each onset to an integer grid step via a note-value-prior DP.

    ``x`` are the onset positions in grid steps relative to the first onset (x[0] = 0). The
    first note anchors to step 0; each later note picks a strictly greater step minimizing
    ``dev_weight * |x_i - s_i|`` (timing fidelity) plus ``_ioi_penalty(s_i - s_{i-1})``
    (rhythmic simplicity), solved exactly by DP over a bounded candidate window. This is the
    fix for human timing jitter snapping to the wrong subdivision; see Params.
    """
    n = len(x)
    if n == 0:
        return []
    if n == 1:
        return [0]
    w = p.quantize_window_steps or sub
    w_dev = p.quantize_dev_weight

    cands: list[list[int]] = [[0]]
    for i in range(1, n):
        c = int(round(float(x[i])))
        cands.append(list(range(c - w, c + w + 1)))

    INF = float("inf")
    costs: list[dict[int, float]] = [{0: 0.0}]
    backs: list[dict[int, int]] = [{0: -1}]
    for i in range(1, n):
        cur_c: dict[int, float] = {}
        cur_b: dict[int, int] = {}
        dev_i = w_dev * np.abs(float(x[i]) - np.array(cands[i], dtype=float))
        for s, dev in zip(cands[i], dev_i):
            best, bp = INF, None
            for sp, pc in costs[i - 1].items():
                if s <= sp:
                    continue
                cost = pc + dev + _ioi_penalty(s - sp, sub, p)
                if cost < best:
                    best, bp = cost, sp
            if bp is not None:
                cur_c[s] = best
                cur_b[s] = bp
        if not cur_c:  # window too tight (huge jitter): force a monotic nearest step
            sp = max(costs[i - 1])
            s = max(int(round(float(x[i]))), sp + 1)
            cur_c[s] = costs[i - 1][sp] + w_dev * abs(float(x[i]) - s)
            cur_b[s] = sp
        costs.append(cur_c)
        backs.append(cur_b)

    s = min(costs[-1], key=costs[-1].get)
    steps = [s]
    for i in range(n - 1, 0, -1):
        s = backs[i][s]
        steps.append(s)
    steps.reverse()
    return steps


def quantize(notes: list[NoteEvent], bpm: float, params: Params) -> float:
    """Fill ``start_ql``/``dur_ql`` on each note. Returns mean onset residual (seconds)."""
    if not notes:
        return 0.0

    p = params
    spb = 60.0 / bpm                 # seconds per quarter note (beat)
    grid = 1.0 / p.quantize_subdiv   # grid step in quarter-note units

    onsets_ql = np.array([n.start / spb for n in notes])
    offsets_ql = np.array([n.end / spb for n in notes])

    # Onset positions in grid steps relative to the first note (anchored to 0), then snapped
    # with a note-value-prior DP rather than a per-note nearest-grid round. Anchoring to the
    # first note absorbs the lead-in/mic latency the old circular-mean phase used to correct.
    x = (onsets_ql - onsets_ql[0]) / grid
    on_steps = np.array(_snap_onsets(x, p.quantize_subdiv, p), dtype=int)

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

    # Diagnostic offset: the mean residual between raw and snapped onsets, in seconds.
    residual = float(np.mean(np.abs(x - on_steps))) * grid * spb if len(notes) else 0.0
    return residual
