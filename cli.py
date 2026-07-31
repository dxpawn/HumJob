"""hum2midi — command-line entry point for the local pipeline.

Examples
--------
    python cli.py tests/data/generated/c_major_scale.wav -o out.mid
    python cli.py hum.wav -o out.mid --plot debug/hum.png --bpm 100
    python cli.py hum.wav --musicxml out.musicxml
"""

from __future__ import annotations

import argparse
import os
import sys

from mouthtranscriber import Params
from mouthtranscriber import export as export_mod
from mouthtranscriber.pipeline import transcribe


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hum2midi", description="Transcribe a hummed melody.")
    ap.add_argument("input", help="input audio file (wav/flac/ogg/mp3)")
    ap.add_argument("-o", "--out", help="output MIDI path")
    ap.add_argument("--musicxml", help="also write MusicXML to this path")
    ap.add_argument("--sheet", help="also engrave sheet-music SVG to this path")
    ap.add_argument("--plot", help="write a debug analysis PNG to this path")
    ap.add_argument("--bpm", type=float, default=120.0, help="tempo (metronome BPM)")
    ap.add_argument("--backend", default="pyin", choices=["pyin", "crepe"])
    ap.add_argument("--sr", type=int, default=22050, help="canonical sample rate")
    args = ap.parse_args(argv)

    if not os.path.exists(args.input):
        print(f"error: no such file: {args.input}", file=sys.stderr)
        return 2

    params = Params(sr=args.sr, backend=args.backend)
    result = transcribe(args.input, params=params, tempo_bpm=args.bpm)
    score = result.score

    print(export_mod.summary(score))
    print(f"tuning offset: {score.tuning_offset_cents:+.0f} cents")
    if score.key_candidates:
        cands = ", ".join(f"{name} ({corr:.2f})" for corr, name in score.key_candidates)
        print(f"key: {cands}")
    if score.chords:
        print(f"chords: {export_mod.chord_summary(score)}")

    out = args.out or (os.path.splitext(args.input)[0] + ".mid")
    _ensure_dir(out)
    export_mod.to_midi(score, out)
    print(f"wrote MIDI -> {out}")

    if args.musicxml:
        _ensure_dir(args.musicxml)
        export_mod.to_musicxml(score, args.musicxml)
        print(f"wrote MusicXML -> {args.musicxml}")

    if args.sheet:
        _ensure_dir(args.sheet)
        export_mod.render_sheet_svg(score, args.sheet, title=os.path.basename(args.input))
        print(f"wrote sheet -> {args.sheet}")

    if args.plot:
        from mouthtranscriber.viz import plot_analysis

        _ensure_dir(args.plot)
        plot_analysis(
            result.frames, result.voiced, score.notes, params, args.plot,
            title=os.path.basename(args.input),
        )
        print(f"wrote plot -> {args.plot}")

    return 0


def _ensure_dir(path: str) -> None:
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
