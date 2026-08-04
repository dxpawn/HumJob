"""Tunable parameters for the whole pipeline.

Every knob the DSP stages use lives here so experiments are one-line changes and
the eval harness (M2) can sweep them. Defaults are chosen for clean "da-da-da"
humming captured at a moderate level. See PROJECT PLAN.md §5 for the reasoning
behind each countermeasure these values implement.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Params:
    # --- framing / sample rate ---
    sr: int = 22050               # canonical rate (pYIN-friendly, light); see PLAN §11
    hop_length: int = 256         # ~11.6 ms hop at 22050 Hz
    frame_length: int = 2048      # pYIN analysis window

    # --- pitch tracking (PLAN §5.3) ---
    fmin: float = 65.0            # ~C2, below any realistic hum
    fmax: float = 1050.0          # ~C6, above any realistic hum -> kills octave-up errors
    backend: str = "pyin"         # "pyin" (DSP default), "pesto", "fcnf0", "crepe", or "basic_pitch"

    # --- crepe backend (neural pitch CNN via torchcrepe; used when backend == "crepe") ---
    # "full" is CREPE's largest, most accurate model (state-of-the-art on singing voice)
    # and is what we default to for humming. "tiny" is ~7x faster on CPU but noticeably
    # less precise (more octave slips / cents jitter). torchcrepe's own default decoder
    # is Viterbi (temporal smoothing), which we keep — it steadies the contour and
    # further suppresses octave errors. Accuracy over speed here; a hum is only seconds long.
    crepe_model: str = "full"     # "full" (accurate, default) or "tiny" (fast)

    # --- pesto backend (self-supervised pitch via pesto-pitch; used when backend == "pesto") ---
    # PESTO (2023) matches/beats CREPE on singing voice while being much lighter/faster.
    # "mir-1k_g7" is the package default, trained on the MIR-1K singing-voice set (fits
    # humming). The tracker derives its step from hop_s, so no extra timing knob is needed.
    pesto_model: str = "mir-1k_g7"

    # --- fcnf0 backend (FCNF0++ via penn; used when backend == "fcnf0") ---
    # FCNF0++ (Morrison 2023) is a precision peer to PESTO. PennTracker decodes with argmax
    # (avoids penn's torbi Viterbi extension, which lacks a wheel for our torch build) and
    # derives its hop from hop_s. Weights download from HF on first use.
    # FCNF0++ periodicity uses an entropy scale (voiced ~0.58, unvoiced ~0.05) that is
    # compressed vs the ~[0,1] confidences the voicing thresholds expect, so a raw value
    # sits right on voiced_enter and vibrato/tremolo dips fragment a held note. PennTracker
    # linearly stretches [lo, hi] -> [0, 1] to restore that margin.
    penn_conf_lo: float = 0.10
    penn_conf_hi: float = 0.45

    # --- basic-pitch backend (neural, ONNX; used when backend == "basic_pitch") ---
    # Spotify's ICASSP-2022 model. Robust on sustained/legato singing where the DSP
    # segmenter fragments. See mouthtranscriber/basicpitch.py.
    bp_onset_threshold: float = 0.5   # note-onset confidence (higher => fewer onsets)
    bp_frame_threshold: float = 0.3   # sustain salience (higher => notes end sooner)
    bp_min_note_ms: float = 90.0      # discard notes shorter than this (milliseconds)

    # --- voicing / silence (PLAN §5.4) ---
    voiced_enter: float = 0.55    # confidence to START a voiced region (hysteresis high)
    voiced_exit: float = 0.40     # confidence to STAY voiced (hysteresis low)
    rms_threshold_db: float = -45.0  # energy gate, dB below peak RMS
    max_gap_merge_s: float = 0.035   # bridge only ultra-short unvoiced glitches; real
                                     # "da" closures (~50 ms) are kept as boundaries
    min_note_s: float = 0.08         # discard notes shorter than this (breath/lip noise)

    # --- segmentation (PLAN §5.5) ---
    pitch_split_semitones: float = 0.6  # a stable step this big starts a new note
    split_stable_frames: int = 3        # frames the new pitch must hold to count
    smooth_frames: int = 15             # median-filter window on the pitch contour.
                                        # Must span roughly one vibrato period (~180 ms
                                        # at 5.5 Hz ~ 15 hops) so wide vibrato is
                                        # flattened BEFORE the pitch-step splitter sees
                                        # it — otherwise a held note shatters into a run
                                        # of alternating-semitone fragments. Still far
                                        # shorter than any hummed note, so real steps survive.
    valley_prominence_db: float = 5.0   # energy dip (dB) that marks a "d" closure when
                                        # the note doesn't fully devoice
    # --- note consolidation (backend-agnostic; PLAN §5.5b) ---
    # A held hum over-segments on EVERY backend: the DSP segmenter shatters it on
    # vibrato/breath, and basic-pitch emits several events per note. Both leave a
    # run of near-touching fragments within a fraction of a semitone. This pipeline
    # stage fuses them back. A real re-articulation leaves a devoiced gap, and a
    # real melodic step moves the pitch past the tolerance, so both survive.
    consolidate: bool = True
    consolidate_gap_s: float = 0.045    # only fuse fragments closer than this in time
    consolidate_semitones: float = 0.7  # ...and within this pitch tolerance (covers
                                        # vibrato that crossed pitch_split_semitones)

    # --- tuning correction (PLAN §5.6) ---
    note_core_lo: float = 0.20    # ignore first 20% of a note (scoops)
    note_core_hi: float = 0.80    # ignore last 20% of a note (release/decay)

    # --- rhythm quantization (PLAN §5.7) ---
    quantize_subdiv: int = 4      # grid steps per quarter note (4 => 1/16 grid)
    rest_threshold_ql: float = 0.5  # a gap (in quarters) at/above this is a genuine
                                    # rest; smaller gaps are the "da" consonant stop,
                                    # folded back into the note's length. See quantize.py.

    # --- preprocess (PLAN §5.2) ---
    highpass_hz: float = 70.0

    @property
    def hop_s(self) -> float:
        """Duration of one hop in seconds."""
        return self.hop_length / self.sr
