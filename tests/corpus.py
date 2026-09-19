"""Assemble a diversified, labelled melody corpus (EVALUATION UPGRADE §4c / §4d).

The eight hand-written FIXTURES saturate the note F1, so a bigger, more varied set of
melodies is needed to keep the diagnostic error-rates (evaluate.diagnostic_scores)
discriminative. This module supplies that set without any copyrighted material or trained
performer:

  * ``PROCEDURAL`` - seeded generators (in-key random walks, arpeggios, interval studies,
    varied rhythms). Deterministic, so the corpus is reproducible.
  * bundled public-domain tunes - tiny tracked JSON specs in tests/data/corpus/*.json.
  * ``melodies_from_score(path, bpm)`` - load ground truth from a MIDI / MusicXML file
    (its own notes ARE the labels), reusing transpose.parse_score + reference.melody_notes.

Every melody is a ``(bpm, sequence)`` pair in the same shape as FIXTURES, so it renders
through make_synthetic.render_sequence at any Expr profile and its ground-truth grid comes
from make_synthetic.grid_of_sequence. Rhythms use eighth-or-coarser values (beats in
{0.5, 1, 1.5, 2}), matching the 0.5 grid the tied-sliver metric measures against.
"""

from __future__ import annotations

import glob
import json
import os

# midi None = rest. Sequences are [(midi_or_None, beats), ...]; beats are quarter notes.
Sequence = list  # list[tuple[int | None, float]]

_HERE = os.path.dirname(os.path.abspath(__file__))
_TUNES_DIR = os.path.join(_HERE, "data", "corpus")

# Diatonic scale-degree offsets from the tonic (semitones), one octave.
_MAJOR = [0, 2, 4, 5, 7, 9, 11]
_MINOR = [0, 2, 3, 5, 7, 8, 10]  # natural minor
_LO, _HI = 55, 76  # keep generated pitches in a comfortable hum range (G3..E5)


def _clamp_octave(midi: int) -> int:
    while midi < _LO:
        midi += 12
    while midi > _HI:
        midi -= 12
    return midi


def _scale_pitches(root: int, mode: list[int], span_octaves: int = 2) -> list[int]:
    """All scale pitches across ``span_octaves`` octaves from ``root``, within [_LO, _HI]."""
    out = []
    for octv in range(span_octaves + 1):
        for off in mode:
            m = root + 12 * octv + off
            if _LO <= m <= _HI:
                out.append(m)
    return sorted(set(out))


def _rng(seed: int):
    import random
    return random.Random(seed)


def gen_walk(seed: int, root: int = 60, mode: list[int] | None = None,
             n: int = 12, bpm: float = 104) -> tuple[str, float, Sequence]:
    """An in-key random walk: step up/down a few scale degrees, quarter notes."""
    mode = mode or _MAJOR
    r = _rng(seed)
    scale = _scale_pitches(root, mode)
    i = scale.index(min(scale, key=lambda m: abs(m - (root + 12))))  # start mid-range
    seq: Sequence = []
    for _ in range(n):
        seq.append((scale[i], 1))
        i = max(0, min(len(scale) - 1, i + r.choice([-2, -1, -1, 1, 1, 2])))
    return (f"walk_{seed}", bpm, seq)


def gen_arpeggio(seed: int, root: int = 57, mode: list[int] | None = None,
                 bpm: float = 112) -> tuple[str, float, Sequence]:
    """A triad (1-3-5-8) arpeggiated up then down, with a couple of random inversions."""
    mode = mode or _MAJOR
    r = _rng(seed)
    root = _clamp_octave(root + r.choice([0, 2, 5, 7]))
    triad = [root, root + mode[2], root + mode[4], root + 12]
    seq: Sequence = [(m, 1) for m in triad] + [(m, 1) for m in reversed(triad[:-1])]
    return (f"arp_{seed}", bpm, seq)


def gen_intervals(seed: int, root: int = 60, mode: list[int] | None = None,
                  n: int = 10, bpm: float = 100) -> tuple[str, float, Sequence]:
    """An interval study: alternate a reference pitch with a scale pitch a random step away."""
    mode = mode or _MAJOR
    r = _rng(seed)
    scale = _scale_pitches(root, mode)
    base = min(scale, key=lambda m: abs(m - (root + 12)))
    bi = scale.index(base)
    seq: Sequence = []
    for k in range(n):
        if k % 2 == 0:
            seq.append((base, 1))
        else:
            j = max(0, min(len(scale) - 1, bi + r.choice([1, 2, 3, 4, -1, -2])))
            seq.append((scale[j], 1))
    return (f"intervals_{seed}", bpm, seq)


def gen_rhythm(seed: int, root: int = 62, mode: list[int] | None = None,
               n: int = 10, bpm: float = 96) -> tuple[str, float, Sequence]:
    """A varied-rhythm line over a random walk: durations drawn from {0.5, 1, 1.5, 2}."""
    mode = mode or _MINOR
    r = _rng(seed)
    scale = _scale_pitches(root, mode)
    i = scale.index(min(scale, key=lambda m: abs(m - (root + 12))))
    durs = [0.5, 1, 1, 1.5, 2]
    seq: Sequence = []
    for _ in range(n):
        seq.append((scale[i], r.choice(durs)))
        i = max(0, min(len(scale) - 1, i + r.choice([-2, -1, 1, 1, 2])))
    return (f"rhythm_{seed}", bpm, seq)


# Deterministic procedural corpus (seeds fixed so the numbers are reproducible).
PROCEDURAL: list[tuple[str, float, Sequence]] = [
    gen_walk(1, root=60, mode=_MAJOR),
    gen_walk(2, root=57, mode=_MINOR),
    gen_arpeggio(3, root=55),
    gen_intervals(4, root=60),
    gen_rhythm(5, root=62, mode=_MINOR),
]


def load_tunes() -> dict[str, tuple[float, Sequence]]:
    """Read the bundled public-domain tune specs (tests/data/corpus/*.json)."""
    out: dict[str, tuple[float, Sequence]] = {}
    for path in sorted(glob.glob(os.path.join(_TUNES_DIR, "*.json"))):
        with open(path, encoding="utf-8") as f:
            spec = json.load(f)
        name = spec.get("name") or os.path.splitext(os.path.basename(path))[0]
        seq = [(None if m is None else int(m), float(b)) for m, b in spec["sequence"]]
        out[name] = (float(spec.get("bpm", 100)), seq)
    return out


def melodies_from_score(path: str, bpm: float) -> tuple[float, Sequence]:
    """Load a MIDI / MusicXML file as a labelled ``(bpm, sequence)`` melody.

    The file's own skyline melody IS the ground truth. Reuses transpose.parse_score +
    reference.melody_notes, then turns the ``{midi, start_ql, dur_ql}`` list into
    ``(midi, beats)`` slots, inserting ``(None, gap)`` rests where the melody leaves a gap.
    Durations are quarter notes, so ``dur_ql`` maps to beats directly.
    """
    from mouthtranscriber import reference as reference_mod
    from mouthtranscriber import transpose as transpose_mod

    score = transpose_mod.parse_score(path)
    notes = reference_mod.melody_notes(score)  # [{midi, start_ql, dur_ql}] sorted by onset

    seq: Sequence = []
    clk = 0.0
    eps = 1e-3
    for nd in notes:
        start, dur = float(nd["start_ql"]), float(nd["dur_ql"])
        gap = start - clk
        if gap > eps:
            seq.append((None, round(gap, 4)))
        seq.append((int(nd["midi"]), round(dur, 4)))
        clk = start + dur
    return float(bpm), seq


def build_corpus(include_fixtures: bool = True) -> dict[str, tuple[float, Sequence]]:
    """The full labelled corpus: procedural + bundled tunes (+ the hand-written FIXTURES).

    A dict ``name -> (bpm, sequence)``, ready for make_synthetic.render_sequence at any Expr
    profile. ``include_fixtures`` keeps the original eight for continuity with the older eval.
    """
    from tests.make_synthetic import FIXTURES

    corpus: dict[str, tuple[float, Sequence]] = {}
    if include_fixtures:
        corpus.update({name: (bpm, seq) for name, (bpm, seq) in FIXTURES.items()})
    corpus.update(load_tunes())
    for name, bpm, seq in PROCEDURAL:
        corpus[name] = (bpm, seq)
    return corpus
