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

    # --- octave-error correction (backend-agnostic; see mouthtranscriber/octave.py) ---
    # Autocorrelation trackers (pYIN especially) can lock onto a SUBHARMONIC on a
    # continuously-voiced legato line: the Viterbi "stay put" prior beats a real octave
    # jump, so a whole note reads back an octave low (octave_leaps: C4 C5 C4 C5 C4 -> all
    # C4). Runs right after tracking, before voicing/segment, so restoring the pitch also
    # restores the pitch step segmentation needs. A subharmonic f (= true/2) has energy
    # ONLY at its even harmonics (2f,4f,6f coincide with the true fundamental's) and none
    # at its odd harmonics (f,3f,5f); a genuine fundamental always keeps odd-harmonic
    # energy (even a missing-fundamental voice has 3f/5f). So when a frame's odd salience
    # collapses below this fraction of its even salience, f0 is doubled. Octave-DOWN only.
    octave_correct: bool = True
    octave_odd_even_ratio: float = 0.3  # odd(f,3f,5f) < this * even(2f,4f,6f) => subharmonic

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

    # --- grid-aware onset detection (PLAN §5.5; uses the known metronome BPM) ---
    # The per-frame RMS the trackers emit is windowed over frame_length (~93 ms), which
    # smears a short (~40 ms) consonant dip so soft "d" closures between two same-pitch
    # notes are missed and the notes merge (the old coarse-RMS valley splitter, tuned at
    # ~5 dB, also fired on wide tremolo troughs). We fix this with a FINE energy envelope
    # (onset_frame_length, ~23 ms) computed in the pipeline and passed to segment.py, gated
    # by a width test (a "d" is sharp, tremolo is wide). A dip on the fine envelope is a
    # note boundary when it is narrow AND either deep on its own (>= onset_prominence_db)
    # OR shallower but landing on a beat (>= grid_valley_prominence_db within
    # grid_align_tol_s of a grid line). consolidate.py then refuses to fuse two notes
    # across such a grid onset. All gated on a known BPM; with none, the fine detector still
    # runs but only its deep-narrow dips count (no grid promotion, no consolidate guard).
    onset_frame_length: int = 512       # fine RMS window (~23 ms at 22050) for onset dips
    onset_prominence_db: float = 8.0    # a dip this deep on the fine envelope is a boundary
                                        # anywhere (a "d" closure is ~11-15 dB deep here)
    onset_max_width_s: float = 0.065    # ...but ONLY if it is this narrow. A consonant dip is
                                        # sharp (~35-55 ms); smooth tremolo troughs are ~2x wider,
                                        # so a width gate cleanly rejects tremolo without touching
                                        # real onsets. This is the key consonant/tremolo separator.
    grid_valley_prominence_db: float = 4.0  # a shallower (but still narrow) dip counts as a
                                            # boundary only when it lands on a beat (soft "d")
    grid_align_tol_s: float = 0.030     # how close a dip/onset must sit to a grid line to count
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
    # Onset snapping uses a note-value prior, not a nearest-grid round, because a real hum
    # jitters off the beat by more than half a subdivision (a note aimed at beat 2 can land
    # at 0.7 of a beat, closer to the 1/16 at 0.75 than to the beat). A per-note snap then
    # lands it on the wrong subdivision -> off-grid onset + non-integer duration = tied
    # slivers. Instead a small DP over the sequence trades timing deviation against a
    # complexity penalty on each inter-onset interval, so a lone jittered note snaps to the
    # beat while a genuine run of fast notes (consistently short intervals) stays fast. The
    # penalties are in units of "grid steps of onset deviation I would trade to simplify the
    # rhythm by one metrical level". See quantize.py; no fixture uses 1/16, so a strong beat
    # bias is safe. Deviation cost is per grid step at the default subdiv.
    quantize_dev_weight: float = 1.0        # cost per grid step of onset timing deviation
    quantize_eighth_penalty: float = 1.0    # IOI at the eighth (half-beat) metrical level
    quantize_sixteenth_penalty: float = 2.5  # IOI at the sixteenth (quarter-beat) level
    quantize_fine_penalty: float = 4.0      # IOI finer than a sixteenth
    quantize_window_steps: int = 0          # candidate half-width per onset in grid steps
                                            # (0 => one beat = quantize_subdiv steps)
    # Tempo refinement: the user hums TO the metronome but drifts a few percent (a careful
    # one-note-per-click take still came back ~10% slow), so the beat length they PERFORMED is
    # near the one they SET but not equal. Before snapping, scan a bounded band of candidate
    # beat lengths (a ratio of the stated one) and adopt one only if it makes the snapped rhythm
    # strictly SIMPLER (more onsets on beats) without fitting worse. Narrow band so it cannot
    # tempo-halve/double; a take already on the beats is left exactly alone. Only the grid
    # mapping changes - the score still notates at the BPM the user set. See quantize._refine_tempo.
    tempo_refine: bool = True
    tempo_refine_lo: float = 0.8            # search band, as a ratio of the stated beat length
    tempo_refine_hi: float = 1.25           # (0.8..1.25 => roughly +-25% around the set tempo)
    tempo_refine_steps: int = 46            # candidates across the band (~1% tempo resolution)
    tempo_refine_min_notes: int = 4         # need this many onsets to estimate tempo reliably

    # --- chord alternatives / reharmonization (chords.alternatives) ---
    # A four-note chord covers more pitch classes than a triad, so on coverage alone a
    # seventh would always beat the plain triad. This flat penalty per chord tone beyond a
    # triad is subtracted from the coverage fit, so a triad wins a tie and a seventh is only
    # chosen when it genuinely covers more of the melody. Tune up to favor simpler chords.
    chord_complexity_penalty: float = 0.05

    # --- preprocess (PLAN §5.2) ---
    highpass_hz: float = 70.0

    @property
    def hop_s(self) -> float:
        """Duration of one hop in seconds."""
        return self.hop_length / self.sr
