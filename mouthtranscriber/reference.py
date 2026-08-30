"""Reduce an uploaded MIDI / MusicXML score to a single karaoke melody line.

This backs the Sing-Along tab (server/static/singalong.js): the user uploads a score,
we play it as a reference and track the voice against it. Scoring needs ONE note
sounding at a time, so a possibly polyphonic file (chords, multiple parts) is reduced
to its skyline - the highest sounding pitch at each moment - and ties are stripped so a
held note is a single target, not two onsets.

We deliberately do NOT reuse transpose.stream_notes: it expands chords to every pitch,
merges all parts, and keeps ties (a tied note becomes two onsets). Those are correct for
the Transposer's playback but wrong for a monophonic sing-against target. Everything else
(parsing, key / tempo / time signature, the engraved SVG) reuses the transpose helpers.

Everything stays local: the file is parsed in a temp path and never leaves the machine.
"""

from __future__ import annotations

import os
import tempfile

from . import export as export_mod
from . import transpose as transpose_mod

SUPPORTED_EXT = transpose_mod.SUPPORTED_EXT

# Above this many notes in the ORIGINAL score, skip engraving the preview SVG (verovio
# on a huge arrangement is slow and the sheet is only a nicety). Playback still works.
_SVG_NOTE_CAP = 2000
_EPS = 1e-6


class NoMelodyError(ValueError):
    """Raised when a file has no pitched notes to sing against (rests / percussion)."""


def _top_midi(el) -> int:
    """Highest MIDI in a note/chord element (the skyline candidate at its onset)."""
    if el.isChord:
        return int(el.sortAscending().pitches[-1].midi)
    return int(el.pitch.midi)


def melody_notes(score) -> list[dict]:
    """Skyline reduction to a monophonic, non-overlapping melody line.

    Returns ``[{midi, start_ql, dur_ql}]`` sorted by onset, in quarter-note units
    (tempo-independent; the browser scales by BPM at play time, like stream_notes).

    Method: strip ties (so a held note is one target), take the top pitch of each
    note/chord, sort by (start, -midi), then a greedy sweep keeps the highest sounding
    note. A higher note that starts inside the current one truncates it and takes over;
    a lower or equal overlapping note is dropped. Known v1 limitation: a lower held note
    is NOT resumed after the higher note that masked it ends (a gap appears instead).
    """
    from music21 import stream as m21stream

    try:
        score = score.stripTies()
    except Exception:
        pass  # some inputs (e.g. bare MIDI) have no ties to strip

    cands: list[list[float]] = []  # [start_ql, end_ql, midi], mutable for truncation
    for el in score.flatten().notes:
        dur = round(float(el.quarterLength), 4)
        if dur <= 0:
            continue  # grace notes carry no duration
        start = round(float(el.offset), 4)
        cands.append([start, round(start + dur, 4), _top_midi(el)])

    # Highest pitch first at any shared onset, so the skyline note is seen before the
    # notes it masks.
    cands.sort(key=lambda c: (c[0], -c[2]))

    out: list[list[float]] = []
    for start, end, midi in cands:
        if out and start < out[-1][1] - _EPS:  # overlaps the current melody note
            prev = out[-1]
            if midi > prev[2] and start > prev[0] + _EPS:
                prev[1] = start  # truncate the lower held note where this one enters
                out.append([start, end, midi])
            # else: lower/equal (or same onset) -> masked, dropped
            continue
        out.append([start, end, midi])

    return [
        {"midi": int(m), "start_ql": round(s, 4), "dur_ql": round(e - s, 4)}
        for s, e, m in out
        if e - s > _EPS
    ]


def n_tempos(score) -> int:
    """How many DISTINCT tempo values the score carries (v1 uses the first; >1 -> warn).

    We count distinct values, not raw marks: MIDI stores tempo per track, so music21
    reads back one identical MetronomeMark per part - that is one tempo, not several.
    A genuine tempo change (different BPM at a later offset) still counts.
    """
    from music21 import tempo as m21tempo

    values = {
        round(float(mm.number), 3)
        for mm in score.recurse().getElementsByClass(m21tempo.MetronomeMark)
        if mm.number
    }
    return len(values)


def reference_payload(raw: bytes, filename: str) -> dict:
    """Parse an uploaded score and return everything the Sing-Along tab needs.

    Returns a JSON-ready dict: the reduced ``melody`` ([{midi, start_ql, dur_ql}]),
    ``n_notes`` / ``duration_ql``, the ``key`` (display + ``key_pc`` / ``key_mode``),
    ``tempo_bpm``, ``n_tempos`` (constant-tempo assumption; >1 warns the user),
    ``time_sig``, and a best-effort engraved ``svg`` of the ORIGINAL score (so the user
    sees the real sheet, not the reduced line). Raises NoMelodyError when there is
    nothing pitched to sing against.
    """
    suffix = os.path.splitext(filename or "")[1].lower()
    if suffix not in SUPPORTED_EXT:
        suffix = ".mid"
    fd, src = tempfile.mkstemp(suffix=suffix)
    os.write(fd, raw)
    os.close(fd)
    try:
        score = transpose_mod.parse_score(src)
    finally:
        if os.path.exists(src):
            os.unlink(src)

    melody = melody_notes(score)
    if not melody:
        raise NoMelodyError("no melody notes found in this file")

    key_disp, key_pc, key_mode = transpose_mod.stream_key(score)
    duration_ql = max(n["start_ql"] + n["dur_ql"] for n in melody)

    total_notes = len(score.flatten().notes)
    svg = ""
    if total_notes <= _SVG_NOTE_CAP:
        try:
            xml = transpose_mod.stream_to_musicxml(score)
            svg = export_mod.render_musicxml_svg(xml, total_notes)
        except Exception:
            svg = ""  # playback + scoring still work if engraving fails on odd input

    return {
        "svg": svg,
        "melody": melody,
        "n_notes": len(melody),
        "duration_ql": round(duration_ql, 4),
        "key": key_disp,
        "key_pc": key_pc,
        "key_mode": key_mode,
        "tempo_bpm": round(transpose_mod.stream_tempo(score), 3),
        "n_tempos": n_tempos(score),
        "time_sig": list(transpose_mod.stream_time_sig(score)),
    }
