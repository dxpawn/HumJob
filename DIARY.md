# MouthTranscriber — Development Diary

A running log so any future session (or a fresh context) can pick up quickly.
Newest entry on top. Keep entries short: what changed, why, and what's next.

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
