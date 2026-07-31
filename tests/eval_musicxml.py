"""Evaluate the pipeline against a real MusicXML melody (PLAN §6).

The MusicXML gives *exact* ground truth (notes, timing, key). We test two ways:

  1. RENDERED: synthesize a humming-style audio from the ground-truth notes and
     run it through the pipeline. Onsets line up by construction, so note-F1 here
     measures pitch + segmentation quality on a real melodic contour. This is the
     controlled test.

  2. PROVIDED AUDIO (optional --audio): run a real MP3/WAV. If it's a direct
     render of the score the note-F1 is meaningful; if it's a different recording
     (rubato, intro, accompaniment) the onset-based F1 is not — so we also report
     the timing-independent signals: detected key and the pitch-class overlap.

Usage:
    python tests/eval_musicxml.py "testMaterials/DUA EM VAO HA.musicxml" \
        --audio "testMaterials/DUA EM VAO HA.mp3" --plotdir debug
"""

from __future__ import annotations

import argparse
import os

import numpy as np

from mouthtranscriber.config import Params
from mouthtranscriber.evaluate import note_scores, ref_notes_from_tuples
from mouthtranscriber.model import midi_to_name
from mouthtranscriber.pipeline import transcribe_array
from tests.make_synthetic import _synth_note

SR = 22050
RENDER_GAP_S = 0.06  # silent gap after each note = the "da" consonant closure


def ground_truth(path: str) -> tuple[list[tuple[float, float, int]], float, str]:
    """Return (notes[(start_s,end_s,midi)], bpm, key_string) from a MusicXML file."""
    from music21 import converter, tempo

    score = converter.parse(path)
    try:
        score = score.stripTies()
    except Exception:
        pass
    flat = score.flatten()

    marks = flat.getElementsByClass(tempo.MetronomeMark)
    bpm = float(marks[0].number) if marks else 120.0
    spq = 60.0 / bpm  # seconds per quarter note

    notes: list[tuple[float, float, int]] = []
    for n in flat.notes:
        ql = float(n.duration.quarterLength)
        if ql <= 0:
            continue  # grace notes
        midi = int(n.sortAscending().pitches[-1].midi) if n.isChord else int(n.pitch.midi)
        start = float(n.offset) * spq
        notes.append((start, start + ql * spq, midi))

    notes.sort(key=lambda x: x[0])
    key_str = str(score.analyze("key")).replace("-", "")  # e.g. "f minor"
    key_str = key_str[0].upper() + key_str[1:]
    return notes, bpm, key_str


def render_hum(notes, sr: int = SR) -> np.ndarray:
    """Synthesize a humming-style rendering of the ground-truth notes."""
    total = max(e for _, e, _ in notes) + 0.3
    y = np.zeros(int(total * sr) + 1, dtype=np.float32)
    for start, end, midi in notes:
        dur = max(0.05, (end - start) - RENDER_GAP_S)
        tone = _synth_note(midi, dur, sr, vibrato=True, scoop=False)
        i0 = int(start * sr)
        i1 = min(i0 + len(tone), len(y))
        y[i0:i1] += tone[: i1 - i0]
    peak = float(np.max(np.abs(y))) + 1e-9
    return (y / peak * 0.9).astype(np.float32)


def _pitch_class_overlap(ref_notes, est_notes) -> float:
    """Duration-weighted cosine overlap of pitch-class histograms (timing-free)."""
    def hist(notes):
        h = np.zeros(12)
        for n in notes:
            h[n.midi % 12] += max(n.duration, 1e-6)
        return h / (np.linalg.norm(h) + 1e-12)
    a, b = hist(ref_notes), hist(est_notes)
    return float(np.dot(a, b))


def _report(tag, ref_notes, analysis, plotpath=None, params=None):
    est = analysis.score.notes
    s = note_scores(ref_notes, est)
    pc = _pitch_class_overlap(ref_notes, est)
    print(f"\n=== {tag} ===")
    print(f"  ref notes: {len(ref_notes)}   est notes: {len(est)}")
    print(f"  note  P={s.precision:.3f}  R={s.recall:.3f}  F1={s.f1:.3f}")
    print(f"  pitch-class overlap (timing-free): {pc:.3f}")
    print(f"  detected key: {analysis.score.key}  (candidates: "
          f"{', '.join(n for _, n in analysis.score.key_candidates)})")
    print(f"  tuning offset: {analysis.score.tuning_offset_cents:+.0f} cents")
    if plotpath and params is not None:
        from mouthtranscriber.viz import plot_analysis
        os.makedirs(os.path.dirname(os.path.abspath(plotpath)), exist_ok=True)
        plot_analysis(analysis.frames, analysis.voiced, est, params, plotpath, title=tag)
        print(f"  plot -> {plotpath}")
    return s


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("musicxml")
    ap.add_argument("--audio", help="also evaluate this real audio file")
    ap.add_argument("--plotdir", default="debug")
    args = ap.parse_args()

    gt, bpm, key_str = ground_truth(args.musicxml)
    ref_notes = ref_notes_from_tuples(gt)
    print(f"ground truth: {len(gt)} notes, bpm={bpm:.0f}, key={key_str}")
    print("first 12 notes:", " ".join(midi_to_name(m) for _, _, m in gt[:12]))

    params = Params(sr=SR)

    # 1) controlled: render a hum from the score and transcribe it
    y = render_hum(gt)
    a_render = transcribe_array(y, params, tempo_bpm=bpm)
    _report("RENDERED HUM", ref_notes, a_render,
            os.path.join(args.plotdir, "musicxml_rendered.png"), params)

    # 2) optional: the provided real audio
    if args.audio:
        from mouthtranscriber.audio_io import load_audio
        y2, _ = load_audio(args.audio, SR)
        a_real = transcribe_array(y2, params, tempo_bpm=bpm)
        _report(f"PROVIDED AUDIO ({os.path.basename(args.audio)})", ref_notes, a_real,
                os.path.join(args.plotdir, "musicxml_provided.png"), params)


if __name__ == "__main__":
    main()
