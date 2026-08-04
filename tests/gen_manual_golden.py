"""Generate the structural golden for the Manual-mode client MusicXML builder.

The client `notesToMusicXML` (server/static/manual.js) must produce notation that
matches, note-for-note, what the server's music21 path produces for the same
melody: same spelled pitches, quarter-length durations, tie flags, measure
numbers, clef, and key/time signatures. Layout, ids, and beams are ignored.

Single source of truth for the input melodies is this file. It builds each melody
through `export.build_stream` (the real server engraver) and writes a compact
structural summary to tests/data/generated/manual_golden.json. The node builder
test reads that JSON, runs the JS builder on the SAME `seq`, and asserts equality.
Re-run this whenever the melodies change:

    .venv/Scripts/python.exe tests/gen_manual_golden.py
"""
from __future__ import annotations

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from music21 import clef as m21clef  # noqa: E402
from music21 import key as m21key  # noqa: E402
from music21 import note as m21note  # noqa: E402

from mouthtranscriber.export import build_stream  # noqa: E402
from mouthtranscriber.model import NoteEvent, Score  # noqa: E402

# A committed reference (not under data/generated/, which is gitignored): small,
# derived from music21, and needed by the node builder test on a fresh checkout.
OUT = os.path.join(os.path.dirname(__file__), "data", "manual_golden.json")

# Each melody: seq is the client sequence model - a list of note events
# {midi, durTicks} or rests {rest:true, durTicks}, durTicks in 1/divisions of a
# quarter note. Onsets are implied by the running sum (reflow).
MELODIES = [
    {
        "name": "c_major_8_quarters",
        "key": "C major",
        "timeSig": [4, 4],
        "divisions": 4,
        "seq": [{"midi": m, "durTicks": 4} for m in (60, 62, 64, 65, 67, 69, 71, 72)],
    },
    {
        "name": "cross_barline_tie",
        "key": "C major",
        "timeSig": [4, 4],
        "divisions": 4,
        # three quarters then a half starting on beat 4 -> ties across the barline
        "seq": [
            {"midi": 60, "durTicks": 4},
            {"midi": 64, "durTicks": 4},
            {"midi": 67, "durTicks": 4},
            {"midi": 69, "durTicks": 8},
        ],
    },
    {
        "name": "dotted_values",
        "key": "C major",
        "timeSig": [4, 4],
        "divisions": 4,
        # bar1: dotted half + quarter; bar2: dotted quarter + eighth + half
        "seq": [
            {"midi": 60, "durTicks": 12},
            {"midi": 62, "durTicks": 4},
            {"midi": 64, "durTicks": 6},
            {"midi": 65, "durTicks": 2},
            {"midi": 67, "durTicks": 8},
        ],
    },
    {
        "name": "rest_gap",
        "key": "C major",
        "timeSig": [4, 4],
        "divisions": 4,
        "seq": [
            {"midi": 60, "durTicks": 4},
            {"rest": True, "durTicks": 4},
            {"midi": 62, "durTicks": 8},
        ],
    },
    {
        "name": "flat_key_f_major",
        "key": "F major",
        "timeSig": [4, 4],
        "divisions": 4,
        # 70 is Bb: must spell as B- (flat) in F major
        "seq": [{"midi": m, "durTicks": 4} for m in (65, 67, 69, 70)],
    },
    {
        "name": "low_range_bass_clef",
        "key": "C major",
        "timeSig": [4, 4],
        "divisions": 4,
        "seq": [{"midi": m, "durTicks": 4} for m in (40, 43, 45, 47)],
    },
    {
        "name": "three_four",
        "key": "G major",
        "timeSig": [3, 4],
        "divisions": 4,
        # 66 is F#: sharp key keeps sharp spelling
        "seq": [
            {"midi": 67, "durTicks": 4},
            {"midi": 66, "durTicks": 4},
            {"midi": 62, "durTicks": 4},
            {"midi": 67, "durTicks": 12},
        ],
    },
]


def _score_from_seq(mel: dict) -> Score:
    div = mel["divisions"]
    notes: list[NoteEvent] = []
    onset_ticks = 0
    for ev in mel["seq"]:
        dur_ticks = ev["durTicks"]
        if not ev.get("rest"):
            start_ql = onset_ticks / div
            dur_ql = dur_ticks / div
            n = NoteEvent(start=start_ql, end=start_ql + dur_ql, midi=ev["midi"])
            n.start_ql = start_ql
            n.dur_ql = dur_ql
            notes.append(n)
        onset_ticks += dur_ticks
    beats, unit = mel["timeSig"]
    return Score(notes=notes, key=mel["key"], time_sig=(beats, unit))


def _structural(mel: dict) -> dict:
    """Run the melody through the real server engraver and summarize structure."""
    part = build_stream(_score_from_seq(mel), include_chords=False)

    cl = part.recurse().getElementsByClass(m21clef.Clef).first()
    clef_sign = cl.sign if cl else "G"

    ks = part.recurse().getElementsByClass(m21key.KeySignature).first()
    fifths = int(ks.sharps) if ks else 0

    events = []
    for m in part.getElementsByClass("Measure"):
        for el in m.notesAndRests:
            if isinstance(el, m21note.Note):
                p = el.pitch
                events.append(
                    {
                        "measure": int(m.number),
                        "step": p.step,
                        "alter": int(p.alter),
                        "octave": int(p.octave),
                        "ql": float(el.quarterLength),
                        "tie": el.tie.type if el.tie else None,
                    }
                )
            else:  # Rest
                events.append(
                    {
                        "measure": int(m.number),
                        "rest": True,
                        "ql": float(el.quarterLength),
                    }
                )
    return {
        "name": mel["name"],
        "key": mel["key"],
        "timeSig": mel["timeSig"],
        "divisions": mel["divisions"],
        "clef": clef_sign,
        "fifths": fifths,
        "seq": mel["seq"],
        "events": events,
    }


def main() -> None:
    golden = [_structural(m) for m in MELODIES]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(golden, fh, indent=2)
    print(f"wrote {OUT} ({len(golden)} melodies)")
    for g in golden:
        assert not any(math.isnan(e.get("ql", 0.0)) for e in g["events"])
        print(f"  {g['name']:22s} clef={g['clef']} events={len(g['events'])}")


if __name__ == "__main__":
    main()
