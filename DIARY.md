# HumJob — Development Diary

A running log so any future session (or a fresh context) can pick up quickly.
Newest entry on top. Keep entries short: what changed, why, and what's next.

---

## 2026-08-04 - Session 31: FCNF0++ added as a fifth backend + a dropdown explainer tooltip

User loved PESTO and asked for FCNF0++ too, plus a hover explainer on the engine dropdown.

- **FCNF0++ backend (`fcnf0`)** via `penn` (Morrison 2023), a precision peer to PESTO. New
  `PennTracker` in [`pitch.py`](mouthtranscriber/pitch.py) on the same `PitchTracker` interface,
  wired through `make_tracker`, server `/api/transcribe`, CLI `--backend fcnf0`, and the web
  dropdown. Test [`test_penn.py`](tests/test_penn.py) mirrors the CREPE/PESTO worst-case; **passes**.
- **Install (numpy pin held).** `pip install penn -c numpy==2.0.2` -> penn 1.0.0; heavier tree
  (tensorboard, huggingface_hub, torbi) but **numpy stayed 2.0.2**. Two integration snags solved:
  1. penn imports the compiled `torbi` Viterbi ext at load, which has **no wheel for torch 2.13**
     (hard `FileNotFoundError`). `pitch.ensure_penn_importable()` stubs torbi (shared by the tracker
     and the test) and we decode with **argmax** - penn refines the argmax bin with a local expected
     value, so cents precision is preserved (3.4 cents on a test tone, between PESTO's 1.0 and
     CREPE-full's 5.8). torbi/Viterbi is never called.
  2. penn's periodicity uses an **entropy scale** (voiced ~0.58, unvoiced ~0.05) that sits right on
     `voiced_enter` 0.55, so vibrato/tremolo dips fragmented held notes. `PennTracker` linearly
     stretches it to [0,1] via new knobs `Params.penn_conf_lo/hi` (0.10/0.45); fixed the fragmentation.
  - penn downloads its checkpoint from HF on first use, then caches; **audio never uploaded**.
- **A/B finding (honest).** On the pure `c_major_scale.wav` fixture FCNF0++ **dropped G4** in-sequence
  (penn's own periodicity collapsed to ~0.07 there) and read the take as A minor; PESTO got all 8.
  penn tracks an **isolated** G4 fine (390.5 Hz), so it is a receptive-field/context weakness of the
  model, not an integration bug. So **PESTO stays the default**; FCNF0++ is the compare option,
  expected to shine on harmonic-rich real voice. Offered, not defaulted.
- **Dropdown explainer.** The "Pitch engine" label is now a `.has-tip` (same hover-bubble the Pitch
  Finder advanced stats use): "FCNF0++ and PESTO are the most precise. CREPE and the rest are less
  precise but faster. Higher precision is usually worth prioritizing." Verified live: all 5 options
  render in order (PESTO default), tooltip text correct, backend suite **20 passed** (penn/pesto/crepe/
  server). Did not start a preview - user's own run.bat holds :8000; verified read-only against it.

---

## 2026-08-04 - Session 30: PESTO added as a fourth backend (the most precise tracker)

Follow-up to Session 29: after upgrading CREPE to `full`, the user asked for something even more
precise. Added **PESTO** (Riou et al., 2023 - self-supervised, transposition-equivariant pitch
estimation) as a fourth note-detection backend. It matches/beats CREPE on singing voice while being
lighter and ~4x faster on CPU, and its default `mir-1k_g7` model is trained on the MIR-1K
singing-voice set, which suits humming.

- **Install (numpy pin protected).** `pip install pesto-pitch -c <numpy==2.0.2 constraint>` -
  resolved to pesto-pitch 2.0.1, pulling only omegaconf / antlr4-runtime / PyYAML (all pure
  Python). **numpy stayed at 2.0.2**; torch/torchaudio/scipy/tqdm were already present from CREPE.
  PESTO 2.0 has its own CQT front end, so **no nnAudio** dependency.
- **New `PestoTracker`** in [`pitch.py`](mouthtranscriber/pitch.py), implementing the same
  `PitchTracker` interface as pYIN/CREPE (so it feeds `segment.py` like the others; NOT the
  basic-pitch bypass). `pesto.predict(x, sr, step_size=hop_s*1000, model_name=...)` returns
  per-frame pitch(Hz) + confidence; we reuse confidence as voiced-prob and compute RMS separately.
  PESTO builds its CQT for the given sr, so it runs **natively at 22050 Hz - no resample** (CREPE
  needs 16k). New knob `Params.pesto_model` (default `"mir-1k_g7"`).
- **Wired everywhere:** `make_tracker` branch; server `/api/transcribe` accepts `backend="pesto"`;
  CLI `--backend pesto`; web dropdown option "PESTO (neural, most precise)"; `app.js` engine label.
- **Verified.** Synthetic A3: PESTO **1.0 cent** error vs CREPE-full's 5.8. Pipeline test
  [`test_pesto.py`](tests/test_pesto.py) (importorskip-guarded, mirrors `test_crepe.py`): the
  worst-case sustained+vibrato+tremolo C scale comes back as whole notes in the right octave, key =
  C major, **2 passed in 10.4s** (CREPE's take 44s). Server round-trip via TestClient: `backend=pesto`
  -> 200, correct scale, 435 frames with `{t,f0,conf}` (Manual-mode strip works). Web dropdown renders
  all four options, no console errors. Preview server run transiently on :8000 then stopped (8000 free).
- **Default flipped to PESTO (same day).** User A/B'd on a real hum and it was decisively better, so
  the **web dropdown + server `Form` default are now `pesto`** (was `crepe`); CREPE stays one click
  away. One-line reversible if ever needed.
- **Next.** The remaining pain is NOT the tracker (its contour is excellent now) - it is
  **segmentation** (`segment.py`/`consolidate.py`/`quantize.py` deciding note boundaries) and
  **Manual-mode UX**. Per CLAUDE.md's own note, that is a segmentation problem, not a model problem;
  a more precise tracker won't move it. Those are the next targets when the user is ready.

---

## 2026-08-04 - Session 29: CREPE upgraded to the "full" model (max pitch accuracy)

User: "the pitch detection is still off... do we have anything more precise? I don't care about
size, accuracy is best." We did have something more precise and were not using it. The web app
defaults to the `crepe` backend, but [`CrepeTracker`](mouthtranscriber/pitch.py) hard-coded
torchcrepe's **`tiny`** model (Session 11 chose it for speed). torchcrepe's own defaults are the
much more accurate **`full`** model + **Viterbi** decoding; we were overriding the model down to
tiny and leaving accuracy on the table.

- **New knob `Params.crepe_model` (default `"full"`)** in [`config.py`](mouthtranscriber/config.py).
  `CrepeTracker` now reads it instead of hard-coding `"tiny"`, and passes
  `decoder=torchcrepe.decode.viterbi` explicitly (already the library default; now documented).
  `"tiny"` is still available as a one-line fallback if a session wants ~7x faster tracking.
- **Why full.** CREPE-full is state-of-the-art monophonic voice pitch; on real humming (breathy
  onsets, vibrato, note transitions) it makes far fewer gross/octave errors than tiny. The weights
  ship bundled with torchcrepe, so no download and **numpy 2.0.2 is untouched** (no install).
- **Cost.** CPU tracking ~9s vs ~7s for a 6-second hum - modest, and accuracy was the priority.
- Verified: full loads and tracks a synthetic A3 to ~6 cents; `test_crepe.py` 2 passed (44s).
- **Next.** User to confirm on a real hum whether "off" was pitch *values* (this fix) or
  *segmentation* (that is Manual mode / `segment.py` knobs, a different lever). If still not
  precise enough, the next rung up is **PESTO** (2023, arguably beats CREPE and faster) as a new
  optional backend - not installed yet; would ask first since it adds a dependency.

---

## 2026-08-04 - Session 28: Manual transcribe mode Phase 4 (editing + undo + live export) - COMPLETE

Final phase of [`MANUAL TRANSCRIBE MODE PLAN.md`](MANUAL%20TRANSCRIBE%20MODE%20PLAN.md); **all four
phases are shipped, Manual mode is done.** You can now fix the auto transcriber's mistakes on the
engraved staff and export the result. Client-side except the two existing edge endpoints.

- **Pure edit ops.** `MT.EDITS` in [`manual.js`](server/static/manual.js) -
  `pitch`/`duration`/`mergeNext`/`split`/`deleteToRest`/`insertAfter` plus `snapSel` - each takes
  a seq + index and returns a NEW seq and the selection to keep (no mutation), so they are
  deterministic and node-unit-tested in [`builder.test.cjs`](tests/manual/builder.test.cjs).
  `mergeNext` is the sliver fix (fuse a held note's fragments); reflow (onsets = running tick
  sum) shifts following notes automatically on any duration/structural change.
- **Editing controller.** `createManual` now selects by seq index, applies an op, then
  **re-engraves synchronously** through the cached verovio toolkit (new `engrave` helper - the
  toolkit is loaded once on entry, so edits redraw instantly with no network). Undo/redo is a
  seq-snapshot stack. A toolbar (`#manualTools`) has every op + Undo/Redo/Revert to auto/Update
  chords + key/Download MIDI/MusicXML; keys: Up/Down = +/- semitone, Shift+Up/Down = octave,
  Left/Right select, Delete = to rest, Ctrl+Z / Ctrl+Shift+Z (or Ctrl+Y) = undo/redo.
- **Wired to the rest of the app.** `onEdit` pushes the edited notes back into `lastResult` so
  **Play** ([app.js](server/static/app.js) `togglePlayback`) uses the edited melody and the
  summary note count tracks; the toolbar's downloads POST the edited notes to
  `/api/export-edited`; **Update chords + key** POSTs to `/api/rescore` and `onRescore` applies
  the returned key + chords to the sheet spelling, chord symbols, the chord strip, the summary,
  and playback (decision 6). Auto's own MIDI/MusicXML links are hidden in Manual so there is one
  clear pair of (edited) downloads. `render()`'s summary + chord-strip builders were extracted
  (`renderSummary`/`renderChordStrip`) so the callbacks reuse them.
- **Lifecycle.** Edits (and undo history) persist across an Auto <-> Manual toggle within a take
  - `enter` reseeds only when the `lastResult` object identity changes, so a new record/upload/
  engine-change starts fresh; "Revert to auto" restores the original notes, key, and chords.
- **Verified in the browser** (server transient on :8000, then **stopped**): +semitone re-engraves
  and syncs `lastResult`; Longer reflows the next note; Merge (8->7), Delete (note->rest), Insert
  (8->9) and Split all work with Undo/Redo restoring exactly; Revert restores the C-major draft;
  `/api/rescore` (200) refreshed chords to C/G; two `/api/export-edited` (200) downloaded edited
  MIDI + MusicXML; an edit survived an Auto/Manual toggle; a new transcription reseeded to the new
  notes; no console errors. Real-mic pass (hum -> fix a sliver/octave -> export -> open in
  MuseScore) is still the user's since the preview has no mic. CLAUDE.md + README Transcriber
  sections updated for the new mode.

## 2026-08-04 - Session 28: Manual transcribe mode Phase 3 (selection + reference pitch strip)

Third phase of [`MANUAL TRANSCRIBE MODE PLAN.md`](MANUAL%20TRANSCRIBE%20MODE%20PLAN.md): you can
now select notes in Manual mode and see your hummed pitch against the chosen notes. Still
read-only (editing is Phase 4); all client-side, no backend changes.

- **Selection controller.** [`manual.js`](server/static/manual.js) gained `createManual(refs)` -
  a stateful controller ([app.js](server/static/app.js) `setSheetMode` builds one lazily and
  calls `enter`/`exit`). Click a notehead (mapped to its `seq` index through the Phase 1
  `noteheadMap`, so a tied note's several noteheads resolve to one note) or press Left/Right to
  move the selection. The selected note is highlighted red on the staff
  (`#sheet g.note.mt-selected`) and the readout (`#manualReadout`) shows its chosen pitch and how
  many cents off it was hummed (from each note's `cents`).
- **Reference pitch strip.** A canvas (`#manualStrip`, in `#manualPane` above `#sheet`, shown
  only in Manual) draws the hummed contour from `frames` as a green line over the chosen notes as
  bars (muted, the selected one red) on a shared seconds axis; clicking it selects the note under
  that time. Mirrors realtime.js's `drawGraph`/`yOf` pitch-to-pixel mapping and reads CSS vars at
  draw time, so it follows light/dark theme. Empty `frames` (basic_pitch) degrades to bars only.
- **Alignment note.** Recording starts *after* the count-in (`startMetronome` fires
  `recorder.start()` only when the count-in ends), so `frames` t=0 and the notes' grid time share
  an origin - the contour and bars line up with no offset correction. The strip is still its own
  linear-time axis, not the staff's x-axis (which verovio spaces musically).
- **Verified in the browser** (server transient on :8000, then **stopped**): Manual shows the
  strip, the contour + bars + selected highlight all paint in the right theme colors
  (color-sampled the canvas), first note auto-selects, Left/Right + notehead-click + strip-click
  all move the selection (readout showed "Note 5 of 8: G4 (you hummed it -40 cents off)" for a
  deliberately flat note), and toggling back to Auto restores the server sheet and removes the
  key listener. No console errors. A real-mic pass is still the user's (preview has no mic).
- **Next (Phase 4):** edit operations over the `seq` (pitch +/-, duration, merge/split, delete ->
  rest, insert, revert), undo/redo, and wiring Play + downloads to the edited melody via
  `/api/export-edited` and `/api/rescore`.

## 2026-08-04 - Session 28: Manual transcribe mode Phase 2 (verovio-WASM + Auto/Manual toggle)

Second phase of [`MANUAL TRANSCRIBE MODE PLAN.md`](MANUAL%20TRANSCRIBE%20MODE%20PLAN.md): the
Transcriber result now has an **Auto / Manual** toggle, and Manual re-engraves the notes in the
browser (read-only for now; editing is Phase 3-4).

- **Vendored verovio-WASM.** [`server/static/vendor/verovio/verovio-toolkit-wasm.js`](server/static/vendor/verovio/verovio-toolkit-wasm.js)
  is the prebuilt light (non-Humdrum) verovio 6.2.0 browser build from npm - 7 MB, the wasm
  inlined as base64 so it is one self-contained file, LGPL-3.0, served locally (no CDN). Chosen
  over the `.mjs` ESM build because FastAPI StaticFiles on Windows can serve `.mjs` with the
  wrong MIME and break module imports; the UMD `.js` loads via a plain script tag. Provenance in
  the sibling `README.md`.
- **`manual.js` render path.** `loadVerovio()` injects the script once and resolves a ready
  `verovio.toolkit`, handling the Emscripten init race (`window.verovio` is set synchronously,
  then `module.calledRun` flips / `onRuntimeInitialized` fires). `renderSeq(seq, opts)` runs the
  Phase 1 `notesToMusicXML` then `loadData` + `renderToSVG` with the same options as
  `export.sheet_svg_string` (plus the white paper rect) so Manual matches Auto. `seqFromNotes`
  builds the reflow seq from `lastResult.notes`, turning gaps into rests.
- **Toggle UI + wiring.** A `.seg` Auto/Manual control in the `#result` card
  ([index.html](server/static/index.html)); `app.js` `setSheetMode()` shows the server SVG for
  Auto and the client render for Manual, caches the server SVG to restore on toggle-back, and
  reseeds to Auto on every new transcription. Enabled by Phase 1's additive `subdiv` + chord
  `root_name` in the transcribe response.
- **Verified in the browser** (server started transiently on :8000 and **stopped**): the
  vendored file is fetched from localhost (200), the runtime initializes, a C-major scale renders
  as **8 noteheads + 2 chord symbols** from the client-built MusicXML, toggling back to Auto
  restores the server sheet, and there are no console errors. **Spike results:** verovio renders
  MusicXML `<harmony>` as chord symbols (so no HTML chord-strip fallback needed); `noteheadMap`
  stays the selection strategy (verovio regenerates ids). A real-mic pass (hum -> Manual) is
  still the user's since the preview has no mic.
- **Next (Phase 3):** click-to-select via `noteheadMap`, arrow-key selection, and the reference
  pitch-trace strip drawn from `frames`.

## 2026-08-04 - Session 28: Manual transcribe mode Phase 1 (backend plumbing + client MusicXML builder)

First phase of [`MANUAL TRANSCRIBE MODE PLAN.md`](MANUAL%20TRANSCRIBE%20MODE%20PLAN.md) (the
interactive staff editor that lets the user fix the auto transcriber's note decisions). This
phase is all headless backend + pure client logic; no UI yet (that is Phase 2+).

- **`/api/transcribe` now surfaces what the editor needs (additive):** the decimated per-hop
  pitch contour `frames` (`t`, `f0` null-if-unvoiced, `conf`) for the reference strip, `subdiv`
  (grid ticks per quarter), and chord `root_name` (music21 spelling, so an edited melody can be
  re-exported). `basic_pitch` still returns `frames: []` (it produces notes without frames).
- **Two edge endpoints in [`server/app.py`](server/app.py)** (never in the instant edit loop):
  `POST /api/export-edited` builds MIDI + MusicXML from an edited note list + the displayed
  chords, and `POST /api/rescore` re-runs `key.detect_key` + `chords.suggest` for the
  on-demand "Update chords + key" button. Shared helper `_notes_from_json` synthesizes seconds
  from the grid (`start = start_ql*60/bpm`) because `export._build_midi` reads seconds and
  `key.detect_key` weights by `n.duration` seconds; edited/inserted notes only carry grid
  positions. Consequence (intended): an edited score's MIDI is fully grid-quantized.
- **The risky pure logic: [`server/static/manual.js`](server/static/manual.js)** exposes
  `MT.notesToMusicXML(seq, opts) -> {xml, noteheadMap}` (on `window.MT` + node `module.exports`,
  mirroring `realtime.js`'s `window.RT`). It ports the `_SHARP`/`_FLAT` spelling and
  `_parse_key` flats logic (baked from music21: major keys flat only at F; minor at C/D/F/G),
  emits divisions/key/time, rests, measures, **barline tie-splitting**, a clef choice
  (treble/bass by average pitch, matching music21's `bestClef`), and `<harmony>` for chords.
  `noteheadMap` maps each DOM notehead back to its `seq` index (a tied note yields several
  noteheads pointing at one seq event) - the fix for the fact that naive DOM-order counting
  breaks on ties.
- **Tests.** [`tests/gen_manual_golden.py`](tests/gen_manual_golden.py) runs 7 melodies (scale,
  cross-barline tie, dotted values, rest gap, flat key, low/bass-clef range, 3/4) through the
  real server engraver and writes a structural golden; [`tests/manual/builder.test.cjs`](tests/manual/builder.test.cjs)
  (node) asserts the JS builder matches it note-for-note (pitch, quarter-length, tie, measure,
  clef, fifths, time). A pytest drift-guard (`test_manual_golden_in_sync`) keeps the committed
  golden honest; new pytest covers both endpoints (MIDI times == `start_ql*60/bpm`, rescore
  finds C major + tonic chord). Full `pytest -q` green; `node builder.test.cjs` green; every
  builder XML also loads + renders in real verovio with the expected notehead count.
- **Learned:** music21 does **not** pad an incomplete final measure (my builder had to stop
  padding to match); verovio regenerates element ids on MusicXML import, so `xml:id` stamping is
  not a viable selection map - `noteheadMap` is.
- **Next (Phase 2):** vendor verovio-WASM, add the Auto/Manual `.seg` toggle to the result card,
  and render the client MusicXML read-only. No files touched outside `server/app.py`,
  `server/static/manual.js`, and `tests/`; no port-8000 server left running.

## 2026-08-04 - Session 27: Vocal trainer Phase E (session history + progress) - plan complete

Final phase of the vocal-training plan ([`VOCAL TRAINER PLAN.md`](VOCAL%20TRAINER%20PLAN.md));
**Phases A-E are now all shipped.** Still all client-side in `realtime.js` / `index.html` /
`style.css`; no backend changes, no upload, no PII.

- **Session record on Stop.** When a voice take gets a key result (`fetchKey`, which only runs
  with enough frames), `buildSession()` snapshots `{ t, key, camelot, inTunePct, bestSustainS,
  steadinessMedianC, vibrato|null, rangeLo, rangeHi }` from the state that's still intact after
  stop, and `saveSession` appends it. It saves once per take (in `fetchKey`, not `renderKey`,
  so picking a target key afterwards doesn't duplicate it). New per-take inputs: `lastVibrato`
  (last detected wobble) and `steadyReadings` (per-frame steadiness, median at save); both reset
  in `resetVoice()`.
- **Persistence.** `localStorage["humjob.voice.history"]`, a JSON array capped at 50 via
  `loadHistory()` / `saveSession()`; quota errors are swallowed.
- **Progress panel.** A collapsible `<details>` `#rtProgress` (hidden until there's history)
  lists the last 8 sessions newest-first (date, key, in-tune %, sustain, range, vibrato) with a
  tiny in-tune-% **sparkline** (`drawSpark` on `#rtSpark`, 0-100 scale, reusing the graph's
  `cssVar` tokens) and a "Clear history" button. `renderProgress()` runs on load and after every
  save/clear.

Verified: `node --check` clean. On the running app, `median` gives 3 / 2.5 / null; two saved
records round-trip (`loadHistory` returns them, newest row reads "Aug 4 ... A minor - 85% in
tune - 3.4s held - A2-A4 - vib 5.6 Hz"); `#rtProgress` shows after a save and the sparkline is
visible with >=2 points; saving 62 records caps storage at 50 while the list shows 8; the Clear
button empties storage and re-hides the panel. Test data was cleared afterward (`loadHistory`
back to 0); no console errors. Started the dev server transiently on :8000 and **stopped it**
(port free). The preview has no mic, so a real take that actually populates history from singing
still wants a real-mic pass (refresh :8000 -> Realtime -> Voice monitor: sing a take, Stop, open
Progress). The plan is done; any further work is polish.

## 2026-08-03 - Session 26: Vocal trainer Phase D (vocal range finder)

Added Phase D of the vocal-training plan ([`VOCAL TRAINER PLAN.md`](VOCAL%20TRAINER%20PLAN.md))
(A, B, C already shipped). Still all client-side in `realtime.js` / `index.html` / `style.css`;
no backend changes.

- **Passive range tracking (any take).** `updateVoice` now tracks the min/max of *stable*
  voiced pitch: a frame only counts once it has been held within `RANGE_STABLE_CENTS` (40c)
  for `RANGE_STABLE_MIN` (5) consecutive frames at clarity >= `RANGE_MIN_CLARITY` (0.6), which
  rejects transient glitches and octave slips. Silence breaks the stable run. The take range
  (`rangeLoTake`/`rangeHiTake`, midiFloat) resets each take in `resetVoice()`; on Stop it is
  folded into the session best (`rangeLoBest`/`rangeHiBest`, kept across takes). `renderKey()`
  appends "Range this take: E2 to A4" and, when the session best is wider, "Best so far: ...".
- **Guided Range sub-mode.** A fourth chip in `#rtSubmode` ("Range") with a panel
  (`#rtRangePanel`): a live readout ("Lowest E2, highest A4 (29 semitones, 2.4 octaves)") that
  updates whenever the range grows, plus a "Reset range" button (`resetRangeTake`). Passive
  tracking runs in every mode; this one just shows it live and prompts the slide.
- **Pure `rangeFromFrames(frames)`** (exposed on `window.RT`) implements the same stability
  rule for verification and mirrors the live inline tracker.

Verified: `node --check` clean. On the running app, `rangeFromFrames` returns E2-A4 (40-69)
for a clean held slide, rejects a one-frame octave-up glitch inside a held note (stays 48-48),
and returns null for low-clarity, too-brief (4-frame), and empty inputs. The "Range" chip's
real click handler switches mode, shows only `#rtRangePanel`, and hides the other three
panels; the reset button and default prompt are present; no console errors. Started the dev
server transiently on :8000 and **stopped it** (port left free). The preview has no mic, so the
live range fill-in and the on-stop range lines want a real-mic pass (refresh :8000 -> Realtime
-> Voice monitor -> Range: slide low to high, then Stop). Remaining: Phase E (session history +
progress), which can now store in-tune %, vibrato, and range.

## 2026-08-03 - Session 25: Vocal trainer Phase B (vibrato analysis + in-tune % per take)

Filled in Phase B of the vocal-training plan ([`VOCAL TRAINER PLAN.md`](VOCAL%20TRAINER%20PLAN.md))
(A and C already shipped). Still all client-side in `realtime.js` / `index.html` / `style.css`;
no backend changes.

- **Vibrato analysis.** Pure `analyzeVibrato(samples, frameHz) -> {rateHz, depthCents} | null`
  over a rolling ~2s buffer of voiced `midiFloat` (`vibBuf`, capped `VIB_MAX = 72`). It removes
  slow pitch drift with a centred ~0.4s moving average, takes **depth** from the robust p95-p5
  spread (semitone peak-to-peak -> +/- cents), and **rate** from hysteretic zero-crossings
  (2 cent deadband) of the detrended signal. Gated to real vibrato: >= ~0.6s voiced, depth
  >= 15c, rate in 3-9 Hz; otherwise null. A new `#rtVibrato` line shows "Vibrato: 5.6 Hz,
  +/-30c" only while a wobble is detected, cleared on silence. The analysis frame rate is
  measured live (`frameHzEma`, EMA of the inter-frame dt) rather than assumed, so the rate is
  right even though the loop runs at ~30-33 Hz.
- **In-tune % per take.** `voicedFrames` / `inTuneFrames` counters increment in `updateVoice`
  off the same in-band test the sustain timer uses (within `BAND_CENTS` of the target, or of
  the nearest semitone when free). On Stop, `renderKey()` appends "This take: N% in tune".
  Both reset in `resetVoice()` (start of the next take), so choosing a target key after Stop
  still re-renders the same number.

Verified: `node --check` clean. On the running app, `window.RT.analyzeVibrato` on synthetic
arrays reads a clean 5 Hz/+/-30c as 4.76 Hz/30c and still reads ~5 Hz/30c through an added
rising drift; a 6 Hz/+/-20c array reads 5.7 Hz/17c; and steady, too-short (0.4s), sub-15c, and
12 Hz inputs all return null (gates hold). The `#rtVibrato` line exists and is empty at rest,
Phase C is intact (sub-mode switching + `scaleSequence`), and there are no console errors.
Started the dev server transiently on :8000 and **stopped it** (port left free). The preview
pane has no mic, so the live vibrato readout and the on-stop in-tune % want a real-mic pass
(refresh :8000 -> Realtime -> Voice monitor: sustain a wobble, then Stop). Remaining: Phase D
(range finder) and Phase E (session history), which can now consume the in-tune % + vibrato.

## 2026-08-03 - Session 24: Vocal trainer Phase C (guided drills - match game + scale trainer)

Second slice of the vocal-training plan ([`VOCAL TRAINER PLAN.md`](VOCAL%20TRAINER%20PLAN.md)),
skipping ahead to Phase C at the user's request. Still all client-side in `realtime.js` /
`index.html` / `style.css`; no backend changes. A **practice sub-mode selector**
("Free" / "Match game" / "Scale trainer", `#rtSubmode`, reusing the existing `.seg` chip
style) branches the same capture loop; the drills drive `targetMidi`, so the graph lane and
the Phase A sustain metric come along for free.

- **Match game (C1).** Plays a random note in C3-C5 (`nextMatchTarget` sets the target and
  calls `playReference`), then listens; holding within the `BAND_CENTS` band for
  `MATCH_HOLD_MS` (800 ms) locks a match and auto-advances (`matchFrame` / `lockMatch`,
  modelled on the tuner's stable-hold). Readout shows the current note, matches locked, and
  average time-to-lock; a "Skip note" button re-rolls. Silence resets the hold.
- **Scale trainer (C2).** Builds a scale/arpeggio from the circle-of-fifths target key
  (root in the C3-B3 octave, else C major) via a pure `scaleSequence(root, mode, pattern)`
  (`up` / `updown` / `arp`). A Web-Audio-scheduled loop (like `startMetronome`'s lookahead)
  steps the target on each beat, sounding `click()` (reused from `app.js`) plus a short
  scheduled guide tone (`scheduleTone`), and moves the graph lane in sync. Local **Tempo**
  slider (40-160, default 80; independent of the Transcriber slider) and pattern picker.
  Scores in-tune % over the run (`scaleFrame` counts frames inside the band) and prints it
  on completion.
- **Lifecycle:** graph-click / stepper target-setting is gated to Free mode (a drill owns the
  target otherwise); switching sub-mode, pressing Stop, leaving the tab/mode all tear down
  the scheduler interval and any scheduled guide tones (`endMatch` / `endScale` /
  `stopScaleTones`, hooked into `stop()`).

Verified: `node --check` clean. On the running app, `window.RT.scaleSequence` returns the
right notes (C major up = 60,62,64,65,67,69,71,72; up/down symmetric; A-minor natural;
major/minor triad arps), sub-mode switching toggles exactly one panel with the active chip
following, the scale info line tracks pattern/BPM/target-key ("A minor arpeggio at 100 BPM"),
graph clicks are ignored outside Free mode, and the reused `click` / `ensureAudio` /
`midiToFreq` globals resolve with a scheduled-tone smoke test throwing nothing; no console
errors. Started the dev server transiently on :8000 and **stopped it** (port left free). The
preview pane has no mic, so the actual match-lock, scale follow-along, and scoring still want
a real-mic pass in the browser (refresh :8000 -> Realtime -> Voice monitor -> Match/Scale).
Next: Phases B (vibrato + in-tune %), D (range finder), E (session history) remain.

## 2026-08-03 - Session 23: Vocal trainer Phase A (target note, drone, steadiness/sustain)

First phase of the vocal-training plan ([`VOCAL TRAINER PLAN.md`](VOCAL%20TRAINER%20PLAN.md)),
turning the Realtime voice monitor from a readout into a practice tool. All client-side in
`realtime.js` / `index.html` / `style.css`; no backend changes.

- **Target note**, chosen by clicking the pitch graph (a `pointerdown` handler inverts the
  graph's `yOf` to the nearest semitone) or a `[- A3 +]` stepper, with Clear. Held across
  takes. Pure `midiFromGraphY(y, H)` does the mapping.
- **Target lane** on the graph: `drawGraph()` now shades a +/-15 cent in-tune band and draws
  a dashed line plus note label at the target (reuses the existing `yOf` / `--accent`).
- **Reference note**: a "Play reference note" button (`playReference`) sounds a `triangle`
  `OscillatorNode` + `GainNode` (via the shared `ensureAudio()`) at `noteFreq(targetMidi)`
  for ~2s with a fade in/out, then stops itself; disabled until a target is set, re-playable
  on demand. (Shipped first as a sustained drone; the user found the continuous tone annoying,
  so it is now a one-shot ~2s pitch-pipe tone.) Independent of the mic; any in-progress tone
  is stopped on tab-leave and sub-mode switch. Headphones still advised (feedback).
- **Live metrics** (`#rtMetrics`): steadiness as the cents std-dev of the last ~16 voiced
  frames (`stdevCents`, pure), and an in-tune sustain timer (current + best this take)
  measured against the target, or the nearest note when no target is set.

Verified: `node --check` clean; on the running app `window.RT.midiFromGraphY` gives 88/40/64
at top/mid/bottom, `stdevCents` gives 0 / 25 / null, `midiName` gives E2/A3/C4; all new DOM
nodes present; `setTargetMidi` updates the label and redraws with no throw; the drone
on/retune/off/clear cycle runs with no console errors. Started the dev server transiently on
:8000 for the check and **stopped it** (port left free). The preview pane has no mic or real
viewport, so live pitch tracking, the drone tone, and the moving readouts still want a quick
real-browser pass (refresh :8000, Realtime tab, Voice monitor). Next: Phase B (vibrato +
in-tune %).

## 2026-08-03 - Session 22: Quantizer gave identical hums different durations

User hummed the same note at the same length several times and got back notes with
different durations. Traced it to `quantize.py`, not the segmenter: the pitch contour and
onsets were fine. Reproduced it on the `repeated_notes` fixture (five identical C4 quarter
notes) - the old quantizer returned dur_ql `{0.75, 1.0}` where every note should be `1.0`.

Root cause was the old "hold to the next onset" (legato) duration rule plus two of its
side effects:

1. A note's printed length was the spacing to the *next* onset, so it tracked rhythm
   spacing, not how long the note was actually held. Wobble in spacing crossed grid lines
   and changed the duration.
2. The **last note** had no next onset, so it fell back to its bare sounded length. A "da"
   note is only voiced for ~0.87 of a beat (the consonant stop clips the end), so that bare
   length rounds down - the last of five identical quarters came back as a dotted eighth.
3. A gap crossing `rest_threshold_ql` flipped a note between legato and its own length, a
   discontinuous jump.

Rewrote `quantize.py` with one uniform rule: **each note's duration is its own sounded
length plus the typical "da" articulation gap (the median of the short inter-note gaps),
snapped to the grid.** Gaps at/above `rest_threshold_ql` are excluded from that median (so
a real rest doesn't inflate it) and simply surface as space before the next onset, i.e. a
rest. Onset snapping and the grid-phase estimate are unchanged. This makes equal notes
quantize equally regardless of spacing and removes the last-note special case entirely.
`rest_threshold_ql` now means "gap this big is a real rest" (comment updated in config.py).

Verified: `repeated_notes` now returns five `1.0`s; `c_major_scale`, `mixed_rhythm`,
`with_silence`, and `twinkle` fixtures all reproduce their existing expected durations
unchanged; sustained-note segmentation (2-beat halves) still lands at `2.0`. Added
`repeated_notes` to `test_quantize.EXPECTED` to lock the regression. README quantize
descriptions updated. Note for the user: this changes durations for genuinely detached
staccato humming (real gaps now print as rests instead of being absorbed legato), which is
the more faithful reading; steady humming still prints as clean note values.

## 2026-08-03 - Session 21: Renamed the product to HumJob

Rebranded the user-facing name from "MouthTranscriber" to "HumJob" per the user. Branding
only, by explicit choice: the visible name changed everywhere it shows (web header +
`<title>`, README, LICENSE, FastAPI app title, `run.bat`/`run.ps1` banners, `requirements.txt`
header, the package/viz docstrings, and the doc titles in CLAUDE.md / PROJECT PLAN.md / this
diary). The internal Python package is deliberately still named `mouthtranscriber` - the
directory, every `import mouthtranscriber`, the `.claude/launch.json` server name, and path
references in docs/comments are untouched, so no imports break and the running app is
unaffected. Next: if a full package rename is ever wanted, that is a separate, larger change.

## 2026-08-03 - Session 20: No-emoji style rule

Removed the target emoji from the "Target key" button and the sung-key readout line, at
the user's request. New standing rule, now in CLAUDE.md's "Writing style" section next to
the no-dash rule: no emojis except in important titles or important warnings, so keep them
out of buttons, labels, readouts and body text. Text-only change.

## 2026-08-02 - Session 19: Target key becomes a circle of fifths

Swapped the Realtime voice monitor's **"Target key" dropdown for an interactive circle
of fifths** (the user's UI idea). A "Target key: Off" button opens an SVG circle:
outer ring is the 12 major keys, inner ring their relative minors, laid out clockwise by
fifths (C at 12 o'clock, G♭ at 6). Click a wedge to set the target, the centre to clear.
Flats are spelled the way the circle expects (G♭/D♭/A♭/E♭/B♭, E♭m/B♭m), and that pretty
name now also drives the readout, so a target reads "E♭ minor" instead of "D# minor".

Kept the comparison logic untouched: the circle just writes the same `pc:mode` string to
a hidden `#rtTarget`, which `compareToTarget` already reads, so all of Session 18's
cents/semitone, relative-key and mode-mismatch handling carries over unchanged. Split
`fetchKey` into fetch + `renderKey`, so picking a target *after* Stop refreshes the "how
far off" line without re-recording. New `buildCircle`/`selectTarget` build the SVG with
`createElementNS` and per-wedge annular-sector paths; styling in `style.css` (`.cof*`)
uses the existing theme tokens and highlights the selected wedge in the accent colour.

Verified in-browser (no mic): 24 wedges + centre render, C major sits at top-centre and
G♭ at the bottom, no malformed paths; selecting E minor then "singing" F minor still
gives "1 semitone sharp (sing 1 semitone lower)"; C major vs A minor reports the relative
minor match; the centre clears back to "Off"; no console errors. The SVG carries explicit
`width`/`height` for robust intrinsic sizing. The preview pane has no real viewport here,
so a quick visual glance in the user's own browser (refresh :8000) is worth doing. No
server of mine was started; port 8000 stays the user's.

## 2026-08-02 - Session 18: Target-key comparison in the voice monitor

Added a **"Target key" dropdown** to the Realtime voice monitor (24 keys plus an "Off"
default). When a target is set, the on-stop readout says not just which key you sang but
**how far off the target you were**, and picks the right unit:

- Right key, slightly off pitch: reports **cents** (e.g. "12 cents sharp (sing a little
  lower)"), from a running mean of each voiced frame's cents vs equal temperament.
- Wrong key by a semitone or more: reports **semitones** (e.g. target E minor, sang F
  minor gives "1 semitone sharp (sing 1 semitone lower)"). The switch happens at 100
  cents, since a whole semitone no longer reads sensibly as cents. This is the user's
  exact example.
- **Relative major/minor** (E minor vs G major) is recognized as the same seven notes, so
  it says "you sang the relative major, so the notes match" instead of a bogus 3-semitone
  gap.
- **Mode mismatch** on the same tonic (E minor vs E major) is called out too ("right
  tonic (E), but you sang major, not minor").

Offset math: `d = signed tonic distance (target -> sung)`, total cents `= d*100 + meanFine`,
which stitches continuously across the semitone boundary. All frontend, in `realtime.js`
(`compareToTarget` / `fmtOffset` / `isRelative`), reusing the existing `/api/key` string
(no backend change). The comparison line tints green when on target, red when off.

Verified in-browser without a mic: dropdown has 25 options and sits beside the hold
checkbox; `fmtOffset` gives cents below 100 and semitones at/above it; the E-minor-vs
F-minor / G-major / E-major cases all read correctly; no console errors. Port 8000 freed.
Live-mic accuracy of the sung-key detection still wants a real-browser check.

## 2026-08-02 - Session 17: "Hold last pitch" option, no-dash writing rule

Two small follow-ups from the user.

1. **"Hold last pitch value" checkbox (Realtime voice monitor).** New `#rtHold` checkbox
   under the Start button. When ticked, an unvoiced/silent frame no longer blanks the
   readout to "-": the note name, Hz, and cents needle stay frozen at the last voiced
   value. The **graph is intentionally unaffected** (it still pushes a NaN gap), so the
   trace still shows the silence while the numeric readout holds steady. Implemented as a
   guard around the else-branch in `updateVoice()` (`realtime.js`); zero effect when the
   box is unticked (default).
2. **Writing rule in CLAUDE.md.** Added a "Writing style" section: do not use any em or
   en dashes, use hyphens if necessary. Applies to new writing going forward (existing
   files not mass-rewritten).

Verified: `#rtHold` renders inside the voice card with the right id/type/label, no console
errors, realtime.js passes `node --check`. Port 8000 freed after the check. Live-mic hold
behavior still wants a quick check in the user's real browser (preview browser has no mic).

## 2026-08-02 — Session 16: Realtime tab (voice monitor + guitar tuner), stat tooltips

Four follow-ups from the user.

1. **Advanced-stat tooltips (Pitch Finder).** Every labelled stat in the Advanced panel
   now carries a one-line, plain-language explanation shown **only on hover** (a CSS
   bubble via `.has-tip[data-tip]::after`, with the native `title` as a11y/mobile
   fallback). `statRow(k, v, desc)` gained the optional `desc`; a `STAT_DESC` map drives
   it, and the two computed section headers (key candidates, pitch-class) also get tips.
2. **BPM double-time note (light touch).** The user reported a 74 BPM song reading 143
   (classic octave error) and said *not* to stress it. No algorithm change — the main BPM
   tile now shows the half-time alternate as a subline (`143` · "or 72") so the likely
   value is visible. `tile()` gained an optional `sub`.
3. **Realtime tab — live vocal-pitch monitor.** New 4th tab. All **client-side** (a
   server round-trip can't be realtime): `server/static/realtime.js` runs Web Audio
   `AnalyserNode` (fftSize **8192** — needed so autocorrelation resolves an 82 Hz low E to
   ~±3¢) → an autocorrelation pitch detector (`detectPitch`, pure/RMS-gated/parabolic
   interp, throttled to ~33 Hz). Shows **note name + Hz + a ±50¢ needle meter** and a
   **scrolling pitch canvas**. On Stop it accumulates a pitch-class histogram from the
   voiced frames and POSTs it to the new **`POST /api/key`** (reuses `key.score_keys` +
   `analyze.to_camelot`; no audio upload) to display the key of what was sung.
4. **Guitar tuner** — sub-mode of Realtime (segmented toggle). Same detector, cents
   referenced to the selected string. Six chips thickest→thinnest (E2/A2/D3/G3/B3/E4 =
   midi 40/45/50/55/59/64); auto-advances when a string holds within ±5¢ for ~1 s; tap a
   chip to redo. Assumes standard EADGBE.

Reuse: `RAW_MIC`, `ensureAudio()`, `midiToFreq`, the tab switcher, and CSS tokens; the
`/api/key` endpoint is pure wiring over the existing Krumhansl scorer. Mic is released on
Stop and on switching away from the tab (no hot mic).

Verified: per-file tests green (test_server **9** incl. `/api/key` + bad-input reject;
test_analyze 5). Live on :8000 (then freed) via `javascript_tool` (preview browser has no
mic, so the pure detector was driven with synthesized sines): 440→A4 +1¢, all six guitar
strings within ±3¢, silence→null; tab + sub-mode switching isolate views; 6 string chips;
`/api/key` → "C major · 8B" (0.965, beats A minor 0.707); Pitch Finder shows 16 stat
tooltips + BPM subline "or 72"; no console errors. **Live-mic behaviour (tracking +
needle) still needs a quick check in a real browser.**

## 2026-08-02 — Session 15: Pitch Finder tab — audio → Key / BPM / Camelot + stats

New side feature (user request): a second tab where you drop in any audio (mp3/wav/…)
and get **Key, BPM, Camelot** + a comprehensive **Advanced statistics** panel. A third
**Transposer** tab is scaffolded as a disabled "coming soon" placeholder.

Key design call: Pitch Finder does NOT reuse the humming pipeline (that segmenter is
monophonic, wrong for full songs). Instead a **new self-contained chroma-based path**
that works on polyphonic songs and single instruments alike.

- **`mouthtranscriber/analyze.py`** (NEW) `analyze_audio(y, sr)`: key via time-averaged
  `chroma_cqt` correlated against the 24 Krumhansl profiles (reuses a new
  `key.score_keys` helper — refactored out of `detect_key` so notes AND chroma share
  it); BPM via librosa onset-strength tempo + `_fold` (reused from tempo.py) with
  half/double + a beat-regularity confidence; Camelot lookup table + `camelot_neighbors`
  (the mixing set); comprehensive stats — tuning/A4, spectral centroid/rolloff/bandwidth,
  ZCR, RMS/peak/dynamic-range, duration, sample rate, onset density, energy, pitch-class
  distribution.
- **`POST /api/analyze`** (server/app.py): reuses `_to_wav`+`load_audio`, returns the dict.
- **Frontend**: index.html got a `.tabs` nav wrapping the existing view in
  `#view-transcriber`, plus `#view-finder` (dropzone + big Key/BPM/Camelot tiles +
  Advanced `<details>` grid) and a placeholder `#view-transposer`. style.css: tabs,
  views, dropzone, big tiles, camelot chips, stats grid (all reuse existing tokens).
  app.js: tab switcher + `analyzeAudio()`/`renderAnalysis()` (drag-drop + file input).

Verified: tests green per-file (test_analyze 5, test_server 7 incl. new endpoint tests,
key.py refactor safe — chords/quantize pass). Live on :8000: generated a C-major WAV
in-browser through the real `analyzeAudio()` path → "C major · 8B", neighbors 7B/9B/8A,
34 advanced rows across 5 sections; tab switching isolates views; Transcriber intact;
Transposer disabled; no console errors. Port 8000 freed. (NOTE: running the whole heavy
test suite in ONE interpreter SIGABRTs — run per-file, as CLAUDE.md says.)

**Next / open**: Transposer implementation; key detection can confuse relative
major/minor on complex songs (chroma+Krumhansl limitation) — a dedicated model is the
future lever if needed. Duration-snap, time-sig, deploy still open.

---

## 2026-08-02 — Session 14: project docs — CLAUDE.md, rewrote README, this entry

User asked for durable docs so future sessions ramp fast. Wrote three things:
  * **CLAUDE.md** (NEW) — the session-onboarding guide: what the project is + the two
    load-bearing UX constraints; the pure-function pipeline chain (audio → preprocess →
    note production → consolidate → tuning → key → quantize → chords → export) with a
    file map; the 3-backend table and the fact that DEFAULTS differ by entry point
    (`Params()`→pyin, CLI→basic_pitch, web→crepe); the install rules that protect the
    numpy 2.0.2 pin (basic-pitch on ONNX not TF; CREPE on CPU torch + torchcrepe
    --no-deps); how to run app/CLI/tests; and the hard-won gotchas — free :8000 before
    ending a turn (WinError 10013), RAW_MIC (never revert to `{audio:true}`), BPM
    mismatch → tied slivers, "segmentation not model" when notes look wrong, keep DIARY
    newest-on-top.
  * **README.md** — full rewrite. Fixed the stale/contradictory bits (it claimed the
    web app defaulted to basic-pitch; it's CREPE). Added the consolidation stage to the
    pipeline description, all three backend install recipes, the web-app walkthrough,
    testing/eval, and an accurate file-layout tree.
  * **DIARY.md** — this entry.

No code changes this session. Suite state unchanged from S13 (green).

**Next / open**: still want the user's held-note recording to confirm the S13
fragmentation fix on real voice. Duration-snap, time-sig detection, web deploy open.

---

## 2026-08-02 — Session 13: fixed the note-fragmentation (one held note -> many slivers)

User confirmed on a clean recording: one held note comes back as many short notes,
wrong in both duration AND pitch, though the overall pitch trend is right — and it
persists across ALL pitch engines (incl. basic-pitch). Asked again about finetuning
a model. Answer given: finetuning is the wrong tool here — this is a segmentation
bug, not a pitch-model bug (the contour/trend is already right). CREPE finetuning
would polish the part that works; basic-pitch finetuning needs a labeled hum dataset
we don't have + resurrecting the TF training stack we dodged. So: fix the DSP.

Traced it in code. quantize.py is exonerated — it emits exactly one output per input
note, never splits. Fragmentation is in note PRODUCTION, per-backend:
  * pYIN/CREPE -> segment_notes() pitch-step splitter fires on vibrato because
    smooth_frames=5 (~58 ms) is far shorter than a 5.5 Hz vibrato period (~180 ms),
    so the wobble survives smoothing and crosses pitch_split_semitones. Each fragment
    takes the median of a HALF-cycle, so adjacent fragments land on OPPOSITE extremes
    (C4/C#4) — that's the "wrong pitch" too. The old _merge_same_pitch only fused
    EXACTLY-equal semitones, so it never rescued these.
  * basic-pitch -> emits several events per held note on salience dips; nothing
    re-merged them (bypasses segment_notes entirely).

Two-part fix (verified end-to-end, all 3 engines, wide +-0.9-semitone vibrato):
  1. **smooth_frames 5 -> 15** (config): span ~one vibrato period so the splitter
     never sees the wobble. Empirical sweep: >=11 frames collapses the 26-fragment
     shatter to 1 clean note. Still << any hummed note, so real steps survive
     (twinkle fixtures unaffected).
  2. **New backend-agnostic consolidate stage** (mouthtranscriber/consolidate.py,
     called in pipeline for EVERY backend): fuses near-touching fragments within a
     pitch tolerance, duration-weighted-mean pitch. Replaces the old exact-pitch
     _merge_same_pitch (deleted from segment.py). This is what catches basic-pitch's
     same-pitch fragments and any residual.
  Result: held C4 wide-vibrato: pYIN 26->1, CREPE 25->1, basic-pitch 6->1.

New params: consolidate/consolidate_gap_s(0.045)/consolidate_semitones(0.7). New
tests: tests/test_consolidate.py (6 unit tests), test_segment wide-vibrato + old-
smoothing-reproduces-the-split. Full suite green (64 pass, 4 skip across runs).

**Next / open**: user re-records held notes, confirms they stay whole. If a specific
mis-segmentation pattern survives on real voice, THAT (with data) is when a trained
model earns its keep. Duration-snap, time-sig, deploy still open.

---

## 2026-08-02 — Session 12: raw-mic capture — every note "cut to 0" was the browser noise gate

User: recorded hum "feels cut abruptly — like it drops to 0 when the volume is below a
point, every note." Correctly guessed it was mic noise reduction, not our algorithm.
Right call. Root cause: both `getUserMedia({ audio: true })` calls (record path + Find-my-
tempo) inherit Chrome's **default speech-call DSP** — `noiseSuppression` (a gate that
ducks quiet audio to ZERO), `echoCancellation`, `autoGainControl`. Those chop a sustained
hum's soft onset/decay → every note reads as abruptly cut. Our pipeline never gates.

Fix: added a `RAW_MIC` constraints const (`noiseSuppression/echoCancellation/autoGain-
Control: false`) and used it in BOTH capture paths (app.js). Verified served file: RAW_MIC
defined, all three off, 2× getUserMedia(RAW_MIC), 0 leftover `{ audio: true }`, no console
errors. (Also reverted a wrong post-roll "tail" edit I'd made from misreading the question
as recording-window clipping — it wasn't that.)

Caveat told to user: this only controls Chrome's software DSP. If gating persists it's an
OS/driver layer OUTSIDE the browser — Windows "Audio enhancements", Realtek Audio Console,
NVIDIA Broadcast / Krisp, or headset firmware — which the user must disable in Windows sound
settings / vendor app. Diagnosis path: download the hum (⬇ button) and listen — still
gated ⇒ OS/driver; clean ⇒ browser (fixed). With echoCancellation off, click bleed is
worse without headphones (headphones already recommended; mute-click option still there).

**Next / open**: user relaunches run.bat (server was stopped) + hard-refresh, re-records,
confirms notes sustain. Still want the actual recording. Duration-snap, time-sig, deploy open.

---

## 2026-08-02 — Session 11: CREPE enabled — the voice/humming-specialized neural pitch model

User asked *"don't we have any AI models for humming instead of instruments?"* — correct
instinct, and the direct fix for the octave-jumping. basic-pitch is **instrument-trained**,
so a bare "da-da-da" is out-of-distribution → mispitch. The voice-specialized answer is
**CREPE** (CNN over the raw waveform, monophonic, built for the singing voice). It was
already ~90% wired in the repo (`CrepeTracker` in `pitch.py`, `--backend crepe`, config +
pipeline + server validator all accept it) — just never installed or exposed in the UI.

Installed it (CPU-only, numpy 2.0.2 untouched — `--no-deps` on torchcrepe):
- `pip install torch torchaudio --index-url .../whl/cpu` → torch 2.13.0+cpu, torchaudio 2.11.0+cpu
- `pip install torchcrepe --no-deps` → 0.0.24; plus `tqdm` (torchcrepe imports it at load).
- torchcrepe also imports `torchaudio` at package load, so that wheel is required even
  though `predict()` doesn't use it. Recipe documented in requirements.txt.

Verified end-to-end: CREPE on the worst-case sustained+vibrato+tremolo scale fixture →
all 8 notes correct `[60,62,64,65,67,69,71,72]`, key **C major**, every note a clean
~1.58 s half note (no splitting). Exposed in the web `#engine` dropdown as the **default**
option ("CREPE (neural, voice/humming) ★"), above basic-pitch and pYIN. app.js already
mapped the `crepe` backend label (refined to "CREPE (voice/humming)"). Verified live on a
throwaway :8021 preview (user's :8000 untouched): dropdown renders with CREPE selected,
no console errors. Added a `mouthtranscriber-verify` (:8021) config to launch.json so
future UI checks never collide with the user's run.bat on :8000.

**Next / open**: user should restart run.bat once (loads CREPE-enabled server) + hard-
refresh, then hum with CREPE selected. STILL want the actual recording to confirm the
octave errors are gone. Duration-snap, time-sig detection, web deploy still open.

---

## 2026-08-01 — Session 10: pitch-engine A/B toggle + download recording + auto-reload

User: real takes are "off by a large margin, high variance", pitch "quite variable" —
and asked *what technique we use for pitch detection*. Answer: the web app runs
**basic-pitch** (neural CNN over a harmonic CQT → note events), NOT a classic tracker.
The classic alternative in the repo is **pYIN** (autocorrelation f0). Real trade-off:
basic-pitch = robust segmentation but instrument-trained, so it can octave-jump / mis-
pitch on a bare hum; pYIN = precise voice f0 but brittle segmentation. Can't diagnose
further without the user's actual audio (still not received).

Built two things to move forward:
1. **Pitch-engine toggle** so the user can A/B on their own voice. `#engine` select
   (basic_pitch / pyin) → `backend` form field → `transcribe(..., backend=Form(...))`
   validates + passes to `Params(backend=...)`. Response now carries `"backend"`, shown
   in the summary as "Engine: …". Verified live on :8010: both backends transcribe the
   scale fixture correctly and the response labels the engine used.
2. **⬇ Download my last recording** — `offerDownload()` stashes the recorded webm blob
   behind an object-URL link so the user can save and send me the exact audio (server
   decodes webm via ffmpeg already). Verified the control renders, no console errors.

Also: **auto-reload** to end the restart-every-change friction. Static files (HTML/JS/
CSS) already served fresh from disk (+ no-cache header from S9), so only Python edits
needed a restart. Installed `watchfiles==1.2.0` (numpy stays 2.0.2) and added `--reload`
to run.bat / run.ps1. So the user restarts run.bat ONE more time (to load the new
server code + reloader), and after that code edits apply live — just refresh the browser.

**Next / open**: GET THE RECORDING (download button now exists). Likely lever if pitch
is the issue: pYIN may beat basic-pitch on voice — the toggle will tell. Duration-snap
to musical values, time-sig detection, web deploy still open.

---

## 2026-08-01 — Session 9: diagnosed note-splitting = BPM mismatch; "find my tempo"

User: "why is it splitting one note into many shorter notes? can we let the user hum
first to detect BPM, show it, and require them to hum at that BPM?"

**Diagnosis (grounded, not guessed).** With basic-pitch now the backend, a single
4-beat held note *with strong vibrato* transcribes as **1 note, dur 4.0** — the
detector does NOT split anymore. The splitting is downstream, at **quantization**:
feed the same hum but tell the app the WRONG bpm and the durations come out
non-integer — told 120 when hummed at 90 gives 1.5 / 1.25 / 2.75 / 5.25 quarters.
Notation renders those as strings of tied slivers, and 5.25 spills across a barline
into yet more ties = "one note split into many shorter notes." So the user's own
hypothesis was right: **wrong tempo is the cause**, and matching it is the fix.

**Feature: "Find my tempo."** New `mouthtranscriber/tempo.py::detect_bpm` — librosa
onset-strength autocorrelation (`feature.rhythm.tempo`, start_bpm=100 prior) + an
octave `_fold` that only shifts *out-of-range* estimates (so an in-range 144 stays
144 instead of being halved toward the prior). Verified it recovers 76/90/100/120/144
and mixed rhythms to within ~1–3 bpm (early "8% low" was a test-synth artifact: a
0.05 s gap *after* each note lengthened the true onset-to-onset period). New endpoint
`POST /api/detect-tempo` (decode → detect_bpm → {"bpm"}). UI: a **🎙 Find my tempo**
button in the tempo row records a free hum, posts it, sets the BPM slider, and prompts
"now Record and hum to the click." The flow is self-correcting: whatever we detect
becomes the click the user then records against, so durations land on whole beats.

**Verified**: `tests/test_tempo.py` (7) + 2 new `test_server` endpoint tests pass
(server suite 5 passed). Live: `POST /api/detect-tempo` with the scale fixture →
`{"bpm":99}`; in-browser the button renders, `detectTempo()` round-trips and moves the
slider 100→115, no console errors. (Note: the user's own `run.bat` server on :8000 was
running pre-change code — 405 on the new route — so **they need to restart it** to pick
this up; verified on a throwaway :8010 instance instead.)

**Follow-up: browser-cache gotcha.** User restarted but still saw no button. Root cause:
`StaticFiles` sent no cache headers, so the browser rendered a **cached old index.html**
even though the server served the new one (proved it: `fetch('/',{cache:'no-store'})`
contained `detectBtn` but the DOM did not; a cache-busted URL rendered the button).
Fix: added a tiny `@app.middleware("http")` that stamps `Cache-Control: no-cache` on
every response, so a restart/UI change is never hidden behind stale cache again. Verified
the header is present on `/` and `/app.js`; server tests still 5 passed. User still needs
**one** hard refresh (Ctrl+Shift+R) to drop the already-cached page.

**Next / open**: still want the user's real recording. Possible follow-up: snap note
durations to common musical values so a *slightly* off tempo still notates cleanly
(complements, doesn't replace, correct-BPM). Time-signature detection + web deploy remain.

---

## 2026-08-01 — Session 8: neural backend (basic-pitch) + run files

User: "switch to basic-pitch, and MAKE A RUN FILE (I can't start the project without
asking you to run it first)." Also asked five questions — key takeaways: the DSP path
is voice-only/monophonic and brittle by style (staccato "da-da-da" is the sweet spot;
sustained/legato and repeated same-pitch notes fail), pitch is the strong part,
rhythm/segmentation the weak part, time-signature is *not* detected (a parameter),
noise handling is deliberately light. The fix for all of it is a learned model.

**Run files.** Added `run.bat` (double-click or terminal) and `run.ps1`. Both prefer
`.venv\Scripts\python.exe`, fall back to system `python`, start uvicorn on :8000, and
open the browser. `.claude/launch.json` already existed (that's the *preview* tool's
launcher, not something the user runs).

**basic-pitch backend (the real work).** Spotify's ICASSP-2022 CNN, audio → note
events directly. Instrument-agnostic + polyphonic, so it fixes sustained/legato
singing AND piano (Q1) in one move.
- **Install gotcha**: `basic-pitch` 0.4.0 hard-pins `tensorflow<2.15.1`, which has NO
  py3.12 wheel → a plain `pip install` backtracks into building an ancient numpy from
  source and dies (`pkgutil.ImpImporter` gone in 3.12). It also ships an ONNX model
  and its inference auto-selects ONNX when TF is absent. So: `pip install basic-pitch
  --no-deps`, `pip install "resampy<0.4.3" --no-deps` (hard top-level import in
  `note_creation.py`), `pip install onnxruntime`. **numpy 2.0.2 / librosa 0.10.2 stay
  untouched** (verified). No TensorFlow at all.
- **New module** `mouthtranscriber/basicpitch.py`: writes the conditioned signal to a
  temp WAV (predict() wants a path; 22050 == the model's own rate), calls
  `predict(..., minimum_frequency=fmin, maximum_frequency=fmax)` to clamp octaves,
  maps each `(start,end,midi,amp,bends)` tuple → `NoteEvent` (amp→velocity;
  raw_midi=midi so the global tuning stage is a near no-op), then `_monophonic()`
  collapses overlaps (louder note wins the contested span — cleans up octave doubles).
- **Wiring**: `Params.backend` gains `"basic_pitch"` + `bp_onset_threshold/
  bp_frame_threshold/bp_min_note_ms` knobs. `pipeline.transcribe_array` branches: for
  basic_pitch it skips tracker/voicing/segment (frames=[], voiced empty) and calls the
  new module; tuning/key/quantize/chords are unchanged downstream. **Server defaults to
  basic_pitch** (`Params(backend="basic_pitch", ...)`) — it's the product surface the
  user judged unusable. **CLI default → basic_pitch** too (`--backend pyin` for DSP;
  `--plot` guarded since neural path has no per-frame data). Library `Params.backend`
  default stays `"pyin"` so the 30+ tuned DSP tests are untouched.
- **Verified**: smoke test — synthetic vibrato C-scale → exact 8 notes, no
  fragmentation (the DSP path shattered this). `test_server` (now through basic-pitch)
  3 passed: fixture → C major, 8 notes C4–C5, chords. New `tests/test_basicpitch.py`
  (2) passed. Live end-to-end `curl` to the running uvicorn (real ffmpeg + ONNX) →
  C major / C4–C5 / C(I)|G(V) / valid SVG+MIDI. README + requirements.txt document
  the ONNX install; DSP backend still fully available.

**Real grand piano for playback.** `server/static/piano/` already held 21 Salamander
Grand Piano samples (CC-BY; every minor third C2–C7, ~3 s each, valid 44.1 kHz MP3) from
a prior session, but `app.js` still played the *synth* `pianoVoice`. Wired the samples
in: `loadPiano()` lazily fetches + `decodeAudioData`s all 21 on first Play (cached);
`sampleVoice()` picks the nearest sample and pitch-shifts via `playbackRate`, rings
through the note duration then a 0.3 s release; melody + chords now use it. Kept the
synth as an automatic fallback if samples don't load. Added `piano/ATTRIBUTION.txt`
(CC-BY). Verified in-browser: 21/21 MP3s GET 200, decode OK, playback engages with **no
console errors** (Play↔Stop toggles). `togglePlayback` is now async (awaits the load).

**Next / open**: still need the user's *real* recording to tune thresholds
(bp_onset/frame). Time-signature detection and web deployment remain. pitch-bend →
micro-tuning (cents) is a possible refinement (currently raw_midi = integer pitch).

---

## 2026-07-31 — Session 7: fix sustained-note fragmentation + piano playback

User feedback after M6: "playback buzzes / where's the piano", "set 76 BPM but got a
141 BPM MIDI", "one quarter/half note split into many 1/16 or 1/32 notes (pitch is
right though)". Three issues, two root causes.

**Root cause — over-segmentation on SUSTAINED humming.** `segment.py` splits on
energy valleys (5 dB dips) and pitch steps (0.6 st). That's right for staccato
"da-da-da" but on a held tone, vibrato + amplitude tremolo trip both splitters and
shatter one note into a run of 16th/32nd fragments. Reproduced: 4 sustained
half-notes (with tremolo/vibrato) → **27 notes**.
- The **"141 BPM"** is a *symptom of this*, not a separate bug: the MIDI file
  correctly embeds 76 (`get_tempo_changes()` → 76.0), but a viewer that
  re-estimates tempo from the shredded onset grid guesses fast (`pretty_midi`'s
  `estimate_tempo()` returned **243** on the fragmented output). Clean notes → sane
  tempo inference.
- **Fix**: `_merge_same_pitch` in `segment.py` (+ `Params.merge_same_pitch`,
  `same_pitch_gap_s=0.04`). Fuses consecutive same-pitch notes that are essentially
  touching (gap < 40 ms). Safe because every fixture note is separated by an 80 ms
  "da" silence gap (→ separate voiced runs → never merged), so a real
  re-articulation survives; only within-a-held-note fragments (gap ~0) merge.
  Result: the 27-note repro → **4 clean half-notes**. All fixtures unchanged
  (`test_pipeline` 30, `test_quantize` 7). New `tests/test_segment.py` (2) locks it,
  incl. a `merge_same_pitch=False` test that documents the over-split.

**Playback buzz.** Two causes: (a) the note-storm above (playing 27 machine-gun
16ths *is* a buzz), now fixed; (b) raw sine/triangle oscillators. Rewrote the Web
Audio voice in `app.js` as a **piano-ish** tone: a `PeriodicWave` with grand-piano
harmonic amplitudes, two slightly-detuned partials, a percussive strike →
exponential ring-down envelope, and a lowpass whose cutoff falls so the tone darkens
as it decays. Chords use the same voice, quieter, decaying (no drone). Verified
in-browser: plays, `_pianoWave` built, no console errors.
- NOTE: this is a *synthesized* piano, not a sampled Steinway. A true sampled grand
  would mean bundling a sample pack (a real asset/download decision) — offered to
  the user as a follow-up if they want it.

**Next**: get the user's actual recording to check for residual issues (wide vibrato
crossing a semitone → C–C#–C warble won't merge; count-in bleed if no headphones).
Then: Web milestone; pYIN speed.

---

## 2026-07-31 — Session 6: M6 done — chord suggestions

**What we did** — added per-measure diatonic chord suggestion end-to-end. Milestone
**M6 complete**. Every stage now runs pitch → … → quantize → **chords** → export.

**Built**
- `chords.py` (PLAN §5.9). `suggest(notes, key, time_sig, params)`:
  - **Diatonic templates** from the key (`music21.key.Key`, so roots are correctly
    spelled — flats in flat keys; the raised **leading-tone** vii° is C♯, not D♭).
    Minor keys also get the harmonic-minor **V** (major) and **vii°** so real
    authentic cadences are available.
  - **Coverage emission**: per measure, score each triad by the weight of melody
    notes it covers. Weight = beat-strength (downbeat 2.0, mid-bar 1.5, on-beat 1.0,
    off-beat 0.4) × duration → short off-beat passing tones can't force a chord.
  - **Progression prior**: Viterbi over a **root-motion** table (down-a-5th like
    V→I = 1.0, up-a-2nd/down-a-3rd strong, retrogressions cheap), + gentle tonic
    bias at the first measure and cadential tonic bias at the last. Emission weight
    3× so the melody leads and the prior only smooths/breaks ties.
- `model.py`: `Chord` dataclass (measure, start_ql, root_pc, root_name, quality,
  symbol, roman) + `Score.chords`. `pipeline.py` calls `chords.suggest` after
  quantize (needs `start_ql`).
- `export.py`: `_add_chord_symbols` inserts `music21.harmony.ChordSymbol` at each
  measure downbeat → engraves **above the staff** in MusicXML/SVG. `chord_summary()`
  one-liner. CLI prints `chords: | C (I) | F (IV) | Dm (ii) | G (V) |`. Server JSON
  gains `chords[]`; UI renders a **chord strip** (symbol + roman) above the sheet.

**Verified** (per-file; see gate note below)
- `test_chords.py` **9 passed**: diatonic-only, C-triad bar → I, dominant bar → V,
  V→I cadence resolves (root motion +5), F-minor uses harmonic **C (V)**, D-minor
  vii° root spelled **C♯**, measures span the tune, twinkle e2e → 4 diatonic chords
  starting on I with 4 `<harmony>` in the XML.
- CLI on twinkle (110 bpm): `| C (I) | F (IV) | Dm (ii) | G (V) |` (I–IV–ii–V).
- Web UI: synthesized C-scale → real `upload()` → chord strip **C·F·C** rendered,
  key C major, sheet SVG present. Engraved SVG contains the chord texts C/F/Dm/G.
- `test_quantize.py` **7 passed** after fixing two tests: adding `<harmony>` made
  `stream.notes` include ChordSymbols (they subclass `chord.Chord`), so the roundtrip
  tests now select `getElementsByClass(note.Note)`. `test_server.py` **3 passed**
  incl. a new chords-in-JSON assertion. `test_pipeline.py` **30 passed, 2 skipped**.

**Known issue — full-suite `pytest tests/` SIGABRTs** (all 38 tests still PASS first).
The abort fires at `test_transcribe_endpoint`, i.e. when the numba/librosa pipeline
runs *again inside the endpoint* after `test_pipeline` already JIT-ran pYIN 30× in
the same interpreter. No `OMP:` line → not an OpenMP double-load; it's accumulated
native DSP state. **Not an M6 regression** — at M5 `test_server` was only ever run
alone, so `pytest tests/` was never actually exercised in one process. Fix: the
documented gate now runs **each test file in its own process** (reliable, all green).
Later: process-isolate the gate properly (pytest-xdist `-n`, since `--forked` needs
`os.fork` and won't work on Windows).

**UI follow-ups (same session)** — two things the user asked for:
- **Preview click** button (step 1): toggles a looping metronome at the current
  tempo so you can hear/check it *before* recording (reads the BPM slider live).
  Previously the only metronome control was "mute click while recording" — there was
  no way to hear the click without committing to a take.
- **▶ Play** button (result): sonifies the transcription via Web Audio — melody
  (triangle) with the suggested chords underneath (soft sines, C3–B3 register),
  "with chords" toggle. Needed chord pitch data, so `/api/transcribe` chords now
  also carry `root_pc` + `quality` (locked by a `test_server` assertion).
  Verified in-browser: both toggles flip cleanly, no console errors, chords voiced.

**Next**: Web milestone (deploy the FastAPI app); pYIN speed (~1.6× real-time);
real-mic smoke test still needs a human to hum.

---

## 2026-07-31 — Session 5: M5 done — local web app (record → sheet)

**What we did** — wrapped the pipeline in a FastAPI backend + browser UI. Milestone
**M5 complete**. This backend IS the future web-app backend (server-side-Python
decision paying off — zero DSP ported to JS).

**Built**
- `server/app.py`: FastAPI. `POST /api/transcribe` (multipart audio + bpm/beats/
  beat_unit/subdiv) → ffmpeg-decodes the upload to mono WAV → runs `transcribe_array`
  → returns JSON {key, key_candidates, tuning, notes, **svg** (engraved sheet),
  **musicxml**, **midi_b64**}. Serves the static UI. `/health`.
- `server/static/{index.html,style.css,app.js}`: 3-step UI (set tempo → record →
  result). Web Audio **metronome with count-in + visual beat dots**, tap-tempo,
  time-sig/grid selectors, "mute click while recording" option, headphones hint.
  `MediaRecorder` capture → upload → renders sheet + key + MIDI/MusicXML download
  links + note list. **File-upload fallback** for mic-less use/testing.
- `export.py`: added `sheet_svg_string()`, `to_musicxml_string()`, `midi_bytes()`
  so the server gets strings/bytes without temp files (midi_bytes uses a temp file
  since pretty_midi.write needs a path).
- `.claude/launch.json` (uvicorn on :8000). `tests/test_server.py` (TestClient).

**Verified**
- Backend: POST twinkle.wav → 200, C major, 14 correct notes, SVG+MusicXML+MIDI.
- Frontend: synthesized a WAV in-page and drove the real `upload()`→`render()`
  path → C-D-E-F came back as C4 D4 E4 F4, sheet SVG injected (945px), download
  links present, summary populated. Server logs clean (200s).
- Tests: `test_server.py` **3 passed** (transcribe endpoint, health, empty-upload
  400). New dep: `httpx` (TestClient), added with pytest to requirements dev section.

**NOT yet verified — the real mic path.** `MediaRecorder`/`getUserMedia` needs a
real microphone + user gesture; can't be automated in this env. The upload→pipeline
→render path is fully proven; the browser *capture* step needs a human to click
Record and hum. **This + pYIN speed (~1.6× real-time) are the two things to check
before calling the web app truly done.**

**Run**: `python -m uvicorn server.app:app --port 8000` → http://localhost:8000

**Next**: real-mic smoke test (needs the user); M6 chords; or pYIN speed work.

---

## 2026-07-31 — Session 4: M4 done — quantization + MusicXML + engraved sheet music

**What we did** — built the whole rhythm→notation→sheet chain. Milestone **M4 complete**.

**Built**
- `quantize.py` (PLAN §5.7): snaps onsets/durations to the known-BPM grid (16th
  default). Estimates a global grid **phase** via circular mean (same trick as the
  tuning offset, but for time) so a lead-in/latency doesn't misalign everything;
  anchors first note to beat 0; **legato-fills** to the next onset unless the real
  gap ≥ `rest_threshold_ql` (0.5 QL) → then a rest. Sets `start_ql`/`dur_ql` on notes.
- `export.py` rewritten: proper MusicXML (time sig, key sig, rests, **key-aware
  enharmonic spelling** — flats in flat keys), `music21` `makeNotation`. New
  `render_sheet_svg()` engraves to SVG via **verovio** (pip wheel, NO MuseScore/
  LilyPond needed) with a white-paper background so it's readable in any theme.
- Wired quantize into `pipeline.py`; added `start_ql/dur_ql` to NoteEvent and
  `timing_offset_s` to Score. CLI gained `--sheet` and `--musicxml`.
- `tests/test_quantize.py`: exact duration checks + MusicXML roundtrip + flat-key
  spelling + SVG generation. **7 passed.**

**Verified**
- Quantizer recovers exact note values: scale = 8 quarters; mixed_rhythm =
  [2,1,0.5,0.5,2]; twinkle has half notes at phrase ends; with_silence has rests.
- Rendered `debug/sheets/twinkle.svg` — correct treble clef, 4/4, ♩=110, quarter +
  half notes, barlines. Viewed in browser: clean, readable. M4 verification met.

**New dependency**: `verovio==4.3.1` (added to requirements.txt).

**Notes / choices**
- MIDI export stays raw-performance timing; only MusicXML/sheet use the quantized
  grid. Clean separation.
- Triplets: v1 uses a straight 16th grid only. Per-beat duple/triplet detection is
  deferred (PLAN §5.7 mentions it) — revisit if a fixture needs it.
- Sheet is SVG (verovio). Later the web UI can use OSMD or verovio-js; the SVG path
  works headless now for the local version.

**Next**: M5 (local FastAPI web UI: record in browser → sheet) or M6 (chords). Also
still open from before: real-mic humming test, and pYIN speed (~1.6× real-time).

---

## 2026-07-31 — Session 3: Validated on a REAL melody (user's MusicXML + MP3)

**What we did**
- User dropped `testMaterials/DUA EM VAO HA.{musicxml,mp3}` — a real song, and (nice
  coincidence) it's in **F minor**, the exact "Fm" example from the original ask.
- Built `tests/eval_musicxml.py`: parse MusicXML → exact ground truth (music21,
  `stripTies`, tempo→seconds), then evaluate two ways.

**Ground truth**: 1 part, **150 notes** (158 pre-stripTies), monophonic, bpm 72,
key F minor. ~82 s.

**Results**
- **RENDERED HUM** (synthesize humming from the score, controlled test):
  **note-F1 = 0.987**, pitch-class overlap **1.000**, key **F minor** ✓, tuning +1c.
  → The pipeline nails a real 150-note melody. This is the strong validation.
- **PROVIDED MP3** (real instrument rendering, clean monophonic — see plot):
  note-F1 = 0.603 BUT pitch-class overlap **1.000**, key **F minor** ✓, 152 vs 150 notes.
  - Diagnosis (see `scratchpad/align_mp3.py`): **145/150 notes recovered at correct
    pitch (97%)**. Onset error median **+7 ms** (excellent) but positive-skewed tail
    (mean +36 ms, p90 103 ms); only 63% within the strict 50 ms window → hence F1 0.60.
  - Global offset/tempo-scale search barely helped (0.60→0.75 at offset≈0), so it is
    NOT a constant shift — it's **local expressive timing** that doesn't sit on the
    72-bpm grid.

**Takeaway (important, shapes the roadmap)**
- Pitch / segmentation / key extraction — the hard part — is **working well on real
  audio**. The MP3's low note-F1 is a *timing-strictness* artifact, not a pitch error.
- This **empirically validates the plan's core UX decision**: rhythm needs the
  **metronome + fixed BPM** lock (PLAN §5.7). A free performance won't align to a
  grid. Do NOT chase note-F1 on free recordings; evaluate rhythm only on
  metronome-locked takes.
- Onset tail (~soft attacks landing late) is the main thing to improve for timing:
  refine onset placement (attack-based backtrack) when we build M4 quantization.

**Artifacts**: `debug/musicxml_rendered.png`, `debug/musicxml_provided.png` (both
show clean tracking). `tests/eval_musicxml.py` reusable for future score-based eval.

---

## 2026-07-31 — Session 2: M0–M1 built, M2 gate passing (synthetic)

**What we did**
- Scaffolded the full package and got the core pipeline working end-to-end on
  synthetic audio. Milestones **M0 and M1 done**; **M2** eval gate passing on
  synthetic clips (real recordings still needed to truly clear the gate).

**Built**
- Env: `.venv` with pinned [`requirements.txt`](requirements.txt) (numpy, scipy,
  librosa, soundfile, pretty_midi, music21, mir_eval, matplotlib, fastapi, uvicorn,
  sounddevice, setuptools<81, pytest). Note: `pretty_midi` needs `pkg_resources`,
  so `setuptools<81` is required — deprecation warning is harmless.
- `mouthtranscriber/`: `config.py` (all tunables as `Params`), `model.py`
  (Frame/NoteEvent/Score + hz/midi helpers), `audio_io`, `preprocess`, `pitch`
  (pYIN default, torchcrepe optional behind a `PitchTracker` interface), `voicing`,
  `segment`, `tuning`, `key`, `export` (MIDI + rough MusicXML), `viz`, `pipeline`.
- `cli.py` (`hum2midi`), `tests/make_synthetic.py` (10 ground-truth fixtures),
  `tests/eval_report.py` (P/R/F1 table), `tests/test_pipeline.py` (pytest gate).
- `conftest.py` at root so `import mouthtranscriber` resolves under pytest.

**Result**
- **All 10 synthetic fixtures transcribe to the exact right notes.** pytest:
  **30 passed, 2 skipped** (the 2 skips are intentionally key-ambiguous clips).
  Gate asserts note-F1 ≥ 0.95, exact note sequence, correct key, tuning rescue,
  and zero phantom notes in silence — all green.
- `eval_report.py`: **mean note-F1 = 1.000**, P=R=1.00 on every fixture, all keys
  correct, flat take detected at −41c (others −1c).
- Hard cases proven: 5 repeated same-pitch notes separated; octave leaps with no
  octave errors; a −40-cent flat take corrected to the right pitches; a
  vibrato+scoop expressive Twinkle.

**PERFORMANCE FLAG — pYIN is slow (misses the latency target)**
- Per-clip processing: 5 s clip → **8.3 s**, ~9 s Twinkle → **10.9 s**. That is
  slower than real time and far off the plan's "<5 s for a 30 s take" (PLAN §1.5).
  pYIN dominates the time. Options to try later: reduce `n_thresholds`, downsample
  further, chunk, or switch to the torchcrepe `tiny` backend (already wired behind
  the `PitchTracker` interface — just `Params(backend="crepe")` once installed).
  Not urgent for correctness, but must be addressed before the web app (M5).

**Key algorithmic decision (deviates from PLAN §5.5 as written)**
- The plan proposed librosa spectral-flux **onset detection** for segmentation.
  In practice it **double-fired** on every hummed note (it's tuned for percussive
  music) → ~2x too many notes. **Replaced it with energy-valley detection**
  (`scipy.signal.find_peaks` on −RMS with a dB prominence): the amplitude dip at
  each "d" closure. Far more reliable for voiced humming, and it's what separates
  two repeats of the same pitch. Also lowered `max_gap_merge_s` 0.06→0.035 so real
  ~50 ms "da" gaps split notes instead of being bridged. See `segment.py` header.

**Gotchas for next session**
- pYIN is **slow**: the full pytest pass took ~3 min (30 pipeline runs). Added an
  analysis cache in `test_pipeline.py` so re-runs reuse each fixture's analysis.
- Everything so far is validated on **synthetic** audio only. The synthetic "da"
  gaps fully devoice, so voicing alone splits most fixtures; the energy-valley
  path is exercised but real humming will stress it harder. **M2 is not truly
  cleared until real recordings pass** (PLAN §6.1) — this is the next priority.
- Run commands with `PYTHONPATH=.` (or from repo root; conftest handles pytest).

**Next step**
- Record real "da-da-da" ground-truth clips (scales, repeats, silences, flat,
  vibrato) into `tests/data/recorded/` + reference MIDI, and re-run the gate.
  Then M3 is already partly done (tuning + key work); M4 = BPM quantization +
  MusicXML sheet rendering is the next real feature.

---

## 2026-07-31 — Session 1: Project plan authored

**What we did**
- Turned the user's idea (hum a melody → get MIDI/MusicXML + key + chords) into a full,
  detailed [`PROJECT PLAN.md`](PROJECT%20PLAN.md).
- The plan is **local-first**: build an excellent Python pipeline, *then* wrap the exact same
  code in a FastAPI web backend (the local UI server literally becomes the web app).

**Key decisions locked in**
- **Web architecture = server-side Python** (user chose this). So the DSP is written once, in
  Python, and reused verbatim on the web. No JS/WASM port of the algorithm.
- **Two UX tricks are load-bearing** and the user agreed to both:
  - Hum **"da-da-da"** (crisp consonant onsets → reliable note segmentation, separates
    repeated same-pitch notes).
  - **Metronome + user-set BPM before humming** (known tempo grid → tractable rhythm quantization).
- **Pitch backend behind an interface**: default **pYIN** (`librosa.pyin`), optional
  **torchcrepe** (PyTorch CREPE — deliberately avoiding TensorFlow-on-Windows). Benchmark both.
- **"Works excellently" gate** = note-level **F1 ≥ 0.95** on the ground-truth set (mir_eval,
  50 ms onset tolerance), 0 phantom notes in silence, key correct on unambiguous melodies,
  < 5 s for a 30 s take. **Do not proceed past milestone M2 until this is met.**

**The 3 original complaints about other apps → where they're solved in the plan**
- "Fails to identify most notes" → real pitch tracker (pYIN/CREPE) not FFT peak-picking (§5.3);
  segmentation via onsets+pitch-jumps (§5.5).
- "Can't recognize silence" → dual confidence+RMS voicing gate with hysteresis (§5.4).
- "Off-key humming" → global tuning-offset estimation before snapping (§5.6).

**Environment verified on this machine**
- Python 3.12.7 (`py -V:3.12`), Node v24.15.0, ffmpeg 8.1.1. Windows 10. Shell: PowerShell.
- Repo was empty except README (now has PROJECT PLAN.md + this diary). Git branch `main`,
  one commit. Nothing committed this session (user hasn't asked to commit).

**Milestone roadmap (from the plan)**
M0 scaffold+deps → M1 CLI wav→midi → **M2 eval harness, hit F1 bar (GATE)** →
M3 tuning+key → M4 quantize+MusicXML+sheet → M5 local web UI → M6 chords → M7 polish
(overlay playback) → Web deploy.

**Next step**
- Awaiting go-ahead to start **M0**: create the `mouthtranscriber/` package skeleton,
  set up a venv, pin `requirements.txt`, and build the `viz.py` pitch-curve plotter.
- Open decisions still to settle with data (see PROJECT PLAN §11): canonical sample rate
  (leaning 22.05 kHz), upload-first vs live-recording for the first UI, default backend.

**Notes for next session**
- No code exists yet — plan only. Start from M0.
- The proposed repo layout is in PROJECT PLAN §10; follow it unless we decide otherwise.
- Need the user to record the ground-truth clips (§6.1) before M2 can be meaningfully evaluated.
