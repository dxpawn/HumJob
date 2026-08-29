# HumJob — Project Plan

> Hum a melody → get back the notes, the key, and suggested chords, as MIDI and MusicXML.
> **Local-first**: build a Python pipeline that works *excellently* on a laptop, then wrap the exact same code in a web backend.

---

## 1. Vision & Success Criteria

### 1.1 The one-liner
You hum "da-da-da" into a mic to a metronome click. The app returns the main melody as **MIDI + MusicXML + rendered sheet music**, identifies the **key and scale** (e.g. F minor), and **suggests compatible chords**.

### 1.2 Why this can work when Play Store apps don't
Humming is **monophonic** (one note at a time) — we skip the genuinely hard part of transcription (separating overlapping notes). The common failures — missed notes, phantom notes in silence, everything shifted because the user hums flat — are algorithm-quality problems, not impossibility. This plan attacks each one directly (see §5).

### 1.3 Target user
The author (single user), on Windows, using a laptop mic or a USB mic + headphones. No accounts, no cloud, no multi-tenant concerns for the local version.

### 1.4 Explicit non-goals for v1
- ❌ Polyphony / chords in the *input* (you hum one line).
- ❌ Transcribing full songs from a mixed recording (vocals + instruments).
- ❌ Lyrics, multiple voices, or drum transcription.
- ❌ Real-time / live transcription while humming (we process after you stop). *Real-time is a later stretch goal.*
- ❌ Beating a professional transcriber on complex rhythm. We target clean, singable melodies.

### 1.5 "Works excellently" — the measurable bar
The local version is **done enough to build the web app on** when, on our ground-truth set of clean da-da-da recordings (§6):

| Metric | Target | Tool |
|---|---|---|
| Note-level F1 (onset within 50 ms, correct pitch) | **≥ 0.95** | `mir_eval.transcription` |
| Phantom notes in silent regions | **0** | manual + eval |
| Raw pitch accuracy (voiced frames) | **≥ 0.98** | `mir_eval.melody` |
| Key detection (unambiguous melodies) | **correct top-1** | vs. reference |
| End-to-end latency for a 30 s take | **< 5 s** on CPU | wall clock |
| Off-key robustness: melody hummed 40 cents flat still transcribes to correct *relative* pitches | **pass** | dedicated test clip |

If we can't hit the note-F1 bar, we do **not** move to the web phase — we fix the core first.

---

## 2. User Experience Flow (local version)

The agreed UX tricks are load-bearing, not cosmetic — they make the DSP tractable:

- **"da-da-da" articulation.** The consonant creates a crisp amplitude onset at every note boundary. This turns note segmentation from "guess where the pitch settled" into "find the attacks," which is far more reliable — and it lets us separate two repeated notes of the *same* pitch, which pitch-tracking alone cannot.
- **Metronome + user-set BPM before humming.** Rhythm quantization is the single hardest stage. Knowing the tempo *a priori* converts it from blind tempo-estimation into snapping to a known grid.

### 2.1 Screen-by-screen
1. **Setup**: BPM slider + **tap-tempo** button; time signature (default 4/4); subdivision grid (default 1/16); count-in length (default 1 bar).
2. **Record**: audible metronome (Web Audio / `sounddevice`) with a **count-in**, visual beat flash, level meter, big record button. Recommend headphones so the click doesn't bleed into the mic.
3. **Results**:
   - Waveform + **pitch curve** (f0 over time) with detected note segments overlaid.
   - **Piano-roll** of the transcription.
   - **Rendered sheet music** (OpenSheetMusicDisplay from our MusicXML).
   - Detected **key/scale** (top-3 with confidence) and **suggested chords** per measure.
4. **The killer verification feature — Overlay Playback**: play the *synthesized* detected melody on top of the *original* recording. If they line up, the transcription is right; if a note is wrong, you hear it instantly. This is how the user (and we) judge quality without reading notation.
5. **Adjust**: change quantization grid, nudge tempo/latency offset, override key, toggle chord suggestions.
6. **Export**: MIDI, MusicXML, and a rendered PDF/PNG of the score.

---

## 3. Architecture

### 3.1 One core, two frontends, one future backend
```
                 ┌───────────────────────────────┐
                 │   mouthtranscriber/  (core)    │
                 │   pure Python DSP pipeline     │
                 └───────────────────────────────┘
                    ▲            ▲            ▲
        ┌───────────┘            │            └────────────┐
   cli.py (iteration)   FastAPI server (server/)     pytest eval harness
                                 │
                        local web UI (static JS)
                                 │
                    ── same server becomes the WEB APP ──
```
Because the web app was decided to be **server-side Python**, the FastAPI app we build for the local UI *is* the web app backend. There is no second implementation to write and no DSP porting to JavaScript. The browser only records audio and renders results.

### 3.2 Pipeline modules (`mouthtranscriber/`)
| Module | Responsibility |
|---|---|
| `audio_io.py` | Load/record WAV, resample, mono downmix (`soundfile`, `sounddevice`, `ffmpeg` fallback) |
| `preprocess.py` | DC removal, high-pass, normalize, optional metronome-notch |
| `pitch.py` | f0 + confidence per frame; pluggable backend (pYIN default, torchcrepe optional) |
| `voicing.py` | Voiced/unvoiced decision (confidence + RMS gate, hysteresis) |
| `segment.py` | f0 contour + onsets → discrete `NoteEvent`s |
| `tuning.py` | Global (and drift) tuning-offset estimation, snap to semitones |
| `quantize.py` | Snap onsets/durations to the known BPM grid |
| `key.py` | Krumhansl–Schmuckler key/scale detection |
| `chords.py` | Per-measure diatonic chord suggestion |
| `export.py` | MIDI (`pretty_midi`) + MusicXML/score (`music21`) |
| `viz.py` | Debug plots (pitch curve + segments + notes) |
| `pipeline.py` | Orchestrates the stages; single `transcribe(audio, params) -> Score` entry point |

### 3.3 Data model
```
Frame(t_sec, f0_hz, confidence, rms)                 # dense, ~10 ms hop
NoteEvent(start_sec, end_sec, midi, velocity, cents_offset)
Score(notes, tempo_bpm, time_sig, key, chords, tuning_offset_cents)
```
Every stage is a pure function (`list[Frame] | list[NoteEvent] -> ...`), so each is independently testable and swappable.

### 3.4 Pitch backend behind an interface
```python
class PitchTracker(Protocol):
    def track(self, y: np.ndarray, sr: int) -> list[Frame]: ...
```
- **Default: pYIN** via `librosa.pyin` — no heavy ML dependency, returns f0 + voiced-probability, works out of the box on Windows.
- **Optional: torchcrepe** (PyTorch port of CREPE) — chosen over the TensorFlow `crepe` package specifically to **avoid TensorFlow-on-Windows pain**. Use the `tiny`/`small` model on CPU.
- We benchmark both on the eval set (§6) and keep whichever wins per metric; the interface makes this a config flag, not a rewrite.

---

## 4. Processing Pipeline (data flow)

```
WAV / mic
  → preprocess       (HPF, normalize)
  → pitch.track()    → dense Frames (f0, confidence, rms)
  → voicing.decide() → voiced mask (hysteresis)
  → segment.notes()  → NoteEvents (onset + pitch region)
  → tuning.correct() → snapped MIDI + global tuning offset
  → quantize.to_grid(bpm) → rhythmic durations on the 1/16 grid
  → key.detect()     → key / scale
  → chords.suggest() → per-measure chords
  → export.midi() / .musicxml()
```

---

## 5. Every Stage: What Can Go Wrong → Countermeasure

This is the core of the plan. Each stage lists the failure modes (many are exactly what makes other apps unusable) and our defense.

### 5.1 Recording
| Problem | Countermeasure |
|---|---|
| Metronome click bleeds into mic and gets transcribed as notes | **Recommend headphones** (primary). Fallback: visual-only metronome mode. Fallback: notch/gate audio at *known* click timestamps (we know exactly when clicks fire). |
| Mic latency: recorded audio lags the click grid, so every note lands off-beat | **One-time loopback calibration**: play a click, record it, measure the offset, store it, subtract it before quantization. |
| Clipping / too-hot input | Live level meter + post-hoc clip detection warning; suggest re-record. |
| Sample-rate / device mismatch, stereo mic | Force mono, resample to a canonical rate (22.05 or 16 kHz) in `audio_io`. |
| Very quiet humming → poor SNR | Level meter nudges user; normalize in preprocess; confidence gating handles the rest. |

### 5.2 Preprocessing
| Problem | Countermeasure |
|---|---|
| Low-frequency rumble, HVAC, desk thumps | High-pass ~70 Hz (below the lowest hum we expect). |
| DC offset from cheap mics | Remove DC (subtract mean / very-low HPF). |
| Inconsistent loudness across takes | Peak/RMS normalize. |
| Broadband room noise | Optional light noise-gate; rely on the confidence + RMS voicing gate rather than aggressive denoising (denoising can distort pitch). |

### 5.3 Pitch tracking (f0)
| Problem | Countermeasure |
|---|---|
| **Octave errors** (tracker reports 2× or ½ the true pitch) — classic and very audible | Constrain f0 search to a human-hum range (**~65–1050 Hz**); Viterbi/HMM smoothing (pYIN has this); post-pass **octave-jump correction** using chroma continuity (an isolated one-note octave leap between two stable regions is snapped back). |
| Vocal fry / creaky voice at phrase ends → garbage f0 | Confidence threshold drops these frames; they become unvoiced. |
| Harmonics mistaken for the fundamental | pYIN/CREPE are designed against this (autocorrelation / learned); range constraint helps. |
| Vibrato read as pitch instability | Median-filter f0 within a note; take the representative pitch from the stable middle (§5.6). |
| Pitch scoops / portamento at note starts | Ignore the first ~15% of each note when computing its pitch. |
| Backend disagreement / edge cases | pYIN vs torchcrepe A/B on the eval set; pick the more robust default. |

### 5.4 Voicing & silence (directly fixes "can't recognize silence")
| Problem | Countermeasure |
|---|---|
| Silence transcribed as notes (phantom notes) | **Dual gate**: a frame is voiced only if pitch-confidence AND RMS both exceed thresholds. |
| On/off flicker at boundaries | **Hysteresis** (different enter/exit thresholds) so voicing doesn't chatter. |
| The "d" consonant briefly drops pitch mid-phrase and splits one held note in two | **Merge unvoiced gaps shorter than ~60 ms** inside an otherwise continuous pitch region. |
| Breath noise / lip smacks become tiny notes | **Minimum note length ~80 ms**; anything shorter is discarded. |
| Trailing breath after the last note | Energy decay + confidence gate trims it. |

### 5.5 Note segmentation (onset/offset — the make-or-break stage)
| Problem | Countermeasure |
|---|---|
| Where does one note end and the next begin? | Combine **three cues**: (a) **spectral-flux/energy onset detection** — the "da" attacks give crisp onsets; (b) **pitch-jump splitting** — a sustained region that steps to a new semitone splits; (c) **silence** boundaries. |
| Two repeated same-pitch notes ("da-da" on one pitch) merge into one | The **onset cue** catches this even without a pitch change — this is exactly why we required da-da-da over "aaah". |
| Glissando / slide between notes creates smeared segments | Pitch-jump detection with a slope threshold; the stable plateaus become notes, the slide between them is attributed to the target note's onset. |
| Vibrato falsely split into multiple notes | Smoothing + a minimum-stability duration before declaring a new note. |
| Legato humming with weak consonants → missed onsets | Fall back to pitch-change + energy dips; UI coaches clearer articulation; onset sensitivity is a tunable param. |

*Reference approach:* Tony / the pYIN note-tracking HMM (Mauch et al.) — segment the continuous f0 into note objects rather than rounding per frame.

### 5.6 Pitch assignment & tuning (fixes "hums 40 cents flat")
| Problem | Countermeasure |
|---|---|
| User isn't at A440 — every note snaps to the wrong semitone | **Estimate a global tuning offset**: find the pitch shift that best aligns all note medians to the semitone grid, then apply it before snapping. |
| Pitch drifts over a long take (starts in tune, sags flat) | Optional **windowed/drift** correction (piecewise offset) for long recordings. |
| Scoops bias a note's pitch | Use the **median f0 over the middle ~60%** of each note, not the mean over the whole thing. |
| Note sits exactly between two semitones (ambiguous) | Snap to nearest; record `cents_offset` so the UI can flag "this one was 48 cents sharp — did you mean X or Y?" |

### 5.7 Rhythm quantization (made tractable by known BPM)
| Problem | Countermeasure |
|---|---|
| Human timing is never exact | Snap onsets/durations to the **known BPM grid** (default 1/16); a tolerance window absorbs natural jitter. |
| Recording offset from the click (latency) | Apply the calibrated latency offset (§5.1) and align to the **count-in** anchor before snapping. |
| Triplets vs straight eighths ambiguity | Per beat, test duple vs triplet subdivision and pick the lower-error fit; expose a manual toggle. |
| Very short/long outliers (grace notes, held finales) | Clamp to min/max note values on the grid; keep fermata-like long notes as tied durations. |
| User rushes / drags the tempo | Trust the set BPM by default; offer a "re-estimate tempo" button if the fit error is high. |

### 5.8 Key & scale detection
| Problem | Countermeasure |
|---|---|
| Wrong key from a short/ambiguous melody | **Duration-weighted Krumhansl–Schmuckler** over all 24 keys (via `music21`); report **top-3 with confidence**. |
| Relative major/minor confusion (e.g. C major vs A minor) | Tie-break with tonic emphasis (first/last note, strong beats); let the user override. |
| Modal or bluesy melodies | Report best diatonic fit + a "low confidence / possibly modal" flag rather than forcing a wrong answer. |
| Accidentals from off-key humming | Run key detection **after** tuning correction (§5.6) so out-of-tune ≠ out-of-key. |

### 5.9 Chord suggestion
| Problem | Countermeasure |
|---|---|
| Which chord fits each measure? | Score each **diatonic** chord by how well it covers that measure's melody notes, weighting **strong beats** higher. |
| Choppy, musically nonsensical progression | Smooth the sequence with a **progression prior** (Viterbi over common functional transitions, cadence bias toward V→I). |
| Melody note is a non-chord tone (passing/neighbor) | Down-weight short off-beat notes so passing tones don't force weird chords. |
| Wanting richer harmony (7ths, borrowed chords) | v1 = triads only; flag as a later enhancement. |

### 5.10 Export & rendering
| Problem | Countermeasure |
|---|---|
| MusicXML that notation software rejects | Build via `music21` (well-formed by construction); validate by round-tripping. |
| Ugly enharmonic spelling (C♯ vs D♭) | Spell accidentals according to the detected key. |
| Messy rhythm on the page | Quantization (§5.7) + `music21` `makeNotation`/beaming cleanup. |
| Sheet rendering in the browser | **OpenSheetMusicDisplay** renders our MusicXML client-side (no server render dependency). |
| Debugging "why is this note wrong" | `viz.py` overlays f0 curve + segments + final notes on one plot — the standard iteration tool. |

---

## 6. Evaluation Strategy

We cannot improve what we can't measure. This is how we prove the app works.

### 6.1 Ground-truth set (the author records these)
~15–20 short clips, each paired with a reference MIDI:
- Ascending / descending **major & minor scales**
- **Arpeggios** and octave leaps
- **Repeated same-pitch** notes (da-da-da on one note)
- **Held notes** and phrases with deliberate **silences** between them
- A couple of **known tunes** (e.g. Twinkle Twinkle, Happy Birthday)
- **Deliberately flat** humming (tests tuning correction)
- **Vibrato-heavy** and **scoopy** takes (stress segmentation)
- A **triplet** rhythm clip

### 6.2 Metrics (`mir_eval`)
- Note-level **precision / recall / F1** (onset tolerance 50 ms, pitch tolerance 50 cents).
- **Raw pitch accuracy** on voiced frames.
- **Key accuracy** vs. reference.
- Wall-clock latency.

### 6.3 Regression harness
- `pytest` runs the pipeline over the ground-truth set and asserts metric thresholds — so a change that helps scoopy clips but breaks repeated notes fails CI.
- **pYIN vs torchcrepe** run side-by-side on the same set; results tabulated.
- The debug overlay plot is saved per clip for eyeball review.

---

## 7. Milestones (each independently shippable & verifiable)

| # | Deliverable | Verification |
|---|---|---|
| **M0** | Repo scaffold, pinned deps, venv, a few test WAVs, `viz.py` pitch-curve plotter | Plot a hum's f0 curve |
| **M1** | CLI `hum2midi in.wav -o out.mid` (pitch → voicing → segment → snap) | Hum a scale, get a recognizable MIDI |
| **M2** | Eval harness + ground-truth recordings; **iterate to F1 ≥ 0.95** | ← the "works excellently" gate |
| **M3** | Tuning-offset correction + key detection | Flat-hummed clip transcribes correctly; correct key reported |
| **M4** | BPM/metronome quantization + MusicXML + OSMD sheet render | Recognizable rhythm on a real staff |
| **M5** | Local web UI end-to-end (record in browser → sheet) via FastAPI | Full loop without touching the CLI |
| **M6** | Chord suggestions | Sensible diatonic chords under a known tune |
| **M7** | Polish: **overlay playback**, grid/key overrides, PDF/PNG export | Detected melody audibly matches the original |
| **Web** | Deploy the FastAPI backend (single-user, auth-less first); browser-recording hardening | Use it from another device |

**Sequencing note:** we do **not** proceed past M2 until the F1 bar is met. Everything after assumes correct notes.

---

## 8. Global Risk Register (cross-cutting)

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `librosa`/`numba` version friction on Windows / Py3.12 | Med | Med | Pin known-good versions; record the working set in `requirements.txt`. |
| `torch`/torchcrepe install weight or CPU slowness | Med | Med | Make it **optional**; pYIN is the default; use CREPE `tiny` on CPU. |
| `sounddevice`/PortAudio device quirks on Windows | Med | Low | Provide a file-upload path as a fallback to live recording; test on the target machine early. |
| Segmentation good on scales but bad on real melodies | Med | High | Diverse ground-truth set (§6.1) + regression thresholds catch overfitting. |
| Rhythm still messy despite known BPM | Med | Med | Adjustable grid + latency calibration + manual nudge in UI. |
| Web phase: browser mic permissions / `getUserMedia` / mobile Safari quirks | Med | Med | Deferred to the Web milestone; keep the upload fallback; document per-browser notes. |
| Scope creep (real-time, polyphony) | High | Med | Non-goals fixed in §1.4; stretch items parked. |
| Latency > 5 s on long takes with CREPE | Low | Low | pYIN fallback; chunked processing; downsample. |

---

## 9. Tech Stack & Dependencies

**Runtime:** Python 3.12 (verified 3.12.7), Node 24 (for OSMD tooling), ffmpeg 8.1.1 (installed) for format conversion.

**Python (core):**
- `numpy`, `scipy` — DSP primitives
- `librosa` — pYIN pitch tracking, onset detection, resampling
- `soundfile`, `sounddevice` — I/O and live recording
- `pretty_midi` — MIDI export
- `music21` — MusicXML, key detection, notation cleanup
- `mir_eval` — evaluation metrics
- `matplotlib` — debug plots
- `fastapi`, `uvicorn` — local server / future web backend
- *(optional)* `torch`, `torchcrepe` — CREPE pitch backend

**Frontend (local UI, later the web app):**
- Vanilla JS + Web Audio API (metronome; recording via `MediaRecorder`/`getUserMedia`)
- **OpenSheetMusicDisplay** for score rendering
- Minimal HTML/CSS; no heavy framework for v1

All versions pinned in `requirements.txt` once M0 confirms a working set on this machine.

---

## 10. Proposed Repository Layout

```
MouthTranscriber/
├── PROJECT PLAN.md            # this file
├── DIARY.md                   # running log for session continuity
├── README.md
├── requirements.txt
├── mouthtranscriber/          # core pipeline package
│   ├── __init__.py
│   ├── audio_io.py
│   ├── preprocess.py
│   ├── pitch.py               # PitchTracker interface + pYIN / torchcrepe
│   ├── voicing.py
│   ├── segment.py
│   ├── tuning.py
│   ├── quantize.py
│   ├── key.py
│   ├── chords.py
│   ├── export.py
│   ├── viz.py
│   └── pipeline.py            # transcribe() entry point
├── cli.py                     # hum2midi command
├── server/                    # FastAPI app + static local UI (= web backend)
│   ├── app.py
│   └── static/
├── tests/
│   ├── test_*.py
│   └── data/                  # ground-truth clips + reference MIDI
└── docs/
```

---

## 11. Open Decisions (to confirm as we build)

- **Canonical sample rate**: 22.05 kHz (pYIN-friendly, light) vs 16 kHz (CREPE-native). *Leaning 22.05 kHz for the pYIN default.*
- **Default pitch backend**: pYIN first; adopt CREPE only if the benchmark clearly wins.
- **Live recording vs upload for the local UI**: build **upload-first** (deterministic, testable), add live recording once the pipeline is trusted.
- **Time signatures beyond 4/4**: v1 assumes 4/4; 3/4 and 6/8 are quick follow-ups.

These don't block M0–M2; they get settled with data from the eval harness.

---

## 12. Open directions (post-eval, appended 2026-08-27)

The synthetic eval is saturated (note F1 1.000) but real hums remain unusable. Three candidate
directions, logged so a fresh session can pick up (details + status in `DIARY.md` Session 39):

- **(A) BPM robustness / tighter detection** *(leading candidate; most tractable, directly
  measurable via `eval_report`'s wrong-BPM pass).* `tempo.detect_bpm` returns a single global tempo
  with a bell-curve prior centred on 100 BPM, which fails on real, variable-tempo, slow hums.
  - **A1**: flatten or remove the 100 BPM prior so slow tempos are not pulled toward 100.
  - **A2**: build the onset envelope from the pipeline's own "da" voicing onsets rather than raw
    spectral flux (cleaner, noise/breath-robust).
- **(B) Diagnose real hums (ground-truth capture).** The ultimate unlock, blocked on ground truth.
  Approach settled 2026-08-27: capture the user's *natural* hums and label them afterward (more
  representative than humming to a target, which changes how the user hums).
  - **Pitch label**: an external pitch-finder app reads each note, but it is another detector, so
    its output is *not* an oracle - the user verifies each note against the tune they knowingly
    hummed (human in the loop). Prefer an app that exports MIDI / a note list so we can import and
    the user only checks.
  - **Rhythm label**: do NOT rely on the app for onsets/durations (pitch apps rarely give clean note
    boundaries, and rhythm is our weak link). Instead hum to a click at a known BPM; the intended
    beats come from the known tune, and a steady tempo also makes the pitch app more reliable.
  - Verified `(audio, notes, BPM)` triples land in `tests/data/recorded/`.
  - **Input technique note.** The project's "hum da-da-da" already sits on the good side of both
    axes that matter: an open vowel (rich harmonics -> better pitch/octave tracking) and a consonant
    onset (crisp attack -> splits repeated notes). Avoid strict closed-mouth humming ("mmm", nasal,
    octave-error-prone) and slurred vowels ("laaaa", no onset). Keep it steady and gentle - wide
    vibrato is the remaining thing that trips the segmenter.
- **(C) Synthesize over-splitting.** Add amplitude shimmer/breath/creak to `make_synthetic` so the
  still-unmeasured over-split failure mode is exposed in the eval.

**Report housekeeping (do later).** The course report [`report.tex`](report.tex) ships with a
bibliography whose details (authors, years, venues, arXiv IDs) were reconstructed from memory and
are not yet verified. Before submission, web-verify each of the 10 references (pYIN, CREPE, PESTO,
FCNF0++/penn, basic-pitch, Krumhansl, librosa, music21, verovio, mir\_eval) so nothing is
mis-cited.
