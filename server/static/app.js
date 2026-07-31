"use strict";

// ---- state & elements -------------------------------------------------------
const $ = (id) => document.getElementById(id);
const el = {
  bpm: $("bpm"), bpmOut: $("bpmOut"), tap: $("tap"), metroBtn: $("metroBtn"),
  timesig: $("timesig"), countin: $("countin"), grid: $("grid"), muteRec: $("muteRec"),
  beats: $("beats"), recBtn: $("recBtn"), stopBtn: $("stopBtn"), status: $("status"),
  file: $("file"), result: $("result"), summary: $("summary"),
  chordList: $("chordList"), playBtn: $("playBtn"), playChords: $("playChords"),
  downloads: $("downloads"), sheet: $("sheet"), noteList: $("noteList"),
};

let audioCtx = null;
let metronome = null;      // scheduler handle
let recorder = null;
let recChunks = [];
let recording = false;
let lastResult = null;     // last transcription JSON, for playback
let player = null;         // Web Audio playback handle

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

// ---- recording --------------------------------------------------------------
el.recBtn.addEventListener("click", async () => {
  try {
    stopPreview();  // don't stack the preview click on top of the count-in
    ensureAudio();
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    recChunks = [];
    recorder = new MediaRecorder(stream);
    recorder.ondataavailable = (e) => { if (e.data.size) recChunks.push(e.data); };
    recorder.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      const blob = new Blob(recChunks, { type: recorder.mimeType || "audio/webm" });
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

// ---- upload & render --------------------------------------------------------
async function upload(blob, filename) {
  const fd = new FormData();
  fd.append("audio", blob, filename);
  fd.append("bpm", el.bpm.value);
  fd.append("beats", beatsPerBar());
  fd.append("beat_unit", beatUnit());
  fd.append("subdiv", el.grid.value);
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

function render(data) {
  stopPlayback();          // silence any playback from a previous result
  lastResult = data;
  el.result.classList.remove("hidden");
  const cands = data.key_candidates.map((c) => `${c.name} (${c.score})`).join(", ");
  el.summary.innerHTML = `
    <div class="stat"><span class="val">${data.key || "—"}</span><span class="lbl">Key</span></div>
    <div class="stat"><span class="val">${data.n_notes}</span><span class="lbl">Notes</span></div>
    <div class="stat"><span class="val">${data.tempo_bpm}</span><span class="lbl">BPM</span></div>
    <div class="stat"><span class="val">${data.tuning_offset_cents > 0 ? "+" : ""}${data.tuning_offset_cents}¢</span><span class="lbl">Tuning</span></div>`;
  const alt = document.createElement("p");
  alt.className = "hint";
  alt.textContent = "Key candidates: " + cands;
  el.summary.appendChild(alt);

  el.chordList.innerHTML = "";
  const chords = data.chords || [];
  if (chords.length) {
    const label = document.createElement("span");
    label.className = "chords-label";
    label.textContent = "Suggested chords";
    el.chordList.appendChild(label);
    const strip = document.createElement("div");
    strip.className = "chord-strip";
    for (const c of chords) {
      const cell = document.createElement("div");
      cell.className = "chord-cell";
      cell.innerHTML = `<span class="chord-sym">${c.symbol}</span><span class="chord-rn">${c.roman}</span>`;
      cell.title = `Measure ${c.measure + 1}`;
      strip.appendChild(cell);
    }
    el.chordList.appendChild(strip);
  }

  el.sheet.innerHTML = data.svg || "<p class='hint'>No notes detected.</p>";

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

// ---- playback (sonify the transcription with Web Audio) ---------------------
const CHORD_INTERVALS = { maj: [0, 4, 7], min: [0, 3, 7], dim: [0, 3, 6] };
const midiToFreq = (m) => 440 * Math.pow(2, (m - 69) / 12);

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

function togglePlayback() {
  if (player) { stopPlayback(); return; }
  const data = lastResult;
  if (!data || !data.notes || !data.notes.length) return;

  const ctx = ensureAudio();
  const spb = 60 / data.tempo_bpm;                 // seconds per quarter note
  const master = ctx.createGain();
  master.gain.value = 0.9;
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
      for (const iv of CHORD_INTERVALS[c.quality] || CHORD_INTERVALS.maj) {
        pianoVoice(ctx, master, midiToFreq(rootMidi + iv), start, dur * 0.9, 0.09);
      }
      endT = Math.max(endT, start + dur);
    }
  }

  // Melody on top.
  for (const n of data.notes) {
    if (n.start_ql == null || n.dur_ql == null) continue;
    const start = t0 + n.start_ql * spb;
    const dur = Math.max(0.08, n.dur_ql * spb);
    pianoVoice(ctx, master, midiToFreq(n.midi), start, dur, 0.3);
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
