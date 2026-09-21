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


def _dp_snap(x: np.ndarray, sub: int, p) -> tuple[list[int], float]:
    """Assign each onset to an integer grid step via a note-value-prior DP.

    ``x`` are the onset positions in grid steps relative to the first onset (x[0] = 0). The
    first note anchors to step 0; each later note picks a strictly greater step minimizing
    ``dev_weight * |x_i - s_i|`` (timing fidelity) plus ``_ioi_penalty(s_i - s_{i-1})``
    (rhythmic simplicity), solved exactly by DP over a bounded candidate window. This is the
    fix for human timing jitter snapping to the wrong subdivision; see Params. Returns the
    steps and the total minimal cost (the cost doubles as the objective ``_refine_tempo``
    minimizes over candidate tempos, since deviation is measured in beat-relative grid steps).
    """
    n = len(x)
    if n == 0:
        return [], 0.0
    if n == 1:
        return [0], 0.0
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
    total = costs[-1][s]
    steps = [s]
    for i in range(n - 1, 0, -1):
        s = backs[i][s]
        steps.append(s)
    steps.reverse()
    return steps, total


def _snap_onsets(x: np.ndarray, sub: int, p) -> list[int]:
    """The grid steps from the note-value-prior DP (``_dp_snap``), cost discarded."""
    return _dp_snap(x, sub, p)[0]


def _metrical_badness(steps: list[int], sub: int) -> float:
    """How off-beat a snapped onset sequence is: 0 for a beat, 1 for an eighth, 2 for finer.

    The tempo objective. A hum is mostly one note per beat, so the RIGHT tempo is the one that
    lands the onsets ON beats; a wrong tempo scatters them onto eighth/sixteenth positions. Pure
    snap COST is the wrong objective - a wrong tempo can score a lower cost by aligning onsets to
    off-beat grid lines with small deviations (observed on the scale: 89 bpm mapped [0,2,6,10..],
    a lower cost than the correct all-beats [0,4,8,12..] at 100). Metrical simplicity is not fooled.
    """
    bad = 0.0
    for s in steps:
        o = s % sub
        if o == 0:
            continue
        bad += 1.0 if (sub % 2 == 0 and o == sub // 2) else 2.0
    return bad


def _refine_tempo(onsets_s, spb: float, grid: float, sub: int, p) -> float:
    """Return a beat length (seconds) that better fits the hum's actual tempo than ``spb``.

    The user hums TO a metronome but drifts a few percent (a careful one-note-per-click take
    can still come out ~10% slow), so their true beat is NEAR the stated one but not equal. We
    scan a bounded band of candidate beat lengths (a ratio of ``spb``, so it cannot tempo-
    halve/double) and adopt one only if it makes the snapped rhythm STRICTLY SIMPLER
    (``_metrical_badness``) AND fits at least as tightly (``_dp_snap`` cost) as the stated tempo.
    A take already mapping to clean beats has badness 0 and is left exactly alone; a take whose
    stated-tempo mapping is full of off-beats (the hum drifted) is pulled onto the beats. The
    refined tempo only changes which grid STEPS onsets map to; the score keeps the user's stated
    BPM (grid steps are tempo-independent), so the output notates at the intended tempo.
    """
    n = len(onsets_s)
    if not p.tempo_refine or n < p.tempo_refine_min_notes:
        return spb
    rel = np.asarray(onsets_s, dtype=float) - float(onsets_s[0])

    def eval_at(cand_spb: float) -> tuple[float, float]:
        steps, cost = _dp_snap(rel / cand_spb / grid, sub, p)
        return _metrical_badness(steps, sub), cost

    base_bad, base_cost = eval_at(spb)
    if base_bad == 0:
        return spb  # already all on beats; nothing to simplify

    best_spb, best = spb, (base_bad, base_cost)
    lo, hi, steps = p.tempo_refine_lo, p.tempo_refine_hi, p.tempo_refine_steps
    for k in range(steps):
        r = lo + (hi - lo) * k / (steps - 1)
        bad, cost = eval_at(spb * r)
        # Only a tempo that is BOTH simpler and no worse-fitting than the stated one.
        if bad < base_bad and cost <= base_cost and (bad, cost) < best:
            best, best_spb = (bad, cost), spb * r
    return best_spb


def _restrict_note_values(notes: list[NoteEvent], p) -> None:
    """Force every ``start_ql``/``dur_ql`` onto three dot-free note values in place.

    A readability pass (Params.restrict_note_values): after the normal grid snap, pull each
    onset to the eighth-note grid (keeping onsets strictly increasing) and each duration to
    the nearest of ``allowed_note_values`` - eighth/quarter/half - with ties broken toward
    the quarter, so a dotted-eighth or dotted-quarter length drops to a plain quarter. A
    duration is then clamped to the largest allowed value that still fits before the next
    onset, so notes never overrun and the leftover surfaces as a (dot-free) rest in export.
    With onsets on the eighth grid and durations in this set, music21 can only spell
    eighth/quarter/half notes and never an augmentation dot. See config for the trade-off.
    """
    grid = 0.5  # eighth-note grid, in quarter-note units
    allowed = sorted(p.allowed_note_values)

    prev = None
    for n in notes:
        s = round(n.start_ql / grid) * grid
        if prev is not None and s <= prev:
            s = prev + grid  # never collide with / precede the previous onset
        n.start_ql = float(s)
        prev = s

    for i, n in enumerate(notes):
        # Nearest allowed value; ties (0.75, 1.5) resolve toward the quarter note.
        d = min(allowed, key=lambda v: (abs(n.dur_ql - v), abs(v - 1.0)))
        if i + 1 < len(notes):
            room = notes[i + 1].start_ql - n.start_ql  # a positive multiple of grid
            if d > room + 1e-9:
                d = max(v for v in allowed if v <= room + 1e-9)  # grid always <= room
        n.dur_ql = float(d)


def quantize(notes: list[NoteEvent], bpm: float, params: Params) -> float:
    """Fill ``start_ql``/``dur_ql`` on each note. Returns mean onset residual (seconds)."""
    if not notes:
        return 0.0

    p = params

    # Bluntest mode: one quarter per note, back to back. The user hums one note per click,
    # so note order is the whole story - rhythm needs no estimation. See Params.
    if p.force_all_quarters:
        for i, note in enumerate(notes):
            note.start_ql = float(i)
            note.dur_ql = 1.0
        return 0.0

    spb = 60.0 / bpm                 # seconds per quarter note (beat)
    grid = 1.0 / p.quantize_subdiv   # grid step in quarter-note units

    # Refine the beat length to the tempo actually performed (the hum drifts a few percent off
    # the metronome), so onsets map to the right grid steps. Only the mapping changes; the
    # score keeps the user's stated BPM. See _refine_tempo.
    spb = _refine_tempo([n.start for n in notes], spb, grid, p.quantize_subdiv, p)

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

    # Optional readability pass: collapse the 1/16 grid onto three dot-free note values.
    if p.restrict_note_values:
        _restrict_note_values(notes, p)

    return residual
