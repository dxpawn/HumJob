# Real hum recordings + ground truth

The synthetic fixtures in `tests/data/generated/` are saturated (note F1 = 1.0), so they
no longer expose real failures. This folder holds **real browser recordings** of a known
melody, with hand-written ground truth, so we can measure and fix what the pipeline does
on actual voice. See `tests/diagnose_recorded.py`.

## Capturing a clip (byte-faithful to production)

Record through the app's own path so the clip is exactly what the pipeline receives
(raw mic, no noise suppression / AGC):

1. Open the app (`run.bat`), go to the **Transcriber** tab.
2. Set the metronome BPM to the target's BPM, pick the backend (leave it on **PESTO**).
3. Hit Record, wait for the count-in, hum the target melody **"da-da-da"** - one crisp
   note per click, a clean "d" between repeats. Stop.
4. The app shows a **download link** for the raw take (`my-hum.webm`). Save it.
5. Move it here as `<name>.webm` and write a sibling `<name>.json` (schema below).

## The real-take set (EVALUATION UPGRADE)

There are **four** real takes here: `scale_1`, `twinkle_1`, `repeated_1`, and `mary_1`. A
larger melodic set was considered and dropped: melodies with leaps, arpeggios, or full tunes
need a trained singer to hum accurately, and per-note pitch errors from an untrained hummer
would be indistinguishable from the pipeline's own errors (they contaminate `diagnose_recorded.py`,
which compares against the intended melody). The four takes here feed only the two
performer-tolerant measurements, which is why an imprecise hum is fine for them:

- **reconstruction round-trip** (`mouthtranscriber/roundtrip.py`) scores each transcription
  against its OWN audio, never against an intended melody, so a wrong note the pipeline
  captures faithfully still agrees;
- **realism calibration** (`tests/calibrate_realism.py`) MEASURES the hum's vibrato, drift,
  jitter, and shimmer - the imprecision is the signal, not an error.

No figure in the report's accuracy story comes from these real takes; that rests on the
synthetic corpus. To add another take, record any melody you can hum comfortably, save it as
`<name>.webm`, and write a sibling `<name>.json` (schema above) - accuracy is not required.

After recording, verify a take with:

```
.venv/Scripts/python.exe tests/diagnose_recorded.py tests/data/recorded/mary_1.json
```

and measure your real realism and the round-trip numbers with:

```
.venv/Scripts/python.exe tests/calibrate_realism.py tests/data/recorded/*.json
python tests/eval_report.py
```

## Ground-truth schema (`<name>.json`)

Same shape as `tests/make_synthetic.FIXTURES`, so a real take is directly comparable to
its synthetic twin.

```json
{
  "audio": "twinkle_1.webm",
  "bpm": 110,
  "time_sig": [4, 4],
  "backend": "pesto",
  "subdiv": 4,
  "sequence": [[60,1],[60,1],[67,1],[67,1],[69,1],[69,1],[67,2],
               [65,1],[65,1],[64,1],[64,1],[62,1],[62,1],[60,2]],
  "pitch_mode": "relative",
  "notes": "Twinkle first phrase, hummed by <who>, <when>"
}
```

- `audio` - optional; defaults to `<json-basename>.webm`.
- `sequence` - list of `[midi, beats]`; `midi = null` is a rest. `C4 = 60`. `beats` is in
  quarter notes. This is the melody you **intended** to hum.
- `pitch_mode` - how pitch is scored. A hummer without a reference tone picks their own
  register AND key, so the default forgives that and scores what they can control:
  - `"relative"` (recommended for a self-pitched hum) - removes one global transposition
    (any number of semitones), so it scores the melody **shape**. A single per-note octave
    slip is an outlier that barely moves the median, so it still flags.
  - `"octave"` (default if unset) - removes only the octave, so a wrong key shows up.
  - `"absolute"` - scores raw MIDI. Use when you hummed to a known reference pitch.
- `subdiv` - grid ticks per quarter (default 4 = sixteenths), matches the app's default.

## Running the diagnosis

```
.venv/Scripts/python.exe tests/diagnose_recorded.py tests/data/recorded/<name>.json
```

It prints the decode, the frame/voicing summary, the note count after each stage
(segment -> consolidate -> quantize), a ref-vs-estimate table, and a verdict naming the
first stage that diverges.
