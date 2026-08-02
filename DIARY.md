# MouthTranscriber — Development Diary

A running log so any future session (or a fresh context) can pick up quickly.
Newest entry on top. Keep entries short: what changed, why, and what's next.

---

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
