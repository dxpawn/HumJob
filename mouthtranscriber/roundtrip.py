"""Reconstruction round-trip: score a transcription against its OWN audio (EVALUATION UPGRADE §3).

Real hums have no ground truth (the user is not a precise singer), so we cannot compute note
F1 on them. This module gives an unsupervised, real-voice sanity signal instead: resynthesise
the transcribed Score to a plain tone track, then ask how well that reconstruction agrees with
the ORIGINAL audio. A transcription that captured the melody produces a resynthesis whose pitch
contour and onsets track the original; a gross error (octave slip, missed/merged note, wrong
onset) shows up as disagreement.

What it does and does NOT prove (stated plainly, and repeated in the report):
  * It CATCHES gross pitch/onset/segmentation mismatch and octave slips - the resynthesis
    diverges from the original where the notes are wrong.
  * It does NOT catch self-consistent errors: a constant tuning offset within 50 cents, or an
    error the resynthesis reproduces, leaves the two contours agreeing. It is a sanity signal,
    not an accuracy number.

The resynthesiser here is deliberately a plain harmonic tone (not the expressive test synth in
tests/make_synthetic): this is production code and must not import the tests, and pitch/onset
agreement does not need vibrato or breath. Everything stays offline (librosa + mir_eval).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import Params
from .model import midi_to_hz

_HARMONICS = (1.0, 0.5, 0.25)  # a voice-ish timbre; enough for a pitch tracker to lock on


@dataclass
class RoundTripScore:
    """One take's agreement between its transcription's resynthesis and the original audio."""
    pitch_agree_50c: float     # fraction of jointly-voiced frames within 50 cents
    median_cents_err: float    # median |cents error| over jointly-voiced frames (nan if none)
    onset_f1: float            # onset-time F1: audio onsets vs transcribed note onsets (50 ms)
    n_joint_frames: int        # frames voiced in BOTH signals (the comparison's support)
    explains_audio: float      # 0..1 summary = mean(pitch_agree_50c, onset_f1)


def resynthesize(notes, sr: int) -> np.ndarray:
    """Render a transcribed note list to a mono tone track (each note a short harmonic tone)."""
    sounded = [n for n in notes if float(n.end) > float(n.start)]
    if not sounded:
        return np.zeros(0, dtype=np.float32)
    total = max(float(n.end) for n in sounded) + 0.05
    y = np.zeros(int(round(total * sr)) + 1, dtype=np.float32)
    for n in sounded:
        s = int(round(float(n.start) * sr))
        e = int(round(float(n.end) * sr))
        m = e - s
        if m <= 1:
            continue
        t = np.arange(m) / sr
        f = midi_to_hz(int(n.midi))
        wave = sum(a * np.sin(2 * np.pi * f * h * t) for h, a in enumerate(_HARMONICS, start=1))
        wave = wave / sum(_HARMONICS)
        env = np.ones(m)
        a = min(m, max(1, int(0.01 * sr)))  # 10 ms attack + release, so onsets are crisp
        env[:a] = np.linspace(0.0, 1.0, a)
        env[-a:] = env[-a:] * np.linspace(1.0, 0.0, a)
        j = min(len(y), s + m)
        if s < len(y) and j > s:
            y[s:j] += (wave * env)[: j - s].astype(np.float32)
    peak = float(np.max(np.abs(y))) + 1e-9
    return (y / peak * 0.9).astype(np.float32)


def _track_f0(y: np.ndarray, p: Params):
    import librosa

    f0, vflag, _ = librosa.pyin(
        y, fmin=p.fmin, fmax=p.fmax, sr=p.sr,
        frame_length=p.frame_length, hop_length=p.hop_length,
    )
    return f0, vflag


def round_trip(y_orig: np.ndarray, notes, params: Params | None = None) -> RoundTripScore:
    """Score how well the transcription (``notes``) explains ``y_orig`` (see module docstring).

    ``notes`` are Score notes with ``.start`` / ``.end`` (seconds) and ``.midi``. Pitch is
    compared frame-by-frame on the frames voiced in BOTH the original and the resynthesis;
    onsets compare librosa's detected onsets on the original against the transcribed onsets.
    """
    p = params or Params()
    resyn = resynthesize(notes, p.sr)
    if len(resyn) == 0 or len(y_orig) == 0:
        return RoundTripScore(0.0, float("nan"), 0.0, 0, 0.0)

    # --- pitch agreement over jointly-voiced frames ---
    f0o, vo = _track_f0(y_orig, p)
    f0r, vr = _track_f0(resyn, p)
    n = min(len(f0o), len(f0r))
    vo, vr = np.asarray(vo[:n], bool), np.asarray(vr[:n], bool)
    f0o, f0r = np.asarray(f0o[:n], float), np.asarray(f0r[:n], float)
    joint = vo & vr & np.isfinite(f0o) & np.isfinite(f0r) & (f0o > 0) & (f0r > 0)
    if joint.any():
        cents = 1200.0 * np.log2(f0o[joint] / f0r[joint])
        pitch_agree = float(np.mean(np.abs(cents) <= 50.0))
        median_err = float(np.median(np.abs(cents)))
    else:
        pitch_agree, median_err = 0.0, float("nan")

    # --- onset agreement: audio onsets vs transcribed note onsets ---
    import librosa
    import mir_eval

    ref_onsets = librosa.onset.onset_detect(
        y=y_orig, sr=p.sr, hop_length=p.hop_length, units="time"
    )
    est_onsets = np.array([float(nn.start) for nn in notes if float(nn.end) > float(nn.start)])
    if len(ref_onsets) == 0 and len(est_onsets) == 0:
        onset_f1 = 1.0
    elif len(ref_onsets) == 0 or len(est_onsets) == 0:
        onset_f1 = 0.0
    else:
        f, _, _ = mir_eval.onset.f_measure(ref_onsets, est_onsets, window=0.05)
        onset_f1 = float(f)

    explains = float(np.mean([pitch_agree, onset_f1]))
    return RoundTripScore(pitch_agree, median_err, onset_f1, int(joint.sum()), explains)
