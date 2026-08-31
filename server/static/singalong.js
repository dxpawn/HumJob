"use strict";

// Sing-Along tab - upload a score, hear it as a karaoke reference, sing against it.
//
// The MIDI/MusicXML drives the clock (no free timing, no DTW): the whole melody is known
// up front, so every count-in click and guide-piano note is scheduled at an absolute
// AudioContext time and ctx.currentTime IS the playhead. The server reduces the upload to
// a single monophonic melody line (POST /api/reference-melody -> mouthtranscriber.reference);
// the browser plays it, tracks the voice live via the same detector the Realtime tab uses,
// and scores each note.
//
// Reuses app.js's shared audio globals (ensureAudio, sampleVoice, loadPiano, click,
// RAW_MIC) and window.RT's pure pitch math (detectPitch, hzToNote), all with typeof/null
// guards so a missing piece degrades cleanly instead of throwing. The scoring/geometry
// math is pure and exposed on window.SA (and as a node module) for
// tests/manual/singalong.test.cjs. No em/en dashes or emojis per CLAUDE.md.

const SA = (() => {
  // ---- constants (shared by the pure core and the controller) ----------------
  // A "hit" is the right semitone within this many cents. The run panel's Difficulty select
  // picks the band; four levels from tightest to most forgiving.
  const BAND_CENTS_STRICT = 25;
  const BAND_CENTS_NORMAL = 50;   // default
  const BAND_CENTS_LENIENT = 75;
  const BAND_CENTS_TONE_DEAF = 100;
  const DIFFICULTY_BANDS = {
    strict: BAND_CENTS_STRICT,
    normal: BAND_CENTS_NORMAL,
    lenient: BAND_CENTS_LENIENT,
    tonedeaf: BAND_CENTS_TONE_DEAF,
  };
  const DEFAULT_DIFFICULTY = "normal";
  const ONSET_GRACE_S = 0.1;      // ignore the first 100 ms of a note (glide / scoop in)
  const ANALYSIS_LATENCY_S = 0.09; // mic->analyser lag, subtracted from frame timestamps
  const PLAYHEAD_FRAC = 0.3;      // the playhead sits this far from the lane's left edge
  const WINDOW_SEC = 5.0;         // how many seconds of the melody the scrolling lane shows
  const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

  const midiName = (m) => {
    const r = Math.round(m);
    return NOTE_NAMES[((r % 12) + 12) % 12] + (Math.floor(r / 12) - 1);
  };

  // ---- pure core (no DOM; unit-tested in tests/manual/singalong.test.cjs) -----

  // Cents from a sung pitch to a reference note. Octave-agnostic (default) folds any
  // octave error to zero; enforcing the octave keeps the full distance.
  function foldCents(midiFloat, refMidi, octaveAgnostic) {
    let d = midiFloat - refMidi;
    if (octaveAgnostic) d = d - 12 * Math.round(d / 12);
    return d * 100;
  }

  // Index of the melody note sounding at quarter-position tQl, or -1 in a gap. The melody
  // is non-overlapping and sorted, so [start, start+dur) partitions cleanly.
  function activeIndex(melody, tQl) {
    for (let i = 0; i < melody.length; i++) {
      const s = melody[i].start_ql, e = s + melody[i].dur_ql;
      if (tQl >= s - 1e-9 && tQl < e - 1e-9) return i;
    }
    return -1;
  }

  // Padded MIDI range covering the melody, with a minimum span so a flat tune still
  // gets vertical room (mirrors manual.js drawStrip).
  function pitchRange(melody, pad, minSpan) {
    pad = pad == null ? 2 : pad;
    minSpan = minSpan == null ? 6 : minSpan;
    if (!melody || !melody.length) return { lo: 55, hi: 67 };
    let lo = Infinity, hi = -Infinity;
    for (const n of melody) { lo = Math.min(lo, n.midi); hi = Math.max(hi, n.midi); }
    lo = Math.floor(lo) - pad; hi = Math.ceil(hi) + pad;
    if (hi - lo < minSpan) { const mid = (hi + lo) / 2; lo = mid - minSpan / 2; hi = mid + minSpan / 2; }
    return { lo, hi };
  }

  // Quarter-notes in one bar of the given time signature, e.g. [6,8] -> 3.
  function barQl(timeSig) {
    const ts = timeSig || [4, 4];
    return ts[0] * (4 / ts[1]);
  }

  // In-tune band (cents) for a difficulty level name, defaulting to Normal for anything unknown.
  function bandForDifficulty(name) {
    return Object.prototype.hasOwnProperty.call(DIFFICULTY_BANDS, name)
      ? DIFFICULTY_BANDS[name] : DIFFICULTY_BANDS[DEFAULT_DIFFICULTY];
  }

  // Note verdict from its in-tune fraction (0..1).
  function verdict(note) {
    const h = note.hitPct == null ? 0 : note.hitPct;
    if (h >= 0.8) return "good";
    if (h >= 0.5) return "ok";
    return "miss";
  }

  // Geometry for the scrolling lane at playhead time tQl. Returns on-screen note rects
  // plus the pitch range and the playhead x, so the controller can place the live trail
  // in the same coordinate system.
  function laneLayout(melody, tQl, opts) {
    const width = opts.width, height = opts.height, pxPerQl = opts.pxPerQl;
    const frac = opts.playheadFrac == null ? PLAYHEAD_FRAC : opts.playheadFrac;
    const range = pitchRange(melody, opts.pad, opts.minSpan);
    const lo = range.lo, hi = range.hi;
    const playheadX = width * frac;
    const xOf = (q) => playheadX + (q - tQl) * pxPerQl;
    const yOf = (m) => height * (1 - (Math.max(lo, Math.min(hi, m)) - lo) / (hi - lo));
    const active = activeIndex(melody, tQl);
    const rects = [];
    for (let i = 0; i < melody.length; i++) {
      const n = melody[i];
      const x = xOf(n.start_ql), w = Math.max(2, n.dur_ql * pxPerQl);
      if (x + w < 0 || x > width) continue;   // fully off-screen
      rects.push({ i, x, w, y: yOf(n.midi), midi: n.midi, active: i === active });
    }
    return { rects, lo, hi, playheadX };
  }

  // Score a captured take against the melody. `frames` carry musical time in seconds from
  // song start (already latency-adjusted) and midiFloat (null = unvoiced). Per note we
  // measure the in-tune fraction of its window (silence counts against it) and the mean
  // absolute cents while voicing; notes are graded good/ok/miss. Notes past stopQl (where a
  // manual Stop landed) are left unscored. Pure: re-run it to re-score with flipped toggles.
  function scoreTake(melody, frames, opts) {
    const bpm = opts.bpm, bandCents = opts.bandCents, graceSec = opts.graceSec;
    const octaveAgnostic = opts.octaveAgnostic;
    const stopQl = opts.stopQl == null ? Infinity : opts.stopQl;
    const spb = 60 / Math.max(bpm, 1e-6);

    const acc = melody.map((n) => ({
      eligible: 0, voiced: 0, inBand: 0, absSum: 0,
      reached: n.start_ql < stopQl - 1e-9,
    }));

    for (const fr of frames) {
      if (fr.t < 0) continue;                    // count-in / pre-roll frame
      const ql = fr.t / spb;
      const idx = activeIndex(melody, ql);
      if (idx < 0) continue;                      // in a gap between notes
      const a = acc[idx];
      if (!a.reached) continue;
      if (fr.t - melody[idx].start_ql * spb < graceSec) continue;  // onset glide grace
      a.eligible++;
      if (fr.midiFloat == null) continue;         // silent within the note
      a.voiced++;
      const c = Math.abs(foldCents(fr.midiFloat, melody[idx].midi, octaveAgnostic));
      a.absSum += c;
      if (c <= bandCents) a.inBand++;
    }

    let totEl = 0, totVoiced = 0, totBand = 0, totAbs = 0, notesGood = 0, scoredNotes = 0;
    const perNote = melody.map((n, i) => {
      const a = acc[i];
      const hitPct = a.eligible ? a.inBand / a.eligible : 0;
      const voicedPct = a.eligible ? a.voiced / a.eligible : 0;
      const meanAbsCents = a.voiced ? a.absSum / a.voiced : null;
      const scored = a.reached && a.eligible > 0;
      const out = {
        midi: n.midi, start_ql: n.start_ql, dur_ql: n.dur_ql,
        hitPct, voicedPct, meanAbsCents, scored,
      };
      out.verdict = scored ? verdict(out) : "unscored";
      if (scored) {
        scoredNotes++;
        totEl += a.eligible; totVoiced += a.voiced; totBand += a.inBand; totAbs += a.absSum;
        if (out.verdict === "good") notesGood++;
      }
      return out;
    });

    return {
      inTunePct: totEl ? totBand / totEl : 0,
      meanAbsCents: totVoiced ? totAbs / totVoiced : null,
      voicedPct: totEl ? totVoiced / totEl : 0,
      notesGood,
      notesTotal: melody.length,
      scoredNotes,
      perNote,
    };
  }

  const api = { foldCents, activeIndex, pitchRange, barQl, verdict, laneLayout, scoreTake, midiName,
    bandForDifficulty, DIFFICULTY_BANDS, DEFAULT_DIFFICULTY,
    BAND_CENTS_STRICT, BAND_CENTS_NORMAL, BAND_CENTS_LENIENT, BAND_CENTS_TONE_DEAF,
    ONSET_GRACE_S, ANALYSIS_LATENCY_S, PLAYHEAD_FRAC };

  // ---- controller (browser only) --------------------------------------------

  function createSingalong() {
    const $ = (id) => document.getElementById(id);
    const refs = {
      source: $("saSource"), drop: $("saDrop"), file: $("saFile"),
      summary: $("saSummary"), warn: $("saWarn"), sheet: $("saSheet"),
      panel: $("saPanel"), preview: $("saPreview"), start: $("saStart"),
      pause: $("saPause"), volume: $("saVolume"), volumeOut: $("saVolumeOut"),
      octave: $("saOctave"), difficulty: $("saDifficulty"), status: $("saStatus"),
      lane: $("saLane"), readout: $("saReadout"),
      results: $("saResults"), scoreSummary: $("saScoreSummary"), overview: $("saOverview"),
    };

    const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
    const hide = (el, yes) => { if (el) el.hidden = yes; };
    const setStatus = (t) => { if (refs.status) refs.status.textContent = t || ""; };
    const RTapi = (typeof window !== "undefined" && window.RT) ? window.RT : null;

    let ref = null;          // last /api/reference-melody payload (melody + meta)
    let file = null;         // uploaded File
    let reqToken = 0;        // drops stale upload responses

    // playback / capture handles
    let ctx = null;          // shared AudioContext
    let master = null;       // guide-piano gain that every scheduled note runs through, ducked on stop
    let stream = null, analyser = null, buf = null;
    let raf = null;
    let running = null;      // null | "preview" | "sing"
    let paused = false;      // playback is suspended (ctx.suspend) but not torn down
    let starting = false;    // begin() is between its awaits (mic / piano) and going live
    let gen = 0;             // bumped by every begin/teardown, so a superseded begin bails
    let songStart = 0;       // ctx time at melody position 0 (after the count-in)
    let songEnd = 0;         // ctx time the last note (plus tail) finishes
    let lastDetect = 0;
    let frames = [];         // captured take: [{t (musical s, latency-adj), midiFloat|null}]

    const spb = () => 60 / Math.max((ref && ref.tempo_bpm) || 100, 1e-6);
    const octaveAgnostic = () => !(refs.octave && refs.octave.checked);   // default: agnostic
    const bandCents = () => bandForDifficulty(refs.difficulty ? refs.difficulty.value : DEFAULT_DIFFICULTY);

    // Guide-piano loudness. The slider is 0..100%; full scale maps to VOL_MAX_GAIN on the
    // master gain (well above the old fixed 0.8 - the guide was too quiet), which multiplies
    // each note's 0.3 sample peak. A lone melody note stays clear of clipping even at max.
    const VOL_MAX_GAIN = 2.4;
    const currentVolumeGain = () => {
      const pct = refs.volume ? Number(refs.volume.value) : 65;
      return (Math.max(0, Math.min(100, pct)) / 100) * VOL_MAX_GAIN;
    };

    // ---- upload -------------------------------------------------------------

    function loadFile(f) {
      if (!f) return;
      stopAll();
      file = f;
      ref = null;
      frames = [];   // drop any retained take so a toggle can't re-score against the old file
      hide(refs.panel, true);
      hide(refs.results, true);
      setSourceStatus(`Loading ${f.name}...`);
      const fd = new FormData();
      fd.append("file", f, f.name);
      const token = ++reqToken;
      fetch("/api/reference-melody", { method: "POST", body: fd })
        .then(async (r) => { if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText); return r.json(); })
        .then((data) => {
          if (token !== reqToken) return;
          ref = data;
          setSourceStatus(`Loaded ${f.name}.`);
          renderSummary();
          if (refs.sheet) refs.sheet.innerHTML = data.svg || "<p class='hint'>Preview unavailable for this file.</p>";
          hide(refs.panel, false);
          if (refs.preview) refs.preview.disabled = false;
          if (refs.start) refs.start.disabled = !RTapi;
          setStatus(RTapi ? "ready" : "sing-along needs the pitch detector; preview only");
          drawLane(-barQl(ref.time_sig));   // show the opening, one bar of lead-in
        })
        .catch((e) => {
          if (token !== reqToken) return;
          setSourceStatus(`Could not read this file: ${e.message}`);
        });
    }

    function setSourceStatus(t) { if (refs.source) { const el = $("saSourceStatus"); if (el) el.textContent = t || ""; } }

    function renderSummary() {
      if (!refs.summary || !ref) return;
      const stat = (val, lbl) => `<div class="stat"><span class="val">${val}</span><span class="lbl">${lbl}</span></div>`;
      refs.summary.innerHTML =
        stat(ref.key || "-", "Key") +
        stat(ref.tempo_bpm ? Math.round(ref.tempo_bpm) : "-", "BPM") +
        stat(ref.n_notes, "Melody notes") +
        stat(`${ref.time_sig[0]}/${ref.time_sig[1]}`, "Time");
      const multi = (ref.n_tempos || 0) > 1;
      hide(refs.warn, !multi);
      if (multi && refs.warn) {
        refs.warn.textContent = `This score has ${ref.n_tempos} tempo changes. Sing-Along assumes a ` +
          `constant tempo and follows the first (${Math.round(ref.tempo_bpm)} BPM), so later sections may drift.`;
      }
    }

    // ---- scheduling (the MIDI drives the clock) -----------------------------

    // Schedule the count-in clicks and every guide-piano note at absolute ctx times, then
    // start the render/capture loop. Shared by Preview and Sing.
    async function begin(mode) {
      if (running || starting) { if (running) stopAll(); return; }
      if (!ref || !ref.melody || !ref.melody.length) return;
      if (typeof ensureAudio !== "function") { setStatus("audio unavailable"); return; }
      ctx = ensureAudio();
      frames = [];
      starting = true;
      const myGen = ++gen;   // any teardown or new begin bumps gen and supersedes this one
      setStatus(mode === "sing" ? "loading voices..." : "loading...");

      if (mode === "sing") {
        const ok = await openMic();
        if (myGen !== gen) { killMic(); starting = false; return; }   // superseded mid-await
        if (!ok) { mode = "preview"; setStatus("mic unavailable - preview only"); }
      }
      if (typeof loadPiano === "function") { try { await loadPiano(ctx); } catch (e) { /* synth fallback */ } }
      if (myGen !== gen || !ref) { killMic(); starting = false; return; }   // torn down while awaiting
      starting = false;

      const s = spb();
      const beats = ref.time_sig[0];
      const beatQl = 4 / ref.time_sig[1];
      const t0 = ctx.currentTime + 0.15;
      // One bar of count-in.
      if (typeof click === "function") {
        for (let b = 0; b < beats; b++) click(t0 + b * beatQl * s, b === 0);
      }
      songStart = t0 + beats * beatQl * s;

      master = ctx.createGain();
      master.gain.value = currentVolumeGain();
      master.connect(ctx.destination);
      songEnd = songStart;
      if (typeof sampleVoice === "function") {
        for (const n of ref.melody) {
          const start = songStart + n.start_ql * s;
          const dur = Math.max(0.08, n.dur_ql * s * 0.95);
          sampleVoice(ctx, master, n.midi, start, dur, 0.3);
          songEnd = Math.max(songEnd, start + n.dur_ql * s);
        }
      }
      songEnd += 1.0;   // tail so the last note is not cut before the loop auto-stops

      running = mode;
      paused = false;
      setButtons();
      setStatus(mode === "sing" ? "sing along!" : "playing...");
      lastDetect = 0;
      tick();
    }

    async function openMic() {
      if (!RTapi || typeof navigator === "undefined" || !navigator.mediaDevices) return false;
      const constraints = (typeof RAW_MIC !== "undefined") ? RAW_MIC
        : { audio: { noiseSuppression: false, echoCancellation: false, autoGainControl: false } };
      try {
        stream = await navigator.mediaDevices.getUserMedia(constraints);
        const srcNode = ctx.createMediaStreamSource(stream);
        analyser = ctx.createAnalyser();
        analyser.fftSize = 8192;          // long window: low notes stay accurate (like Realtime)
        buf = new Float32Array(analyser.fftSize);
        srcNode.connect(analyser);
        return true;
      } catch (e) {
        stream = null; analyser = null;
        return false;
      }
    }

    // ---- the render + capture loop ------------------------------------------

    function tick() {
      raf = requestAnimationFrame(tick);
      if (!ctx) return;
      if (paused) return;   // ctx is suspended: the clock is frozen, capture and draw nothing
      const tSec = ctx.currentTime - songStart;
      const tQl = tSec / spb();

      if (running === "sing" && analyser) {
        const now = performance.now();
        if (now - lastDetect >= 30) {         // ~33 Hz analysis, matching the Realtime tab
          lastDetect = now;
          analyser.getFloatTimeDomainData(buf);
          const p = RTapi.detectPitch(buf, ctx.sampleRate);
          const midiFloat = p ? RTapi.hzToNote(p.hz).midiFloat : null;
          frames.push({ t: tSec - ANALYSIS_LATENCY_S, midiFloat });
        }
      }

      drawLane(tQl);
      updateReadout(tQl);

      if (ctx.currentTime >= songEnd) finish(false);
    }

    function drawLane(tQl) {
      const cv = refs.lane;
      if (!cv || !ref) return;
      const g = cv.getContext("2d");
      const W = cv.width, H = cv.height;
      g.clearRect(0, 0, W, H);
      const pxPerQl = (W / WINDOW_SEC) * spb();
      const lay = laneLayout(ref.melody, tQl, { width: W, height: H, pxPerQl });
      const yOf = (m) => H * (1 - (Math.max(lay.lo, Math.min(lay.hi, m)) - lay.lo) / (lay.hi - lay.lo));

      // Octave gridlines.
      g.strokeStyle = cssVar("--border"); g.lineWidth = 1;
      for (let m = Math.ceil(lay.lo / 12) * 12; m <= lay.hi; m += 12) {
        const y = Math.round(yOf(m)) + 0.5;
        g.beginPath(); g.moveTo(0, y); g.lineTo(W, y); g.stroke();
      }

      // The in-tune band around the active note (borrowed from Realtime's drawGraph).
      const active = activeIndex(ref.melody, tQl);
      if (active >= 0) {
        const am = ref.melody[active].midi, band = bandCents() / 100;
        const yHi = yOf(am + band), yLo = yOf(am - band);
        g.save(); g.globalAlpha = 0.16; g.fillStyle = cssVar("--accent");
        g.fillRect(0, Math.min(yHi, yLo), W, Math.max(2, Math.abs(yLo - yHi)));
        g.restore();
      }

      // Note bars: upcoming muted, the active one accented.
      for (const r of lay.rects) {
        g.globalAlpha = r.active ? 1 : 0.5;
        g.fillStyle = r.active ? cssVar("--accent") : cssVar("--muted");
        g.fillRect(r.x, r.y - 4, r.w, 8);
      }
      g.globalAlpha = 1;

      // Playhead.
      g.strokeStyle = cssVar("--danger"); g.lineWidth = 1.5;
      g.beginPath(); g.moveTo(lay.playheadX + 0.5, 0); g.lineTo(lay.playheadX + 0.5, H); g.stroke();

      // Live sung trail, octave-folded onto the target, broken over unvoiced gaps.
      if (running === "sing" && frames.length) {
        const s = spb();
        g.strokeStyle = cssVar("--ink"); g.lineWidth = 2; g.lineJoin = "round";
        g.beginPath();
        let pen = false;
        for (const fr of frames) {
          if (fr.midiFloat == null) { pen = false; continue; }
          const fq = fr.t / s;
          const x = lay.playheadX + (fq - tQl) * pxPerQl;
          if (x < -20 || x > W + 20) { pen = false; continue; }
          const idx = activeIndex(ref.melody, fq);
          const disp = idx >= 0
            ? ref.melody[idx].midi + foldCents(fr.midiFloat, ref.melody[idx].midi, octaveAgnostic()) / 100
            : fr.midiFloat;
          const y = yOf(disp);
          if (!pen) { g.moveTo(x, y); pen = true; } else g.lineTo(x, y);
        }
        g.stroke();
      }
    }

    function updateReadout(tQl) {
      if (!refs.readout || !ref) return;
      if (tQl < 0) { refs.readout.textContent = "count-in..."; return; }
      const idx = activeIndex(ref.melody, tQl);
      if (idx < 0) { refs.readout.textContent = "rest"; return; }
      const target = ref.melody[idx].midi;
      let msg = `Target ${midiName(target)}`;
      if (running === "sing") {
        const last = frames.length ? frames[frames.length - 1] : null;
        if (last && last.midiFloat != null) {
          const c = Math.round(foldCents(last.midiFloat, target, octaveAgnostic()));
          const inBand = Math.abs(c) <= bandCents();
          msg += ` - you ${c > 0 ? "+" : ""}${c}c ${inBand ? "(in tune)" : ""}`;
        }
      }
      refs.readout.textContent = msg;
    }

    // ---- pause / resume -----------------------------------------------------

    // Suspend/resume the AudioContext. Because every click and note is scheduled at an
    // absolute ctx time and ctx.currentTime is the playhead, suspending freezes the whole
    // performance (audio, clock, capture) and resuming continues it seamlessly - the pause
    // gap shifts every future event forward together, so timing and scoring stay aligned.
    function togglePause() {
      if (!running || !ctx) return;
      if (paused) {
        paused = false;
        try { ctx.resume(); } catch (e) {}
        setStatus(running === "sing" ? "sing along!" : "playing...");
      } else {
        paused = true;
        try { ctx.suspend(); } catch (e) {}
        setStatus("paused");
      }
      setButtons();
    }

    // ---- stop / score -------------------------------------------------------

    function finish(manual) {
      const wasSing = running === "sing";
      const reachedQl = Math.max(0, (ctx ? ctx.currentTime - songStart : 0) / spb());
      const stopQl = manual ? Math.min(reachedQl, ref ? ref.duration_ql : reachedQl) : (ref ? ref.duration_ql : reachedQl);
      teardown();
      if (wasSing && ref && frames.length) {
        score(stopQl);
      } else if (!wasSing) {
        setStatus("stopped");
      } else {
        setStatus("stopped - no voice captured");
      }
    }

    function score(stopQl) {
      const res = scoreTake(ref.melody, frames, {
        bpm: ref.tempo_bpm, bandCents: bandCents(), graceSec: ONSET_GRACE_S,
        octaveAgnostic: octaveAgnostic(), stopQl,
      });
      renderResults(res);
      setStatus(`scored ${res.scoredNotes} of ${res.notesTotal} notes`);
    }

    function renderResults(res) {
      hide(refs.results, false);
      if (refs.scoreSummary) {
        const pct = (x) => `${Math.round(x * 100)}%`;
        const stat = (val, lbl, cls) => `<div class="stat"><span class="val${cls ? " " + cls : ""}">${val}</span><span class="lbl">${lbl}</span></div>`;
        const it = res.inTunePct;
        const cls = it >= 0.8 ? "good" : it >= 0.5 ? "ok" : "miss";
        refs.scoreSummary.innerHTML =
          stat(pct(it), "In tune", cls) +
          stat(res.meanAbsCents == null ? "-" : `${Math.round(res.meanAbsCents)}c`, "Avg off") +
          stat(`${res.notesGood}/${res.scoredNotes}`, "Notes nailed") +
          stat(pct(res.voicedPct), "Voiced");
      }
      drawOverview(res);
    }

    // Whole-song overview: every note laid out across the width, colored by verdict, with
    // the sung trail overlaid. No scrolling - it is a static summary of the take.
    function drawOverview(res) {
      const cv = refs.overview;
      if (!cv || !ref) return;
      const g = cv.getContext("2d");
      const W = cv.width, H = cv.height;
      g.clearRect(0, 0, W, H);
      const dur = Math.max(1e-6, ref.duration_ql);
      const range = pitchRange(ref.melody);
      const yOf = (m) => H * (1 - (Math.max(range.lo, Math.min(range.hi, m)) - range.lo) / (range.hi - range.lo));
      const xOf = (q) => (q / dur) * W;
      const COLOR = { good: cssVar("--accent"), ok: cssVar("--warn"), miss: cssVar("--danger"), unscored: cssVar("--muted") };

      for (const n of res.perNote) {
        const x = xOf(n.start_ql), w = Math.max(2, n.dur_ql / dur * W);
        g.globalAlpha = n.verdict === "unscored" ? 0.35 : 0.9;
        g.fillStyle = COLOR[n.verdict] || cssVar("--muted");
        g.fillRect(x, yOf(n.midi) - 3, w, 6);
      }
      g.globalAlpha = 1;

      // Sung trail across the whole take.
      const s = spb();
      g.strokeStyle = cssVar("--ink"); g.lineWidth = 1.5; g.globalAlpha = 0.7;
      g.beginPath();
      let pen = false;
      for (const fr of frames) {
        if (fr.midiFloat == null || fr.t < 0) { pen = false; continue; }
        const fq = fr.t / s;
        const idx = activeIndex(ref.melody, fq);
        const disp = idx >= 0
          ? ref.melody[idx].midi + foldCents(fr.midiFloat, ref.melody[idx].midi, octaveAgnostic()) / 100
          : fr.midiFloat;
        const x = xOf(fq), y = yOf(disp);
        if (!pen) { g.moveTo(x, y); pen = true; } else g.lineTo(x, y);
      }
      g.stroke();
      g.globalAlpha = 1;
    }

    // ---- teardown -----------------------------------------------------------

    function killMic() {
      if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; }
      analyser = null; buf = null;
    }

    function teardown() {
      gen++;              // supersede any begin() still waiting on a mic / piano await
      starting = false;
      paused = false;
      if (raf) { cancelAnimationFrame(raf); raf = null; }
      if (master) {
        try { master.gain.setTargetAtTime(0.0001, ctx.currentTime, 0.02); } catch (e) {}
        const m = master;
        setTimeout(() => { try { m.disconnect(); } catch (e) {} }, 150);
        master = null;
      }
      // The context is shared with the other tabs; never hand it back suspended.
      if (ctx && ctx.state === "suspended") { try { ctx.resume(); } catch (e) {} }
      killMic();
      running = null;
      setButtons();
      if (refs.readout) refs.readout.textContent = "";
    }

    function stopAll() { if (running) finish(true); else teardown(); }

    function setButtons() {
      if (refs.preview) {
        const on = running === "preview";
        refs.preview.textContent = on ? "■ Stop" : "▶ Preview";
        refs.preview.classList.toggle("playing", on);
        refs.preview.disabled = !ref || running === "sing";
      }
      if (refs.start) {
        const on = running === "sing";
        refs.start.textContent = on ? "■ Stop" : "● Sing along";
        refs.start.classList.toggle("playing", on);
        refs.start.disabled = !ref || !RTapi || running === "preview";
      }
      if (refs.pause) {
        refs.pause.textContent = paused ? "▶ Resume" : "⏸ Pause";
        refs.pause.classList.toggle("playing", paused);
        refs.pause.disabled = !running;
      }
    }

    // ---- wiring -------------------------------------------------------------

    if (refs.file) refs.file.addEventListener("change", (e) => {
      const f = e.target.files && e.target.files[0];
      if (f) loadFile(f);
    });
    if (refs.drop) {
      refs.drop.addEventListener("dragover", (e) => { e.preventDefault(); refs.drop.classList.add("drag"); });
      refs.drop.addEventListener("dragleave", () => refs.drop.classList.remove("drag"));
      refs.drop.addEventListener("drop", (e) => {
        e.preventDefault(); refs.drop.classList.remove("drag");
        const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
        if (f) loadFile(f);
      });
    }
    if (refs.preview) refs.preview.addEventListener("click", () => { running === "preview" ? stopAll() : begin("preview"); });
    if (refs.start) refs.start.addEventListener("click", () => { running === "sing" ? stopAll() : begin("sing"); });
    if (refs.pause) refs.pause.addEventListener("click", togglePause);
    if (refs.volume) refs.volume.addEventListener("input", () => {
      if (refs.volumeOut) refs.volumeOut.textContent = refs.volume.value;
      if (master && ctx && running) {   // live-adjust the take already playing
        try { master.gain.setTargetAtTime(currentVolumeGain(), ctx.currentTime, 0.02); } catch (e) {}
      }
    });
    // Flipping a toggle after a take re-scores the retained frames instantly.
    const reScore = () => { if (!running && ref && frames.length) score(ref.duration_ql); };
    if (refs.octave) refs.octave.addEventListener("change", reScore);
    if (refs.difficulty) refs.difficulty.addEventListener("change", reScore);

    // ---- lifecycle ----------------------------------------------------------

    function enter() { /* nothing to refresh; the tab keeps its loaded score */ }
    function exit() { stopAll(); }   // the single teardown path: releases mic, stops playback

    return { enter, exit, _get: () => ref, _frames: () => frames };
  }

  const out = Object.assign({}, api, { createSingalong });
  return out;
})();

if (typeof window !== "undefined") window.SA = SA;
if (typeof module !== "undefined" && module.exports) module.exports = SA;
