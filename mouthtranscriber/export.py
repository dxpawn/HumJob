"""Export a Score to MIDI, MusicXML, and engraved sheet-music SVG (PLAN §5.10).

MIDI keeps the raw performance timing (note seconds). MusicXML uses the quantized
grid (``start_ql``/``dur_ql`` from quantize.py), a proper time signature and key
signature, rests, and enharmonic spelling that follows the key (flats in flat
keys). Sheet SVG is engraved locally by verovio — no MuseScore/LilyPond needed.
"""

from __future__ import annotations

import math
import re

from .model import Score, midi_to_name

# music21 spelling: '-' = flat, '#' = sharp.
_SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_FLAT = ["C", "D-", "D", "E-", "E", "F", "G-", "G", "A-", "A", "B-", "B"]

# Chord-quality -> music21 ChordSymbol kind.
_KIND = {"maj": "major", "min": "minor", "dim": "diminished"}


def _build_midi(score: Score):
    import pretty_midi

    pm = pretty_midi.PrettyMIDI(initial_tempo=score.tempo_bpm)
    inst = pretty_midi.Instrument(program=54)  # "Voice Oohs"
    for n in score.notes:
        inst.notes.append(
            pretty_midi.Note(
                velocity=int(n.velocity),
                pitch=int(n.midi),
                start=float(n.start),
                end=float(max(n.end, n.start + 0.02)),
            )
        )
    pm.instruments.append(inst)
    return pm


def to_midi(score: Score, path: str) -> str:
    _build_midi(score).write(path)
    return path


def midi_bytes(score: Score) -> bytes:
    import os
    import tempfile

    fd, tmp = tempfile.mkstemp(suffix=".mid")
    os.close(fd)
    try:
        _build_midi(score).write(tmp)
        with open(tmp, "rb") as fh:
            return fh.read()
    finally:
        os.unlink(tmp)


def _spell(midi: int, use_flats: bool) -> str:
    pc = midi % 12
    octave = midi // 12 - 1
    name = (_FLAT if use_flats else _SHARP)[pc]
    return f"{name}{octave}"


def _parse_key(key_str: str | None):
    """Return (music21 Key or None, use_flats)."""
    from music21 import key as m21key

    if not key_str:
        return None, True
    try:
        tonic, mode = key_str.split()
        k = m21key.Key(tonic, mode.lower())
        return k, (k.sharps < 0)
    except Exception:
        return None, True


def build_stream(score: Score, include_chords: bool = True):
    """Build a notated music21 Part (measures, beams) from the quantized score.

    When ``include_chords`` and the score carries suggested chords, each measure's
    chord is inserted as a music21 ChordSymbol so it engraves above the staff.
    """
    from music21 import stream, note as m21note, meter, tempo as m21tempo

    part = stream.Part()
    part.append(m21tempo.MetronomeMark(number=score.tempo_bpm))
    part.append(meter.TimeSignature(f"{score.time_sig[0]}/{score.time_sig[1]}"))
    k, use_flats = _parse_key(score.key)
    if k is not None:
        part.append(k)

    cursor = 0.0
    for n in score.notes:
        start = n.start_ql if not math.isnan(n.start_ql) else cursor
        dur = n.dur_ql if not math.isnan(n.dur_ql) else 1.0
        if start > cursor + 1e-3:  # gap -> rest
            part.append(m21note.Rest(quarterLength=round(start - cursor, 4)))
        m = m21note.Note(_spell(int(n.midi), use_flats))
        m.quarterLength = max(0.25, round(dur, 4))
        part.append(m)
        cursor = start + m.quarterLength

    if include_chords and score.chords:
        _add_chord_symbols(part, score, end_ql=cursor)

    return part.makeNotation()


def _add_chord_symbols(part, score: Score, end_ql: float) -> None:
    """Insert one ChordSymbol per measure at its downbeat (best-effort)."""
    from music21 import harmony

    for ch in score.chords:
        if ch.start_ql >= end_ql - 1e-6:  # no notes here -> nothing to sit over
            continue
        try:
            cs = harmony.ChordSymbol(root=ch.root_name, kind=_KIND[ch.quality])
            cs.writeAsChord = False
            part.insert(round(ch.start_ql, 4), cs)
        except Exception:
            # A spelling music21 won't parse should never break notation export.
            continue


def to_musicxml(score: Score, path: str) -> str:
    build_stream(score).write("musicxml", fp=path)
    return path


def to_musicxml_string(score: Score) -> str:
    from music21.musicxml.m21ToXml import GeneralObjectExporter

    return GeneralObjectExporter(build_stream(score)).parse().decode("utf-8")


def sheet_svg_string(score: Score, title: str = "") -> str:
    """Engrave the score to a standalone sheet-music SVG string via verovio."""
    import verovio

    xml = to_musicxml_string(score)
    tk = verovio.toolkit()
    tk.setOptions(
        {
            "pageWidth": 2100,
            "pageHeight": 900 + 300 * max(0, len(score.notes) // 24),
            "scale": 45,
            "adjustPageHeight": True,
            "header": "none",
            "footer": "none",
        }
    )
    if not tk.loadData(xml):
        raise RuntimeError("verovio failed to load the MusicXML")
    svg = tk.renderToSVG(1)
    # verovio draws black-on-transparent; add a white "paper" background so the
    # sheet is readable on any theme (and looks like real sheet music).
    return re.sub(
        r"(<svg\b[^>]*>)",
        r'\1<rect width="100%" height="100%" fill="#fff"/>',
        svg,
        count=1,
    )


def render_sheet_svg(score: Score, path: str, title: str = "") -> str:
    """Engrave the score to a standalone sheet-music SVG file via verovio."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(sheet_svg_string(score, title))
    return path


def summary(score: Score) -> str:
    seq = " ".join(midi_to_name(n.midi) for n in score.notes)
    return f"{len(score.notes)} notes: {seq}"


def chord_summary(score: Score) -> str:
    """One-line progression, e.g. '| Fm (i) | E♭ (VII) | Fm (i) |'."""
    if not score.chords:
        return ""
    cells = " | ".join(f"{c.symbol} ({c.roman})" for c in score.chords)
    return f"| {cells} |"
