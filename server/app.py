"""FastAPI backend for HumJob (PLAN §3, milestone M5).

This is the same Python pipeline the CLI uses, wrapped in HTTP. The browser only
records audio (to a metronome) and renders results — all DSP runs here. This app
IS the future web-app backend; the local UI is just its first client.

Run:  uvicorn server.app:app --reload  (or via .claude/launch.json / preview)
"""

from __future__ import annotations

import base64
import math
import os
import subprocess
import tempfile

from fastapi import Body, FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from mouthtranscriber import analyze as analyze_mod
from mouthtranscriber import chords as chords_mod
from mouthtranscriber import coach as coach_mod
from mouthtranscriber import export as export_mod
from mouthtranscriber import key as key_mod
from mouthtranscriber import reference as reference_mod
from mouthtranscriber import tempo as tempo_mod
from mouthtranscriber import transpose as transpose_mod
from mouthtranscriber.audio_io import load_audio
from mouthtranscriber.config import Params
from mouthtranscriber.model import Chord, NoteEvent, Score, midi_to_name
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


def _serialize_frames(frames, max_points: int = 1200) -> list[dict]:
    """Decimate the per-hop pitch contour for the Manual-mode reference strip.

    crepe/pyin produce a dense frame per ~12 ms hop; basic_pitch produces none
    (the strip then degrades to note blocks only). NaN f0 (unvoiced) becomes null.
    """
    if not frames:
        return []
    step = max(1, len(frames) // max_points)
    out = []
    for fr in frames[::step]:
        f0 = None if fr.f0 is None or math.isnan(fr.f0) else round(fr.f0, 2)
        out.append({"t": round(fr.t, 3), "f0": f0, "conf": round(fr.confidence, 2)})
    return out


def _notes_from_json(items: list[dict], bpm: float) -> list[NoteEvent]:
    """Rebuild NoteEvents from posted note dicts, synthesizing seconds from the grid.

    Manual-mode edits carry only grid positions (start_ql/dur_ql). The MIDI exporter
    reads raw seconds (n.start/n.end) and key detection weights its histogram by
    n.duration seconds, so we derive seconds from the tempo: start = start_ql*60/bpm.
    Consequence: an edited score's MIDI is fully grid-quantized (the original take's
    raw performance timing no longer applies once notes are edited).
    """
    spb = 60.0 / max(bpm, 1e-6)
    notes: list[NoteEvent] = []
    for it in items:
        start_ql = float(it["start_ql"])
        dur_ql = float(it["dur_ql"])
        start = start_ql * spb
        n = NoteEvent(
            start=start,
            end=start + dur_ql * spb,
            midi=int(it["midi"]),
            velocity=int(it.get("velocity", 80)),
        )
        n.start_ql = start_ql
        n.dur_ql = dur_ql
        notes.append(n)
    return notes


def _chords_from_json(items: list[dict]) -> list[Chord]:
    """Rebuild Chords from the client's displayed chord list, for the edited export.

    Malformed chords are skipped rather than failing the whole export.
    """
    out: list[Chord] = []
    for c in items or []:
        try:
            out.append(
                Chord(
                    measure=int(c["measure"]),
                    start_ql=float(c["start_ql"]),
                    root_pc=int(c["root_pc"]),
                    root_name=str(c["root_name"]),
                    quality=str(c["quality"]),
                    symbol=str(c.get("symbol", "")),
                    roman=str(c.get("roman", "")),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _parse_time_sig(raw) -> tuple[int, int]:
    try:
        return (int(raw[0]), int(raw[1]))
    except (TypeError, ValueError, IndexError):
        return (4, 4)


@app.post("/api/transcribe")
async def transcribe(
    audio: UploadFile,
    bpm: float = Form(120.0),
    beats: int = Form(4),
    beat_unit: int = Form(4),
    subdiv: int = Form(4),
    backend: str = Form("pesto"),
):
    raw = await audio.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty audio upload")

    # Default to PESTO (most precise on voice); the UI can switch to FCNF0++, CREPE,
    # basic-pitch (instruments), or the classic pYIN tracker to compare.
    if backend not in ("basic_pitch", "pyin", "crepe", "pesto", "fcnf0"):
        backend = "pesto"
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
                    "root_name": c.root_name,  # music21 spelling, for edited export
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
            # Per-hop pitch contour for the Manual-mode reference strip (empty for
            # basic_pitch, which produces notes without frames).
            "frames": _serialize_frames(analysis.frames),
            "subdiv": params.quantize_subdiv,  # grid ticks per quarter, for the editor
        }
    )


@app.post("/api/export-edited")
async def export_edited(payload: dict = Body(...)):
    """Manual mode: build MIDI + MusicXML from an edited note list.

    Called only on a download click, never in the instant edit loop. Rebuilds a
    Score from grid positions (seconds synthesized from tempo) and the client's
    displayed chords, then reuses the same exporters the auto pipeline uses.
    """
    notes_in = payload.get("notes") or []
    if not notes_in:
        raise HTTPException(status_code=400, detail="no notes to export")
    bpm = float(payload.get("tempo", 120.0))
    time_sig = _parse_time_sig(payload.get("time_sig", [4, 4]))
    try:
        notes = _notes_from_json(notes_in, bpm)
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"bad note data: {e}")

    score = Score(
        notes=notes,
        sr=Params().sr,
        tempo_bpm=bpm,
        time_sig=time_sig,
        key=payload.get("key"),
        chords=_chords_from_json(payload.get("chords") or []),
    )
    return JSONResponse(
        {
            "musicxml": export_mod.to_musicxml_string(score),
            "midi_b64": base64.b64encode(export_mod.midi_bytes(score)).decode("ascii"),
        }
    )


@app.post("/api/rescore")
async def rescore(payload: dict = Body(...)):
    """Manual mode: re-detect key and re-suggest chords for an edited melody.

    Backs the on-demand "Update chords + key" button. Runs the same key scorer and
    chord suggester the auto pipeline uses, on the edited notes (seconds synthesized
    from tempo so the duration-weighted key histogram is correct).
    """
    notes_in = payload.get("notes") or []
    if not notes_in:
        raise HTTPException(status_code=400, detail="no notes to rescore")
    bpm = float(payload.get("tempo", 120.0))
    time_sig = _parse_time_sig(payload.get("time_sig", [4, 4]))
    try:
        notes = _notes_from_json(notes_in, bpm)
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"bad note data: {e}")

    candidates = key_mod.detect_key(notes)
    key = candidates[0][1] if candidates else None
    chord_seq = chords_mod.suggest(notes, key, time_sig)
    return JSONResponse(
        {
            "key": key,
            "key_candidates": [
                {"name": name, "score": round(corr, 3)} for corr, name in candidates
            ],
            "chords": [
                {
                    "measure": c.measure,
                    "symbol": c.symbol,
                    "roman": c.roman,
                    "start_ql": round(c.start_ql, 3),
                    "root_pc": c.root_pc,
                    "root_name": c.root_name,
                    "quality": c.quality,
                }
                for c in chord_seq
            ],
        }
    )


@app.post("/api/transpose-file")
async def transpose_file(file: UploadFile, semitones: int = Form(0)):
    """Transposer tab: transpose a whole uploaded MIDI / MusicXML score by N semitones.

    Polyphony-safe (music21 transposes every voice + the key signature), unlike the
    hummed-melody path which is monophonic and transposes client-side. Returns the
    engraved SVG, transposed MusicXML + MIDI, and a flat note list for browser playback.
    Called on upload (semitones=0) and again on each shift change. Stays fully local.
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty file upload")
    semitones = max(-24, min(24, int(semitones)))
    try:
        result = transpose_mod.transpose_file(raw, file.filename or "score.mid", semitones)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"could not read this file: {e}")
    return JSONResponse(result)


@app.post("/api/reference-melody")
async def reference_melody(file: UploadFile):
    """Sing-Along tab: reduce an uploaded MIDI / MusicXML score to a karaoke melody.

    Unlike the Transposer's flat note list (every voice, ties kept), scoring a sung take
    needs ONE target at a time, so the score is reduced to its skyline and ties are
    stripped (mouthtranscriber.reference). Returns the monophonic melody in quarter
    notes, the key / tempo / time signature, a tempo-mark count (v1 assumes constant
    tempo), and a best-effort engraved SVG of the original sheet. Stays fully local.
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty file upload")
    try:
        result = reference_mod.reference_payload(raw, file.filename or "score.mid")
    except reference_mod.NoMelodyError:
        raise HTTPException(status_code=400, detail="no melody notes found in this file")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"could not read this file: {e}")
    return JSONResponse(result)


@app.post("/api/coach")
def coach(payload: dict = Body(...)):
    """Sing-Along coaching: numeric take report -> spoken-language singing feedback.

    Sync def on purpose: FastAPI runs it in the threadpool, so the up-to-60s upstream call
    to DeepSeek never blocks the async event loop. The browser sends {report, language};
    only that numeric summary leaves the machine (no audio, no recording, no filename).
    503 = no API key configured (the detail tells the user to set up .env); 502 = the
    upstream LLM API failed.
    """
    report = payload.get("report") if isinstance(payload, dict) else None
    if not isinstance(report, dict):
        raise HTTPException(status_code=400, detail="request must include a take report object")
    language = payload.get("language", "en")
    try:
        result = coach_mod.coach_feedback(report, language)
    except coach_mod.CoachNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    except coach_mod.CoachUpstreamError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return JSONResponse(result)


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
