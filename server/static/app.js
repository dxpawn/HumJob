"use strict";

// ---- state & elements -------------------------------------------------------
const $ = (id) => document.getElementById(id);
const el = {
  bpm: $("bpm"), bpmOut: $("bpmOut"), tap: $("tap"), metroBtn: $("metroBtn"),
  detectBtn: $("detectBtn"), engine: $("engine"),
  recDownload: $("recDownload"), recDownloadHint: $("recDownloadHint"),
  timesig: $("timesig"), countin: $("countin"), grid: $("grid"), muteRec: $("muteRec"),
  beats: $("beats"), recBtn: $("recBtn"), stopBtn: $("stopBtn"), status: $("status"),
  file: $("file"), result: $("result"), summary: $("summary"),
  chordList: $("chordList"), playBtn: $("playBtn"), playChords: $("playChords"),
  downloads: $("downloads"), sheet: $("sheet"), noteList: $("noteList"),
  modeSeg: $("modeSeg"), manualHint: $("manualHint"),
  reharmRow: $("reharmRow"), reharmChips: $("reharmChips"), reharmStyle: $("reharmStyle"),
  reharmGo: $("reharmGo"), reharmRevert: $("reharmRevert"),
  reharmStatus: $("reharmStatus"), reharmSummary: $("reharmSummary"),
  // Pitch Finder tab
  tabs: $("tabs"), finderFile: $("finderFile"), finderDrop: $("finderDrop"),
  finderStatus: $("finderStatus"), finderResult: $("finderResult"),
  finderTiles: $("finderTiles"), finderNeighbors: $("finderNeighbors"),
  finderAdv: $("finderAdv"),
};

// Capture the RAW mic signal. By default browsers turn on speech-call DSP —
// noiseSuppression (a gate that ducks quiet audio to ZERO), echoCancellation, and
// autoGainControl — which chop a sustained hum's soft onset/tail and make every
// note feel abruptly cut. We want the untouched waveform for pitch transcription.
const RAW_MIC = {
  audio: {
    noiseSuppression: false,
    echoCancellation: false,
    autoGainControl: false,
  },
};

let audioCtx = null;
let metronome = null;      // scheduler handle
let recorder = null;
let recChunks = [];
let recording = false;
let lastResult = null;     // last transcription JSON, for playback
let player = null;         // Web Audio playback handle
let sheetMode = "auto";    // Transcriber sheet: "auto" (server SVG) or "manual" (client)
let serverSvg = "";        // the server-engraved sheet, restored when switching to Auto
let autoSvg = "";          // the ORIGINAL auto sheet, restored by "Revert chords"
let manual = null;         // MT.createManual controller, built lazily on first Manual entry

// ---- tempo controls ---------------------------------------------------------
el.bpm.addEventListener("input", () => { el.bpmOut.value = el.bpm.value; drawBeats(); });

let taps = [];
el.tap.addEventListener("click", () => {
  const now = performance.now();
  taps = taps.filter((t) => now - t < 2500);
  taps.push(now);
  if (taps.length >= 2) {
    const spans = [];
    for (let i = 1; i < taps.length; i++) spans.push(taps[i] - taps[i - 1]);
    const avg = spans.reduce((a, b) => a + b, 0) / spans.length;
    const bpm = Math.round(60000 / avg);
    if (bpm >= 40 && bpm <= 200) { el.bpm.value = bpm; el.bpmOut.value = bpm; drawBeats(); }
  }
});

el.timesig.addEventListener("change", drawBeats);

function beatsPerBar() { return parseInt(el.timesig.value.split("/")[0], 10); }
function beatUnit() { return parseInt(el.timesig.value.split("/")[1], 10); }

function drawBeats() {
  el.beats.innerHTML = "";
  for (let i = 0; i < beatsPerBar(); i++) {
    const d = document.createElement("div");
    d.className = "beat-dot" + (i === 0 ? " accent" : "");
    el.beats.appendChild(d);
  }
}
drawBeats();

// ---- metronome (Web Audio lookahead scheduler) ------------------------------
function ensureAudio() {
  if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  if (audioCtx.state === "suspended") audioCtx.resume();
  return audioCtx;
}

function click(time, accent) {
  const ctx = audioCtx;
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.frequency.value = accent ? 1500 : 1000;
  gain.gain.setValueAtTime(0.0001, time);
  gain.gain.exponentialRampToValueAtTime(accent ? 0.5 : 0.3, time + 0.001);
  gain.gain.exponentialRampToValueAtTime(0.0001, time + 0.05);
  osc.connect(gain).connect(ctx.destination);
  osc.start(time); osc.stop(time + 0.06);
}

function flashBeat(idx) {
  const dots = el.beats.children;
  for (const d of dots) d.classList.remove("on");
  if (dots[idx]) dots[idx].classList.add("on");
}

// Starts count-in clicks, then invokes onRecord() when the bar(s) finish and
// keeps clicking through the take (unless muted). Returns a stop() function.
function startMetronome(onRecord) {
  const ctx = ensureAudio();
  const bpm = parseInt(el.bpm.value, 10);
  const spb = 60 / bpm;                     // seconds per beat
  const nBeats = beatsPerBar();
  const countInBeats = nBeats * parseInt(el.countin.value, 10);
  const mute = el.muteRec.checked;

  let beat = 0;
  let nextTime = ctx.currentTime + 0.1;
  let started = false;
  const startAt = ctx.currentTime + 0.1 + countInBeats * spb;

  const timer = setInterval(() => {
    while (nextTime < ctx.currentTime + 0.15) {
      const barBeat = beat % nBeats;
      const inCountIn = beat < countInBeats;
      if (inCountIn || !mute) click(nextTime, barBeat === 0);
      const t = nextTime, b = barBeat;
      setTimeout(() => flashBeat(b), Math.max(0, (t - ctx.currentTime) * 1000));
      nextTime += spb; beat++;
    }
    if (!started && ctx.currentTime >= startAt) { started = true; onRecord(); }
  }, 25);

  return () => { clearInterval(timer); for (const d of el.beats.children) d.classList.remove("on"); };
}

// ---- metronome preview (hear/check the tempo before recording) ---------------
let preview = null;  // stop() handle while previewing

function togglePreview() {
  if (preview) { stopPreview(); return; }
  const ctx = ensureAudio();
  let beat = 0;
  let nextTime = ctx.currentTime + 0.1;
  const timer = setInterval(() => {
    const spb = 60 / parseInt(el.bpm.value, 10);  // follow the slider live
    const nBeats = beatsPerBar();
    while (nextTime < ctx.currentTime + 0.15) {
      const b = beat % nBeats;
      click(nextTime, b === 0);
      const t = nextTime;
      setTimeout(() => flashBeat(b), Math.max(0, (t - ctx.currentTime) * 1000));
      nextTime += spb; beat++;
    }
  }, 25);
  preview = () => { clearInterval(timer); for (const d of el.beats.children) d.classList.remove("on"); };
  el.metroBtn.textContent = "■ Stop click";
  el.metroBtn.classList.add("playing");
}

function stopPreview() {
  if (!preview) return;
  preview();
  preview = null;
  el.metroBtn.textContent = "▶ Preview click";
  el.metroBtn.classList.remove("playing");
}

el.metroBtn.addEventListener("click", togglePreview);

// ---- "find my tempo" (hum freely, we estimate your BPM) ----------------------
let detecting = false;
let detectRecorder = null;
let detectChunks = [];

el.detectBtn.addEventListener("click", async () => {
  if (detecting) { if (detectRecorder) detectRecorder.stop(); return; }
  try {
    stopPreview();
    ensureAudio();
    const stream = await navigator.mediaDevices.getUserMedia(RAW_MIC);
    detectChunks = [];
    detectRecorder = new MediaRecorder(stream);
    detectRecorder.ondataavailable = (e) => { if (e.data.size) detectChunks.push(e.data); };
    detectRecorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      detecting = false;
      el.detectBtn.textContent = "🎙 Find my tempo";
      el.detectBtn.classList.remove("playing");
      const blob = new Blob(detectChunks, { type: detectRecorder.mimeType || "audio/webm" });
      await detectTempo(blob);
    };
    detecting = true;
    el.detectBtn.textContent = "■ Stop & detect";
    el.detectBtn.classList.add("playing");
    setStatus("hum a few steady beats… then Stop & detect", true);
    detectRecorder.start();
  } catch (err) {
    setStatus("mic error: " + err.message, false);
    detecting = false;
  }
});

async function detectTempo(blob) {
  setStatus("estimating tempo…", false);
  const fd = new FormData();
  fd.append("audio", blob, "tempo.webm");
  try {
    const res = await fetch("/api/detect-tempo", { method: "POST", body: fd });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || res.statusText);
    }
    const { bpm } = await res.json();
    el.bpm.value = bpm;
    el.bpmOut.value = bpm;
    drawBeats();
    setStatus(`detected ~${bpm} BPM — now Record and hum to the click`, false);
  } catch (err) {
    setStatus("tempo detection failed: " + err.message, false);
  }
}

// ---- recording --------------------------------------------------------------
el.recBtn.addEventListener("click", async () => {
  try {
    stopPreview();  // don't stack the preview click on top of the count-in
    ensureAudio();
    const stream = await navigator.mediaDevices.getUserMedia(RAW_MIC);
    recChunks = [];
    recorder = new MediaRecorder(stream);
    recorder.ondataavailable = (e) => { if (e.data.size) recChunks.push(e.data); };
    recorder.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      const blob = new Blob(recChunks, { type: recorder.mimeType || "audio/webm" });
      offerDownload(blob, "my-hum.webm");
      upload(blob, "recording.webm");
    };

    el.recBtn.disabled = true;
    setStatus("count-in…", false);
    metronome = startMetronome(() => {
      recorder.start();
      recording = true;
      el.stopBtn.disabled = false;
      setStatus("● recording — hum now", true);
    });
  } catch (err) {
    setStatus("mic error: " + err.message, false);
    el.recBtn.disabled = false;
  }
});

el.stopBtn.addEventListener("click", () => {
  if (metronome) metronome();
  if (recorder && recording) recorder.stop();
  recording = false;
  el.stopBtn.disabled = true;
  setStatus("processing…", false);
});

el.file.addEventListener("change", (e) => {
  const f = e.target.files[0];
  if (f) { setStatus("processing…", false); upload(f, f.name); }
});

function setStatus(text, rec) {
  el.status.textContent = text;
  el.status.classList.toggle("rec", !!rec);
}

// Expose the exact audio that was just captured, so it can be saved and shared.
let _recUrl = null;
function offerDownload(blob, filename) {
  if (_recUrl) URL.revokeObjectURL(_recUrl);
  _recUrl = URL.createObjectURL(blob);
  el.recDownload.href = _recUrl;
  el.recDownload.download = filename;
  el.recDownload.classList.remove("hidden");
  el.recDownloadHint.classList.remove("hidden");
}

// ---- upload & render --------------------------------------------------------
async function upload(blob, filename) {
  const fd = new FormData();
  fd.append("audio", blob, filename);
  fd.append("bpm", el.bpm.value);
  fd.append("beats", beatsPerBar());
  fd.append("beat_unit", beatUnit());
  fd.append("subdiv", el.grid.value);
  fd.append("backend", el.engine.value);
  try {
    const res = await fetch("/api/transcribe", { method: "POST", body: fd });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || res.statusText);
    }
    render(await res.json());
    setStatus("done", false);
  } catch (err) {
    setStatus("error: " + err.message, false);
  } finally {
    el.recBtn.disabled = false;
  }
}

function renderSummary(data) {
  const cands = data.key_candidates.map((c) => `${c.name} (${c.score})`).join(", ");
  el.summary.innerHTML = `
    <div class="stat"><span class="val">${data.key || "—"}</span><span class="lbl">Key</span></div>
    <div class="stat"><span class="val">${data.n_notes}</span><span class="lbl">Notes</span></div>
    <div class="stat"><span class="val">${data.tempo_bpm}</span><span class="lbl">BPM</span></div>
    <div class="stat"><span class="val">${data.tuning_offset_cents > 0 ? "+" : ""}${data.tuning_offset_cents}¢</span><span class="lbl">Tuning</span></div>`;
  const alt = document.createElement("p");
  alt.className = "hint";
  const eng = data.backend === "pyin" ? "pYIN (classic)"
            : data.backend === "crepe" ? "CREPE (voice/humming)"
            : data.backend === "pesto" ? "PESTO (most precise)"
            : data.backend === "fcnf0" ? "FCNF0++ (most precise)"
            : "basic-pitch (neural)";
  alt.textContent = `Engine: ${eng} · Key candidates: ${cands}`;
  el.summary.appendChild(alt);
}

// fit (optional) is a per-chord coverage fraction (0..1); a bar under 0.5 gets a warn dot,
// so a reharmonization that clashes with the melody is shown, not hidden. Cells are
// clickable to open the deterministic alternatives popover (see openAltPopover).
function renderChordStrip(chords, fit) {
  el.chordList.innerHTML = "";
  chords = chords || [];
  if (!chords.length) return;
  const label = document.createElement("span");
  label.className = "chords-label";
  label.textContent = "Suggested chords";
  el.chordList.appendChild(label);
  const strip = document.createElement("div");
  strip.className = "chord-strip";
  chords.forEach((c, i) => {
    const cell = document.createElement("button");
    cell.type = "button";
    cell.className = "chord-cell";
    let dot = "";
    if (fit && typeof fit[i] === "number") {
      const warn = fit[i] < 0.5 ? " warn" : "";
      dot = `<span class="chord-fit${warn}" title="Fits ${Math.round(fit[i] * 100)}% of this bar's melody"></span>`;
    }
    cell.innerHTML = `<span class="chord-sym">${c.symbol}</span><span class="chord-rn">${c.roman || ""}</span>${dot}`;
    cell.title = `Measure ${c.measure + 1} - click for alternatives`;
    cell.addEventListener("click", () => openAltPopover(cell, c.measure));
    strip.appendChild(cell);
  });
  el.chordList.appendChild(strip);
}

// ---- reharmonization (chord alternatives popover + LLM reharmonize) ---------
let reharmToken = 0;       // drops stale /api/reharmonize responses
let reharmBusy = false;
let altPopover = null;     // the open alternatives popover element, if any
const altCache = {};       // measure -> [option, ...] from /api/chord-alternatives

// The alternatives cache is keyed by measure and computed from the CURRENT melody, so it is
// stale the moment the melody changes. Drop it (and any open popover) whenever the notes are
// edited, inserted, reorganized, rescored, or a new take is loaded, so a click always re-fetches
// against what is on the staff now. Called from resetReharm and the Manual edit/rescore hooks.
function clearAltCache() {
  closeAltPopover();
  for (const k of Object.keys(altCache)) delete altCache[k];
}

function resetReharm(data) {
  const has = !!(data && data.chords && data.chords.length && data.notes && data.notes.length);
  if (el.reharmRow) el.reharmRow.hidden = !has;
  if (el.reharmRevert) el.reharmRevert.hidden = true;
  if (el.reharmSummary) { el.reharmSummary.hidden = true; el.reharmSummary.textContent = ""; }
  if (el.reharmStatus) el.reharmStatus.textContent = "";
  clearAltCache();
}

// Apply a new progression to the sheet, strip, playback, Manual editor, and Transposer.
function applyChords(chords, fit, svg) {
  lastResult.chords = chords;
  renderChordStrip(chords, fit);
  if (svg) {
    serverSvg = svg;
    if (sheetMode === "auto") el.sheet.innerHTML = svg;
  }
  if (manual && typeof manual.setChords === "function") manual.setChords(chords);
}

function revertChords() {
  if (!lastResult || !lastResult.auto_chords) return;
  applyChords(lastResult.auto_chords.map((c) => ({ ...c })), null, autoSvg);
  if (el.reharmRevert) el.reharmRevert.hidden = true;
  if (el.reharmSummary) { el.reharmSummary.hidden = true; el.reharmSummary.textContent = ""; }
  if (el.reharmStatus) el.reharmStatus.textContent = "Reverted to the auto chords.";
}

// LLM reharmonization: send only notes + current chords + the style string.
function reharmonize(style) {
  if (!lastResult || !lastResult.notes || !lastResult.notes.length || reharmBusy) return;
  style = (style || "").trim();
  if (!style) { if (el.reharmStatus) el.reharmStatus.textContent = "Pick a style or type one first."; return; }
  const token = ++reharmToken;
  reharmBusy = true;
  if (el.reharmGo) el.reharmGo.disabled = true;
  if (el.reharmStatus) el.reharmStatus.textContent = "Reharmonizing...";
  fetch("/api/reharmonize", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      notes: lastResult.notes.map((n) => ({ midi: n.midi, start_ql: n.start_ql, dur_ql: n.dur_ql })),
      tempo: lastResult.tempo_bpm, time_sig: lastResult.time_sig,
      key: lastResult.key, chords: lastResult.chords, style, language: "en",
    }),
  })
    .then(async (r) => {
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || r.statusText);
      return data;
    })
    .then((data) => {
      if (token !== reharmToken) return;
      reharmBusy = false;
      if (el.reharmGo) el.reharmGo.disabled = false;
      applyChords(data.chords, data.fit, data.svg);
      if (el.reharmRevert) el.reharmRevert.hidden = false;
      if (el.reharmStatus) el.reharmStatus.textContent = data.model ? `via ${data.model}` : "";
      if (el.reharmSummary && data.summary) { el.reharmSummary.textContent = data.summary; el.reharmSummary.hidden = false; }
    })
    .catch((e) => {
      if (token !== reharmToken) return;
      reharmBusy = false;
      if (el.reharmGo) el.reharmGo.disabled = false;
      if (el.reharmStatus) el.reharmStatus.textContent = e.message || "Reharmonization failed.";
    });
}

// ---- deterministic per-bar alternatives popover (offline, no key) -----------
function closeAltPopover() {
  if (altPopover && altPopover.parentNode) altPopover.parentNode.removeChild(altPopover);
  altPopover = null;
}

function openAltPopover(cell, measure) {
  if (altPopover && altPopover._measure === measure) { closeAltPopover(); return; }
  closeAltPopover();
  const pop = document.createElement("div");
  pop.className = "alt-popover";
  pop._measure = measure;
  pop.innerHTML = "<div class='alt-loading'>Loading alternatives...</div>";
  cell.appendChild(pop);
  altPopover = pop;

  const fill = (options) => {
    if (altPopover !== pop) return;
    pop.innerHTML = "";
    if (!options || !options.length) { pop.innerHTML = "<div class='alt-loading'>No alternatives.</div>"; return; }
    for (const o of options) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "alt-item";
      b.innerHTML = `<span class="alt-sym">${o.symbol}</span><span class="alt-rn">${o.roman || ""}</span>` +
        `<span class="alt-fit">${Math.round((o.fit || 0) * 100)}%</span>`;
      b.addEventListener("click", (e) => { e.stopPropagation(); applyAlternative(measure, o); });
      pop.appendChild(b);
    }
  };

  if (altCache[measure]) { fill(altCache[measure]); return; }
  fetch("/api/chord-alternatives", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      notes: lastResult.notes.map((n) => ({ midi: n.midi, start_ql: n.start_ql, dur_ql: n.dur_ql })),
      tempo: lastResult.tempo_bpm, time_sig: lastResult.time_sig, key: lastResult.key, style: "extended",
    }),
  })
    .then((r) => r.json())
    .then((data) => {
      for (const m of (data.measures || [])) altCache[m.measure] = m.options;
      fill(altCache[measure] || []);
    })
    .catch(() => fill([]));
}

// Replace one measure's chord with a chosen alternative (client-side; no re-engrave svg,
// so we drop the server sheet's stale symbol by re-rendering just the strip + Manual).
function applyAlternative(measure, opt) {
  closeAltPopover();
  if (!lastResult || !lastResult.chords) return;
  const chords = lastResult.chords.map((c) => (c.measure === measure
    ? { measure: c.measure, start_ql: c.start_ql, root_pc: opt.root_pc, root_name: opt.root_name,
        quality: opt.quality, symbol: opt.symbol, roman: opt.roman }
    : c));
  applyChords(chords, null, null);
  if (el.reharmRevert) el.reharmRevert.hidden = false;
  if (el.reharmStatus) el.reharmStatus.textContent = `Set measure ${measure + 1} to ${opt.symbol}.`;
}

document.addEventListener("click", (e) => {
  // Close the popover when clicking elsewhere (a chord cell handles its own toggle).
  if (altPopover && !e.target.closest(".alt-popover") && !e.target.closest(".chord-cell")) closeAltPopover();
});

if (el.reharmChips) {
  el.reharmChips.addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (chip) reharmonize(chip.dataset.style);
  });
}
if (el.reharmGo) el.reharmGo.addEventListener("click", () => reharmonize(el.reharmStyle ? el.reharmStyle.value : ""));
if (el.reharmStyle) el.reharmStyle.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); reharmonize(el.reharmStyle.value); } });
if (el.reharmRevert) el.reharmRevert.addEventListener("click", revertChords);

function render(data) {
  stopPlayback();          // silence any playback from a previous result
  lastResult = data;
  el.result.classList.remove("hidden");
  renderSummary(data);
  renderChordStrip(data.chords);

  // Seed the reharmonization baseline: keep the auto progression + sheet so "Revert
  // chords" can restore them, and offer the row only when there are chords to work with.
  data.auto_chords = (data.chords || []).map((c) => ({ ...c }));
  autoSvg = data.svg || "";
  resetReharm(data);

  el.sheet.innerHTML = data.svg || "<p class='hint'>No notes detected.</p>";
  // A fresh transcription reseeds Manual mode and resets the sheet to Auto.
  serverSvg = data.svg || "";
  if (manual) manual.exit();
  sheetMode = "auto";
  if (el.modeSeg) {
    for (const b of el.modeSeg.querySelectorAll(".seg-btn")) {
      b.classList.toggle("active", b.dataset.mode === "auto");
    }
  }
  if (el.manualHint) el.manualHint.hidden = true;

  el.downloads.hidden = false;   // Auto downloads; hidden again if the user enters Manual
  el.downloads.innerHTML = "";
  el.downloads.appendChild(downloadLink("Download MIDI", b64ToBlob(data.midi_b64, "audio/midi"), "melody.mid"));
  el.downloads.appendChild(downloadLink("Download MusicXML", new Blob([data.musicxml], { type: "application/xml" }), "melody.musicxml"));

  el.noteList.innerHTML = "";
  for (const n of data.notes) {
    const s = document.createElement("span");
    s.textContent = n.name;
    if (Math.abs(n.cents) >= 35) s.title = `${n.cents > 0 ? "+" : ""}${n.cents}¢ off`;
    el.noteList.appendChild(s);
  }
  el.result.scrollIntoView({ behavior: "smooth", block: "start" });
}

function downloadLink(text, blob, filename) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.textContent = text;
  return a;
}

function b64ToBlob(b64, mime) {
  const bin = atob(b64);
  const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  return new Blob([arr], { type: mime });
}

// ---- Manual mode: Auto/Manual sheet toggle -----------------------------------
// Auto shows the frozen server SVG (with its original MIDI/MusicXML downloads);
// Manual re-engraves lastResult.notes in the browser via verovio-WASM (manual.js)
// and lets you edit them. The Auto download links are hidden in Manual because the
// editor has its own (edited) downloads that call /api/export-edited.
async function setSheetMode(mode) {
  if (!lastResult) return;
  sheetMode = mode;
  if (el.modeSeg) {
    for (const b of el.modeSeg.querySelectorAll(".seg-btn")) {
      b.classList.toggle("active", b.dataset.mode === mode);
    }
  }
  if (el.manualHint) el.manualHint.hidden = mode !== "manual";
  if (el.downloads) el.downloads.hidden = mode === "manual";

  if (mode === "auto") {
    if (manual) manual.exit();
    el.sheet.innerHTML = serverSvg || "<p class='hint'>No notes detected.</p>";
    return;
  }
  if (typeof MT === "undefined") {
    el.sheet.innerHTML = "<p class='hint'>Manual mode is unavailable (editor not loaded).</p>";
    return;
  }
  if (!lastResult.notes || !lastResult.notes.length) {
    el.sheet.innerHTML = "<p class='hint'>No notes to edit.</p>";
    return;
  }
  if (!manual) {
    manual = MT.createManual({
      sheet: el.sheet, pane: $("manualPane"), strip: $("manualStrip"),
      readout: $("manualReadout"), tools: $("manualTools"), status: $("manualStatus"),
      ask: $("manualAsk"),
      // Keep lastResult in sync so Play uses the edited melody and the summary tracks.
      // The melody changed, so the per-bar alternatives cache is stale: drop it.
      onEdit: (notes) => {
        lastResult.notes = notes;
        lastResult.n_notes = notes.length;
        clearAltCache();
        renderSummary(lastResult);
      },
      // "Update chords + key" re-scored the edited melody: apply key + chords everywhere,
      // and clear the stale alternatives cache (new melody, new measures).
      onRescore: (data) => {
        lastResult.key = data.key;
        lastResult.key_candidates = data.key_candidates || lastResult.key_candidates;
        lastResult.chords = data.chords || [];
        clearAltCache();
        renderSummary(lastResult);
        renderChordStrip(lastResult.chords);
      },
    });
  }
  manual.enter(lastResult);   // async; manages its own render + the toggled-away guard
}

if (el.modeSeg) {
  el.modeSeg.addEventListener("click", (e) => {
    const btn = e.target.closest(".seg-btn");
    if (btn && btn.dataset.mode !== sheetMode) setSheetMode(btn.dataset.mode);
  });
}

// ---- playback (sonify the transcription with Web Audio) ---------------------
// Triad fallback only; the full quality table (incl. sevenths) lives in MT.CHORD_QUALITIES
// (manual.js), read at call time via chordIntervals so seventh chords voice correctly.
const CHORD_INTERVALS = { maj: [0, 4, 7], min: [0, 3, 7], dim: [0, 3, 6] };
function chordIntervals(quality) {
  if (typeof MT !== "undefined" && MT.CHORD_QUALITIES && MT.CHORD_QUALITIES[quality]) {
    return MT.CHORD_QUALITIES[quality].intervals;
  }
  return CHORD_INTERVALS[quality] || CHORD_INTERVALS.maj;
}
const midiToFreq = (m) => 440 * Math.pow(2, (m - 69) / 12);

// ---- sampled grand piano (Salamander, CC-BY; see piano/ATTRIBUTION.txt) ------
// One real recorded note every minor third; any pitch is the nearest sample
// pitch-shifted. Loaded lazily on first Play, then cached.
const PIANO_SAMPLES = {
  36: "C2", 39: "Eb2", 42: "Gb2", 45: "A2",
  48: "C3", 51: "Eb3", 54: "Gb3", 57: "A3",
  60: "C4", 63: "Eb4", 66: "Gb4", 69: "A4",
  72: "C5", 75: "Eb5", 78: "Gb5", 81: "A5",
  84: "C6", 87: "Eb6", 90: "Gb6", 93: "A6",
  96: "C7",
};
const PIANO_MIDIS = Object.keys(PIANO_SAMPLES).map(Number).sort((a, b) => a - b);
let pianoBuffers = null;   // { midi: AudioBuffer } once loaded (null => use synth)
let _pianoLoad = null;     // in-flight/settled load promise
let playLoading = false;

function loadPiano(ctx) {
  if (_pianoLoad) return _pianoLoad;
  _pianoLoad = (async () => {
    const buffers = {};
    await Promise.all(PIANO_MIDIS.map(async (m) => {
      try {
        const res = await fetch(`piano/${PIANO_SAMPLES[m]}.mp3`);
        if (!res.ok) return;
        buffers[m] = await ctx.decodeAudioData(await res.arrayBuffer());
      } catch (e) { /* leave this pitch unsampled; nearest one covers it */ }
    }));
    pianoBuffers = Object.keys(buffers).length ? buffers : null;
    return pianoBuffers;
  })();
  return _pianoLoad;
}

function nearestSample(midi) {
  let best = PIANO_MIDIS[0];
  for (const m of PIANO_MIDIS) {
    if (Math.abs(m - midi) < Math.abs(best - midi)) best = m;
  }
  return best;
}

// One real piano note, pitch-shifted from the nearest sample. Lets the note ring
// through its duration, then a short release fade. Falls back to the synth voice
// if the samples didn't load.
function sampleVoice(ctx, dest, midi, start, dur, peak) {
  if (!pianoBuffers) { pianoVoice(ctx, dest, midiToFreq(midi), start, dur, peak); return; }
  const sm = nearestSample(midi);
  const src = ctx.createBufferSource();
  src.buffer = pianoBuffers[sm];
  src.playbackRate.value = Math.pow(2, (midi - sm) / 12);  // pitch-shift to target
  const g = ctx.createGain();
  const rel = 0.3;
  g.gain.setValueAtTime(peak, start);
  g.gain.setValueAtTime(peak, start + dur);
  g.gain.exponentialRampToValueAtTime(0.0006, start + dur + rel);
  src.connect(g).connect(dest);
  src.start(start);
  src.stop(start + dur + rel + 0.05);
}

// A piano-ish timbre: harmonic amplitudes of a soft grand (index 0 = DC).
let _pianoWave = null;
function pianoWave(ctx) {
  if (_pianoWave) return _pianoWave;
  const real = new Float32Array([0, 1, 0.28, 0.42, 0.16, 0.12, 0.08, 0.05, 0.04, 0.03, 0.02]);
  _pianoWave = ctx.createPeriodicWave(real, new Float32Array(real.length));
  return _pianoWave;
}

// One plucked/struck piano-like note: fast strike, exponential ring-down, and a
// lowpass whose cutoff falls so the tone darkens as it decays (like a real piano).
function pianoVoice(ctx, dest, freq, start, dur, peak) {
  const wave = pianoWave(ctx);
  const amp = ctx.createGain();
  const lp = ctx.createBiquadFilter();
  lp.type = "lowpass";
  lp.Q.value = 0.3;
  lp.frequency.setValueAtTime(Math.min(7000, freq * 6 + 1500), start);
  lp.frequency.exponentialRampToValueAtTime(Math.max(500, freq * 1.8), start + 0.5);

  const attack = 0.004;
  const hold = Math.max(attack + 0.08, dur);
  amp.gain.setValueAtTime(0.0001, start);
  amp.gain.exponentialRampToValueAtTime(peak, start + attack);           // strike
  amp.gain.exponentialRampToValueAtTime(Math.max(0.0002, peak * 0.06), start + hold);  // ring-down
  amp.gain.exponentialRampToValueAtTime(0.00008, start + hold + 0.09);   // release
  amp.connect(lp).connect(dest);

  for (const detune of [-3, 3]) {  // two slightly detuned partials for warmth
    const osc = ctx.createOscillator();
    osc.setPeriodicWave(wave);
    osc.frequency.value = freq;
    osc.detune.value = detune;
    osc.connect(amp);
    osc.start(start);
    osc.stop(start + hold + 0.16);
  }
}

async function togglePlayback() {
  if (player) { stopPlayback(); return; }
  if (playLoading) return;
  const data = lastResult;
  if (!data || !data.notes || !data.notes.length) return;

  const ctx = ensureAudio();
  playLoading = true;
  el.playBtn.textContent = "…";
  try { await loadPiano(ctx); } finally { playLoading = false; }
  if (player) return;                              // (guard) another play started

  const spb = 60 / data.tempo_bpm;                 // seconds per quarter note
  const master = ctx.createGain();
  master.gain.value = 0.8;
  master.connect(ctx.destination);

  const t0 = ctx.currentTime + 0.08;
  let endT = t0;

  // Soft triad accompaniment (optional), an octave-ish below the tune.
  if (el.playChords.checked && data.chords) {
    const barQl = data.time_sig[0] * (4 / data.time_sig[1]);
    for (const c of data.chords) {
      const start = t0 + c.start_ql * spb;
      const dur = barQl * spb;
      const rootMidi = 48 + c.root_pc;             // C3..B3 register
      for (const iv of chordIntervals(c.quality)) {
        sampleVoice(ctx, master, rootMidi + iv, start, dur * 0.9, 0.09);
      }
      endT = Math.max(endT, start + dur);
    }
  }

  // Melody on top.
  for (const n of data.notes) {
    if (n.start_ql == null || n.dur_ql == null) continue;
    const start = t0 + n.start_ql * spb;
    const dur = Math.max(0.08, n.dur_ql * spb);
    sampleVoice(ctx, master, n.midi, start, dur, 0.42);
    endT = Math.max(endT, start + dur);
  }

  el.playBtn.textContent = "■ Stop";
  el.playBtn.classList.add("playing");
  const timer = setTimeout(stopPlayback, (endT - ctx.currentTime + 0.15) * 1000);
  player = { master, timer };
}

function stopPlayback() {
  if (!player) return;
  clearTimeout(player.timer);
  const m = player.master;
  try { m.gain.setTargetAtTime(0.0001, audioCtx.currentTime, 0.02); } catch (e) {}
  setTimeout(() => { try { m.disconnect(); } catch (e) {} }, 120);
  player = null;
  el.playBtn.textContent = "▶ Play";
  el.playBtn.classList.remove("playing");
}

el.playBtn.addEventListener("click", togglePlayback);

// ---- tabs -------------------------------------------------------------------
let transposer = null;   // created lazily on first switch (transposer.js loads after app.js)
let singalong = null;    // created lazily on first switch (singalong.js loads after app.js)
let eartrainer = null;   // created lazily on first switch (eartrainer.js loads after app.js)
el.tabs.addEventListener("click", (e) => {
  const btn = e.target.closest(".tab");
  if (!btn || btn.disabled) return;
  const view = btn.dataset.view;
  for (const t of el.tabs.querySelectorAll(".tab")) t.classList.toggle("active", t === btn);
  for (const v of document.querySelectorAll(".view")) {
    v.classList.toggle("hidden", v.id !== "view-" + view);
  }
  // The Transposer reads the last Transcriber result when its tab opens, and stops its
  // own playback when you leave it.
  if (view === "transposer") {
    if (!transposer && typeof TR !== "undefined") transposer = TR.createTransposer();
    if (transposer) transposer.enter();
  } else if (transposer) {
    transposer.exit();
  }
  // Sing-Along owns its own mic + playback; exit() on leave is the single teardown path
  // (never leave a hot mic running when the tab is hidden).
  if (view === "singalong") {
    if (!singalong && typeof SA !== "undefined") singalong = SA.createSingalong();
    if (singalong) singalong.enter();
  } else if (singalong) {
    singalong.exit();
  }
  // Ear Trainer is playback-only; exit() just stops any sound when you leave the tab.
  if (view === "eartrainer") {
    if (!eartrainer && typeof ET !== "undefined") eartrainer = ET.createEarTrainer();
    if (eartrainer) eartrainer.enter();
  } else if (eartrainer) {
    eartrainer.exit();
  }
});

// ---- Pitch Finder -----------------------------------------------------------
el.finderFile.addEventListener("change", (e) => {
  const f = e.target.files[0];
  if (f) analyzeAudio(f);
});

["dragenter", "dragover"].forEach((ev) =>
  el.finderDrop.addEventListener(ev, (e) => { e.preventDefault(); el.finderDrop.classList.add("drag"); }));
["dragleave", "drop"].forEach((ev) =>
  el.finderDrop.addEventListener(ev, (e) => { e.preventDefault(); el.finderDrop.classList.remove("drag"); }));
el.finderDrop.addEventListener("drop", (e) => {
  const f = e.dataTransfer.files[0];
  if (f) analyzeAudio(f);
});

async function analyzeAudio(file) {
  el.finderStatus.textContent = "analyzing… (this can take a few seconds)";
  el.finderResult.classList.add("hidden");
  try {
    const fd = new FormData();
    fd.append("audio", file, file.name);
    const res = await fetch("/api/analyze", { method: "POST", body: fd });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || res.statusText);
    }
    renderAnalysis(await res.json());
    el.finderStatus.textContent = `analyzed “${file.name}”`;
  } catch (e) {
    el.finderStatus.textContent = "error: " + e.message;
  }
}

function tile(val, lbl, cls, sub) {
  const d = document.createElement("div");
  d.className = "stat";
  const v = document.createElement("span");
  v.className = "val" + (cls ? " " + cls : "");
  v.textContent = val;
  const l = document.createElement("span");
  l.className = "lbl";
  l.textContent = lbl;
  d.append(v, l);
  if (sub) {
    const s = document.createElement("span");
    s.className = "sub";
    s.textContent = sub;
    d.append(s);
  }
  return d;
}

// One-line, plain-language explanation of each advanced stat. Shown on hover only
// (see .has-tip in style.css) so the grid stays uncluttered.
const STAT_DESC = {
  "Key confidence": "How strongly the audio's pitch profile matches this key (0–1). Higher = more certain.",
  "BPM confidence": "How regular the detected beat is (0–1). Low means the tempo is ambiguous.",
  "Half / double time": "The same tempo at half and double speed. Beat trackers often lock onto one of these instead of the true tempo.",
  "Tuning offset": "How far the track sits from standard A=440 tuning, in cents (100 cents = one semitone).",
  "Reference A4": "The tuning of concert A implied by the offset above (440 Hz is standard).",
  "Spectral centroid": "The spectrum's centre of gravity. Higher = a brighter sound.",
  "Spectral rolloff": "Frequency below which ~85% of the energy sits — another brightness measure.",
  "Spectral bandwidth": "How spread out the spectrum is around its centroid — the width of the timbre.",
  "Zero-crossing rate": "How often the waveform crosses zero. High for noisy, percussive or sibilant sounds.",
  "RMS loudness": "Average signal level, in decibels.",
  "Peak": "The loudest single sample relative to full scale (0 dBFS is the maximum).",
  "Dynamic range": "The gap between the loud and quiet sections, in dB. Larger = more dynamic.",
  "Energy": "Average level relative to the peak (0–1). How consistently loud the track is.",
  "Duration": "Length of the analysed audio, in seconds.",
  "Sample rate": "Samples per second used for the analysis.",
  "Onset density": "Detected note/attack onsets per second — a rough measure of busyness.",
  "Top key candidates": "The keys whose pitch profile best matches the audio, best first, with the match score.",
  "Pitch-class distribution": "How much of each of the 12 pitch classes is present — the raw material the key estimate is built from.",
};

function statRow(k, v, desc) {
  const d = document.createElement("div");
  d.className = "stat-row";
  const kk = document.createElement("span");
  kk.className = "k";
  kk.textContent = k;
  if (desc) {
    kk.classList.add("has-tip");
    kk.title = desc;                  // native fallback (mobile / a11y)
    kk.setAttribute("data-tip", desc); // styled CSS bubble on hover
  }
  const vv = document.createElement("span");
  vv.className = "v";
  vv.textContent = v;
  d.append(kk, vv);
  return d;
}

function renderAnalysis(d) {
  el.finderTiles.replaceChildren(
    tile(d.key, "Key"),
    // Beat trackers often lock onto double-time; surface the half-time alternate
    // so the likely-correct value is visible at a glance.
    tile(d.bpm, "BPM", null, `or ${Math.round(d.bpm_half)}`),
    tile(d.camelot, "Camelot", "camelot"),
  );

  el.finderNeighbors.replaceChildren();
  const nlabel = document.createElement("span");
  nlabel.className = "nlabel";
  nlabel.textContent = "Mixes with";
  el.finderNeighbors.appendChild(nlabel);
  for (const c of d.camelot_neighbors) {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = c;
    el.finderNeighbors.appendChild(chip);
  }

  const a = d.advanced;
  const g = el.finderAdv;
  g.replaceChildren();
  const section = (t) => {
    const s = document.createElement("div");
    s.className = "stat-section";
    s.textContent = t;
    if (STAT_DESC[t]) {
      s.classList.add("has-tip");
      s.title = STAT_DESC[t];
      s.setAttribute("data-tip", STAT_DESC[t]);
    }
    g.appendChild(s);
  };
  // Each labelled stat gets its hover explanation from STAT_DESC automatically.
  const row = (label, val) => g.appendChild(statRow(label, val, STAT_DESC[label]));

  section("Key & tempo");
  row("Key confidence", d.key_score);
  row("BPM confidence", d.bpm_confidence);
  row("Half / double time", `${d.bpm_half} / ${d.bpm_double}`);
  row("Tuning offset", `${a.tuning_cents >= 0 ? "+" : ""}${a.tuning_cents} cents`);
  row("Reference A4", `${a.a4_hz} Hz`);

  section("Spectral & loudness");
  row("Spectral centroid", `${a.spectral_centroid_hz} Hz`);
  row("Spectral rolloff", `${a.spectral_rolloff_hz} Hz`);
  row("Spectral bandwidth", `${a.spectral_bandwidth_hz} Hz`);
  row("Zero-crossing rate", a.zero_crossing_rate);
  row("RMS loudness", `${a.rms_loudness_db} dB`);
  row("Peak", `${a.peak_dbfs} dBFS`);
  row("Dynamic range", `${a.dynamic_range_db} dB`);
  row("Energy", a.energy);

  section("Signal");
  row("Duration", `${a.duration_s} s`);
  row("Sample rate", `${a.sample_rate} Hz`);
  row("Onset density", `${a.onset_density_hz} /s`);

  section("Top key candidates");
  for (const c of a.key_candidates.slice(0, 6)) {
    g.appendChild(statRow(`${c.key} · ${c.camelot}`, c.score));
  }

  section("Pitch-class distribution");
  for (const p of a.pitch_class_distribution) {
    g.appendChild(statRow(p.name, p.weight));
  }

  el.finderResult.classList.remove("hidden");
}
