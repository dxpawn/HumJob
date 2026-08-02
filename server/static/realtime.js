"use strict";

// ===== Realtime pitch monitor + guitar tuner ================================
// A self-contained client-side subsystem. Realtime feedback can't survive a
// server round-trip, so pitch detection runs in the browser via Web Audio
// (AnalyserNode + autocorrelation). It reuses the globals defined in app.js:
//   RAW_MIC       — mic constraints with the speech-DSP noise gate turned off
//   ensureAudio() — shared AudioContext (created/resumed on demand)
//   midiToFreq(m) — MIDI note number -> Hz
// The only server call is POST /api/key at the end of a voice take, which reuses
// the same Krumhansl key scorer as the Pitch Finder.

const RT = (() => {
  const $ = (id) => document.getElementById(id);
  const view = $("view-realtime");
  if (!view) return null; // realtime UI not present; nothing to wire

  // ---- elements ----
  const modeEl = $("rtMode");
  const voiceCard = $("rtVoice"), tunerCard = $("rtTuner");
  const voiceBtn = $("rtVoiceBtn"), tunerBtn = $("rtTunerBtn");
  const voiceStatus = $("rtVoiceStatus"), tunerStatus = $("rtTunerStatus");
  const noteEl = $("rtNote"), freqEl = $("rtFreq"), centsEl = $("rtCents");
  const meterEl = $("rtMeter"), needleEl = $("rtNeedle");
  const keyEl = $("rtKey"), canvas = $("pitchGraph");
  const stringsEl = $("strings");
  const tTarget = $("tunerTarget"), tFreq = $("tunerFreq"), tCents = $("tunerCents");
  const tMeter = $("tunerMeter"), tNeedle = $("tunerNeedle");

  const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

  // ---- pure pitch/note math (unit-testable without a mic) -------------------

  // Autocorrelation pitch detector. Returns {hz, clarity, rms} or null when the
  // frame is too quiet / has no clear period (so silence reads as unvoiced).
  function detectPitch(buf, sr) {
    const N = buf.length;
    let rms = 0;
    for (let i = 0; i < N; i++) rms += buf[i] * buf[i];
    rms = Math.sqrt(rms / N);
    if (rms < 0.008) return null; // gate: below this it's silence/noise

    // Trim quiet edges so onset/tail noise doesn't skew the correlation.
    const thres = 0.2;
    let r1 = 0, r2 = N - 1;
    for (let i = 0; i < N >> 1; i++) if (Math.abs(buf[i]) > thres) { r1 = i; break; }
    for (let i = 0; i < N >> 1; i++) if (Math.abs(buf[N - 1 - i]) > thres) { r2 = N - 1 - i; break; }
    const b = buf.subarray(r1, r2 + 1);
    const M = b.length;
    if (M < 128) return null;

    // Only correlate over lags that map to a plausible pitch (50–2000 Hz). The low
    // bound covers the guitar's low E (82 Hz) with headroom; a longer window (see
    // fftSize below) is what makes low notes accurate enough to tune against.
    const minLag = Math.max(1, Math.floor(sr / 2000));
    const maxLag = Math.min(M - 1, Math.floor(sr / 50));
    const c = new Float32Array(maxLag + 2);
    for (let lag = 0; lag <= maxLag + 1 && lag < M; lag++) {
      let sum = 0;
      for (let i = 0; i < M - lag; i++) sum += b[i] * b[i + lag];
      c[lag] = sum;
    }

    // Walk past the initial descent, then take the strongest peak (the period).
    let d = 0;
    while (d < maxLag && c[d] > c[d + 1]) d++;
    const start = Math.max(d, minLag);
    let maxval = -Infinity, maxpos = -1;
    for (let i = start; i <= maxLag; i++) {
      if (c[i] > maxval) { maxval = c[i]; maxpos = i; }
    }
    if (maxpos <= 0) return null;

    // Parabolic interpolation of the peak for sub-sample period accuracy.
    let T = maxpos;
    const x1 = c[T - 1] || 0, x2 = c[T], x3 = c[T + 1] || 0;
    const denom = 2 * (x1 + x3 - 2 * x2);
    if (denom) T += (x1 - x3) / denom;

    const hz = sr / T;
    if (hz < 40 || hz > 2000) return null;
    const clarity = c[0] ? maxval / c[0] : 0;
    if (clarity < 0.5) return null; // weak/ambiguous period -> treat as unvoiced
    return { hz, clarity, rms };
  }

  const hzToMidi = (hz) => 69 + 12 * Math.log2(hz / 440);

  function hzToNote(hz) {
    const m = hzToMidi(hz);
    const r = Math.round(m);
    const pc = ((r % 12) + 12) % 12;
    return {
      name: NOTE_NAMES[pc],
      octave: Math.floor(r / 12) - 1,
      midi: r,
      pc,
      midiFloat: m,
      cents: Math.round((m - r) * 100),
    };
  }

  const centsBetween = (hz, targetHz) => Math.round(1200 * Math.log2(hz / targetHz));
  const noteFreq = (midi) => (typeof midiToFreq === "function"
    ? midiToFreq(midi)
    : 440 * Math.pow(2, (midi - 69) / 12));

  // expose the pure core for in-browser verification
  const api = { detectPitch, hzToNote, centsBetween };

  // ---- shared capture state -------------------------------------------------
  let stream = null, analyser = null, raf = null, buf = null;
  let running = false;       // is capture live?
  let runMode = null;        // "voice" | "tuner" while running
  let visibleMode = "voice"; // which card is shown

  async function start(which) {
    if (running) return;
    const statusEl = which === "voice" ? voiceStatus : tunerStatus;
    try {
      const ctx = ensureAudio();
      stream = await navigator.mediaDevices.getUserMedia(RAW_MIC);
      const src = ctx.createMediaStreamSource(stream);
      analyser = ctx.createAnalyser();
      // 8192 samples (~170–185 ms) holds enough periods of a low note (82 Hz E2)
      // for autocorrelation to resolve it to ~±3 cents — tuner-grade accuracy.
      analyser.fftSize = 8192;
      buf = new Float32Array(analyser.fftSize);
      src.connect(analyser); // tap only — never to destination (no feedback)
      running = true;
      runMode = which;
      if (which === "voice") { resetVoice(); }
      else { inTuneSince = 0; }
      setButtons(true);
      if (statusEl) statusEl.textContent = which === "voice" ? "● listening" : "● listening — play a string";
      if (canvas && canvas.clientWidth) canvas.width = canvas.clientWidth;
      loop(ctx.sampleRate);
    } catch (e) {
      running = false;
      if (statusEl) statusEl.textContent = "mic error: " + e.message;
    }
  }

  function stop() {
    const wasVoice = runMode === "voice";
    const wasRunning = running;
    if (raf) cancelAnimationFrame(raf);
    raf = null;
    if (stream) stream.getTracks().forEach((t) => t.stop());
    stream = null; analyser = null;
    running = false;
    setButtons(false);
    setNeedle(needleEl, meterEl, null);
    setNeedle(tNeedle, tMeter, null);
    if (voiceStatus && wasVoice) voiceStatus.textContent = "stopped";
    if (tunerStatus && !wasVoice) tunerStatus.textContent = "stopped";
    if (wasVoice && wasRunning && histFrames > 20) fetchKey();
  }

  let lastDetect = 0;
  function loop(sr) {
    raf = requestAnimationFrame(() => loop(sr));
    if (!analyser) return;
    const now = performance.now();
    if (now - lastDetect < 30) return; // ~33 Hz analysis: smooth, well below rAF cost
    lastDetect = now;
    analyser.getFloatTimeDomainData(buf);
    const p = detectPitch(buf, sr);
    if (runMode === "voice") updateVoice(p);
    else updateTuner(p);
  }

  function setButtons(on) {
    if (voiceBtn) {
      const v = on && runMode === "voice";
      voiceBtn.textContent = v ? "■ Stop" : "● Start monitoring";
      voiceBtn.classList.toggle("playing", v);
    }
    if (tunerBtn) {
      const t = on && runMode === "tuner";
      tunerBtn.textContent = t ? "■ Stop" : "● Start tuner";
      tunerBtn.classList.toggle("playing", t);
    }
  }

  // Position the ±50¢ needle (null = no reading -> dim + centred).
  function setNeedle(needle, meter, cents) {
    if (!needle) return;
    if (cents == null) {
      needle.style.opacity = "0.25";
      needle.style.left = "50%";
      if (meter) meter.classList.remove("intune");
      return;
    }
    needle.style.opacity = "1";
    const clamped = Math.max(-50, Math.min(50, cents));
    needle.style.left = (50 + clamped) + "%";
    if (meter) meter.classList.toggle("intune", Math.abs(cents) <= 5);
  }

  // ---- voice monitor --------------------------------------------------------
  const GRAPH_LEN = 240;
  let graph = [];             // recent midiFloat samples (NaN = unvoiced)
  const hist = new Array(12).fill(0);
  let histFrames = 0;
  const liveStyle = getComputedStyle(document.documentElement);
  const cssVar = (n) => (liveStyle.getPropertyValue(n).trim() || "#888");

  function resetVoice() {
    graph = [];
    hist.fill(0);
    histFrames = 0;
    if (keyEl) keyEl.classList.add("hidden");
    drawGraph();
  }

  function updateVoice(p) {
    if (p) {
      const n = hzToNote(p.hz);
      if (noteEl) noteEl.textContent = n.name + n.octave;
      if (freqEl) freqEl.textContent = p.hz.toFixed(1) + " Hz";
      if (centsEl) centsEl.textContent = (n.cents > 0 ? "+" : "") + n.cents + "¢";
      setNeedle(needleEl, meterEl, n.cents);
      graph.push(n.midiFloat);
      hist[n.pc] += 1;
      histFrames++;
    } else {
      if (noteEl) noteEl.textContent = "—";
      if (freqEl) freqEl.textContent = "— Hz";
      if (centsEl) centsEl.textContent = "—";
      setNeedle(needleEl, meterEl, null);
      graph.push(NaN);
    }
    if (graph.length > GRAPH_LEN) graph.shift();
    drawGraph();
  }

  function drawGraph() {
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const W = canvas.width, H = canvas.height;
    ctx.clearRect(0, 0, W, H);
    const LO = 40, HI = 88; // E2..E6 covers voice + guitar
    const yOf = (m) => H * (1 - (Math.max(LO, Math.min(HI, m)) - LO) / (HI - LO));

    ctx.strokeStyle = cssVar("--border");
    ctx.lineWidth = 1;
    for (let m = LO; m <= HI; m += 12) {
      const y = Math.round(yOf(m)) + 0.5;
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
    }

    ctx.strokeStyle = cssVar("--accent");
    ctx.lineWidth = 2;
    ctx.lineJoin = "round";
    ctx.beginPath();
    let pen = false;
    for (let i = 0; i < graph.length; i++) {
      const m = graph[i];
      const x = W * (i / (GRAPH_LEN - 1));
      if (Number.isNaN(m)) { pen = false; continue; }
      const y = yOf(m);
      if (!pen) { ctx.moveTo(x, y); pen = true; } else ctx.lineTo(x, y);
    }
    ctx.stroke();
  }

  async function fetchKey() {
    try {
      const res = await fetch("/api/key", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ histogram: hist }),
      });
      if (!res.ok) return;
      const d = await res.json();
      if (keyEl) {
        keyEl.textContent = `🎼 Key of what you sang: ${d.key} · ${d.camelot}`;
        keyEl.classList.remove("hidden");
      }
    } catch (e) { /* offline / no result — leave the readout as-is */ }
  }

  // ---- guitar tuner ---------------------------------------------------------
  // Standard tuning, thickest (low E, 6th) -> thinnest (high E, 1st).
  const STRINGS = [
    { n: 6, name: "E2", midi: 40 },
    { n: 5, name: "A2", midi: 45 },
    { n: 4, name: "D3", midi: 50 },
    { n: 3, name: "G3", midi: 55 },
    { n: 2, name: "B3", midi: 59 },
    { n: 1, name: "E4", midi: 64 },
  ];
  let curString = 0;
  const done = new Set();
  let inTuneSince = 0;

  function buildStrings() {
    if (!stringsEl) return;
    stringsEl.replaceChildren();
    STRINGS.forEach((s, i) => {
      const b = document.createElement("button");
      b.className = "string-chip" + (i === curString ? " active" : "") + (done.has(i) ? " done" : "");
      b.innerHTML = `<span class="s-name">${s.name}</span><span class="s-num">${done.has(i) ? "✓" : "str " + s.n}</span>`;
      // Tap a string to (re)tune it — clears its done mark and makes it the target.
      b.addEventListener("click", () => { done.delete(i); curString = i; inTuneSince = 0; buildStrings(); });
      stringsEl.appendChild(b);
    });
  }

  function updateTuner(p) {
    if (done.size >= STRINGS.length) return; // all tuned — hold the "done" readout
    const target = STRINGS[curString];
    const targetHz = noteFreq(target.midi);
    if (tTarget) tTarget.textContent = `${target.name} (string ${target.n})`;
    if (!p) {
      if (tFreq) tFreq.textContent = "— Hz";
      if (tCents) tCents.textContent = "play the " + target.name + " string";
      setNeedle(tNeedle, tMeter, null);
      inTuneSince = 0;
      return;
    }
    if (tFreq) tFreq.textContent = p.hz.toFixed(1) + " Hz";
    const cents = centsBetween(p.hz, targetHz);
    setNeedle(tNeedle, tMeter, cents);
    if (Math.abs(cents) <= 5) {
      if (tCents) tCents.textContent = "in tune ✓";
      if (!inTuneSince) inTuneSince = performance.now();
      else if (performance.now() - inTuneSince > 900) markDone();
    } else if (Math.abs(cents) <= 60) {
      inTuneSince = 0;
      if (tCents) tCents.textContent = `${Math.abs(cents)}¢ ${cents > 0 ? "tune down ▼" : "tune up ▲"}`;
    } else {
      inTuneSince = 0;
      const n = hzToNote(p.hz);
      if (tCents) tCents.textContent = `heard ${n.name}${n.octave} — play the ${target.name} string`;
    }
  }

  function markDone() {
    done.add(curString);
    inTuneSince = 0;
    if (done.size >= STRINGS.length) {
      buildStrings();
      if (tCents) tCents.textContent = "All strings in tune ✓ 🎉";
      if (tTarget) tTarget.textContent = "Done";
      return;
    }
    for (let k = 1; k <= STRINGS.length; k++) {
      const cand = (curString + k) % STRINGS.length;
      if (!done.has(cand)) { curString = cand; break; }
    }
    buildStrings();
  }

  // ---- wiring ---------------------------------------------------------------
  if (voiceBtn) voiceBtn.addEventListener("click", () => (running ? stop() : start("voice")));
  if (tunerBtn) tunerBtn.addEventListener("click", () => (running ? stop() : start("tuner")));

  if (modeEl) modeEl.addEventListener("click", (e) => {
    const btn = e.target.closest(".seg-btn");
    if (!btn) return;
    const mode = btn.dataset.mode;
    if (mode === visibleMode) return;
    if (running) stop();
    visibleMode = mode;
    for (const b of modeEl.querySelectorAll(".seg-btn")) b.classList.toggle("active", b === btn);
    if (voiceCard) voiceCard.classList.toggle("hidden", mode !== "voice");
    if (tunerCard) tunerCard.classList.toggle("hidden", mode !== "tuner");
  });

  // Leaving the Realtime tab must release the mic (no hot mic left running).
  const tabsEl = document.getElementById("tabs");
  if (tabsEl) tabsEl.addEventListener("click", (e) => {
    const btn = e.target.closest(".tab");
    if (!btn || btn.disabled) return;
    if (btn.dataset.view !== "realtime" && running) stop();
  });

  buildStrings();
  return api;
})();

// Exposed for verification: window.RT.detectPitch(float32, sampleRate), etc.
if (typeof window !== "undefined") window.RT = RT;
