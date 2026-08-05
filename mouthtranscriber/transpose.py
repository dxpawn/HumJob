"""Transpose an uploaded MIDI / MusicXML score by a fixed interval (full score).

This backs the Transposer tab's file path (server/static/transposer.js). Unlike the
hummed-melody path - which is monophonic and transposes client-side - an uploaded file
can be polyphonic (chords, multiple parts), so we lean on music21, which parses MIDI and
MusicXML, transposes every voice and the key signature with correct spelling in one call,
and re-serializes. The engraved preview reuses export.render_musicxml_svg (server verovio),
so the browser just drops in the returned SVG.

Everything stays local: the file is parsed in a temp path and never leaves the machine.
"""

from __future__ import annotations

import base64
import os
import tempfile

from . import export as export_mod

# Extensions music21's converter understands for our two input families.
SUPPORTED_EXT = {".mid", ".midi", ".xml", ".musicxml", ".mxl"}


def parse_score(path: str):
    """Parse a MIDI / MusicXML file into a music21 stream. Raises on bad input."""
    from music21 import converter

    return converter.parse(path)


def transpose_stream(s, semitones: int):
    """Return the score transposed by ``semitones`` half steps (0 = unchanged copy).

    music21 transposes notes AND key signatures with correct enharmonic spelling. We
    transpose by a chromatic Interval so the diatonic spelling stays sensible (a bare
    int can spell as an augmented unison); the destination key drives the note letters.
    """
    if not semitones:
        return s
    from music21 import interval

    return s.transpose(interval.Interval(int(semitones)))


def _pretty_key(k) -> str:
    """music21 Key -> 'E-flat major' style display using unicode accidentals."""
    tonic = k.tonic.name.replace("-", "♭").replace("#", "♯")
    return f"{tonic} {k.mode}"


def stream_key(s):
    """Best key for the score: an explicit key signature if present, else analysis.

    Returns ``(display, pc, mode)`` or ``(None, None, None)``. ``pc``/``mode`` let the
    client's To-key dropdown compute a target shift without parsing the display name.
    """
    from music21 import key as m21key

    try:
        k = s.recurse().getElementsByClass(m21key.Key).first()
        if k is None:
            ks = s.recurse().getElementsByClass(m21key.KeySignature).first()
            if ks is not None:
                k = ks.asKey()
        if k is None:
            k = s.analyze("key")
        return _pretty_key(k), int(k.tonic.pitchClass), k.mode
    except Exception:
        return None, None, None


def stream_tempo(s) -> float:
    from music21 import tempo as m21tempo

    mm = s.recurse().getElementsByClass(m21tempo.MetronomeMark).first()
    if mm is not None and mm.number:
        return float(mm.number)
    return 120.0


def stream_time_sig(s) -> tuple[int, int]:
    from music21 import meter

    ts = s.recurse().getElementsByClass(meter.TimeSignature).first()
    if ts is not None:
        return (int(ts.numerator), int(ts.denominator))
    return (4, 4)


def stream_notes(s) -> list[dict]:
    """Flatten to a note list [{midi, start_ql, dur_ql}] for browser playback.

    Chords expand to one entry per pitch; offsets are absolute quarter-note positions
    (music21 offsets are tempo-independent, so the client scales by BPM at play time).
    """
    out: list[dict] = []
    for el in s.flatten().notes:
        start = round(float(el.offset), 4)
        dur = round(float(el.quarterLength), 4)
        if dur <= 0:
            continue
        pitches = el.pitches if el.isChord else [el.pitch]
        for p in pitches:
            out.append({"midi": int(p.midi), "start_ql": start, "dur_ql": dur})
    out.sort(key=lambda n: (n["start_ql"], n["midi"]))
    return out


def stream_to_musicxml(s) -> str:
    from music21.musicxml.m21ToXml import GeneralObjectExporter

    return GeneralObjectExporter(s).parse().decode("utf-8")


def stream_to_midi_bytes(s) -> bytes:
    fd, tmp = tempfile.mkstemp(suffix=".mid")
    os.close(fd)
    try:
        s.write("midi", fp=tmp)
        with open(tmp, "rb") as fh:
            return fh.read()
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def transpose_file(raw: bytes, filename: str, semitones: int) -> dict:
    """Parse an uploaded score, transpose it, and return everything the tab needs.

    Returns a JSON-ready dict: engraved ``svg`` (best-effort), transposed ``musicxml``
    and ``midi_b64``, a flat ``notes`` list for playback, plus the transposed ``key``
    (display + ``key_pc``/``key_mode``), ``tempo_bpm``, ``time_sig`` and ``n_notes``.
    """
    suffix = os.path.splitext(filename or "")[1].lower()
    if suffix not in SUPPORTED_EXT:
        suffix = ".mid"
    fd, src = tempfile.mkstemp(suffix=suffix)
    os.write(fd, raw)
    os.close(fd)
    try:
        score = parse_score(src)
    finally:
        if os.path.exists(src):
            os.unlink(src)

    score = transpose_stream(score, semitones)

    notes = stream_notes(score)
    key_disp, key_pc, key_mode = stream_key(score)
    xml = stream_to_musicxml(score)
    try:
        svg = export_mod.render_musicxml_svg(xml, len(notes))
    except Exception:
        svg = ""  # playback + downloads still work if engraving fails on odd input

    return {
        "svg": svg,
        "musicxml": xml,
        "midi_b64": base64.b64encode(stream_to_midi_bytes(score)).decode("ascii"),
        "notes": notes,
        "n_notes": len(notes),
        "key": key_disp,
        "key_pc": key_pc,
        "key_mode": key_mode,
        "tempo_bpm": round(stream_tempo(score), 3),
        "time_sig": list(stream_time_sig(score)),
        "semitones": int(semitones),
    }
