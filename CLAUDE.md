# CLAUDE.md

Guidance for AI coding sessions on **MouthTranscriber**. Read this first, then skim
the latest entries in [`DIARY.md`](DIARY.md) for what changed recently. The full design
rationale lives in [`PROJECT PLAN.md`](PROJECT%20PLAN.md).

## What this project is

Hum a melody → get back **notes, key, and suggested chords** as MIDI, MusicXML, and
engraved sheet music. Local-first Python pipeline plus a FastAPI web app that records
in the browser and calls the same pipeline.

Two load-bearing UX constraints make it tractable (do not "optimize" them away):
- The user hums **"da-da-da"** — the consonant gives every note a crisp onset and
  separates repeated same-pitch notes.
- The user hums **to a metronome at a known BPM** — a known tempo grid turns rhythm
  from estimation into snapping.

## Architecture — a chain of pure functions over 3 data types

Types live in [`mouthtranscriber/model.py`](mouthtranscriber/model.py): `Frame` (one per
~12 ms hop), `NoteEvent` (a discrete note), `Score` (the whole transcription).
[`pipeline.py`](mouthtranscriber/pipeline.py) wires the stages; every stage is a module:

```
audio → preprocess → [note production] → consolidate → tuning → key → quantize → chords → export
```

- **note production** has two paths, chosen by `Params.backend`:
  - `basic_pitch` → [`basicpitch.py`](mouthtranscriber/basicpitch.py): neural note events, no frames.
  - `pyin` / `crepe` → [`pitch.py`](mouthtranscriber/pitch.py) tracker → [`voicing.py`](mouthtranscriber/voicing.py) gate → [`segment.py`](mouthtranscriber/segment.py).
- **consolidate** ([`consolidate.py`](mouthtranscriber/consolidate.py)) is **backend-agnostic** and runs for
  every backend — it fuses the fragments a held (vibrato'd) note leaves behind. This is
  the fix for the "one note → many slivers" bug; don't remove it.
- Then [`tuning.py`](mouthtranscriber/tuning.py), [`key.py`](mouthtranscriber/key.py), [`quantize.py`](mouthtranscriber/quantize.py), [`chords.py`](mouthtranscriber/chords.py), [`export.py`](mouthtranscriber/export.py).

**Every tunable knob is in [`config.py`](mouthtranscriber/config.py) (`Params`).** Change behavior there,
not with magic numbers in the stages.

## Other independent paths: Pitch Finder & Realtime

The web app is tabbed. The **Transcriber** tab is the pipeline above; three others are
separate paths (don't route them through `segment.py`/`pipeline.py`):

- **Pitch Finder** (audio → Key / BPM / Camelot + technical stats) — [`analyze.py`](mouthtranscriber/analyze.py)
  (`analyze_audio`) behind `POST /api/analyze`. Chroma-based (works on polyphonic audio),
  reusing `key.score_keys` and `tempo._fold`. The segmenter is monophonic; wrong for full songs.
- **Realtime** (live pitch monitor + guitar tuner) — **entirely client-side** in
  [`realtime.js`](server/static/realtime.js) (Web Audio `AnalyserNode` fftSize 8192 →
  autocorrelation pitch detector; a server round-trip can't be realtime). Voice monitor
  shows note/Hz/±50¢ + a pitch graph and, on stop, gets the sung key from `POST /api/key`
  (histogram → `key.score_keys` + `analyze.to_camelot`, **no audio upload**). The tuner is
  the same detector with cents referenced to a fixed string (standard EADGBE). Mic is
  released on stop and on switching away from the tab.
- **Transposer** — a disabled placeholder for now.

## The three note-detection backends

| backend | what it is | best for | notes |
|---|---|---|---|
| `crepe` | neural pitch CNN, voice-trained (torchcrepe) | **humming / singing** | web app default; steadiest pitch on voice |
| `basic_pitch` | Spotify CNN, instrument-trained (ONNX) | instrument clips | can octave-jump on bare voice |
| `pyin` | classic DSP f0 + our segmenter | crisp staccato "da-da-da" | `Params()` default |

**Defaults differ by entry point** (intentional): `Params()` → `pyin`; CLI `--backend`
→ `basic_pitch`; the **web app → `crepe`** (dropdown default + server `Form` default).
CREPE/pYIN both feed `segment.py`; basic-pitch bypasses it.

## Environment & install — READ BEFORE `pip install`

- **Python 3.12 on Windows**, interpreter at `.venv/Scripts/python.exe`. Shells:
  PowerShell (primary) and Git Bash. ffmpeg must be on PATH.
- **numpy is pinned to 2.0.2. Do not let any install bump it.** The neural backends
  are installed in a way that protects this:
  - **basic-pitch runs on ONNX Runtime, NOT TensorFlow** (its TF pin has no 3.12
    wheel). Install: `pip install basic-pitch --no-deps` + `pip install "resampy<0.4.3" --no-deps` + `pip install onnxruntime`.
  - **CREPE**: `pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu`
    (CPU-only), then `pip install torchcrepe --no-deps` + `pip install tqdm`.
- Full recipe with reasoning is in [`requirements.txt`](requirements.txt).

## Running things

- **Web app (what the user uses):** `run.bat` (or `./run.ps1`). Starts uvicorn on
  **:8000** with `--reload` and opens the browser. Static files are served fresh from
  disk (a no-cache middleware in [`server/app.py`](server/app.py)), so JS/HTML/CSS edits need only a
  browser refresh; **Python edits reload automatically** via watchfiles.
- **CLI:** `python cli.py <audio> -o out.mid --sheet out.svg --bpm 100 [--backend crepe]`.
- **Tests:** `.venv/Scripts/python.exe -m pytest tests/ -q` (PYTHONPATH handled by
  [`conftest.py`](conftest.py)). The neural backends make some tests slow; `test_crepe.py` /
  `test_basicpitch.py` `importorskip` if their libs are missing.

## Working conventions & gotchas learned the hard way

- **Port 8000 is the user's.** If you start a preview/verify server on 8000, **stop it
  before ending your turn** — a lingering server binds the port and the user's `run.bat`
  fails with `WinError 10013`. The launch config `mouthtranscriber-web` in
  `.claude/launch.json` is on 8000; only run it transiently.
- **Mic capture must be raw.** The browser records with `RAW_MIC` constraints
  (`noiseSuppression/echoCancellation/autoGainControl: false`) in [`server/static/app.js`](server/static/app.js).
  The browser's default speech DSP is a noise gate that zeros quiet audio and chops
  notes — never revert to `getUserMedia({ audio: true })`.
- **BPM mismatch is the usual "why is my note split into tied slivers".** Wrong tempo →
  non-integer quantized durations → notation renders ties. The "🎙 Find my tempo" button
  ([`tempo.py`](mouthtranscriber/tempo.py) + `/api/detect-tempo`) exists to prevent this.
- **This is a segmentation problem, not a model problem.** When notes come out wrong,
  the pitch *contour* is usually fine — look at `segment.py` / `consolidate.py` /
  `quantize.py` before reaching for a model. Finetuning was considered and rejected:
  wrong tool, and we have no labeled hum dataset.

## Keep the docs current

- **After each meaningful change, add a `DIARY.md` entry (newest on top):** what
  changed, why, and what's next. Convert relative dates to absolute.
- Update this file when architecture, backends, defaults, or install steps change.
- `PROJECT PLAN.md` is the frozen design reference — don't rewrite it; cite its §s.
