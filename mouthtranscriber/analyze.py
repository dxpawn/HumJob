"""Pitch Finder — general-audio analysis (Key / BPM / Camelot + technical stats).

This is a SEPARATE path from the humming pipeline. `transcribe_array` runs our
monophonic "da-da-da" segmenter, which is wrong for a full polyphonic song. Here we
analyse arbitrary audio (an uploaded mp3/wav) directly from its chroma and onset
features — works on mixed tracks and single instruments alike — and never touch
`segment.py`.

Key detection reuses the Krumhansl profiles/correlation in `key.py` (via
`score_keys`), fed a time-averaged chroma vector instead of a note histogram. BPM,
tuning, and spectral stats come from librosa (already a pinned dependency). Camelot
codes are a fixed lookup off the detected key.
"""

from __future__ import annotations

import numpy as np

from . import key as key_mod
from .tempo import _fold

# Camelot wheel: a major key uses the "B" ring, its relative minor the "A" ring, and
# both share a number. Number by MAJOR tonic pitch-class (C=8B ... going up a fifth
# increments the number). A minor key borrows its relative major's number ((t+3)%12).
_MAJOR_CAMELOT_NUM = {0: 8, 1: 3, 2: 10, 3: 5, 4: 12, 5: 7,
                      6: 2, 7: 9, 8: 4, 9: 11, 10: 6, 11: 1}


def to_camelot(tonic: int, mode: str) -> str:
    """(tonic pitch-class, "major"/"minor") -> Camelot code like ``"8B"`` / ``"8A"``."""
    if mode == "major":
        return f"{_MAJOR_CAMELOT_NUM[tonic % 12]}B"
    return f"{_MAJOR_CAMELOT_NUM[(tonic + 3) % 12]}A"


def camelot_neighbors(code: str) -> list[str]:
    """Harmonically-compatible codes: one step each way on the wheel (same letter) and
    the relative major/minor (same number, other letter). The DJ mixing set."""
    num = int(code[:-1])
    letter = code[-1]
    up = num % 12 + 1
    down = 12 if num == 1 else num - 1
    other = "A" if letter == "B" else "B"
    return [f"{down}{letter}", f"{up}{letter}", f"{num}{other}"]


def _key_name(tonic: int, mode: str) -> str:
    return f"{key_mod._NAMES[tonic]} {mode}"


def _estimate_bpm(y: np.ndarray, sr: int, lo: float = 70.0, hi: float = 190.0):
    """Song-tempo estimate + a rough confidence from beat-interval regularity."""
    import librosa

    oenv = librosa.onset.onset_strength(y=y, sr=sr)
    if not np.any(oenv):
        return 0.0, 0.0
    try:  # librosa >= 0.10
        from librosa.feature.rhythm import tempo as _tempo
    except ImportError:  # older librosa
        from librosa.beat import tempo as _tempo
    bpm = float(np.ravel(_tempo(onset_envelope=oenv, sr=sr, start_bpm=120))[0])
    bpm = _fold(bpm, lo, hi, 120.0)

    conf = 0.0
    try:
        _t, beats = librosa.beat.beat_track(y=y, sr=sr)
        times = librosa.frames_to_time(beats, sr=sr)
        if len(times) > 2:
            iv = np.diff(times)
            conf = float(max(0.0, 1.0 - np.std(iv) / (np.mean(iv) + 1e-9)))
    except Exception:
        conf = 0.0
    return round(bpm, 1), round(conf, 2)


def _db(x: float) -> float:
    return float(20.0 * np.log10(max(x, 0.0) + 1e-12))


def analyze_audio(y: np.ndarray, sr: int) -> dict:
    """Analyse arbitrary audio -> Key / BPM / Camelot + a comprehensive stats block."""
    import librosa

    y = np.asarray(y, dtype=float)
    if y.size == 0:
        raise ValueError("empty audio")
    dur = len(y) / sr

    # --- Key: time-averaged chroma correlated with the 24 Krumhansl profiles ---
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)      # (12, frames)
    chroma_mean = chroma.mean(axis=1)
    ranked = key_mod.score_keys(chroma_mean)             # [(corr, tonic, mode)], best first
    best_corr, best_tonic, best_mode = ranked[0]
    key_name = _key_name(best_tonic, best_mode)
    camelot = to_camelot(best_tonic, best_mode)
    key_candidates = [
        {"key": _key_name(t, m), "camelot": to_camelot(t, m), "score": round(c, 3)}
        for c, t, m in ranked[:8]
    ]

    # --- Tempo ---
    bpm, bpm_conf = _estimate_bpm(y, sr)

    # --- Tuning ---
    tuning = float(librosa.estimate_tuning(y=y, sr=sr))  # fraction of a semitone
    a4 = 440.0 * (2.0 ** (tuning / 12.0))

    # --- Spectral / loudness ---
    centroid = float(np.mean(librosa.feature.spectral_centroid(y=y, sr=sr)))
    rolloff = float(np.mean(librosa.feature.spectral_rolloff(y=y, sr=sr)))
    bandwidth = float(np.mean(librosa.feature.spectral_bandwidth(y=y, sr=sr)))
    zcr = float(np.mean(librosa.feature.zero_crossing_rate(y)))
    rms = librosa.feature.rms(y=y)[0]
    rms_mean = float(np.mean(rms))
    peak = float(np.max(np.abs(y)))
    dyn_range = _db(np.percentile(rms, 95)) - _db(np.percentile(rms, 5))
    energy = round(min(1.0, rms_mean / (peak + 1e-12)), 3) if peak > 0 else 0.0

    # --- Onset density ---
    onsets = librosa.onset.onset_detect(y=y, sr=sr)
    onset_density = round(len(onsets) / dur, 2) if dur > 0 else 0.0

    # --- Pitch-class (chroma) distribution ---
    pc = chroma_mean / (chroma_mean.sum() + 1e-12)
    pc_dist = [{"name": key_mod._NAMES[i], "weight": round(float(pc[i]), 3)} for i in range(12)]

    return {
        "key": key_name,
        "key_score": round(best_corr, 3),
        "camelot": camelot,
        "camelot_neighbors": camelot_neighbors(camelot),
        "bpm": bpm,
        "bpm_half": round(bpm / 2, 1),
        "bpm_double": round(bpm * 2, 1),
        "bpm_confidence": bpm_conf,
        "advanced": {
            "key_candidates": key_candidates,
            "tuning_cents": round(tuning * 100.0, 1),
            "a4_hz": round(a4, 1),
            "spectral_centroid_hz": round(centroid, 1),
            "spectral_rolloff_hz": round(rolloff, 1),
            "spectral_bandwidth_hz": round(bandwidth, 1),
            "zero_crossing_rate": round(zcr, 4),
            "rms_loudness_db": round(_db(rms_mean), 1),
            "peak_dbfs": round(_db(peak), 1),
            "dynamic_range_db": round(dyn_range, 1),
            "duration_s": round(dur, 2),
            "sample_rate": int(sr),
            "onset_density_hz": onset_density,
            "energy": energy,
            "pitch_class_distribution": pc_dist,
        },
    }
