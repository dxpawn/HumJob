"""Pitch Finder — general-audio analysis (mouthtranscriber/analyze.py).

Covers the Camelot lookup (pure, deterministic), and end-to-end Key/BPM detection on
synthetic signals with a known answer (a C-major triad and a 120 BPM click track), plus
a shape check that the comprehensive stats block carries every field the UI expects.
"""

from __future__ import annotations

import numpy as np

from mouthtranscriber.analyze import analyze_audio, camelot_neighbors, to_camelot

SR = 22050


def _tone(freqs, secs=3.0, sr=SR):
    t = np.arange(int(secs * sr)) / sr
    y = sum(np.sin(2 * np.pi * f * t) for f in freqs)
    return (0.3 * y / len(freqs)).astype(np.float32)


def _click_track(bpm=120, secs=8.0, sr=SR):
    y = np.zeros(int(secs * sr), dtype=np.float32)
    period = int(60.0 / bpm * sr)
    n = int(0.02 * sr)
    click = (np.sin(2 * np.pi * 1000 * np.arange(n) / sr) * np.hanning(n)).astype(np.float32)
    for i in range(0, len(y) - n, period):
        y[i:i + n] += click
    return y


def test_camelot_mapping():
    assert to_camelot(0, "major") == "8B"   # C major
    assert to_camelot(9, "minor") == "8A"    # A minor (relative of C)
    assert to_camelot(7, "major") == "9B"    # G major
    assert to_camelot(4, "minor") == "9A"    # E minor (relative of G)
    assert to_camelot(11, "major") == "1B"   # B major


def test_camelot_neighbors_wrap():
    assert camelot_neighbors("8B") == ["7B", "9B", "8A"]
    assert camelot_neighbors("1B") == ["12B", "2B", "1A"]   # wraps down 1 -> 12
    assert camelot_neighbors("12A") == ["11A", "1A", "12B"]  # wraps up 12 -> 1


def test_detects_c_major_and_camelot():
    y = _tone([261.63, 130.81, 329.63, 392.00])  # C4/C3 + E4 + G4, tonic emphasized
    r = analyze_audio(y, SR)
    assert r["key"] == "C major", r["key"]
    assert r["camelot"] == "8B"
    assert r["key_score"] > 0.5


def test_bpm_on_click_track():
    r = analyze_audio(_click_track(120), SR)
    family = [r["bpm"], r["bpm"] * 2, r["bpm"] / 2]
    assert any(abs(b - 120) < 8 for b in family), r["bpm"]
    assert 0.0 <= r["bpm_confidence"] <= 1.0


def test_result_has_all_fields():
    r = analyze_audio(_tone([261.63, 329.63, 392.00]), SR)
    for k in ("key", "key_score", "camelot", "camelot_neighbors", "bpm",
              "bpm_half", "bpm_double", "bpm_confidence", "advanced"):
        assert k in r
    a = r["advanced"]
    for k in ("key_candidates", "tuning_cents", "a4_hz", "spectral_centroid_hz",
              "spectral_rolloff_hz", "spectral_bandwidth_hz", "zero_crossing_rate",
              "rms_loudness_db", "peak_dbfs", "dynamic_range_db", "duration_s",
              "sample_rate", "onset_density_hz", "energy", "pitch_class_distribution"):
        assert k in a
    assert len(a["pitch_class_distribution"]) == 12
    assert len(r["camelot_neighbors"]) == 3
