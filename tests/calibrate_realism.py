"""Measure the realism knobs from REAL hum recordings (EVALUATION UPGRADE §5).

The synthetic profiles REALISTIC / HARD (tests/make_synthetic.py) are only honest if their
numbers reflect what a real voice actually does. This script decodes each real ``.webm`` take,
runs the tracker, and MEASURES the quantities the Expr knobs model:

  * vibrato depth (cents) and rate (Hz) - from the FFT of the de-drifted cents contour of held notes,
  * pitch drift (cents) - the slow wander of that contour, per held note,
  * timing jitter (ms) - onset spacing vs the metronome grid,
  * closure depth (dB) and width (ms) - the "d" energy dips between notes,
  * shimmer (dB) - cycle-scale amplitude variation within held notes.

It prints the per-take numbers and their distribution, then emits a FITTED ``Expr(...)`` built
from the medians, so the report can say the profiles are justified by measured data rather than
guessed. This is a diagnosis/print tool (like tests/diagnose_recorded.py), not a CI gate; the
measurements are estimates and are labelled as such.

    .venv/Scripts/python.exe tests/calibrate_realism.py tests/data/recorded/*.json
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mouthtranscriber.config import Params  # noqa: E402
from mouthtranscriber.model import hz_to_midi  # noqa: E402
from mouthtranscriber.pipeline import transcribe_array  # noqa: E402


def _moving_average(x: np.ndarray, w: int) -> np.ndarray:
    if w <= 1 or len(x) < w:
        return x.copy()
    k = np.ones(w) / w
    return np.convolve(x, k, mode="same")


def _held_note_contours(frames, notes, hop_s, min_dur_s=0.30):
    """Cents contours (relative to each note's median) for notes long enough to hold a vibrato."""
    if not frames:
        return []
    f0 = np.array([fr.f0 if (fr.f0 and fr.f0 == fr.f0) else np.nan for fr in frames])
    out = []
    for n in notes:
        if float(n.end) - float(n.start) < min_dur_s:
            continue
        lo = int((float(n.start) + 0.05) / hop_s)
        hi = int((float(n.end) - 0.05) / hop_s)
        seg = f0[max(0, lo):min(len(f0), hi)]
        seg = seg[np.isfinite(seg) & (seg > 0)]
        if len(seg) < 8:
            continue
        cents = 1200.0 * np.log2(seg / np.median(seg))
        out.append(cents)
    return out


def measure_take(frames, notes, bpm, hop_s, y=None, sr=None) -> dict:
    """Measure the realism quantities for one take. Pure given the transcription + audio.

    Returns a dict of estimates (nan where a take is too short to measure something). Kept
    free of any file I/O so it can be unit-tested on a synthetic render with known knobs.
    """
    m: dict[str, float] = {}

    # --- vibrato + drift, from held-note cents contours ---
    contours = _held_note_contours(frames, notes, hop_s)
    vib_depths, vib_rates, drifts = [], [], []
    smooth_w = max(1, int(round(0.30 / hop_s)))  # ~300 ms: splits drift (slow) from vibrato (fast)
    for cents in contours:
        drift_comp = _moving_average(cents, smooth_w)
        vib_comp = cents - drift_comp
        vib_depths.append((np.percentile(vib_comp, 95) - np.percentile(vib_comp, 5)) / 2.0)
        drifts.append(np.percentile(drift_comp, 95) - np.percentile(drift_comp, 5))
        if len(vib_comp) >= 8:
            spec = np.abs(np.fft.rfft(vib_comp * np.hanning(len(vib_comp))))
            freqs = np.fft.rfftfreq(len(vib_comp), d=hop_s)
            band = (freqs >= 3.0) & (freqs <= 9.0)
            if band.any() and spec[band].max() > 0:
                vib_rates.append(float(freqs[band][np.argmax(spec[band])]))
    m["vibrato_cents"] = float(np.median(vib_depths)) if vib_depths else float("nan")
    m["vibrato_rate"] = float(np.median(vib_rates)) if vib_rates else float("nan")
    m["drift_cents"] = float(np.median(drifts)) if drifts else float("nan")
    m["n_held"] = len(contours)

    # --- timing jitter vs the metronome grid ---
    onsets = np.array([float(n.start) for n in notes])
    if len(onsets) >= 3 and bpm:
        beat_s = 60.0 / bpm
        rel = onsets - onsets[0]
        grid = np.round(rel / beat_s) * beat_s
        m["timing_jitter_ms"] = float(np.std(rel - grid) * 1000.0)
    else:
        m["timing_jitter_ms"] = float("nan")

    # --- closure depth / width and shimmer, from a fine energy envelope ---
    if y is not None and sr and len(y):
        import librosa
        hop = int(round(hop_s * sr))
        rms = librosa.feature.rms(y=y, frame_length=512, hop_length=hop)[0]
        db = 20.0 * np.log10(rms / (rms.max() + 1e-12) + 1e-12)
        # dips: local minima at least 3 dB below the higher neighbouring peak
        depths, widths = [], []
        i = 1
        while i < len(db) - 1:
            if db[i] < db[i - 1] and db[i] <= db[i + 1]:
                lpk = db[:i].max() if i else db[i]
                rpk = db[i + 1:].max() if i + 1 < len(db) else db[i]
                depth = min(lpk, rpk) - db[i]
                if depth >= 3.0:
                    lo = i
                    while lo > 0 and db[lo] < min(lpk, rpk) - depth / 2:
                        lo -= 1
                    hi = i
                    while hi < len(db) - 1 and db[hi] < min(lpk, rpk) - depth / 2:
                        hi += 1
                    depths.append(depth)
                    widths.append((hi - lo) * hop_s * 1000.0)
            i += 1
        m["closure_depth_db"] = float(np.median(depths)) if depths else float("nan")
        m["closure_width_ms"] = float(np.median(widths)) if widths else float("nan")

        # shimmer: dB spread of the short-window RMS within held notes, minus its slow trend
        shimmer = []
        for n in notes:
            if float(n.end) - float(n.start) < 0.30:
                continue
            lo = int((float(n.start) + 0.05) * sr / hop)
            hi = int((float(n.end) - 0.05) * sr / hop)
            seg = db[max(0, lo):min(len(db), hi)]
            if len(seg) >= 8:
                shimmer.append(float(np.std(seg - _moving_average(seg, smooth_w))))
        m["shimmer_db"] = float(np.median(shimmer)) if shimmer else float("nan")
    else:
        m["closure_depth_db"] = m["closure_width_ms"] = m["shimmer_db"] = float("nan")

    return m


def _fmt(v, unit=""):
    return "  n/a" if v != v else f"{v:6.1f}{unit}"


def calibrate(paths: list[str]) -> None:
    rows = []
    for gt_path in paths:
        with open(gt_path, encoding="utf-8") as f:
            gt = json.load(f)
        base = os.path.splitext(gt_path)[0]
        audio = gt.get("audio")
        audio = os.path.join(os.path.dirname(gt_path), audio) if audio else base + ".webm"
        if not os.path.exists(audio):
            print(f"! skip {os.path.basename(gt_path)}: audio not found ({audio})")
            continue
        bpm = float(gt.get("bpm", 100))
        backend = gt.get("backend", "pesto")
        subdiv = int(gt.get("subdiv", 4))

        from tests.diagnose_recorded import decode
        params = Params(backend=backend, quantize_subdiv=subdiv)
        y = decode(audio, params.sr)
        analysis = transcribe_array(y, params, tempo_bpm=bpm)
        m = measure_take(analysis.frames, analysis.score.notes, bpm, params.hop_s, y=y, sr=params.sr)
        m["name"] = os.path.basename(base)
        rows.append(m)

    if not rows:
        print("no takes measured.")
        return

    hdr = ("take", "vib_c", "vib_Hz", "drift_c", "jit_ms", "clo_dB", "clo_ms", "shim_dB", "held")
    print("\n" + "=" * 86)
    print("MEASURED REALISM (per take)")
    print("=" * 86)
    print(f"{hdr[0]:<20}{hdr[1]:>8}{hdr[2]:>8}{hdr[3]:>9}{hdr[4]:>8}{hdr[5]:>8}{hdr[6]:>8}{hdr[7]:>9}{hdr[8]:>6}")
    for r in rows:
        print(f"{r['name']:<20}{_fmt(r['vibrato_cents'])}{_fmt(r['vibrato_rate'])}"
              f"{_fmt(r['drift_cents'])}{_fmt(r['timing_jitter_ms'])}{_fmt(r['closure_depth_db'])}"
              f"{_fmt(r['closure_width_ms'])}{_fmt(r['shimmer_db'])}{int(r['n_held']):>6}")

    def med(key):
        vals = [r[key] for r in rows if r[key] == r[key]]
        return statistics.median(vals) if vals else float("nan")

    keys = ["vibrato_cents", "vibrato_rate", "drift_cents", "timing_jitter_ms",
            "closure_depth_db", "closure_width_ms", "shimmer_db"]
    print("-" * 86)
    print("median              " + "".join(
        _fmt(med(k)) for k in ["vibrato_cents", "vibrato_rate", "drift_cents",
                               "timing_jitter_ms", "closure_depth_db", "closure_width_ms",
                               "shimmer_db"]))

    # --- fitted Expr from the medians (closure_db is a dip level, ~ -closure_depth) ---
    vib = med("vibrato_cents"); rate = med("vibrato_rate"); drift = med("drift_cents")
    jit = med("timing_jitter_ms"); clo = med("closure_depth_db"); shim = med("shimmer_db")
    print("\nFITTED Expr (medians; edit make_synthetic if these diverge from REALISTIC):")
    print("  Expr(")
    if vib == vib:  print(f"      vibrato_cents={vib:.0f},")
    if rate == rate: print(f"      vibrato_rate={rate:.1f},")
    if drift == drift: print(f"      drift_cents={drift:.0f},")
    if jit == jit:  print(f"      timing_jitter_s={jit/1000.0:.3f},")
    if clo == clo:  print(f"      closure_db={-abs(clo):.0f},")
    if shim == shim: print(f"      shimmer_db={shim:.1f},")
    print("  )")
    print("\n(estimates from a small sample; see the module docstring for method + caveats.)")


def main():
    ap = argparse.ArgumentParser(description="Measure realism knobs from real hum recordings.")
    ap.add_argument("paths", nargs="+", help="ground-truth JSON files (globs ok)")
    args = ap.parse_args()
    paths = []
    for p in args.paths:
        paths.extend(sorted(glob.glob(p)) if any(c in p for c in "*?[") else [p])
    calibrate([p for p in paths if p.lower().endswith(".json")])


if __name__ == "__main__":
    main()
