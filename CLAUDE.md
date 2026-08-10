# CLAUDE.md

Guidance for AI coding sessions on **HumJob**. Read this first, then skim
the latest entries in [`DIARY.md`](DIARY.md) for what changed recently. The full design
rationale lives in [`PROJECT PLAN.md`](PROJECT%20PLAN.md).

## Version control

**BY DEFAULT, DO NOT WORK ON BRANCHES. DO NOT COMMIT ANYTHING ON YOUR OWN. ALWAYS ASK FOR
PERMISSION BEFORE BRANCHING OR COMMITTING.** Work directly on `main` in the working tree and
leave changes uncommitted for the user to review. This overrides any default "branch before
editing main" behavior.

When the user does ask for a commit, **do not add any Claude / AI attribution** - no
`Co-Authored-By` trailer, no "Generated with" line, no assistant name or info in the message.

## Writing style

Do not use any em or en dashes. Use hyphens if necessary.

Do not use emojis except in important titles or important warnings. Do not sprinkle them
into buttons, labels, readouts, or body text.

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
- **segment** is **grid-aware** ([`grid.py`](mouthtranscriber/grid.py)): the pipeline passes it the
  known `bpm` and a **fine energy envelope** (short window, `Params.onset_frame_length`) so a
  short/soft "d" closure between two same-pitch notes is detected as a narrow energy dip (a width
  gate separates it from wide tremolo), and a shallower dip still counts when it lands on a beat.
  This is what separates repeats of the SAME pitch; it replaced a coarse-RMS valley splitter.
- **consolidate** ([`consolidate.py`](mouthtranscriber/consolidate.py)) is **backend-agnostic** and runs for
  every backend — it fuses the fragments a held (vibrato'd) note leaves behind. This is
  the fix for the "one note → many slivers" bug; don't remove it. It is also **grid-aware** (takes
  `bpm`): it will NOT fuse two same-pitch notes across a grid onset, so it never undoes a
  re-articulation `segment` deliberately split on the beat.
- Then [`tuning.py`](mouthtranscriber/tuning.py), [`key.py`](mouthtranscriber/key.py), [`quantize.py`](mouthtranscriber/quantize.py), [`chords.py`](mouthtranscriber/chords.py), [`export.py`](mouthtranscriber/export.py).

**Every tunable knob is in [`config.py`](mouthtranscriber/config.py) (`Params`).** Change behavior there,
not with magic numbers in the stages.

## Transcriber Auto / Manual mode (interactive staff editor)

The Transcriber result card has an **Auto / Manual** toggle. Auto is the read-only server sheet.
**Manual** is an in-browser MuseScore-style editor for fixing the auto draft (the segmenter is the
weak link; the pitch contour is usually fine). It is almost entirely client-side in
[`server/static/manual.js`](server/static/manual.js) (`window.MT`), the full design is
[`MANUAL TRANSCRIBE MODE PLAN.md`](MANUAL%20TRANSCRIBE%20MODE%20PLAN.md) (all 4 phases shipped):

- **Client MusicXML + engraving.** `MT.notesToMusicXML(seq, opts)` is a pure builder that ports
  `export.py`'s spelling/key logic to JS (key-aware enharmonics, barline tie-splitting, clef by
  register, `<harmony>`) and is validated against a music21 structural golden
  (`tests/gen_manual_golden.py` -> `tests/data/manual_golden.json`, checked by
  `tests/manual/builder.test.cjs` and the pytest drift-guard). It returns a `noteheadMap` (DOM
  notehead -> seq index) because a tied note is several noteheads. Engraving is **vendored
  verovio-WASM** ([`server/static/vendor/verovio/`](server/static/vendor/verovio/), light 6.2.0
  build, wasm inlined, no CDN); the toolkit is cached so edits re-engrave synchronously.
- **Model + edits.** A reflow `seq` (ordered notes/rests in `1/subdiv` ticks; onsets = running
  sum). Pure ops `MT.EDITS` (pitch/duration/mergeNext/split/deleteToRest/insertAfter) + `snapSel`
  return a new seq (node-tested). The `createManual` controller does selection, undo/redo, the
  reference pitch strip (hummed `frames` vs chosen notes), and the toolbar.
- **Server touches (edges only).** `/api/transcribe` additionally returns `frames`, `subdiv`, and
  chord `root_name`. `POST /api/export-edited` (edited notes -> MIDI + MusicXML) and
  `POST /api/rescore` (edited notes -> key + chords) reuse `export`/`key`/`chords`; both rebuild
  `NoteEvent`s via `app._notes_from_json`, which **synthesizes seconds from the grid**
  (`start_ql*60/bpm`) since edited notes have no raw performance timing. Everything stays local.

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
  (histogram → `key.score_keys` + `analyze.to_camelot`, **no audio upload**). Two
  client-only voice options: a "Hold last pitch" checkbox (freezes the readout during
  silence; graph still gaps) and a "Target key" picker - an interactive **circle of
  fifths** (`buildCircle` in `realtime.js`; outer ring major, inner ring relative minor,
  centre clears) whose wedge click sets a hidden `#rtTarget` to the same `pc:mode` value
  the old dropdown produced, so `compareToTarget` reports how far off the sung key was in
  cents, or semitones once the gap reaches a semitone. **Vocal-training features** are being
  added in phases per [`VOCAL TRAINER PLAN.md`](VOCAL%20TRAINER%20PLAN.md); **all phases A-E are
  in (the plan is complete).** Phase A: a **target note** (click the pitch graph, or the `#rtTgt*` stepper;
  `setTargetMidi`) that `drawGraph` shows as a dashed line plus a `BAND_CENTS` in-tune band, a
  **reference note** (`#rtRef` button, `playReference` sounds a triangle oscillator at the
  target for ~2s then stops itself; a sustained drone was annoying), and a live **steadiness +
  sustain** readout (`#rtMetrics`, from `stdevCents` and an in-tune timer). Phase C adds a
  **practice sub-mode selector** (`#rtSubmode`: Free / Match game / Scale trainer) branching
  `updateVoice` on the same capture loop; the drills drive `targetMidi` so the lane + sustain
  come free. **Match game** (`startMatch`/`matchFrame`/`lockMatch`) plays a random C3-C5 note
  via `playReference` and auto-advances after an 800 ms in-band hold; **Scale trainer**
  (`startScale`/`scaleSequence`/`scheduleTone`) steps a scale/arpeggio target on each beat
  with `click()` (reused from `app.js`) + a guide tone, at a local BPM, scoring in-tune %.
  Drills tear down (`endMatch`/`endScale`/`stopScaleTones`) on Stop, sub-mode switch, and
  tab-leave. Phase B adds **vibrato** (pure `analyzeVibrato` over a ~2s `vibBuf`: drift-removed,
  depth = p95-p5 spread, rate = zero-crossings; gated to 3-9 Hz / >=15c; shown on `#rtVibrato`
  only when present) and an **in-tune %** per take (`voicedFrames`/`inTuneFrames`, appended to
  the on-stop key readout). Phase D adds a **vocal range finder**: `updateVoice` tracks the
  min/max of *stable* pitch (held ~40c for >=5 clear frames, so glitches/octave slips don't
  widen it; pure `rangeFromFrames` mirrors it), folds the take into a session best on Stop
  (range lines in `renderKey`), and a guided **Range** sub-mode (`#rtSubmode`) shows lo/hi live.
  Phase E logs each finished take (`buildSession`/`saveSession` in `fetchKey`) to
  `localStorage["humjob.voice.history"]` (capped 50, no upload) and shows a collapsible
  **Progress** panel (`#rtProgress`) with the last 8 takes and an in-tune-% sparkline
  (`drawSpark`). The tuner is the same detector with cents referenced to a fixed string (standard
  EADGBE). Mic is released on stop and on switching away from the tab; reference/guide tones
  stop with it.
- **Transposer** ([`transposer.js`](server/static/transposer.js), `window.TR`) — shifts a score to
  a new key. **Two source modes** (branch on `mode`), no recording required:
  - **File** (primary): upload a MIDI/MusicXML file. Transposed **server-side by music21**
    ([`mouthtranscriber/transpose.py`](mouthtranscriber/transpose.py) behind `POST /api/transpose-file`),
    which moves **every voice + the key signature** (polyphony-safe) and returns the engraved SVG
    (server verovio), transposed MusicXML/MIDI, a flat note list for playback, and the key. Re-posted
    (debounced) on each shift; covered by [`tests/test_transpose.py`](tests/test_transpose.py).
    File mode also shows **Camelot compatible-key presets** (`renderCamelot`): the source key's
    Camelot code + its two perfect-fifth neighbours as one-click shifts (each reuses
    `setShift(minimalShift(...))`). `TR.toCamelot` ports `analyze.py`'s `to_camelot`; the relative
    major/minor is a mode change (not a rigid shift) so it is not offered. **File mode only** - the
    row is hidden for hums (key-mixing is a full-song idea).
  - **Hum** (secondary): transpose the last Transcriber result (`lastResult`), entirely client-side
    and monophonic — transposes notes/key/chords by N semitones, re-engraves via the same `MT` +
    verovio path Manual mode uses, plays with app.js's piano voices, exports via `POST /api/export-edited`.
    Offered as a link only when a hum exists. A rigid transposition preserves chord *function*, so
    Roman numerals are invariant; only chord roots move. Pure math is node-tested in
    [`tests/manual/transposer.test.cjs`](tests/manual/transposer.test.cjs).
  - Note: `export.render_musicxml_svg` (shared by the auto sheet and the file path) **caches one
    verovio toolkit** — creating a fresh toolkit per render aborts under repeated calls. Deferred:
    real audio pitch-shift (the Camelot compatible-key helper shipped, see File mode above).

## The note-detection backends

| backend | what it is | best for | notes |
|---|---|---|---|
| `pesto` | self-supervised pitch (pesto-pitch, 2023) | **most precise** on voice | **web app default**; matches/beats CREPE, lighter + faster (~4x). Native at any sr, no resample. Model `mir-1k_g7` (`Params.pesto_model`), singing-voice trained. Optional install |
| `fcnf0` | FCNF0++ (penn, Morrison 2023) | **most precise** (peer to PESTO) | fully-convolutional f0. Decoded with argmax (avoids penn's `torbi` Viterbi ext, no wheel for our torch) via `PennTracker`; entropy periodicity rescaled by `Params.penn_conf_lo/hi`. Weights download from HF once, then cached. In our synthetic A/B it was less robust in-sequence than PESTO (dropped a note); shines on real voice. Optional install |
| `crepe` | neural pitch CNN, voice-trained (torchcrepe) | **humming / singing** | steadiest CNN on voice. Uses the accurate **`full`** model (`Params.crepe_model`, default `"full"`; `"tiny"` = ~7x faster, less precise) + Viterbi decoding |
| `basic_pitch` | Spotify CNN, instrument-trained (ONNX) | instrument clips | can octave-jump on bare voice |
| `pyin` | classic DSP f0 + our segmenter | crisp staccato "da-da-da" | `Params()` default |

**Defaults differ by entry point** (intentional): `Params()` → `pyin`; CLI `--backend`
→ `basic_pitch`; the **web app → `pesto`** (dropdown default + server `Form` default; was
`crepe` until 2026-08-04). `pesto`/`fcnf0`/`crepe`/`pyin` are per-frame trackers that all
feed `segment.py`; basic-pitch bypasses it.

## Environment & install — READ BEFORE `pip install`

- **Python 3.12 on Windows**, interpreter at `.venv/Scripts/python.exe`. Shells:
  PowerShell (primary) and Git Bash. ffmpeg must be on PATH.
- **numpy is pinned to 2.0.2. Do not let any install bump it.** The neural backends
  are installed in a way that protects this:
  - **basic-pitch runs on ONNX Runtime, NOT TensorFlow** (its TF pin has no 3.12
    wheel). Install: `pip install basic-pitch --no-deps` + `pip install "resampy<0.4.3" --no-deps` + `pip install onnxruntime`.
  - **CREPE**: `pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu`
    (CPU-only), then `pip install torchcrepe --no-deps` + `pip install tqdm`.
  - **PESTO** (most precise; reuses CREPE's torch): pin numpy while installing so its
    deps can't bump it - `pip install pesto-pitch -c <(echo numpy==2.0.2)` (or write the
    pin to a file first). Pulls only omegaconf/antlr4/PyYAML; numpy 2.0.2 stays put.
  - **FCNF0++** (penn; precision peer to PESTO, reuses CREPE's torch): `pip install penn -c
    <numpy==2.0.2>`. Heavier tree (tensorboard, huggingface_hub, torbi) but numpy stays put.
    penn's `torbi` Viterbi ext has no wheel for our torch, so `PennTracker` stubs it and
    decodes with argmax; nothing to fix. Weights download from HF on first use.
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
- **Segmentation eval:** `python tests/eval_report.py` prints a P/R/F1 table in **two passes** -
  CLEAN (the gentle fixtures the gate expects at F1 = 1.0) and REALISTIC (an expressive take: wide
  vibrato, tremolo, drift, partial "d" closures - [`make_synthetic.py`](tests/make_synthetic.py)
  `Expr`/`REALISTIC`). REALISTIC is where the segmenter actually fails; **its mean F1 is the number
  to drive up** (baseline 0.799 as of Session 35). This is the iteration dashboard - do not tune
  segmentation against CLEAN alone (it is saturated at 1.0 and hides the problem).

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
- **The three diagnosed segmentation failures** (Session 35, from the REALISTIC eval; all showed as
  *merged* notes / recall drops). **(1) and (2) are FIXED by grid-aware segmentation (Session 36).**
  (1) the splitter **missed short/soft "d" closures** because RMS is windowed over ~93 ms
  (`frame_length=2048`), smearing a ~40 ms dip — fixed by the fine envelope + width gate in
  `segment`; (2) **`consolidate` over-merged** correctly-split same-pitch repeats — fixed by its
  grid onset guard; (3) **pitch octave errors** on legato/continuous voiced audio (pYIN reads C5 as
  C4) **remain** — this is a pitch-backend issue, not segmentation (try PESTO/CREPE), and is partly
  an artifact of the voiced-through synthetic closure. Realistic eval mean F1: 0.799 -> 0.931.

## Keep the docs current

- **After each meaningful change, add a `DIARY.md` entry (newest on top):** what
  changed, why, and what's next. Convert relative dates to absolute.
- Update this file when architecture, backends, defaults, or install steps change.
- `PROJECT PLAN.md` is the frozen design reference — don't rewrite it; cite its §s.
