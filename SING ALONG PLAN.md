# Sing-Along tab: reviewed and corrected plan

## Context

The user wants a new feature: upload a MIDI, the system plays it as a karaoke-style
reference while tracking the user's voice live, and scores how well the singing matches
the notes. Two decisions are already made by the user: it is a new top-level tab, and
the MIDI drives the clock (no free timing, no DTW). This file is the review of the
prior conversational plan, corrected against the actual code, plus the final
implementation plan. Incidental bonus: this is also the "sing against a known score"
capture flow that DIARY Session 39's open direction (B) wants for real ground truth.

## Corrections to the prior plan (each verified against code)

1. **No refactor of realtime.js is needed.** The prior plan proposed factoring the
   pitch detector out into a shared helper. Unnecessary: `window.RT` already exports
   the pure, DOM-free functions `detectPitch(buf, sr)` (realtime.js:45-94),
   `hzToNote(hz)` (:97-109), and `centsBetween` (:112, exported at :118, :1158).
   A new module calls them directly and owns its own mic/analyser/loop.
2. **The drill engine cannot be called, only copied.** `BAND_CENTS`, `startScale`,
   `scheduleTone`, `scaleFrame` are private in RT's closure. The exported
   `setTargetMidi` is DOM-coupled and integer-only; do not use it.
3. **Server melody reduction is mandatory, not optional.** `stream_notes`
   (transpose.py:91-107) expands chords to ALL pitches, merges all parts, and does
   NOT strip ties (a tied note becomes two onsets). The client cannot fix ties after
   JSON (tie info is gone). New pure `melody_notes()`; do not modify `stream_notes`
   (transpose-file endpoint + tests/test_transpose.py:88-89 depend on it).
4. **First-tempo-only is a real limitation.** `stream_tempo` (transpose.py:73-79)
   reads only the first MetronomeMark. v1 states the constant-tempo assumption,
   returns an `n_tempos` count, and the client warns when it is > 1.
5. **Scoring band: 50 cents default, not BAND_CENTS=15.** 15 is a sustain-drill
   constant; melody matching has transitions and scoops. Hit = right semitone
   (within 50 cents, 25 in a Strict toggle); precision = mean absolute folded cents.
6. **No lookahead scheduler needed.** The whole song is known upfront: schedule all
   count-in clicks and all notes at absolute AudioContext times (the transposer
   pattern, transposer.js:494-500). `ctx.currentTime` is the playhead clock.
7. **Lane precedent is manual.js drawStrip (:558-612), not realtime's drawGraph**
   (a ring buffer with no time axis). Borrow only drawGraph's in-tune band visual
   (realtime.js:704-720).
8. **Endpoint renamed** `/api/reference-melody` (it accepts MusicXML too).

## Design decisions

- **D1 Melody reduction (new `mouthtranscriber/reference.py`):** `stripTies()` first
  (precedent tests/eval_musicxml.py:43), then a skyline sweep over `flatten().notes`:
  top pitch per element (`sortAscending().pitches[-1]` idiom, tests/eval_musicxml.py:57),
  sort by (start, -midi), sweep keeping the highest sounding note; a higher overlapping
  candidate truncates the current note, a lower one is dropped. Output is monophonic,
  non-overlapping `[{midi, start_ql, dur_ql}]` (4 dp rounding per transpose.py:99-100).
  v1 limitation (documented): a lower held note is not resumed after a melody note ends.
  Not chordify(): it fragments held melody notes and leaks held bass under melody rests.
- **D2 Endpoint:** `POST /api/reference-melody` in server/app.py above the static mount
  (app.py:392), param `file: UploadFile` (transposer precedent app.py:293), errors
  HTTPException 400 only. Backed by `reference.reference_payload(raw, filename)` which
  reuses transpose.py's `parse_score`, `stream_key`, `stream_tempo`, `stream_time_sig`,
  `SUPPORTED_EXT`, the mkstemp spill pattern (:135-145), and the best-effort SVG wrap
  (:152-155, from the ORIGINAL score so the user sees the real sheet; skip when
  n_notes > 2000). 400 "no melody notes found in this file" for rest/percussion-only.
- **D3 Response:** `{svg, melody, n_notes, duration_ql, key, key_pc, key_mode,
  tempo_bpm, n_tempos, time_sig}`. Quarterlengths + tempo_bpm only (existing contract:
  browser converts with spb = 60/bpm, transposer.js:471,496); no seconds duplication.
- **D4 Scoring:** per voiced frame, folded cents vs the active note. Hit band 50
  cents (Strict toggle: 25). Frames within 0.1 s of a note onset are excluded (glide
  grace). `ANALYSIS_LATENCY_S = 0.09` subtracted from frame timestamps. Per-note
  {hitPct, meanAbsCents, voicedPct}, verdict good >= 80% / ok >= 50% / miss. Song
  summary: in-tune %, mean abs cents, notesGood/notesTotal.
- **D5 Timing:** one bar count-in via app.js `click(time, accent)` (:84-94) at
  tempo_bpm and time_sig; all audio scheduled upfront; auto-stop at
  end + 1 s tail; manual Stop scores what was reached (`stopQl`, "scored N of M").
  Guide piano via `await loadPiano(ctx)` then `sampleVoice(...)` (app.js:468-511),
  stop by ducking a master gain (app.js:599-607 idiom). Headphones hint reused
  (index.html:72 convention) against guide-piano bleed.
- **D6 Octave fold:** `dev = d - 12 * Math.round(d / 12)` semitones when octave-
  agnostic (default), raw `d` when "Enforce octave" is checked; cents = dev * 100.
  Frames are retained after a take, so flipping either toggle re-scores instantly.

## Files

Create:
- `mouthtranscriber/reference.py` - melody_notes, n_tempo_marks, reference_payload.
- `server/static/singalong.js` - `const SA = (() => {...})()`, NO DOM at load time;
  pure section at IIFE top (node-testable), DOM controller in `createSingalong()`
  returning {enter, exit}; tail `window.SA` + `module.exports` (transposer.js:583-584
  pattern). Constants: BAND_CENTS_NORMAL 50, BAND_CENTS_STRICT 25, ONSET_GRACE_S 0.1,
  ANALYSIS_LATENCY_S 0.09, PLAYHEAD_FRAC 0.3.
- `tests/test_reference_melody.py` - template tests/test_transpose.py: in-memory
  music21 fixtures (incl. a tied note, a chord, a bass part below the melody, and a
  truncation case), TestClient (keeps verovio single-threaded, test_transpose.py:8-11),
  parametrized [".mid", ".musicxml"], 400 cases (empty, garbage, rest-only),
  multi-tempo n_tempos == 2 case. music21 is a hard dep, no skip.
- `tests/manual/singalong.test.cjs` - template transposer.test.cjs (require the static
  file, hand-rolled eq/deepEq, exit code). Covers foldCents (octave -> 0, fifth ->
  -500, +-6 tie -> -600), activeIndex boundaries, pitchRange (pad 2, min span 6),
  laneLayout, and the whole scoreTake matrix (perfect / octave-down / 30-cent-sharp
  takes, grace exclusion, stopQl, verdicts).
- `SING-ALONG PLAN.md` - design doc per MANUAL TRANSCRIBE MODE PLAN.md precedent.

Modify:
- `server/app.py` - the /api/reference-melody route (shape of transpose_file
  app.py:292-309).
- `server/static/index.html` - nav button `🎵 Sing-Along` (matches the emoji-per-tab
  nav style, index.html:17-20), `#view-singalong` container with source card
  (#saDrop/#saFile/#saSummary/#saWarn/#saSheet + melody-on-top hint), run card
  (#saPreview/#saStart/#saOctave/#saStrict/#saStatus/#saLane/#saReadout + headphones
  hint), results card (#saScoreSummary/#saOverview); `<script src="singalong.js">`
  LAST after transposer (index.html:349-352).
- `server/static/app.js` - one lazy-create branch in the tab handler (app.js:614-631),
  mirroring the transposer branch: create SA controller on first entry, `exit()` on
  leave (the single teardown path; releases mic, stops playback).
- `server/static/style.css` - #saLane/#saOverview sizes, verdict colors.
- `CLAUDE.md` - one bullet under "Other independent paths". `DIARY.md` - entry per
  session, newest on top, absolute dates (today 2026-08-30). PROJECT PLAN.md untouched.

## Reused as-is (no changes)

- `RT.detectPitch`, `RT.hzToNote`, `RT.centsBetween` (realtime.js, exported).
- `RAW_MIC` (app.js:26-33; never plain {audio:true}), `ensureAudio` (:78-82),
  `loadPiano` (:468-483, must await or synth fallback), `sampleVoice` (:496-511),
  `click` (:84-94) - bare script-scope globals, consumed with typeof guards exactly
  like transposer.js:461-501.
- transpose.py helpers listed in D2. Demo files exist: `testMaterials/3 OCTAVES TEST
  NOI LAI TINH XUA.mid` and `.musicxml`.

## Pure-function inventory (singalong.js top level)

```
foldCents(midiFloat, refMidi, octaveAgnostic) -> cents
activeIndex(melody, tQl) -> idx | -1            // [start, start+dur), melody non-overlapping
pitchRange(melody, pad=2, minSpan=6) -> {lo, hi}
barQl(timeSig) -> ql                            // ts[0] * (4 / ts[1])
laneLayout(melody, tQl, opts) -> {rects, lo, hi, playheadX}
verdict(note) -> "good" | "ok" | "miss"
scoreTake(melody, frames, {bpm, bandCents, graceSec, octaveAgnostic, stopQl})
  -> {inTunePct, meanAbsCents, voicedPct, notesGood, notesTotal, scoredNotes, perNote}
```

## Phases (each demoable)

1. **Load and watch:** reference.py + endpoint + pytest; tab scaffold + lazy branch;
   upload -> summary/SVG/tempo warning; Preview = count-in + guide piano + scrolling
   lane (drawStrip-style bars, fixed playhead, own rAF reading ctx.currentTime).
   No mic. Node tests for the geometry/fold functions land here too.
2. **Sing along live:** Start = Preview + getUserMedia(RAW_MIC) + own AnalyserNode
   (fftSize 8192, ~33 Hz throttle per realtime.js:175-181) -> RT.detectPitch ->
   frames `{t, midiFloat|null}`; live trail with pen-up gaps (manual.js:600-611),
   octave-folded onto the bars, active-bar tint by band; mic failure degrades to
   preview-only; exit() can never leave a hot mic.
3. **Scoring:** scoreTake + results card + full-song overview lane with verdict
   colors; toggle flips re-score retained frames; node scoring tests are the gate.
4. **Deferred (listed in SING-ALONG PLAN.md, not built):** practice tempo 0.5-1x,
   part picker, multi-tempo map, transpose-to-my-range, guide mute, localStorage
   take history, full-arrangement backing.

## Verification

- Per phase: `.venv/Scripts/python.exe -m pytest tests/ -q` (test_transpose.py green
  proves stream_notes untouched); `node tests/manual/singalong.test.cjs` and the two
  existing .cjs tests (regression on shared globals).
- Browser: dev server via the `mouthtranscriber-web` launch config, upload both
  testMaterials variants, Preview/Start/Stop/tab-switch teardown. Mic path headlessly
  via scratchpad playwright with `--use-fake-ui-for-media-stream
  --use-fake-device-for-media-stream` (rehearsed in this repo; script stays in
  scratchpad, not the repo), then a real-mic pass by the user.
- **Port 8000 must be freed before ending any turn** (CLAUDE.md rule).
- No branching, no commits; working-tree changes only (CLAUDE.md rule).

## Risks

- R1 multi-tempo desync -> n_tempos warning; clock and scorer share one spb so
  results match what the user heard. R2 mic failure -> degrade to preview.
- R3 huge/empty files -> 400s per convention; SVG skipped > 2000 notes; no size cap
  (none exists anywhere in app.py, consistent).
- R4 melody in a lower voice -> skyline picks wrong; upload-card hint + visible SVG;
  part picker deferred.
- R5 guide-piano bleed scores as in-tune -> headphones hint; RMS gate 0.008 rejects
  quiet bleed; guide mute deferred.
- R6 analysis latency -> single ANALYSIS_LATENCY_S constant + onset grace, tunable.
- R7 script-scope coupling -> typeof guards on every app.js global (transposer.js:464).