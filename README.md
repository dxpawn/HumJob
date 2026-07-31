# MouthTranscriber

Hum a melody → get back the notes, the key, and suggested chords, as **MIDI** and
**MusicXML**. Local-first Python pipeline; a web app is the eventual goal (same
Python backend). See [`PROJECT PLAN.md`](PROJECT%20PLAN.md) for the full design and
[`DIARY.md`](DIARY.md) for the running build log.

**Hum "da-da-da" to a metronome click.** The consonant gives every note a crisp
boundary, and a known BPM makes the rhythm tractable — the two tricks that make
this work where phone apps fail.

## Status

Milestones **M0–M6 complete**. The pipeline — pitch → voicing → segmentation →
tuning → key → **rhythm quantization → chord suggestion → MusicXML → engraved
sheet music** — transcribes all synthetic fixtures at note-F1 ≥ 0.95, has been
validated on a real 150-note melody (note-F1 0.987, key correct), suggests
diatonic chords per measure (printed above the staff), and runs behind a **local
web app** (record to a metronome in the browser → get the sheet + chords back).
Web deployment is the remaining milestone (see the plan's milestone table).

## Setup

Requires Python 3.12 and ffmpeg on PATH.

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt   # Windows
# or: source .venv/bin/activate && pip install -r requirements.txt
```

## Usage

Generate the synthetic test clips (no recordings needed yet):

```bash
python tests/make_synthetic.py
```

Transcribe a hum to MIDI + sheet music, with a debug plot:

```bash
python cli.py tests/data/generated/twinkle.wav -o out/twinkle.mid --sheet out/twinkle.svg --bpm 110
```

The CLI prints the note sequence, detected key candidates, and the estimated
tuning offset. `--sheet out.svg` engraves readable sheet music (verovio),
`--musicxml out.musicxml` writes notation, and `--plot debug.png` writes the
pitch/voicing debug figure. Pass the metronome tempo with `--bpm`.

## Web app (local)

Record to a metronome in your browser and get the sheet back:

```bash
python -m uvicorn server.app:app --port 8000
```

Then open http://localhost:8000 — set the tempo (tap-tempo works, and **Preview
click** lets you hear it first), hit Record, hum "da-da-da" to the click (headphones
recommended), and Stop. You get the detected key, suggested chords, engraved sheet
music, and MIDI/MusicXML downloads — plus a **▶ Play** button that sonifies the
result (melody, with the chords underneath). No mic? Use the file-upload link. Same
Python pipeline as the CLI; the browser only records and displays.

## Evaluate

```bash
python tests/eval_report.py         # precision/recall/F1 table over all fixtures

# Regression gate (F1 >= 0.95, key, tuning, silence, chords, API). Run each test
# file in its own process — on Windows the native DSP libs (numba/librosa) can
# SIGABRT if the whole heavy suite shares one long-lived interpreter.
for f in tests/test_*.py; do python -m pytest "$f" -q || break; done
```

## Layout

```
mouthtranscriber/   core pipeline package (one module per stage)
cli.py              hum2midi command
tests/              synthetic generator, eval report, pytest gate
server/             FastAPI web backend + browser UI (record → sheet)
```

## How it works (short version)

1. **pitch** — pYIN (`librosa`) estimates f0 per ~12 ms frame, constrained to a
   human-hum range to avoid octave errors. CREPE (torchcrepe) is an optional backend.
2. **voicing** — a frame counts as sung only if pitch-confidence *and* energy both
   clear a gate (with hysteresis); this is what kills phantom notes in silence.
3. **segment** — the continuous pitch is cut into notes at silences, energy valleys
   (the "d" closures — this separates repeated same-pitch notes), and sustained
   pitch steps.
4. **tuning** — one global offset is estimated so humming 40 cents flat still lands
   on the right semitones.
5. **key** — Krumhansl–Schmuckler correlation over a duration-weighted pitch-class
   histogram.
6. **quantize** — snaps onsets/durations to the known-BPM grid, estimating a global
   grid phase (like the tuning offset, but for time) so a lead-in doesn't misalign
   everything; holds notes legato unless a real rest is detected.
7. **chords** — suggests one diatonic triad per measure: each candidate is scored by
   how much of the measure's melody it covers (strong beats and long notes weighted
   heaviest), then the sequence is smoothed with a root-motion progression prior
   (Viterbi — circle-of-fifths moves like V→I are cheap, retrogressions dear).
8. **export** — performance MIDI, grid-quantized MusicXML (time/key signature,
   rests, key-aware enharmonic spelling, chord symbols above the staff), and
   engraved sheet-music SVG via verovio.
