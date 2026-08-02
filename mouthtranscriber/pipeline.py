"""End-to-end orchestration: audio -> Score (PLAN §4).

A single ``transcribe`` entry point wires the pure-function stages together.
It also exposes the intermediate frames/voicing/onsets so the CLI and eval
harness can visualize and debug without re-running the analysis.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import basicpitch as basicpitch_mod
from . import chords as chords_mod
from . import consolidate as consolidate_mod
from . import key as key_mod
from . import preprocess as preprocess_mod
from . import quantize as quantize_mod
from . import segment as segment_mod
from . import tuning as tuning_mod
from . import voicing as voicing_mod
from .config import Params
from .model import Frame, NoteEvent, Score
from .pitch import make_tracker


@dataclass
class Analysis:
    """Everything the pipeline produced, for debugging/visualization."""
    frames: list[Frame]
    voiced: np.ndarray
    score: Score


def transcribe_array(
    y: np.ndarray,
    params: Params | None = None,
    tempo_bpm: float = 120.0,
    time_sig: tuple[int, int] = (4, 4),
) -> Analysis:
    """Run the full pipeline on an in-memory mono signal."""
    p = params or Params()

    y = preprocess_mod.preprocess(y, p.sr, p.highpass_hz)

    if p.backend == "basic_pitch":
        # Neural backend goes straight from audio to notes; there are no dense
        # per-hop frames or a voicing mask to expose (they stay empty).
        frames: list[Frame] = []
        voiced = np.zeros(0, dtype=bool)
        notes = basicpitch_mod.transcribe_notes(y, p)
    else:
        tracker = make_tracker(p)
        frames = tracker.track(y, p.sr)
        voiced = voicing_mod.decide_voicing(frames, p)
        notes = segment_mod.segment_notes(frames, voiced, p)

    # Backend-agnostic: fuse the fragments every note-producer leaves on a held
    # hum (DSP segmenter shatters on vibrato; basic-pitch splits on salience dips).
    notes = consolidate_mod.consolidate_notes(notes, p)

    tuning_cents = tuning_mod.correct(notes)
    candidates = key_mod.detect_key(notes)
    timing_offset = quantize_mod.quantize(notes, tempo_bpm, p)

    key = candidates[0][1] if candidates else None
    chord_seq = chords_mod.suggest(notes, key, time_sig, p)  # needs quantized notes

    score = Score(
        notes=notes,
        sr=p.sr,
        tempo_bpm=tempo_bpm,
        time_sig=time_sig,
        key=key,
        key_candidates=candidates,
        chords=chord_seq,
        tuning_offset_cents=tuning_cents,
        timing_offset_s=timing_offset,
    )
    return Analysis(frames=frames, voiced=voiced, score=score)


def transcribe(
    path: str,
    params: Params | None = None,
    tempo_bpm: float = 120.0,
    time_sig: tuple[int, int] = (4, 4),
) -> Analysis:
    """Load an audio file and transcribe it."""
    from .audio_io import load_audio

    p = params or Params()
    y, _ = load_audio(path, p.sr)
    return transcribe_array(y, p, tempo_bpm, time_sig)
