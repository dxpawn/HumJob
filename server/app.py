"""FastAPI backend for HumJob (PLAN §3, milestone M5).

This is the same Python pipeline the CLI uses, wrapped in HTTP. The browser only
records audio (to a metronome) and renders results — all DSP runs here. This app
IS the future web-app backend; the local UI is just its first client.

Run:  uvicorn server.app:app --reload  (or via .claude/launch.json / preview)
"""

from __future__ import annotations

import base64
import os
import subprocess
import tempfile

from fastapi import Body, FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from mouthtranscriber import analyze as analyze_mod
from mouthtranscriber import export as export_mod
from mouthtranscriber import key as key_mod
from mouthtranscriber import tempo as tempo_mod
from mouthtranscriber.audio_io import load_audio
from mouthtranscriber.config import Params
from mouthtranscriber.model import midi_to_name
from mouthtranscriber.pipeline import transcribe_array

app = FastAPI(title="HumJob")

_STATIC = os.path.join(os.path.dirname(__file__), "static")


@app.middleware("http")
async def _no_cache(request, call_next):
    """Local dev app: force the browser to revalidate, so a restart/UI change is
    never hidden behind a stale cached index.html / app.js / style.css."""
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache"
    return response


def _to_wav(raw: bytes, filename: str, sr: int) -> str:
    """Decode any uploaded audio blob to a mono WAV via ffmpeg. Returns a path."""
    suffix = os.path.splitext(filename or "")[1] or ".webm"
    fd, src = tempfile.mkstemp(suffix=suffix)
    os.write(fd, raw)
    os.close(fd)
    dst = src + ".wav"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", src, "-ac", "1", "-ar", str(sr), dst],
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        detail = e.stderr.decode("utf-8", "ignore")[-500:] if hasattr(e, "stderr") and e.stderr else str(e)
        raise HTTPException(status_code=400, detail=f"audio decode failed: {detail}")
    finally:
        if os.path.exists(src):
            os.unlink(src)
    return dst


@app.post("/api/transcribe")
async def transcribe(
    audio: UploadFile,
    bpm: float = Form(120.0),
    beats: int = Form(4),
    beat_unit: int = Form(4),
    subdiv: int = Form(4),
    backend: str = Form("crepe"),
):
    raw = await audio.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty audio upload")

    # Default to CREPE (voice/humming-specialized neural pitch), but let the UI
    # switch to basic-pitch (instruments) or the classic pYIN tracker to compare.
    if backend not in ("basic_pitch", "pyin", "crepe"):
        backend = "crepe"
    params = Params(backend=backend, quantize_subdiv=subdiv)
    wav = _to_wav(raw, audio.filename or "rec.webm", params.sr)
    try:
        y, _ = load_audio(wav, params.sr)
    finally:
        os.unlink(wav)

    analysis = transcribe_array(y, params, tempo_bpm=bpm, time_sig=(beats, beat_unit))
    score = analysis.score

    return JSONResponse(
        {
            "key": score.key,
            "key_candidates": [
                {"name": name, "score": round(corr, 3)}
                for corr, name in score.key_candidates
            ],
            "tuning_offset_cents": round(score.tuning_offset_cents, 1),
            "tempo_bpm": score.tempo_bpm,
            "time_sig": list(score.time_sig),
            "backend": params.backend,
            "n_notes": len(score.notes),
            "chords": [
                {
                    "measure": c.measure,
                    "symbol": c.symbol,
                    "roman": c.roman,
                    "start_ql": round(c.start_ql, 3),
                    "root_pc": c.root_pc,   # for in-browser playback
                    "quality": c.quality,   # "maj" | "min" | "dim"
                }
                for c in score.chords
            ],
            "notes": [
                {
                    "name": midi_to_name(n.midi),
                    "midi": n.midi,
                    "start_ql": None if n.start_ql != n.start_ql else round(n.start_ql, 3),
                    "dur_ql": None if n.dur_ql != n.dur_ql else round(n.dur_ql, 3),
                    "cents": round(n.cents_offset, 1),
                }
                for n in score.notes
            ],
            "svg": export_mod.sheet_svg_string(score),
            "musicxml": export_mod.to_musicxml_string(score),
            "midi_b64": base64.b64encode(export_mod.midi_bytes(score)).decode("ascii"),
        }
    )


@app.post("/api/detect-tempo")
async def detect_tempo(audio: UploadFile):
    """Estimate BPM from a free hum so the user can then record to that click."""
    raw = await audio.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty audio upload")

    params = Params()
    wav = _to_wav(raw, audio.filename or "tempo.webm", params.sr)
    try:
        y, _ = load_audio(wav, params.sr)
    finally:
        os.unlink(wav)

    bpm = tempo_mod.detect_bpm(y, params.sr, params)
    return JSONResponse({"bpm": int(bpm)})


@app.post("/api/analyze")
async def analyze(audio: UploadFile):
    """Pitch Finder: decode any audio and return Key / BPM / Camelot + technical stats.

    Independent of the transcription pipeline — runs the chroma-based general-audio
    analyzer, which works on full songs as well as single instruments.
    """
    raw = await audio.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty audio upload")

    params = Params()
    wav = _to_wav(raw, audio.filename or "track.mp3", params.sr)
    try:
        y, _ = load_audio(wav, params.sr)
    finally:
        os.unlink(wav)

    return JSONResponse(analyze_mod.analyze_audio(y, params.sr))


@app.post("/api/key")
async def detect_key(histogram: list[float] = Body(..., embed=True)):
    """Realtime voice monitor: key from a 12-bin pitch-class histogram.

    The browser tracks pitch live (client-side) and accumulates a pitch-class
    histogram; on stop it POSTs the 12 numbers here. We reuse the same Krumhansl
    scorer as the Pitch Finder (`key.score_keys`) — but fed our own accurate
    monophonic pitch data rather than re-decoding audio through the chroma path.
    """
    if len(histogram) != 12:
        raise HTTPException(status_code=400, detail="histogram must have 12 values")
    hist = [float(x) for x in histogram]
    if sum(hist) <= 0:
        raise HTTPException(status_code=400, detail="empty histogram")

    ranked = key_mod.score_keys(hist)
    best_corr, best_tonic, best_mode = ranked[0]

    def named(corr, tonic, mode):
        return {
            "key": f"{key_mod._NAMES[tonic]} {mode}",
            "camelot": analyze_mod.to_camelot(tonic, mode),
            "score": round(corr, 3),
        }

    return JSONResponse(
        {
            "key": f"{key_mod._NAMES[best_tonic]} {best_mode}",
            "key_score": round(best_corr, 3),
            "camelot": analyze_mod.to_camelot(best_tonic, best_mode),
            "candidates": [named(c, t, m) for c, t, m in ranked[:6]],
        }
    )


@app.get("/health")
def health():
    return {"status": "ok"}


# Static UI (mounted last so /api/* and /health win).
app.mount("/", StaticFiles(directory=_STATIC, html=True), name="static")
