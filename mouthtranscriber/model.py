"""Core data model shared by every pipeline stage.

The pipeline is a chain of pure functions over these three types:

    Frame      -- dense, one per hop (~11.6 ms): what the pitch tracker sees
    NoteEvent  -- a discrete note after segmentation
    Score      -- the whole transcription, ready to export
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


def hz_to_midi(hz: float) -> float:
    """Continuous MIDI note number for a frequency in Hz (69 = A4 = 440 Hz)."""
    if hz is None or hz <= 0 or math.isnan(hz):
        return float("nan")
    return 69.0 + 12.0 * math.log2(hz / 440.0)


def midi_to_hz(midi: float) -> float:
    return 440.0 * (2.0 ** ((midi - 69.0) / 12.0))


_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def midi_to_name(midi: int) -> str:
    """E.g. 60 -> 'C4'."""
    return f"{_NAMES[midi % 12]}{midi // 12 - 1}"


@dataclass
class Frame:
    """One analysis frame. f0 is NaN when the frame is unvoiced."""
    t: float           # seconds (frame center)
    f0: float          # Hz, NaN if no pitch
    confidence: float  # 0..1 voiced probability from the tracker
    rms: float         # short-time energy


@dataclass
class NoteEvent:
    """A single discrete note.

    ``raw_midi`` is the measured (fractional) pitch straight from the audio;
    ``midi`` is the integer semitone after global tuning correction (§5.6).
    ``cents_offset`` records how far the raw pitch sat from that semitone, so
    the UI can flag ambiguous notes.
    """
    start: float
    end: float
    midi: int
    velocity: int = 80
    raw_midi: float = float("nan")
    cents_offset: float = 0.0
    # Filled by the quantizer (PLAN §5.7): grid-aligned position and duration in
    # quarter-note units (music21 "quarterLength"). NaN until quantized.
    start_ql: float = float("nan")
    dur_ql: float = float("nan")

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def name(self) -> str:
        return midi_to_name(self.midi)


@dataclass
class Chord:
    """One suggested harmony, covering a single measure (PLAN §5.9).

    Diatonic triad chosen to fit that measure's melody, smoothed across the tune
    by a progression prior. ``root_name`` is music21-style spelling ('E-', 'C#')
    for notation; ``symbol`` is the pretty display form ('E♭m', 'C♯dim').
    """
    measure: int         # 0-based measure index
    start_ql: float      # measure start in quarter-note units (absolute)
    root_pc: int         # root pitch class, 0..11
    root_name: str       # music21 spelling of the root ('F', 'E-', 'C#')
    quality: str         # "maj" | "min" | "dim"
    symbol: str          # pretty display, e.g. "Fm", "E♭", "C♯dim"
    roman: str           # function label, e.g. "i", "V", "iv", "vii°"


@dataclass
class Score:
    """A complete transcription."""
    notes: list[NoteEvent] = field(default_factory=list)
    sr: int = 22050
    tempo_bpm: float = 120.0
    time_sig: tuple[int, int] = (4, 4)
    key: str | None = None
    key_candidates: list[tuple[float, str]] = field(default_factory=list)
    chords: list[Chord] = field(default_factory=list)
    tuning_offset_cents: float = 0.0
    timing_offset_s: float = 0.0  # grid phase found by the quantizer

    def __repr__(self) -> str:
        return (
            f"Score({len(self.notes)} notes, key={self.key}, "
            f"tempo={self.tempo_bpm:.0f}bpm, "
            f"tuning_offset={self.tuning_offset_cents:+.0f}c)"
        )
