"""Tests for the diversified corpus assembly (EVALUATION UPGRADE §4c / §4d).

Pins that the procedural generators are deterministic, in-key and in-range; that the bundled
public-domain tunes load; and that ``melodies_from_score`` round-trips a MIDI file (its notes
are the ground truth). No pipeline run, so these are fast.
"""

from __future__ import annotations

import os
import tempfile

from tests import corpus
from tests.make_synthetic import grid_of_sequence, render_sequence


def test_procedural_is_deterministic():
    a = corpus.gen_walk(1)
    b = corpus.gen_walk(1)
    assert a == b
    assert corpus.gen_walk(1) != corpus.gen_walk(2)  # different seed -> different melody


def test_procedural_pitches_in_range_and_in_key():
    _name, _bpm, seq = corpus.gen_walk(1, root=60, mode=corpus._MAJOR)
    scale_pcs = {pc % 12 for pc in corpus._MAJOR}
    for midi, beats in seq:
        assert corpus._LO <= midi <= corpus._HI
        assert (midi - 60) % 12 in scale_pcs
        assert beats in (0.5, 1, 1.5, 2)


def test_rhythm_generator_uses_varied_but_simple_durations():
    _name, _bpm, seq = corpus.gen_rhythm(5)
    durs = {beats for _m, beats in seq}
    assert durs.issubset({0.5, 1, 1.5, 2})
    assert len(durs) > 1  # actually varied


def test_bundled_tunes_load():
    tunes = corpus.load_tunes()
    assert {"ode_to_joy", "mary_lamb", "frere_jacques"} <= set(tunes)
    bpm, seq = tunes["mary_lamb"]
    assert bpm > 0 and len(seq) > 0
    # every entry is (midi_or_None, beats)
    for midi, beats in seq:
        assert midi is None or isinstance(midi, int)
        assert beats > 0


def test_build_corpus_includes_all_sources():
    full = corpus.build_corpus(include_fixtures=True)
    assert "c_major_scale" in full          # a hand-written fixture
    assert "ode_to_joy" in full             # a bundled tune
    assert any(k.startswith("walk_") for k in full)   # a procedural melody
    lean = corpus.build_corpus(include_fixtures=False)
    assert "c_major_scale" not in lean


def test_corpus_melody_renders_and_grids():
    """Every corpus melody must render through the synth and yield a matching ground grid."""
    _name, (bpm, seq) = next(iter(corpus.load_tunes().items()))
    y, sr, refs = render_sequence(seq, bpm)
    starts, durs = grid_of_sequence(seq)
    assert len(refs) == len(starts) == len(durs)
    assert len(y) > 0


def test_melodies_from_score_round_trip():
    """A MIDI file's own notes are the ground truth: write one, load it, recover the melody."""
    from music21 import note as m21note
    from music21 import stream as m21stream

    src_seq = [(60, 1), (62, 1), (None, 1), (64, 2), (65, 0.5), (65, 0.5)]
    s = m21stream.Stream()
    for midi, beats in src_seq:
        if midi is None:
            s.append(m21note.Rest(quarterLength=beats))
        else:
            s.append(m21note.Note(midi=midi, quarterLength=beats))

    fd, path = tempfile.mkstemp(suffix=".mid")
    os.close(fd)
    try:
        s.write("midi", fp=path)
        bpm, got = corpus.melodies_from_score(path, bpm=100)
    finally:
        if os.path.exists(path):
            os.remove(path)

    assert bpm == 100
    sounded_src = [(m, b) for m, b in src_seq if m is not None]
    sounded_got = [(m, round(b, 2)) for m, b in got if m is not None]
    assert sounded_got == sounded_src
    # the rest between note 2 and note 3 survives as a (None, ~1) gap
    assert any(m is None and abs(b - 1.0) < 1e-2 for m, b in got)
