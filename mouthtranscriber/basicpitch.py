"""Neural note-transcription backend via Spotify's basic-pitch (PLAN §5.3 alt).

The DSP path (tracker + energy/pitch segmenter) is excellent for staccato
"da-da-da" but brittle on sustained or legato singing: vibrato and amplitude
tremolo trip the boundary detectors and shatter one held note into a run of
fragments. This backend sidesteps all of that by running Spotify's small
pretrained CNN (ICASSP-2022) which maps audio *straight* to note events.

Key facts:
  * Runs via **ONNX Runtime** — no TensorFlow. basic-pitch is installed
    ``--no-deps`` + ``onnxruntime`` so it never drags TF / an old numpy in
    (see README "basic-pitch backend").
  * Instrument-agnostic and polyphonic, so it also copes with sung vowels and
    simple instrument recordings, not just humming.
  * We collapse its (possibly polyphonic) output to a single monophonic melody
    line — this app is single-voice — then hand plain ``NoteEvent``s to the same
    downstream tuning / key / quantize / chord stages.

basic-pitch is imported lazily so the default DSP path keeps working with no
hard dependency on it.
"""

from __future__ import annotations

import os
import tempfile

import numpy as np
import soundfile as sf

from .config import Params
from .model import NoteEvent


def transcribe_notes(y: np.ndarray, params: Params) -> list[NoteEvent]:
    """Audio -> monophonic ``NoteEvent``s (start/end in seconds) via basic-pitch."""
    from basic_pitch.inference import predict  # lazy: heavy import, optional backend

    p = params
    # predict() takes a file path, so write the already-conditioned signal to a
    # temp WAV at our canonical rate. That rate (22050) is basic-pitch's own model
    # rate, so there is no extra resampling loss.
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        sf.write(path, np.asarray(y, dtype=np.float32), p.sr)
        _model_out, _midi, events = predict(
            path,
            onset_threshold=p.bp_onset_threshold,
            frame_threshold=p.bp_frame_threshold,
            minimum_note_length=p.bp_min_note_ms,
            minimum_frequency=p.fmin,   # clamp to the hum range -> kills octave errors
            maximum_frequency=p.fmax,
            melodia_trick=True,
        )
    finally:
        if os.path.exists(path):
            os.unlink(path)

    notes = [_to_note_event(ev) for ev in events]
    notes.sort(key=lambda n: (n.start, n.midi))
    return _monophonic(notes, p)


def _to_note_event(ev) -> NoteEvent:
    """One basic-pitch tuple ``(start_s, end_s, midi, amplitude, pitch_bends)``."""
    start, end, pitch = float(ev[0]), float(ev[1]), int(ev[2])
    amp = float(ev[3]) if len(ev) > 3 else 0.6
    velocity = int(np.clip(round(30 + amp * 97), 1, 127))  # amp 0..1 -> vel 30..127
    return NoteEvent(
        start=start,
        end=end,
        midi=pitch,
        raw_midi=float(pitch),  # model already snaps to a semitone; tuning ~ no-op
        cents_offset=0.0,
        velocity=velocity,
    )


def _monophonic(notes: list[NoteEvent], p: Params) -> list[NoteEvent]:
    """Collapse overlaps to a single voice: the louder note wins the contested span.

    basic-pitch is near-monophonic on humming already; this only cleans up the
    occasional octave/harmonic double that the model emits alongside the melody.
    Notes are assumed pre-sorted by start time.
    """
    kept: list[NoteEvent] = []
    for n in notes:
        if kept and n.start < kept[-1].end:
            prev = kept[-1]
            if n.velocity > prev.velocity:
                prev.end = n.start                 # louder newcomer trims the old note
                if prev.duration < p.min_note_s:
                    kept.pop()                     # ...which collapsed to nothing
                kept.append(n)
            else:
                n.start = prev.end                 # keep old note, start this one after
                if n.duration >= p.min_note_s:
                    kept.append(n)
        else:
            kept.append(n)
    return kept
