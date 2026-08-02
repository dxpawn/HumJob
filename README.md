# MouthTranscriber

**Hum a melody → get back the notes, the key, and suggested chords** — as MIDI,
MusicXML, and engraved sheet music. A local-first Python pipeline with a browser app
that records to a metronome and calls the same pipeline.

> **Hum "da-da-da" to a metronome click.** The consonant gives every note a crisp
> boundary (and separates repeated same-pitch notes); a known BPM makes the rhythm
> tractable. Those two tricks are why this works where phone apps fail.

See [`CLAUDE.md`](CLAUDE.md) for the architecture at a glance, [`DIARY.md`](DIARY.md)
for the running build log, and [`PROJECT PLAN.md`](PROJECT%20PLAN.md) for the full design.

## Status

Milestones **M0–M6 complete**. The pipeline — note detection → consolidation → tuning
→ key → **rhythm quantization → chord suggestion → MusicXML → engraved sheet music** —
transcribes all synthetic fixtures at note-F1 ≥ 0.95, has been validated on a real
150-note melody (note-F1 0.987, key correct), and runs behind a **local web app**
(record to a metronome in the browser → get the sheet + chords back). Web deployment is
the remaining milestone.

### Three interchangeable note-detection backends

| Backend | What it is | Best for |
|---|---|---|
| **CREPE** (`crepe`) | Neural pitch CNN, **trained for the singing voice** (torchcrepe) | **Humming / singing** — steadiest pitch, no octave jumps. **Web app default.** |
| **basic-pitch** (`basic_pitch`) | Spotify's pretrained CNN, run on ONNX Runtime | Simple instrument recordings — robust segmentation, but *instrument*-trained so it can octave-jump on bare voice. |
| **pYIN** (`pyin`) | Classic DSP f0 tracker + our segmenter | Crisp staccato "da-da-da". |

The web UI exposes all three in a **Pitch engine** dropdown so you can A/B on your own
voice. The CLI defaults to `basic_pitch`; the library default (`Params()`) is `pyin`.

## Setup

Requires **Python 3.12** and **ffmpeg** on PATH.

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt   # Windows
# or: source .venv/bin/activate && pip install -r requirements.txt
```

The neural backends are installed separately and **isolated from the pinned numpy 2.0.2
stack** (see [`requirements.txt`](requirements.txt) for the reasoning). To use them:

```bash
# basic-pitch — runs on ONNX Runtime, not TensorFlow (no TF 3.12 wheel exists):
.venv\Scripts\python -m pip install basic-pitch --no-deps
.venv\Scripts\python -m pip install "resampy<0.4.3" --no-deps
.venv\Scripts\python -m pip install onnxruntime

# CREPE (the web app's default) — CPU-only PyTorch (~120 MB) + torchcrepe:
.venv\Scripts\python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
.venv\Scripts\python -m pip install torchcrepe --no-deps
.venv\Scripts\python -m pip install tqdm
```

## Web app (the main way to use it)

**Easiest:** double-click **`run.bat`** (or `./run.ps1` in PowerShell). It starts the
server on http://localhost:8000, opens your browser, and live-reloads on code edits.
Equivalent manual command:

```bash
python -m uvicorn server.app:app --port 8000 --reload
```

In the browser:

1. **Set the tempo** — drag the slider, **Tap tempo**, or **🎙 Find my tempo** (hum a
   few beats and it detects your BPM, so you record to a click that matches your
   phrasing — a mismatched tempo is what makes held notes notate as tied slivers).
   **▶ Preview click** lets you hear the metronome first.
2. **Record** — hit Record, wait for the count-in, and hum "da-da-da" to the click.
   **Use headphones** so the click doesn't bleed into the mic. Then Stop. (No mic? Use
   the file-upload link.)
3. **Get back** the detected key, suggested chords, engraved sheet music, and
   MIDI/MusicXML downloads — plus **▶ Play** (piano playback of melody + chords) and a
   link to download your raw recording.

The browser only records and displays; all analysis is the same Python pipeline as the
CLI.

### Pitch Finder tab

A second tab analyzes **any** audio file (mp3/wav/…) — full songs or single instruments
— and returns **Key, BPM, and Camelot code**, with an expandable **Advanced statistics**
panel (key/tempo confidence, tuning & reference A4, spectral centroid/rolloff/bandwidth,
zero-crossing rate, loudness/peak/dynamic range, onset density, pitch-class distribution,
and the compatible Camelot codes for harmonic mixing). This is a separate chroma-based
analyzer ([`analyze.py`](mouthtranscriber/analyze.py) → `POST /api/analyze`), not the
humming pipeline, so it works on polyphonic tracks. A **Transposer** tab is scaffolded
as "coming soon".

### Realtime tab (live pitch monitor + guitar tuner)

A third tab does **live, in-browser pitch tracking** (no server round-trip — it runs on
Web Audio autocorrelation). Two modes, via a toggle:

- **Voice monitor** — sing or hum and see the **note name, frequency (Hz), and a ±50-cent
  needle**, with a scrolling pitch graph. Hit **Stop** and it reports the **key** of what
  you sang (it accumulates a pitch-class histogram from the tracked pitch and posts it to
  `POST /api/key`, which reuses the same Krumhansl key scorer — no audio upload).
- **Guitar tuner** — walk the six strings **thickest→thinnest** (standard EADGBE); the
  needle shows cents flat/sharp against the target string and **auto-advances** once a
  string holds in tune. Tap a string to redo it.

The 8192-sample analysis window resolves even the low-E string to ~±3 cents. Live-mic
behaviour needs a real browser (a headless preview has no microphone).

## Command line

```bash
# generate the synthetic test clips (no recordings needed):
python tests/make_synthetic.py

# transcribe a hum to MIDI + sheet music:
python cli.py tests/data/generated/twinkle.wav -o out/twinkle.mid --sheet out/twinkle.svg --bpm 110
```

The CLI prints the note sequence, key candidates, and tuning offset. Flags:
`--musicxml out.musicxml` writes notation, `--sheet out.svg` engraves sheet music
(verovio), `--plot debug.png` writes a pitch/voicing debug figure (DSP backends only),
`--bpm` sets the metronome tempo, and `--backend {crepe,basic_pitch,pyin}` picks the
engine.

## Testing & evaluation

```bash
# regression suite (F1 >= 0.95, key, tuning, silence, chords, consolidation, API):
.venv/Scripts/python -m pytest tests/ -q

# precision/recall/F1 table over all fixtures:
python tests/eval_report.py
```

`test_crepe.py` and `test_basicpitch.py` skip automatically if their optional libraries
aren't installed. On Windows the native DSP libs (numba/librosa) can occasionally
SIGABRT when the whole heavy suite shares one long-lived interpreter; if you hit that,
run each file in its own process:

```bash
for f in tests/test_*.py; do .venv/Scripts/python -m pytest "$f" -q || break; done
```

## Layout

```
mouthtranscriber/   core pipeline package (one module per stage)
  config.py           every tunable knob (Params)
  model.py            Frame / NoteEvent / Score
  pipeline.py         wires the stages together
  pitch.py            pYIN + CREPE frame-level f0 trackers
  basicpitch.py       basic-pitch (ONNX) neural note events
  voicing.py          silence / phantom-note gate (DSP path)
  segment.py          f0 contour → notes (DSP path)
  consolidate.py      backend-agnostic: fuse over-segmented fragments
  tuning.py key.py quantize.py chords.py   downstream musical analysis
  tempo.py            hum-based BPM detection ("Find my tempo")
  analyze.py          Pitch Finder: audio → Key / BPM / Camelot + stats (own path)
  export.py viz.py    MIDI/MusicXML/SVG export, debug plots
cli.py                hum2midi command
server/               FastAPI backend + browser UI (record → sheet)
tests/                synthetic generator, eval report, pytest gate
run.bat / run.ps1     one-click launchers
```

## How it works

1. **Note detection** — one of three backends turns audio into discrete notes (see the
   table above). CREPE/pYIN estimate f0 per frame, then a **voicing** gate (pitch
   confidence *and* energy, with hysteresis) kills phantom notes in silence and a
   **segmenter** cuts the contour into notes at silences, energy valleys (the "d"
   closures separating repeated notes), and sustained pitch steps. basic-pitch maps
   audio straight to note events.
2. **Consolidation** — a backend-agnostic pass fuses the fragments a sustained,
   vibrato'd note leaves behind (every detector over-segments held notes in its own
   way), so one held note comes back as one note.
3. **Tuning** — a single global offset so humming 40 cents flat still lands on the right
   semitones.
4. **Key** — Krumhansl–Schmuckler correlation over a duration-weighted pitch-class
   histogram.
5. **Quantize** — snaps onsets/durations to the known-BPM grid, estimating a global grid
   phase (like the tuning offset, but for time) so a lead-in doesn't misalign
   everything; holds notes legato unless a real rest is detected.
6. **Chords** — one diatonic triad per measure, scored by how much of the measure's
   melody it covers (strong beats and long notes weighted heaviest), then smoothed with
   a root-motion progression prior (Viterbi — moves like V→I are cheap).
7. **Export** — performance MIDI, grid-quantized MusicXML (time/key signature, rests,
   key-aware enharmonic spelling, chord symbols above the staff), and engraved
   sheet-music SVG via verovio.
