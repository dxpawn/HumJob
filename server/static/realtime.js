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
  const meterEl = $("rtMeter"), needleEl = $("rtNeedle"), holdEl = $("rtHold");
  const keyEl = $("rtKey"), canvas = $("pitchGraph"), targetEl = $("rtTarget");
  const circleEl = $("rtCircle"), targetBtn = $("rtTargetBtn"), targetLabel = $("rtTargetLabel");
  const stringsEl = $("strings");
  const tTarget = $("tunerTarget"), tFreq = $("tunerFreq"), tCents = $("tunerCents");
  const tMeter = $("tunerMeter"), tNeedle = $("tunerNeedle");
  const tgtLabel = $("rtTgtLabel"), tgtUp = $("rtTgtUp"), tgtDown = $("rtTgtDown"), tgtClear = $("rtTgtClear");
  const refBtn = $("rtRef"), metricsEl = $("rtMetrics");

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
  const GRAPH_LO = 40, GRAPH_HI = 88; // MIDI range drawn on the pitch graph (E2..E6)
  const BAND_CENTS = 15;              // half-width of the in-tune zone around a target
  let graph = [];             // recent midiFloat samples (NaN = unvoiced)
  const hist = new Array(12).fill(0);
  let histFrames = 0;
  let centsSum = 0, centsFrames = 0; // running mean of fine intonation (cents vs equal temperament)
  const liveStyle = getComputedStyle(document.documentElement);
  const cssVar = (n) => (liveStyle.getPropertyValue(n).trim() || "#888");

  // ---- vocal-practice state + helpers (Phase A) -----------------------------
  let targetMidi = null;              // integer MIDI to practise against, or null
  let recentMidi = [];                // recent voiced midiFloat samples, for steadiness
  let sustainMs = 0, bestSustainMs = 0; // current + best in-tune hold this take
  let lastFrameT = 0, silentFrames = 0;

  // Pure: map a graph y-pixel to the nearest semitone in the graph's MIDI range.
  function midiFromGraphY(y, H) {
    const m = GRAPH_LO + (1 - y / H) * (GRAPH_HI - GRAPH_LO);
    return Math.max(GRAPH_LO, Math.min(GRAPH_HI, Math.round(m)));
  }
  // Pure: pitch steadiness as the standard deviation (in cents) of a run of
  // midiFloat samples. null when there is too little to measure.
  function stdevCents(midiArr) {
    const a = (midiArr || []).filter((v) => Number.isFinite(v));
    if (a.length < 2) return null;
    const mean = a.reduce((s, v) => s + v, 0) / a.length;
    const varc = a.reduce((s, v) => s + (v - mean) * (v - mean), 0) / a.length;
    return Math.sqrt(varc) * 100; // semitone stdev -> cents
  }
  const midiName = (midi) => NOTE_NAMES[((midi % 12) + 12) % 12] + (Math.floor(midi / 12) - 1);

  // Set (or clear, with null) the practice target; refresh label, drone and graph.
  function setTargetMidi(midi) {
    targetMidi = midi == null ? null
      : Math.max(GRAPH_LO, Math.min(GRAPH_HI, Math.round(midi)));
    if (tgtLabel) tgtLabel.textContent = targetMidi == null ? "off" : midiName(targetMidi);
    if (refBtn) refBtn.disabled = targetMidi == null;
    stopReference();
    drawGraph();
  }

  // Reference note: a short pitch-pipe tone at the target, so the singer can match
  // by ear. It plays for REF_TONE_S and stops on its own (a sustained drone was
  // annoying); press again to replay. Independent of the mic.
  const REF_TONE_S = 2.0;
  let refOsc = null, refGain = null;
  function playReference() {
    if (targetMidi == null) return;
    stopReference();                 // restart cleanly if pressed again
    const ctx = ensureAudio();
    const t0 = ctx.currentTime;
    const osc = ctx.createOscillator();
    const g = ctx.createGain();
    osc.type = "triangle";
    osc.frequency.value = noteFreq(targetMidi);
    g.gain.setValueAtTime(0, t0);
    g.gain.linearRampToValueAtTime(0.09, t0 + 0.03);            // fade in
    g.gain.setValueAtTime(0.09, t0 + REF_TONE_S - 0.12);        // hold
    g.gain.linearRampToValueAtTime(0, t0 + REF_TONE_S);         // fade out
    osc.connect(g).connect(ctx.destination);
    osc.onended = () => { if (refOsc === osc) { refOsc = null; refGain = null; } };
    osc.start(t0);
    osc.stop(t0 + REF_TONE_S + 0.02);
    refOsc = osc; refGain = g;
  }
  function stopReference() {
    if (!refOsc) return;
    const ctx = ensureAudio();
    const osc = refOsc, g = refGain;
    refOsc = null; refGain = null;   // clear first so the old onended guard is false
    try {
      g.gain.cancelScheduledValues(ctx.currentTime);
      g.gain.setValueAtTime(g.gain.value, ctx.currentTime);
      g.gain.linearRampToValueAtTime(0, ctx.currentTime + 0.05);
      osc.stop(ctx.currentTime + 0.08);
    } catch (e) { try { osc.stop(); } catch (_) {} }
  }

  // Live steadiness + in-tune sustain readout.
  function updateMetrics() {
    if (!metricsEl) return;
    const parts = [];
    const sd = stdevCents(recentMidi);
    if (sd != null) parts.push(`Steady: ${Math.round(sd)}¢`);
    if (bestSustainMs > 0) {
      parts.push(`Held in tune: ${(sustainMs / 1000).toFixed(1)}s (best ${(bestSustainMs / 1000).toFixed(1)}s)`);
    }
    metricsEl.textContent = parts.join("   |   ");
  }

  function resetVoice() {
    graph = [];
    hist.fill(0);
    histFrames = 0;
    centsSum = 0; centsFrames = 0;
    lastKeyData = null;
    recentMidi = [];
    sustainMs = 0; bestSustainMs = 0; lastFrameT = 0; silentFrames = 0;
    updateMetrics();
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
      centsSum += n.cents; centsFrames++;

      // practice metrics: steadiness window + in-tune sustain timer
      const now = performance.now();
      const dtMs = lastFrameT ? Math.min(200, now - lastFrameT) : 0;
      lastFrameT = now; silentFrames = 0;
      recentMidi.push(n.midiFloat);
      if (recentMidi.length > 16) recentMidi.shift();
      const ref = targetMidi != null ? targetMidi : Math.round(n.midiFloat);
      if (Math.abs(n.midiFloat - ref) * 100 <= BAND_CENTS) {
        sustainMs += dtMs;
        if (sustainMs > bestSustainMs) bestSustainMs = sustainMs;
      } else {
        sustainMs = 0;
      }
      updateMetrics();
    } else {
      // "Hold last pitch" keeps the readout frozen at the last voiced note instead
      // of blanking to "—". The graph is unaffected: it still gets a gap (NaN).
      if (!(holdEl && holdEl.checked)) {
        if (noteEl) noteEl.textContent = "—";
        if (freqEl) freqEl.textContent = "— Hz";
        if (centsEl) centsEl.textContent = "—";
        setNeedle(needleEl, meterEl, null);
      }
      graph.push(NaN);
      sustainMs = 0; lastFrameT = 0;
      if (++silentFrames > 8) recentMidi = [];
      updateMetrics();
    }
    if (graph.length > GRAPH_LEN) graph.shift();
    drawGraph();
  }

  function drawGraph() {
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const W = canvas.width, H = canvas.height;
    ctx.clearRect(0, 0, W, H);
    const LO = GRAPH_LO, HI = GRAPH_HI; // E2..E6 covers voice + guitar
    const yOf = (m) => H * (1 - (Math.max(LO, Math.min(HI, m)) - LO) / (HI - LO));

    ctx.strokeStyle = cssVar("--border");
    ctx.lineWidth = 1;
    for (let m = LO; m <= HI; m += 12) {
      const y = Math.round(yOf(m)) + 0.5;
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
    }

    // Practice target: a shaded in-tune band and a dashed line at the target note.
    if (targetMidi != null) {
      const yHi = yOf(targetMidi + BAND_CENTS / 100);
      const yLo = yOf(targetMidi - BAND_CENTS / 100);
      ctx.save();
      ctx.globalAlpha = 0.16;
      ctx.fillStyle = cssVar("--accent");
      ctx.fillRect(0, Math.min(yHi, yLo), W, Math.max(2, Math.abs(yLo - yHi)));
      ctx.restore();
      const yt = Math.round(yOf(targetMidi)) + 0.5;
      ctx.strokeStyle = cssVar("--accent");
      ctx.lineWidth = 1.5;
      ctx.setLineDash([6, 4]);
      ctx.beginPath(); ctx.moveTo(0, yt); ctx.lineTo(W, yt); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = cssVar("--accent");
      ctx.font = "600 11px system-ui, sans-serif";
      ctx.fillText(midiName(targetMidi), 4, yt - 4);
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

  let lastKeyData = null; // last /api/key result, so picking a target re-renders

  async function fetchKey() {
    try {
      const res = await fetch("/api/key", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ histogram: hist }),
      });
      if (!res.ok) return;
      lastKeyData = await res.json();
      renderKey();
    } catch (e) { /* offline / no result, leave the readout as-is */ }
  }

  // Render the on-stop key readout, plus how far off the target it was (if set).
  // Split from the fetch so choosing a target after Stop refreshes without a re-record.
  function renderKey() {
    if (!keyEl || !lastKeyData) return;
    const d = lastKeyData;
    let html = `🎼 Key of what you sang: ${d.key} · ${d.camelot}`;
    const cmp = compareToTarget(d.key);
    if (cmp) html += `<br><span class="rt-cmp ${cmp.good ? "good" : "off"}">Target ${cmp.targetName}: ${cmp.text}</span>`;
    keyEl.innerHTML = html;
    keyEl.classList.remove("hidden");
  }

  // ---- target-key picker: an interactive circle of fifths --------------------
  // Replaces the old dropdown. Outer ring = major keys, inner ring = their
  // relative minors, laid out clockwise by fifths (C at 12 o'clock). Clicking a
  // wedge sets #rtTarget to "pc:mode" (the exact value the old <select> produced,
  // so compareToTarget is unchanged); the centre clears the target.
  const COF = [
    { maj: 0,  min: 9,  majL: "C",  minL: "Am" },
    { maj: 7,  min: 4,  majL: "G",  minL: "Em" },
    { maj: 2,  min: 11, majL: "D",  minL: "Bm" },
    { maj: 9,  min: 6,  majL: "A",  minL: "F♯m" },
    { maj: 4,  min: 1,  majL: "E",  minL: "C♯m" },
    { maj: 11, min: 8,  majL: "B",  minL: "G♯m" },
    { maj: 6,  min: 3,  majL: "G♭", minL: "E♭m" },
    { maj: 1,  min: 10, majL: "D♭", minL: "B♭m" },
    { maj: 8,  min: 5,  majL: "A♭", minL: "Fm" },
    { maj: 3,  min: 0,  majL: "E♭", minL: "Cm" },
    { maj: 10, min: 7,  majL: "B♭", minL: "Gm" },
    { maj: 5,  min: 2,  majL: "F",  minL: "Dm" },
  ];
  const SVGNS = "http://www.w3.org/2000/svg";
  let segs = [];           // { value, path, text } per wedge, for highlighting
  let targetLabels = {};   // "pc:mode" -> pretty name (flats/sharps as on the circle)
  let centerText = null;

  function buildCircle() {
    if (!circleEl) return;
    const cx = 150, cy = 150, rOut = 146, rMid = 100, rIn = 58;
    const P = (deg, r) => {
      const a = (deg - 90) * Math.PI / 180; // 0deg at 12 o'clock, clockwise
      return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
    };
    const sector = (a0, a1, rO, rI) => {
      const [x0o, y0o] = P(a0, rO), [x1o, y1o] = P(a1, rO);
      const [x1i, y1i] = P(a1, rI), [x0i, y0i] = P(a0, rI);
      return `M${x0o} ${y0o} A${rO} ${rO} 0 0 1 ${x1o} ${y1o} `
           + `L${x1i} ${y1i} A${rI} ${rI} 0 0 0 ${x0i} ${y0i} Z`;
    };
    const el = (tag, attrs) => {
      const n = document.createElementNS(SVGNS, tag);
      for (const k in attrs) n.setAttribute(k, attrs[k]);
      return n;
    };

    const svg = el("svg", { viewBox: "0 0 300 300", width: 300, height: 300, class: "cof",
      role: "group", "aria-label": "Circle of fifths target-key picker" });
    segs = [];
    targetLabels = {};

    COF.forEach((e, i) => {
      const a0 = i * 30 - 15, a1 = i * 30 + 15;
      const minBase = e.minL.slice(0, -1); // "F#m" -> "F#"
      const rings = [
        { min: false, value: `${e.maj}:major`, label: e.majL, pretty: `${e.majL} major`, rO: rOut, rI: rMid },
        { min: true,  value: `${e.min}:minor`, label: e.minL, pretty: `${minBase} minor`, rO: rMid, rI: rIn },
      ];
      for (const r of rings) {
        targetLabels[r.value] = r.pretty;
        const path = el("path", { class: "cof-seg" + (r.min ? " min" : ""), d: sector(a0, a1, r.rO, r.rI) });
        path.addEventListener("click", () => selectTarget(r.value));
        const [lx, ly] = P(i * 30, (r.rO + r.rI) / 2);
        const text = el("text", { class: "cof-label" + (r.min ? " min" : ""),
          x: lx, y: ly, "text-anchor": "middle", "dominant-baseline": "central" });
        text.textContent = r.label;
        svg.appendChild(path);
        svg.appendChild(text);
        segs.push({ value: r.value, path, text });
      }
    });

    const center = el("circle", { class: "cof-center", cx, cy, r: rIn });
    center.addEventListener("click", () => selectTarget(""));
    centerText = el("text", { class: "cof-center-text", x: cx, y: cy,
      "text-anchor": "middle", "dominant-baseline": "central" });
    centerText.textContent = "Off";
    svg.appendChild(center);
    svg.appendChild(centerText);

    circleEl.replaceChildren(svg);
  }

  // Set the target key (value "pc:mode", or "" for none): update the highlight,
  // the button label and centre text, and refresh the readout if a key is shown.
  function selectTarget(value) {
    if (!targetEl) return;
    targetEl.value = value || "";
    const pretty = value ? (targetLabels[value] || value) : "Off";
    if (targetLabel) targetLabel.textContent = pretty;
    if (centerText) centerText.textContent = pretty;
    for (const s of segs) {
      const on = !!value && s.value === value;
      s.path.classList.toggle("selected", on);
      s.text.classList.toggle("selected", on);
    }
    renderKey();
    if (value) closeCircle(); // picked a key -> collapse; "Off" leaves it open to re-pick
  }

  function toggleCircle() {
    if (!circleEl || !targetBtn) return;
    const open = circleEl.classList.toggle("hidden") === false;
    targetBtn.setAttribute("aria-expanded", String(open));
  }
  function closeCircle() {
    if (!circleEl || !targetBtn) return;
    circleEl.classList.add("hidden");
    targetBtn.setAttribute("aria-expanded", "false");
  }

  const wrapSigned = (x) => { const d = ((x % 12) + 12) % 12; return d > 6 ? d - 12 : d; };

  // Relative major/minor share all seven notes, so pitch-wise you're on target.
  function isRelative(dt, dm, tt, tm) {
    if (dm === tm) return false;
    if (tm === "minor" && dm === "major") return (tt + 3) % 12 === dt;
    if (tm === "major" && dm === "minor") return ((tt - 3) % 12 + 12) % 12 === dt;
    return false;
  }

  // Signed cents offset -> readable text: cents when small, semitones once the gap
  // is a semitone or more (100+ cents no longer reads sensibly as cents). Positive
  // means the singer was sharp (above the target), so the fix is to sing lower.
  function fmtOffset(cents) {
    const a = Math.abs(cents);
    if (a < 4) return { good: true, phrase: "you're on target" };
    const dir = cents > 0 ? "sharp" : "flat";
    const way = cents > 0 ? "lower" : "higher";
    if (a < 100) return { good: a <= 15, phrase: `${Math.round(a)} cents ${dir} (sing a little ${way})` };
    const semis = Math.round(a / 10) / 10; // one decimal, in semitones
    const s = Number.isInteger(semis) ? String(semis) : semis.toFixed(1);
    const unit = semis === 1 ? "semitone" : "semitones";
    return { good: false, phrase: `${s} ${unit} ${dir} (sing ${s} ${unit} ${way})` };
  }

  // Compare the detected sung key ("F minor") to the chosen target key.
  function compareToTarget(detKey) {
    if (!targetEl || !targetEl.value) return null;
    const [ttStr, tm] = targetEl.value.split(":");
    const tt = parseInt(ttStr, 10);
    const targetName = targetLabels[targetEl.value] || `${NOTE_NAMES[tt]} ${tm}`;
    const [dName, dm] = detKey.split(" ");
    const dt = NOTE_NAMES.indexOf(dName);
    const meanFine = centsFrames ? centsSum / centsFrames : 0;

    if (isRelative(dt, dm, tt, tm)) {
      const f = fmtOffset(meanFine);
      return { targetName, good: f.good,
        text: `you sang the relative ${dm} (${detKey}), so the notes match. ${f.phrase}` };
    }
    const d = wrapSigned(dt - tt);
    const offset = d * 100 + meanFine;
    const f = fmtOffset(offset);
    if (d === 0 && dm !== tm) {
      const tail = Math.abs(offset) >= 4 ? ` ${f.phrase}` : "";
      return { targetName, good: false,
        text: `right tonic (${NOTE_NAMES[tt]}), but you sang ${dm}, not ${tm}.${tail}` };
    }
    const note = dm !== tm ? ` (and ${dm}, not ${tm})` : "";
    return { targetName, good: f.good, text: f.phrase + note };
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

  // Practice target-note controls + reference drone.
  if (canvas) canvas.addEventListener("pointerdown", (e) => {
    const rect = canvas.getBoundingClientRect();
    const y = (e.clientY - rect.top) * (canvas.height / rect.height);
    setTargetMidi(midiFromGraphY(y, canvas.height));
  });
  if (tgtUp) tgtUp.addEventListener("click", () => setTargetMidi((targetMidi == null ? 60 : targetMidi) + 1));
  if (tgtDown) tgtDown.addEventListener("click", () => setTargetMidi((targetMidi == null ? 60 : targetMidi) - 1));
  if (tgtClear) tgtClear.addEventListener("click", () => setTargetMidi(null));
  if (refBtn) refBtn.addEventListener("click", playReference);

  if (modeEl) modeEl.addEventListener("click", (e) => {
    const btn = e.target.closest(".seg-btn");
    if (!btn) return;
    const mode = btn.dataset.mode;
    if (mode === visibleMode) return;
    if (running) stop();
    stopReference();
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
    if (btn.dataset.view !== "realtime") {
      if (running) stop();
      stopReference();
    }
  });

  buildStrings();
  buildCircle();
  if (targetBtn) targetBtn.addEventListener("click", toggleCircle);
  Object.assign(api, { fmtOffset, isRelative, wrapSigned, compareToTarget, selectTarget,
    midiFromGraphY, stdevCents, setTargetMidi, midiName });
  return api;
})();

// Exposed for verification: window.RT.detectPitch(float32, sampleRate), etc.
if (typeof window !== "undefined") window.RT = RT;
