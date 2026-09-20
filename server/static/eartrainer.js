"use strict";

// ===== Ear Trainer: listen-and-identify drills (client-side) =================
// The mirror of the rest of the app: instead of you making a sound and the app
// analyzing it, the app PLAYS something (via the shared piano samples in app.js)
// and you identify it by tapping a multiple-choice button. Four sub-modes:
//   interval / chord / scale / key(scale-degree).
// Pure question-generation + answer-checking are exported and node-tested;
// createEarTrainer() is the DOM/audio controller. No mic, no server.
//
// Reused app.js globals (guarded with typeof): ensureAudio(), loadPiano(ctx),
// sampleVoice(ctx, dest, midi, start, dur, peak). Theory tables are local copies
// (NOTE_NAMES / scale steps / chord stacks live inside other modules' IIFEs).

const ET = (() => {
  // ---- theory tables (pure) --------------------------------------------------
  const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  const noteName = (midi) => {
    const r = Math.round(midi);
    return NOTE_NAMES[((r % 12) + 12) % 12] + (Math.floor(r / 12) - 1);
  };

  // Interval label indexed by semitone distance 0..12.
  const INTERVAL_NAMES = ["Unison", "Minor 2nd", "Major 2nd", "Minor 3rd", "Major 3rd",
    "Perfect 4th", "Tritone", "Perfect 5th", "Minor 6th", "Major 6th", "Minor 7th",
    "Major 7th", "Octave"];

  // Chord qualities as root-position semitone stacks.
  const CHORD_QUALITIES = [
    { id: "maj", label: "Major", intervals: [0, 4, 7] },
    { id: "min", label: "Minor", intervals: [0, 3, 7] },
    { id: "dim", label: "Diminished", intervals: [0, 3, 6] },
    { id: "aug", label: "Augmented", intervals: [0, 4, 8] },
    { id: "dom7", label: "Dominant 7th", intervals: [0, 4, 7, 10] },
    { id: "maj7", label: "Major 7th", intervals: [0, 4, 7, 11] },
    { id: "min7", label: "Minor 7th", intervals: [0, 3, 7, 10] },
  ];

  // Scale / mode types: semitone steps from the tonic (one octave, tonic repeated on top).
  const SCALE_TYPES = [
    { id: "major", label: "Major (Ionian)", steps: [0, 2, 4, 5, 7, 9, 11] },
    { id: "minor", label: "Natural minor (Aeolian)", steps: [0, 2, 3, 5, 7, 8, 10] },
    { id: "dorian", label: "Dorian", steps: [0, 2, 3, 5, 7, 9, 10] },
    { id: "phrygian", label: "Phrygian", steps: [0, 1, 3, 5, 7, 8, 10] },
    { id: "lydian", label: "Lydian", steps: [0, 2, 4, 6, 7, 9, 11] },
    { id: "mixo", label: "Mixolydian", steps: [0, 2, 4, 5, 7, 9, 10] },
    { id: "locrian", label: "Locrian", steps: [0, 1, 3, 5, 6, 8, 10] },
    { id: "harmonic", label: "Harmonic minor", steps: [0, 2, 3, 5, 7, 8, 11] },
  ];

  const MAJOR_STEPS = [0, 2, 4, 5, 7, 9, 11]; // for the key-mode cadence + degrees
  const DEGREE_NAMES = ["1 (do)", "2 (re)", "3 (mi)", "4 (fa)", "5 (sol)", "6 (la)", "7 (ti)"];

  // Difficulty pools per mode: which intervals (semitones) / quality ids / scale ids /
  // scale-degree indexes are drawn on, tightest to widest.
  const POOLS = {
    interval: {
      beginner: [3, 4, 5, 7, 12],
      intermediate: [2, 3, 4, 5, 7, 9, 12],
      advanced: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
    },
    chord: {
      beginner: ["maj", "min"],
      intermediate: ["maj", "min", "dim", "aug"],
      advanced: ["maj", "min", "dim", "aug", "dom7", "maj7", "min7"],
    },
    scale: {
      beginner: ["major", "minor"],
      intermediate: ["major", "minor", "dorian", "mixo"],
      advanced: ["major", "minor", "dorian", "phrygian", "lydian", "mixo", "locrian", "harmonic"],
    },
    key: {
      beginner: [0, 2, 4],
      intermediate: [0, 2, 3, 4, 5, 6],
      advanced: [0, 1, 2, 3, 4, 5, 6],
    },
  };
  const DIFFICULTIES = ["beginner", "intermediate", "advanced"];
  const CHOICE_COUNT = 4; // multiple-choice buttons (fewer if the pool is smaller)

  // ---- pure helpers ----------------------------------------------------------
  const randInt = (rand, n) => Math.floor(rand() * n);
  const pick = (arr, rand) => arr[randInt(rand, arr.length)];
  function shuffle(arr, rand) {
    const a = arr.slice();
    for (let i = a.length - 1; i > 0; i--) { const j = randInt(rand, i + 1); const t = a[i]; a[i] = a[j]; a[j] = t; }
    return a;
  }
  // A choice set: up to n distinct pool entries, always including `must`, shuffled.
  function choiceSet(pool, n, must, rand) {
    const rest = shuffle(pool.filter((x) => x !== must), rand).slice(0, Math.max(0, n - 1));
    return shuffle(rest.concat([must]), rand);
  }
  const byId = (arr, id) => arr.find((x) => x.id === id);
  const norm = (d) => (DIFFICULTIES.indexOf(d) >= 0 ? d : "beginner");

  // ---- question builders (pure; `rand` injected for deterministic tests) -----
  // Each returns: { mode, prompt, notes:[{midi, beat, dur, peak}], play, answerId, choices:[{id,label}] }
  // `beat` is in abstract beats; the controller multiplies by seconds-per-beat.

  function makeInterval(opts, rand) {
    const pool = POOLS.interval[norm(opts.difficulty)];
    const semis = pick(pool, rand);
    const root = 48 + randInt(rand, 8); // C3..G3
    const style = opts.play === "harmonic" || opts.play === "descending" || opts.play === "ascending"
      ? opts.play : pick(["ascending", "descending", "harmonic"], rand);
    let notes;
    if (style === "harmonic") {
      notes = [{ midi: root, beat: 0, dur: 3, peak: 0.24 }, { midi: root + semis, beat: 0, dur: 3, peak: 0.24 }];
    } else if (style === "descending") {
      notes = [{ midi: root + semis, beat: 0, dur: 1.4, peak: 0.42 }, { midi: root, beat: 1.5, dur: 1.6, peak: 0.42 }];
    } else {
      notes = [{ midi: root, beat: 0, dur: 1.4, peak: 0.42 }, { midi: root + semis, beat: 1.5, dur: 1.6, peak: 0.42 }];
    }
    return {
      mode: "interval",
      prompt: "Which interval did you hear?",
      notes, play: style, answerId: String(semis),
      choices: choiceSet(pool, CHOICE_COUNT, semis, rand).map((s) => ({ id: String(s), label: INTERVAL_NAMES[s] })),
    };
  }

  function makeChord(opts, rand) {
    const pool = POOLS.chord[norm(opts.difficulty)];
    const qid = pick(pool, rand);
    const q = byId(CHORD_QUALITIES, qid);
    const root = 48 + randInt(rand, 8);
    const notes = q.intervals.map((iv) => ({ midi: root + iv, beat: 0, dur: 3, peak: 0.13 }));
    return {
      mode: "chord",
      prompt: "Which chord quality did you hear?",
      notes, play: "harmonic", answerId: qid,
      choices: choiceSet(pool, CHOICE_COUNT, qid, rand).map((id) => ({ id, label: byId(CHORD_QUALITIES, id).label })),
    };
  }

  function makeScale(opts, rand) {
    const pool = POOLS.scale[norm(opts.difficulty)];
    const sid = pick(pool, rand);
    const s = byId(SCALE_TYPES, sid);
    const root = 48 + randInt(rand, 6); // C3..F3
    const midis = s.steps.concat([12]).map((st) => root + st);
    const notes = midis.map((m, i) => ({ midi: m, beat: i * 0.5, dur: 0.45, peak: 0.42 }));
    return {
      mode: "scale",
      prompt: "Which scale or mode did you hear?",
      notes, play: "ascending", answerId: sid,
      choices: choiceSet(pool, CHOICE_COUNT, sid, rand).map((id) => ({ id, label: byId(SCALE_TYPES, id).label })),
    };
  }

  function makeKey(opts, rand) {
    const pool = POOLS.key[norm(opts.difficulty)];
    const degIdx = pick(pool, rand);
    const tonic = 48 + randInt(rand, 6); // C3..F3, major key
    // I - IV - V - I cadence (all major triads in a major key) to establish the tonic.
    const triad = (deg) => [0, 4, 7].map((iv) => tonic + MAJOR_STEPS[deg] + iv);
    const notes = [];
    [0, 3, 4, 0].forEach((deg, i) => triad(deg).forEach((m) => notes.push({ midi: m, beat: i * 1.6, dur: 1.5, peak: 0.12 })));
    // then the target scale tone to identify.
    notes.push({ midi: tonic + MAJOR_STEPS[degIdx], beat: 4 * 1.6 + 0.8, dur: 2, peak: 0.42 });
    return {
      mode: "key",
      prompt: "The cadence sets the key. Which scale degree was the final note?",
      notes, play: "sequence", answerId: String(degIdx),
      choices: choiceSet(pool, CHOICE_COUNT, degIdx, rand).map((d) => ({ id: String(d), label: DEGREE_NAMES[d] })),
    };
  }

  function makeQuestion(mode, opts, rand) {
    rand = rand || Math.random;
    opts = opts || {};
    if (mode === "chord") return makeChord(opts, rand);
    if (mode === "scale") return makeScale(opts, rand);
    if (mode === "key") return makeKey(opts, rand);
    return makeInterval(opts, rand);
  }

  function checkAnswer(question, id) {
    return !!question && String(id) === String(question.answerId);
  }
  function accuracy(results) {
    if (!results || !results.length) return 0;
    return results.filter((r) => r).length / results.length;
  }

  const api = {
    NOTE_NAMES, INTERVAL_NAMES, CHORD_QUALITIES, SCALE_TYPES, DEGREE_NAMES, POOLS, DIFFICULTIES,
    noteName, shuffle, choiceSet, makeQuestion, checkAnswer, accuracy,
  };

  // ---- controller (browser only) ---------------------------------------------
  function createEarTrainer() {
    const $ = (id) => document.getElementById(id);
    const view = $("view-eartrainer");
    if (!view) return { enter() {}, exit() {} };
    const refs = {
      submode: $("etSubmode"), difficulty: $("etDifficulty"),
      replay: $("etReplay"), next: $("etNext"),
      volume: $("etVolume"), volumeOut: $("etVolumeOut"),
      prompt: $("etPrompt"), choices: $("etChoices"), feedback: $("etFeedback"),
      score: $("etScore"), progress: $("etProgress"), history: $("etHistory"),
      spark: $("etSpark"), clearHist: $("etClearHist"),
    };
    const cssVar = (n) => (getComputedStyle(document.documentElement).getPropertyValue(n).trim() || "#888");

    const HISTORY_KEY = "humjob.eartrainer.history"; // client-only, no upload
    const HISTORY_CAP = 300;
    const VOLUME_KEY = "humjob.eartrainer.volume"; // client-only, remembered per device
    const SPB = 0.5; // seconds per abstract beat in a question's note plan
    const MODE_LABEL = { interval: "Interval", chord: "Chord", scale: "Scale", key: "Key" };

    // Playback loudness. The slider is 0..100%; full scale maps to VOL_MAX_GAIN on the
    // master gain (the old value was a fixed 0.9, which was too quiet on phones), which
    // multiplies each note's sample peak (0.42 for a lone tone, up to ~0.52 summed for a
    // dominant-7th chord). The 70% default (=1.68) is a clear boost yet leaves the loudest
    // chord clear of clipping; max is reserved for quiet devices. Matches Sing-Along.
    const VOL_MAX_GAIN = 2.4;
    const DEFAULT_VOLUME = 70;
    const currentVolumeGain = () => {
      const pct = refs.volume ? Number(refs.volume.value) : DEFAULT_VOLUME;
      return (Math.max(0, Math.min(100, pct)) / 100) * VOL_MAX_GAIN;
    };

    let mode = "interval", difficulty = "beginner";
    let question = null, answered = false, master = null;
    const session = { asked: 0, correct: 0, streak: 0, best: 0 };

    // ---- audio ----
    function stopSound() {
      if (master && typeof ensureAudio === "function") {
        try { const ctx = ensureAudio(); master.gain.setTargetAtTime(0.0001, ctx.currentTime, 0.02); } catch (e) { /* ignore */ }
      }
      master = null; // scheduled voices finish silently through the muted node
    }
    async function playNotes(notes) {
      if (typeof ensureAudio !== "function" || typeof sampleVoice !== "function") return;
      stopSound();
      const ctx = ensureAudio();
      if (typeof loadPiano === "function") { try { await loadPiano(ctx); } catch (e) { /* synth fallback */ } }
      const m = ctx.createGain();
      m.gain.value = currentVolumeGain();
      m.connect(ctx.destination);
      master = m;
      const t0 = ctx.currentTime + 0.06;
      for (const n of notes) {
        sampleVoice(ctx, m, n.midi, t0 + n.beat * SPB, n.dur * SPB, n.peak == null ? 0.4 : n.peak);
      }
    }
    function replay() { if (question) playNotes(question.notes); }

    // ---- question flow ----
    function newQuestion() {
      answered = false;
      question = makeQuestion(mode, { difficulty }, Math.random);
      if (refs.prompt) refs.prompt.textContent = question.prompt;
      if (refs.feedback) { refs.feedback.textContent = ""; refs.feedback.className = "et-feedback"; }
      if (refs.next) refs.next.classList.add("hidden");
      renderChoices();
      playNotes(question.notes);
    }
    function renderChoices() {
      if (!refs.choices) return;
      const btns = question.choices.map((c) => {
        const b = document.createElement("button");
        b.type = "button"; b.className = "ghost et-choice"; b.textContent = c.label; b.dataset.id = c.id;
        b.addEventListener("click", () => onChoice(c.id, b));
        return b;
      });
      refs.choices.replaceChildren(...btns);
    }
    function onChoice(id, btn) {
      if (answered || !question) return;
      answered = true;
      const ok = checkAnswer(question, id);
      session.asked++;
      if (ok) { session.correct++; session.streak++; if (session.streak > session.best) session.best = session.streak; }
      else session.streak = 0;
      if (refs.choices) for (const b of refs.choices.querySelectorAll(".et-choice")) {
        b.disabled = true;
        if (b.dataset.id === question.answerId) b.classList.add("et-right");
        else if (b === btn) b.classList.add("et-wrong");
      }
      const correctLabel = (question.choices.find((c) => c.id === question.answerId) || {}).label || "";
      if (refs.feedback) {
        refs.feedback.textContent = ok ? "Correct." : `Not quite - it was ${correctLabel}.`;
        refs.feedback.className = "et-feedback " + (ok ? "good" : "bad");
      }
      if (refs.next) refs.next.classList.remove("hidden");
      saveResult(ok);
      renderScore();
    }
    function renderScore() {
      if (!refs.score) return;
      const pct = session.asked ? Math.round(100 * session.correct / session.asked) : 0;
      refs.score.textContent = `Streak ${session.streak} (best ${session.best})   |   ${session.correct}/${session.asked} correct (${pct}%)`;
    }

    // ---- persistence + progress ----
    function loadHistory() {
      try { const a = JSON.parse(localStorage.getItem(HISTORY_KEY)); return Array.isArray(a) ? a : []; }
      catch (e) { return []; }
    }
    function saveResult(ok) {
      const a = loadHistory();
      a.push({ t: Date.now(), mode, difficulty, ok: ok ? 1 : 0 });
      while (a.length > HISTORY_CAP) a.shift();
      try { localStorage.setItem(HISTORY_KEY, JSON.stringify(a)); } catch (e) { /* quota: ignore */ }
      renderProgress();
    }
    function renderProgress() {
      const a = loadHistory();
      if (refs.progress) refs.progress.classList.toggle("hidden", a.length === 0);
      if (!a.length) { if (refs.history) refs.history.replaceChildren(); drawSpark([]); return; }
      if (refs.history) {
        const rows = ["interval", "chord", "scale", "key"].map((mo) => {
          const set = a.filter((r) => r.mode === mo);
          if (!set.length) return null;
          const c = set.filter((r) => r.ok).length;
          const div = document.createElement("div");
          div.className = "rt-hist-row";
          div.textContent = `${MODE_LABEL[mo]}: ${c}/${set.length} (${Math.round(100 * c / set.length)}%)`;
          return div;
        }).filter(Boolean);
        refs.history.replaceChildren(...rows);
      }
      drawSpark(rollingAccuracy(a, 10));
    }
    function rollingAccuracy(a, win) {
      const out = [];
      for (let i = 0; i < a.length; i++) {
        const slice = a.slice(Math.max(0, i - win + 1), i + 1);
        out.push(100 * slice.filter((r) => r.ok).length / slice.length);
      }
      return out.slice(-40);
    }
    function drawSpark(vals) {
      const cv = refs.spark; if (!cv) return;
      const g = cv.getContext("2d"); const W = cv.width, H = cv.height, pad = 5;
      g.clearRect(0, 0, W, H);
      if (vals.length < 2) { cv.classList.add("hidden"); return; }
      cv.classList.remove("hidden");
      const xOf = (i) => pad + (W - 2 * pad) * (i / (vals.length - 1));
      const yOf = (v) => H - pad - (H - 2 * pad) * (Math.max(0, Math.min(100, v)) / 100);
      g.strokeStyle = cssVar("--border"); g.lineWidth = 1;
      g.beginPath(); g.moveTo(pad, yOf(0) + 0.5); g.lineTo(W - pad, yOf(0) + 0.5); g.stroke();
      g.strokeStyle = cssVar("--accent"); g.lineWidth = 2; g.lineJoin = "round";
      g.beginPath();
      vals.forEach((v, i) => { const x = xOf(i), y = yOf(v); if (i === 0) g.moveTo(x, y); else g.lineTo(x, y); });
      g.stroke();
    }

    // ---- mode / difficulty ----
    function setMode(m) {
      if (m === mode) return;
      stopSound();
      mode = m;
      if (refs.submode) for (const b of refs.submode.querySelectorAll(".seg-btn")) b.classList.toggle("active", b.dataset.mode === m);
      newQuestion();
    }
    function setDifficulty(d) { difficulty = norm(d); newQuestion(); }

    // ---- wiring ----
    if (refs.submode) refs.submode.addEventListener("click", (e) => {
      const b = e.target.closest(".seg-btn"); if (b) setMode(b.dataset.mode);
    });
    if (refs.difficulty) refs.difficulty.addEventListener("change", () => setDifficulty(refs.difficulty.value));
    if (refs.replay) refs.replay.addEventListener("click", replay);
    if (refs.next) refs.next.addEventListener("click", newQuestion);
    if (refs.volume) {
      // restore the remembered level, then keep it live: update the readout, adjust any
      // note currently ringing, and persist the choice.
      try {
        const raw = localStorage.getItem(VOLUME_KEY);
        const saved = raw == null ? NaN : Number(raw);
        if (Number.isFinite(saved) && saved >= 0 && saved <= 100) refs.volume.value = String(saved);
      } catch (e) { /* ignore */ }
      if (refs.volumeOut) refs.volumeOut.textContent = refs.volume.value;
      refs.volume.addEventListener("input", () => {
        if (refs.volumeOut) refs.volumeOut.textContent = refs.volume.value;
        if (master && typeof ensureAudio === "function") {
          try { const ctx = ensureAudio(); master.gain.setTargetAtTime(currentVolumeGain(), ctx.currentTime, 0.02); } catch (e) { /* ignore */ }
        }
        try { localStorage.setItem(VOLUME_KEY, refs.volume.value); } catch (e) { /* quota: ignore */ }
      });
    }
    if (refs.clearHist) refs.clearHist.addEventListener("click", () => {
      try { localStorage.removeItem(HISTORY_KEY); } catch (e) { /* ignore */ }
      renderProgress();
    });
    renderProgress();

    function enter() { if (!question) newQuestion(); else renderScore(); }
    function exit() { stopSound(); }
    return { enter, exit };
  }

  const out = Object.assign({}, api, { createEarTrainer });
  return out;
})();

if (typeof window !== "undefined") window.ET = ET;
if (typeof module !== "undefined" && module.exports) module.exports = ET;
