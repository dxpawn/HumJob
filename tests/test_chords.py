"""Chord-suggestion tests (PLAN §5.9, milestone M6).

Most tests drive ``chords.suggest`` directly with hand-built, already-quantized
notes so the harmony logic is checked without any DSP in the loop. One integration
test runs a real fixture end-to-end.
"""

from __future__ import annotations

import pytest

from mouthtranscriber import chords as chords_mod
from mouthtranscriber import export as export_mod
from mouthtranscriber.config import Params
from mouthtranscriber.model import NoteEvent
from mouthtranscriber.pipeline import transcribe_array
from tests.make_synthetic import FIXTURES, build

TS = (4, 4)  # 4/4: one measure = 4 quarter-note units


def _note(midi: int, start_ql: float, dur_ql: float = 1.0) -> NoteEvent:
    """A quantized note; seconds are irrelevant to chord suggestion."""
    n = NoteEvent(start=start_ql * 0.5, end=(start_ql + dur_ql) * 0.5, midi=midi)
    n.start_ql = start_ql
    n.dur_ql = dur_ql
    return n


def _diatonic_set(key: str) -> set[tuple[int, str]]:
    return {(t.root_pc, t.quality) for t in chords_mod._templates(key)}


# midi: C4=60 D4=62 E4=64 F4=65 G4=67 A4=69 B4=71 C5=72 D5=74 G5=79
def _measure(midis: list[int], bar: int = 0) -> list[NoteEvent]:
    return [_note(m, bar * 4 + i) for i, m in enumerate(midis)]


def test_empty_inputs_return_no_chords():
    assert chords_mod.suggest([], "C major", TS) == []
    assert chords_mod.suggest(_measure([60, 64, 67, 72]), None, TS) == []


def test_c_major_triad_measure_is_I():
    chords = chords_mod.suggest(_measure([60, 64, 67, 72]), "C major", TS)
    assert len(chords) == 1
    assert (chords[0].symbol, chords[0].roman) == ("C", "I")
    assert chords[0].start_ql == 0.0


def test_dominant_triad_measure_is_V():
    chords = chords_mod.suggest(_measure([67, 71, 74, 79]), "C major", TS)
    assert (chords[0].symbol, chords[0].roman) == ("G", "V")


def test_all_suggested_chords_are_diatonic():
    # A wandering C-major tune across three bars.
    notes = (
        _measure([60, 64, 67, 72], 0)
        + _measure([65, 69, 60, 64], 1)
        + _measure([67, 71, 74, 67], 2)
    )
    chords = chords_mod.suggest(notes, "C major", TS)
    allowed = _diatonic_set("C major")
    assert len(chords) == 3
    for c in chords:
        assert (c.root_pc, c.quality) in allowed, c.symbol


def test_cadence_prefers_V_then_I():
    """Dominant bar followed by a tonic-capable bar should resolve V -> I."""
    notes = _measure([67, 71, 74, 67], 0) + _measure([60, 64, 67, 72], 1)
    chords = chords_mod.suggest(notes, "C major", TS)
    assert [c.roman for c in chords] == ["V", "I"]
    # V -> I is root motion of a descending fifth (+5 semitones mod 12).
    assert (chords[1].root_pc - chords[0].root_pc) % 12 == 5


def test_minor_key_uses_harmonic_major_dominant():
    """In F minor, a C-major (with the raised leading tone E) is the V chord."""
    chords = chords_mod.suggest(_measure([60, 64, 67, 72], 0), "F minor", TS)
    assert (chords[0].symbol, chords[0].roman) == ("C", "V")


def test_flat_minor_leading_tone_spelled_sharp():
    """D minor's vii° root is C#, not D-flat, even though it's not in the key sig."""
    by_roman = {t.roman: t for t in chords_mod._templates("D minor")}
    assert "vii°" in by_roman
    lead = by_roman["vii°"]
    assert lead.root_name == "C#"          # raised leading tone, not "D-"
    assert lead.quality == "dim"


def test_measures_span_the_whole_melody():
    # Two full 4/4 bars -> exactly two chords, at ql 0 and 4.
    notes = _measure([60, 62, 64, 65], 0) + _measure([67, 69, 71, 72], 1)
    chords = chords_mod.suggest(notes, "C major", TS)
    assert [c.start_ql for c in chords] == [0.0, 4.0]


def test_twinkle_end_to_end_is_diatonic_in_C():
    """Full pipeline: the classic tune harmonizes to diatonic chords, starting on I."""
    bpm = FIXTURES["twinkle"][0]
    y, sr, _ = build("twinkle")
    score = transcribe_array(y, Params(sr=sr), tempo_bpm=bpm).score
    assert score.key == "C major"
    assert len(score.chords) == 4  # 14 notes of twinkle span four 4/4 bars
    assert score.chords[0].roman == "I"
    allowed = _diatonic_set("C major")
    for c in score.chords:
        assert (c.root_pc, c.quality) in allowed, c.symbol
    # The progression must survive into the engraved sheet as <harmony> elements.
    assert export_mod.to_musicxml_string(score).count("<harmony") == 4
