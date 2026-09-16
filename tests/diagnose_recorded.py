"""Diagnose the pipeline on a REAL hummed recording against its ground truth.

The synthetic eval (tests/eval_report.py) is saturated at F1 = 1.0, so it no longer
exposes real failures. This tool runs the actual audio a browser recorded (raw-mic webm)
through the REAL pipeline and shows, stage by stage, WHERE it diverges from what the user
intended to hum. It is a diagnosis tool, not a CI gate - it prints, it does not assert.

    .venv/Scripts/python.exe tests/diagnose_recorded.py tests/data/recorded/<name>.json

Ground truth is (bpm, sequence of [midi_or_null, beats]) - the same shape as
tests/make_synthetic.FIXTURES - so a real take is directly comparable to its synthetic
twin. Capture + schema: tests/data/recorded/README.md.

Because a hummer picks their own octave, pitch is compared octave-normalized by default
(one global octave shift is detected and removed, then per-note octave outliers are
flagged separately - those are the real octave-error bug). Set "octave_strict": true in
the JSON to score absolute MIDI.

You can also point it straight at an audio file with no ground truth to just dump what the
pipeline produces:

    .venv/Scripts/python.exe tests/diagnose_recorded.py my-hum.webm --bpm 110
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile

# Run from anywhere: make the repo root importable (conftest handles pytest, not __main__).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mouthtranscriber.config import Params  # noqa: E402
from mouthtranscriber.evaluate import note_scores, ref_notes_from_tuples, rhythm_scores  # noqa: E402
from mouthtranscriber.model import midi_to_name  # noqa: E402
from mouthtranscriber.pipeline import transcribe_array  # noqa: E402


# ---- audio decode (mirror server/app.py _to_wav: ffmpeg -> mono wav @ sr) ---------------

def decode(path: str, sr: int):
    """Decode any audio blob to mono float32 @ sr, exactly as the server does."""
    import librosa

    dst = tempfile.mktemp(suffix=".wav")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", path, "-ac", "1", "-ar", str(sr), dst],
            check=True,
            capture_output=True,
        )
        y, _ = librosa.load(dst, sr=sr, mono=True)
    finally:
        if os.path.exists(dst):
            os.unlink(dst)
    return y


# ---- ground truth -----------------------------------------------------------------------

def grid_from_sequence(seq):
    """(sounded midis, start_ql, dur_ql), anchored so the first sounded note is 0.

    Mirrors make_synthetic.intended_grid: a rest ([null, beats]) advances the clock without
    emitting a note; a note's notated length is its full slot (the "da" gap is articulation).
    """
    midis, starts, durs = [], [], []
    clk = 0.0
    first = None
    for midi, beats in seq:
        if midi is not None:
            if first is None:
                first = clk
            midis.append(int(midi))
            starts.append(clk - first)
            durs.append(float(beats))
        clk += float(beats)
    return midis, starts, durs


# ---- formatting -------------------------------------------------------------------------

def _fmt_seq(midis):
    return " ".join(f"{midi_to_name(m)}({m})" for m in midis)


def _global_shift(est_midis, ref_midis, quantum):
    """Best global pitch shift (a multiple of ``quantum`` semitones) to add to REF to match
    EST, from the positional median. A global shift is just the hummer's chosen register/key
    (fine); per-note departures from it are the real pitch/octave bug.

    ``quantum`` = 0 -> no shift (absolute scoring); 12 -> octave-normalized (removes the
    hummer's octave choice); 1 -> transposition-normalized (removes their whole key choice,
    so it scores melody SHAPE - what a self-pitched hum with no reference tone can control).
    A single per-note octave slip is an outlier and barely moves the median, so it still
    flags in every mode.
    """
    m = min(len(est_midis), len(ref_midis))
    if m == 0 or quantum == 0:
        return 0
    diffs = sorted(est_midis[i] - ref_midis[i] for i in range(m))
    med = diffs[m // 2]
    return int(round(med / quantum)) * quantum


# How pitch is compared. "relative" removes the hummer's whole key (scores melody shape),
# "octave" removes only their octave, "absolute" scores raw MIDI. See _global_shift.
_QUANTUM = {"absolute": 0, "octave": 12, "relative": 1}


def diagnose(gt_path: str = None, audio: str = None, bpm: float = None,
             backend: str = None, subdiv: int = None, pitch_mode: str = "octave"):
    if gt_path:
        with open(gt_path, "r", encoding="utf-8") as f:
            gt = json.load(f)
        base = os.path.splitext(gt_path)[0]
        audio = gt.get("audio")
        audio = os.path.join(os.path.dirname(gt_path), audio) if audio else base + ".webm"
        bpm = float(gt.get("bpm", 120))
        backend = gt.get("backend", "pesto")
        subdiv = int(gt.get("subdiv", 4))
        time_sig = tuple(gt.get("time_sig", [4, 4]))
        seq = gt.get("sequence")
        # pitch_mode is the knob; legacy "octave_strict": true still means absolute.
        pitch_mode = gt.get("pitch_mode", "absolute" if gt.get("octave_strict") else "octave")
        ref_midis, ref_starts, ref_durs = grid_from_sequence(seq)
    else:
        gt = None
        bpm = float(bpm or 120)
        backend = backend or "pesto"
        subdiv = int(subdiv or 4)
        time_sig = (4, 4)
        ref_midis = ref_starts = ref_durs = None
    quantum = _QUANTUM.get(pitch_mode, 12)

    if not os.path.exists(audio):
        sys.exit(f"audio not found: {audio}")

    params = Params(backend=backend, quantize_subdiv=subdiv)
    print("=" * 78)
    print(f"DIAGNOSE  {os.path.basename(audio)}")
    print(f"  backend={backend}  bpm={bpm}  subdiv={subdiv}  time_sig={time_sig}")
    if gt and gt.get("notes"):
        print(f"  note: {gt['notes']}")
    print("=" * 78)

    y = decode(audio, params.sr)
    dur_s = len(y) / params.sr
    peak = float(max(abs(y.min()), abs(y.max()))) if len(y) else 0.0
    print(f"\n[decode]  {dur_s:.2f}s  {len(y)} samples @ {params.sr}Hz  peak={peak:.3f}")

    trace: dict = {}
    analysis = transcribe_array(y, params, tempo_bpm=bpm, time_sig=time_sig, trace=trace)

    # frame / voicing summary (empty for basic_pitch)
    frames = analysis.frames
    voiced = analysis.voiced
    if len(frames):
        f0s = [fr.f0 for fr in frames if fr.f0 and fr.f0 == fr.f0]
        vfrac = float(voiced.mean()) if len(voiced) else 0.0
        if f0s:
            f0s_sorted = sorted(f0s)
            import statistics
            from mouthtranscriber.model import hz_to_midi
            med_hz = f0s_sorted[len(f0s_sorted) // 2]
            lo_m, hi_m = hz_to_midi(f0s_sorted[0]), hz_to_midi(f0s_sorted[-1])
            print(f"[frames]  {len(frames)} frames  voiced={vfrac*100:.0f}%  "
                  f"f0 median={med_hz:.1f}Hz ({midi_to_name(round(hz_to_midi(med_hz)))})  "
                  f"range {midi_to_name(round(lo_m))}..{midi_to_name(round(hi_m))}")
    else:
        print("[frames]  (none - neural backend produces notes directly)")

    seg = trace.get("notes_segmented", [])
    con = trace.get("notes_consolidated", [])
    fin = trace.get("notes_final", [])
    print(f"\n[stage counts]  segment={len(seg)}  ->  consolidate={len(con)}  ->  final={len(fin)}")
    print(f"  segment    pitches: {_fmt_seq([n['midi'] for n in seg])}")
    if len(con) != len(seg) or [n['midi'] for n in con] != [n['midi'] for n in seg]:
        print(f"  consolidate pitches: {_fmt_seq([n['midi'] for n in con])}")
    print(f"  final      pitches: {_fmt_seq([n['midi'] for n in fin])}")

    est_midis = [n["midi"] for n in fin]
    est_starts = [n["start_ql"] for n in fin]
    est_durs = [n["dur_ql"] for n in fin]

    print("\n[final notes]")
    for i, n in enumerate(fin):
        sq = "-" if n["start_ql"] is None else f"{n['start_ql']:.3f}"
        dq = "-" if n["dur_ql"] is None else f"{n['dur_ql']:.3f}"
        print(f"  {i:2d}  {midi_to_name(n['midi']):>4}({n['midi']})  "
              f"start={n['start']:.3f}s  start_ql={sq}  dur_ql={dq}")

    if ref_midis is None:
        print("\n(no ground truth - dump only)")
        return

    # ---- compare to ground truth --------------------------------------------------------
    shift = _global_shift(est_midis, ref_midis, quantum)
    ref_cmp = [m + shift for m in ref_midis]  # ref shifted into the hummed register/key
    print("\n" + "-" * 78)
    print(f"GROUND TRUTH vs ESTIMATE   (pitch_mode={pitch_mode})")
    print(f"  intended: {_fmt_seq(ref_midis)}")
    if shift:
        print(f"  (global shift detected: {shift:+d} semitones; comparing shape-normalized)")
    print("-" * 78)
    print(f"  {'#':>2}  {'ref':>8} {'start':>6} {'dur':>5}   {'est':>8} {'start':>6} {'dur':>5}   flags")
    m = min(len(ref_cmp), len(est_midis))
    pitch_ok = onset_ok = dur_ok = 0
    for i in range(m):
        p_ok = est_midis[i] == ref_cmp[i]
        oct_off = (not p_ok) and (est_midis[i] - ref_cmp[i]) % 12 == 0
        e_start = est_starts[i]
        e_dur = est_durs[i]
        on_ok = e_start is not None and abs(e_start - ref_starts[i]) <= 1e-3
        d_ok = e_dur is not None and abs(e_dur - ref_durs[i]) <= 1e-3
        pitch_ok += p_ok
        onset_ok += on_ok
        dur_ok += d_ok
        flags = []
        if not p_ok:
            flags.append("OCTAVE" if oct_off else "PITCH")
        if not on_ok:
            flags.append("onset")
        if not d_ok:
            flags.append("dur")
        es = "-" if e_start is None else f"{e_start:.2f}"
        ed = "-" if e_dur is None else f"{e_dur:.2f}"
        print(f"  {i:2d}  {midi_to_name(ref_cmp[i]):>4}({ref_cmp[i]:>2}) {ref_starts[i]:>6.2f} "
              f"{ref_durs[i]:>5.2f}   {midi_to_name(est_midis[i]):>4}({est_midis[i]:>2}) "
              f"{es:>6} {ed:>5}   {' '.join(flags)}")
    # unmatched tail
    for i in range(m, len(ref_cmp)):
        print(f"  {i:2d}  {midi_to_name(ref_cmp[i]):>4}({ref_cmp[i]:>2}) {ref_starts[i]:>6.2f} "
              f"{ref_durs[i]:>5.2f}   {'(missing)':>21}   MISSING")
    for i in range(m, len(est_midis)):
        es = "-" if est_starts[i] is None else f"{est_starts[i]:.2f}"
        print(f"  {i:2d}  {'(extra)':>15}          {midi_to_name(est_midis[i]):>4}"
              f"({est_midis[i]:>2}) {es:>6}       EXTRA")

    # metrics
    rs = rhythm_scores(ref_starts, ref_durs, [
        type("N", (), {"start_ql": (n["start_ql"] if n["start_ql"] is not None else float("nan")),
                       "dur_ql": (n["dur_ql"] if n["dur_ql"] is not None else float("nan"))})()
        for n in fin
    ])
    print("\n[metrics]")
    print(f"  count:  ref={len(ref_midis)}  est={len(est_midis)}  "
          f"delta={len(est_midis) - len(ref_midis):+d}")
    if m:
        print(f"  pitch ({pitch_mode}-normalized): {pitch_ok}/{m} correct")
    print(f"  rhythm: onset={rs.onset_acc*100:.0f}%  dur={rs.dur_acc*100:.0f}%  "
          f"both={rs.both_acc*100:.0f}%  mean|onset err|={rs.mean_onset_err_ql:.3f} ql")

    # ---- verdict ------------------------------------------------------------------------
    print("\n[verdict]")
    n_ref = len(ref_midis)
    # Note (informational) when an intermediate stage's count differed but a later stage
    # recovered the right FINAL count - a latent weakness, not the output's problem.
    if len(fin) == n_ref and (len(seg) != n_ref or len(con) != n_ref):
        print(f"  (note: intermediate counts wobbled seg={len(seg)} con={len(con)} but the "
              f"final count recovered to {n_ref}.)")
    if len(fin) != n_ref:
        # The final count is wrong; trace where it first went off.
        if len(seg) != n_ref:
            rel = "over-split" if len(seg) > n_ref else "under-split (merged / missed onsets)"
            print(f"  * SEGMENT is the first divergence: produced {len(seg)} notes for {n_ref} "
                  f"intended ({rel}). Look at segment.py / voicing.py / the fine energy envelope.")
        else:
            print(f"  * CONSOLIDATE changed a correct {n_ref}-note segmentation to {len(con)} "
                  f"(over-merged same-pitch repeats?). Look at consolidate.py's grid guard.")
    elif m and pitch_ok < m:
        n_oct = sum(1 for i in range(m)
                    if est_midis[i] != ref_cmp[i] and (est_midis[i] - ref_cmp[i]) % 12 == 0)
        if n_oct:
            print(f"  * PITCH: counts line up but {m - pitch_ok} note(s) wrong, {n_oct} by a "
                  f"full octave -> octave.py / the tracker on that region.")
        else:
            print(f"  * PITCH: counts line up but {m - pitch_ok} note(s) wrong (non-octave) -> "
                  f"tracker/tuning on that region.")
    elif rs.both_acc < 0.999:
        print("  * RHYTHM: pitch + count are right but quantized onsets/durations miss the "
              "grid -> quantize.py, or the BPM used here is not the BPM hummed to.")
    else:
        print("  * MATCH: pitch, count, and rhythm all recovered. This take transcribes "
              "correctly - capture a harder one.")


def main():
    ap = argparse.ArgumentParser(description="Diagnose the pipeline on a real hum recording.")
    ap.add_argument("target", help="ground-truth JSON, or an audio file (with --bpm)")
    ap.add_argument("--bpm", type=float, default=None, help="BPM (audio-only mode)")
    ap.add_argument("--backend", default=None, help="backend (audio-only mode; default pesto)")
    ap.add_argument("--subdiv", type=int, default=None, help="grid ticks per quarter")
    args = ap.parse_args()

    if args.target.lower().endswith(".json"):
        diagnose(gt_path=args.target)
    else:
        diagnose(audio=args.target, bpm=args.bpm, backend=args.backend, subdiv=args.subdiv)


if __name__ == "__main__":
    main()
