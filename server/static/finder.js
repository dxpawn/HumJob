"use strict";

// Pitch Finder rendering.
//
// app.js declares `renderAnalysis` as a top-level function in a classic script, so it
// lives on the global object and is resolved at call time. Loading this file after
// app.js therefore replaces the renderer without touching app.js: analyzeAudio() still
// does the upload, the status line and the show/hide, and calls whatever
// `renderAnalysis` is current when the response lands.
//
// Everything below is drawn from the /api/analyze payload that already exists
// (key, key_score, camelot, camelot_neighbors, bpm, bpm_half, bpm_double,
// bpm_confidence, advanced.*) - no server change.

const PF = (() => {
  const $ = (id) => document.getElementById(id);
  const TAU = Math.PI * 2;

  // Camelot rings, 1-12 clockwise from the top. Outer = major (B), inner = minor (A).
  const RING_B = ["B", "F\u266F", "D\u266D", "A\u266D", "E\u266D", "B\u266D",
    "F", "C", "G", "D", "A", "E"];
  const RING_A = ["A\u266Dm", "E\u266Dm", "B\u266Dm", "Fm", "Cm", "Gm",
    "Dm", "Am", "Em", "Bm", "F\u266Fm", "C\u266Fm"];

  const MAJOR_SCALE = [0, 2, 4, 5, 7, 9, 11];
  const MINOR_SCALE = [0, 2, 3, 5, 7, 8, 10];

  // Full key name for a bare Camelot code, so the "mixes with" chips read as music
  // and not just as coordinates.
  function keyOfCode(code) {
    const n = parseInt(code, 10);
    const letter = code[code.length - 1].toUpperCase();
    if (!n || n < 1 || n > 12) return code;
    return letter === "A" ? RING_A[n - 1].replace(/m$/, "") + " minor"
      : RING_B[n - 1] + " major";
  }

  const cssVar = (name, fallback) => {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  };

  function colors() {
    return {
      accent: cssVar("--accent", "#17B980"),
      accent2: cssVar("--accent-2", "#21CE91"),
      accentInk: cssVar("--accent-ink", "#04140E"),
      ink: cssVar("--ink", "#EDF0F2"),
      ink2: cssVar("--ink-2", "#B9C1C8"),
      muted: cssVar("--muted", "#8E979E"),
      border: cssVar("--border", "#262B31"),
      card: cssVar("--card", "#15181B"),
      panel: cssVar("--panel", "#15181B"),
      raised: cssVar("--raised", "#1C2126"),
      mono: cssVar("--font-mono", "monospace"),
      display: cssVar("--font-display", "Georgia, serif"),
    };
  }

  // Size the bitmap to the CSS box at paint time (the pattern hub.js uses), so the
  // wheel stays sharp and is never stretched by a fixed width/height attribute.
  function fit(cv) {
    const rect = cv.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const W = Math.max(1, Math.round(rect.width));
    const H = Math.max(1, Math.round(rect.height));
    cv.width = Math.round(W * dpr);
    cv.height = Math.round(H * dpr);
    const g = cv.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);
    return { g, W, H };
  }

  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };

  function mmss(seconds) {
    const s = Math.max(0, Math.round(seconds || 0));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }

  // ---- the wheel ------------------------------------------------------------

  let wheelData = null;   // kept so resize / tab-switch can redraw

  function drawWheel() {
    const cv = $("finderWheel");
    if (!cv || !wheelData) return;
    const rect = cv.getBoundingClientRect();
    if (rect.width < 40) return;    // still hidden; a later hook will redraw

    const c = colors();
    const { g, W, H } = fit(cv);
    const cx = W / 2, cy = H / 2;
    const R = Math.min(W, H) / 2 - 4;
    const found = wheelData.camelot;
    const near = wheelData.neighbors;
    const rings = [
      { r0: R * 0.74, r1: R, suffix: "B", keys: RING_B, big: true },
      { r0: R * 0.50, r1: R * 0.72, suffix: "A", keys: RING_A, big: false },
    ];

    for (const ring of rings) {
      ring.keys.forEach((name, i) => {
        const code = (i + 1) + ring.suffix;
        const mid = (i / 12) * TAU - Math.PI / 2;
        const a0 = mid - TAU / 24 + 0.012;
        const a1 = mid + TAU / 24 - 0.012;
        const isFound = code === found;
        const isNear = near.indexOf(code) >= 0;

        g.beginPath();
        g.arc(cx, cy, ring.r1, a0, a1);
        g.arc(cx, cy, ring.r0, a1, a0, true);
        g.closePath();
        g.fillStyle = isFound ? c.accent : isNear ? "rgba(23,185,128,0.16)" : c.card;
        g.fill();
        g.strokeStyle = isFound ? c.accent2 : isNear ? "rgba(23,185,128,0.55)" : c.border;
        g.lineWidth = 1;
        g.stroke();

        // Upright labels: a rotated ring reads upside-down across the bottom half.
        const rt = (ring.r0 + ring.r1) / 2;
        const tx = cx + Math.cos(mid) * rt, ty = cy + Math.sin(mid) * rt;
        g.textAlign = "center";
        g.textBaseline = "middle";
        g.fillStyle = isFound ? c.accentInk : isNear ? c.accent2 : c.ink2;
        g.font = `${isFound ? 700 : 500} ${ring.big ? 13 : 12}px ${c.mono}`;
        g.fillText(name, tx, ty - (ring.big ? 6 : 5));
        g.fillStyle = isFound ? c.accentInk : isNear ? c.accent2 : c.muted;
        g.font = `500 9px ${c.mono}`;
        g.fillText(code, tx, ty + (ring.big ? 7 : 6));
      });
    }

    // Centre: the answer itself.
    g.beginPath();
    g.arc(cx, cy, R * 0.47, 0, TAU);
    g.fillStyle = c.panel;
    g.fill();
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillStyle = c.ink;
    g.font = `400 ${Math.round(R * 0.26)}px ${c.display}`;
    g.fillText(wheelData.key, cx, cy - R * 0.08);
    g.fillStyle = c.accent;
    g.font = `700 ${Math.round(R * 0.11)}px ${c.mono}`;
    g.fillText(found, cx, cy + R * 0.16);
    g.fillStyle = c.muted;
    g.font = `500 ${Math.round(R * 0.072)}px ${c.mono}`;
    g.fillText(`confidence ${wheelData.score}`, cx, cy + R * 0.31);
  }

  // ---- right rail: tempo, tuning, signal ------------------------------------

  function kicker(text) { return el("div", "pf-kicker", text); }

  function tempoCard(d) {
    const box = el("div", "pf-tile");
    box.append(kicker("Tempo"));
    const row = el("div", "pf-big");
    row.append(el("span", "pf-big-val", String(Math.round(d.bpm))),
      el("span", "pf-big-unit", "BPM"));
    box.append(row);
    const alts = el("div", "pf-pills");
    alts.append(el("span", "pf-pill", `half ${Math.round(d.bpm_half)}`),
      el("span", "pf-pill", `double ${Math.round(d.bpm_double)}`));
    box.append(alts);
    const conf = d.bpm_confidence;
    box.append(el("div", "pf-note",
      `${conf >= 0.75 ? "Beat is regular" : conf >= 0.45 ? "Beat is fairly steady"
        : "Beat is ambiguous"} - confidence ${conf}.`));
    return box;
  }

  function tuningCard(a) {
    const cents = a.tuning_cents;
    const box = el("div", "pf-tile");
    box.append(kicker("Tuning"));
    const row = el("div", "pf-big pf-big-sm");
    row.append(el("span", "pf-big-val", `${cents > 0 ? "+" : cents < 0 ? "\u2212" : ""}${Math.abs(cents)}\u00A2`),
      el("span", "pf-big-unit", `A4 = ${a.a4_hz} Hz`));
    box.append(row);

    const meter = el("div", "pf-meter");
    meter.append(el("div", "pf-meter-centre"), el("div", "pf-meter-zone"));
    const needle = el("div", "pf-meter-needle");
    // The scale is +/-50 cents (a semitone wide); clamp so an extreme stays on screen.
    needle.style.left = `${Math.max(1.5, Math.min(98.5, 50 + cents)).toFixed(1)}%`;
    meter.append(needle);
    box.append(meter);
    const scale = el("div", "pf-meter-scale");
    scale.append(el("span", null, "\u221250\u00A2"), el("span", null, "0"),
      el("span", null, "+50\u00A2"));
    box.append(scale);
    return box;
  }

  function signalCard(a) {
    const box = el("div", "pf-tile pf-tile-rows");
    const row = (k, v) => {
      const r = el("div", "stat-row");
      r.append(el("span", "k", k), el("span", "v", v));
      box.append(r);
    };
    row("Duration", mmss(a.duration_s));
    row("Sample rate", `${a.sample_rate} Hz`);
    row("Onset density", `${a.onset_density_hz} /s`);
    row("Dynamic range", `${a.dynamic_range_db} dB`);
    return box;
  }

  // ---- pitch-class weight ---------------------------------------------------

  // Which pitch classes the detected key's scale uses, so the histogram shows the
  // evidence the key estimate is actually built on.
  function scaleSet(d, pcDist) {
    const tonicName = String(d.key || "").trim().split(/\s+/)[0];
    const mode = /minor/i.test(d.key || "") ? "minor" : "major";
    const tonic = pcDist.findIndex((p) => p.name === tonicName);
    if (tonic < 0) return { tonic: -1, set: new Set() };
    const steps = mode === "minor" ? MINOR_SCALE : MAJOR_SCALE;
    return { tonic, set: new Set(steps.map((s) => (tonic + s) % 12)) };
  }

  function chromaPanel(d, a) {
    const box = $("finderChroma");
    if (!box) return;
    box.replaceChildren();
    const pc = a.pitch_class_distribution || [];
    if (!pc.length) return;
    const max = pc.reduce((m, p) => Math.max(m, p.weight), 0) || 1;
    const { tonic, set } = scaleSet(d, pc);

    const chart = el("div", "pf-chroma-chart");
    pc.forEach((p, i) => {
      const col = el("div", "pf-chroma-col");
      const bar = el("div", "pf-chroma-bar");
      bar.style.height = `${Math.max(4, (p.weight / max) * 100).toFixed(1)}%`;
      if (i === tonic) bar.classList.add("tonic");
      else if (set.has(i)) bar.classList.add("in-key");
      const lbl = el("span", "pf-chroma-lbl", p.name);
      if (i === tonic) lbl.classList.add("tonic");
      else if (set.has(i)) lbl.classList.add("in-key");
      col.title = `${p.name} - weight ${p.weight}`;
      col.append(bar, lbl);
      chart.append(col);
    });
    box.append(chart);

    if (tonic >= 0) {
      const inKey = pc.reduce((s, p, i) => s + (set.has(i) ? p.weight : 0), 0);
      box.append(el("div", "pf-note",
        `${Math.round(inKey * 100)}% of the weight sits inside the ${d.key} scale - that is what the wheel is reading.`));
    }
  }

  // ---- runners-up -----------------------------------------------------------

  function runnersPanel(a) {
    const box = $("finderRunners");
    if (!box) return;
    box.replaceChildren();
    const cands = (a.key_candidates || []).slice(0, 5);
    if (!cands.length) return;
    const top = Math.max(0.0001, cands[0].score);
    const list = el("div", "pf-runners");
    cands.forEach((c, i) => {
      const item = el("div", "pf-runner" + (i === 0 ? " lead" : ""));
      const head = el("div", "pf-runner-head");
      const name = el("span", "pf-runner-key", c.key + " ");
      name.append(el("span", "pf-runner-code", c.camelot));
      head.append(name, el("span", "pf-runner-score", String(c.score)));
      const track = el("div", "pf-runner-track");
      const fill = el("div", "pf-runner-fill");
      fill.style.width = `${Math.max(3, Math.min(100, (c.score / top) * 100)).toFixed(1)}%`;
      track.append(fill);
      item.append(head, track);
      list.append(item);
    });
    box.append(list);
  }

  // ---- advanced statistics --------------------------------------------------

  // Reuses app.js's statRow()/STAT_DESC when they are in scope (same classic-script
  // scope), and falls back to a local row builder if they ever move.
  function advRow(g, label, value) {
    if (typeof statRow === "function") {
      const desc = typeof STAT_DESC !== "undefined" ? STAT_DESC[label] : null;
      g.append(statRow(label, value, desc));
      return;
    }
    const r = el("div", "stat-row");
    r.append(el("span", "k", label), el("span", "v", value));
    g.append(r);
  }

  function advancedPanel(d, a) {
    const g = $("finderAdv");
    if (!g) return;
    g.replaceChildren();
    const col = () => { const c = el("div", "pf-adv-col"); g.append(c); return c; };

    let c1 = col();
    c1.append(el("div", "stat-section", "Spectral"));
    advRow(c1, "Spectral centroid", `${a.spectral_centroid_hz} Hz`);
    advRow(c1, "Spectral rolloff", `${a.spectral_rolloff_hz} Hz`);
    advRow(c1, "Spectral bandwidth", `${a.spectral_bandwidth_hz} Hz`);
    advRow(c1, "Zero-crossing rate", String(a.zero_crossing_rate));

    let c2 = col();
    c2.append(el("div", "stat-section", "Loudness"));
    advRow(c2, "RMS loudness", `${a.rms_loudness_db} dB`);
    advRow(c2, "Peak", `${a.peak_dbfs} dBFS`);
    advRow(c2, "Energy", String(a.energy));
    advRow(c2, "Dynamic range", `${a.dynamic_range_db} dB`);

    let c3 = col();
    c3.append(el("div", "stat-section", "Key & tempo"));
    advRow(c3, "Key confidence", String(d.key_score));
    advRow(c3, "BPM confidence", String(d.bpm_confidence));
    advRow(c3, "Half / double time", `${d.bpm_half} / ${d.bpm_double}`);
    advRow(c3, "Tuning offset", `${a.tuning_cents >= 0 ? "+" : ""}${a.tuning_cents} cents`);
    advRow(c3, "Reference A4", `${a.a4_hz} Hz`);
  }

  // ---- entry point ----------------------------------------------------------

  function render(d) {
    const a = d.advanced || {};

    wheelData = {
      key: d.key,
      camelot: d.camelot,
      score: d.key_score,
      neighbors: d.camelot_neighbors || [],
    };

    const chips = $("finderNeighbors");
    if (chips) {
      chips.replaceChildren(el("span", "nlabel", "Mixes with"));
      for (const code of wheelData.neighbors) {
        chips.append(el("span", "chip", `${code} \u00B7 ${keyOfCode(code)}`));
      }
    }

    const rail = $("finderTiles");
    if (rail) rail.replaceChildren(tempoCard(d), tuningCard(a), signalCard(a));

    chromaPanel(d, a);
    runnersPanel(a);
    advancedPanel(d, a);

    // The dropzone is the empty state; once there is a result, the header's
    // "Analyze another" control takes over.
    const upload = $("finderUpload");
    if (upload) upload.hidden = true;
    const again = $("finderAgain");
    if (again) again.hidden = false;

    // app.js's renderAnalysis ended with this reveal, so the override owns it now.
    // The wheel is drawn after it: a canvas inside a display:none panel has no box
    // to measure, so fit() would bail and leave the default 300x150 bitmap.
    const panel = $("finderResult");
    if (panel) panel.classList.remove("hidden");
    requestAnimationFrame(drawWheel);
  }

  // Redraw hooks: the canvas has no width while its view is hidden, so redraw when
  // the tab is opened, on resize, and on a colour-scheme flip.
  function hooks() {
    window.addEventListener("resize", drawWheel);
    const tab = document.querySelector('.tab[data-view="finder"]');
    if (tab) tab.addEventListener("click", () => requestAnimationFrame(drawWheel));
    document.querySelectorAll('[data-goto="finder"]').forEach((b) =>
      b.addEventListener("click", () => requestAnimationFrame(drawWheel)));
    if (window.matchMedia) {
      const mq = window.matchMedia("(prefers-color-scheme: dark)");
      if (mq.addEventListener) mq.addEventListener("change", () => requestAnimationFrame(drawWheel));
    }
    const again = $("finderAgain");
    const file = $("finderFile");
    if (again && file) again.addEventListener("click", () => file.click());
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", hooks);
  } else {
    hooks();
  }

  return { render, drawWheel, keyOfCode };
})();

// Replace app.js's renderer. analyzeAudio() resolves this global at call time.
window.renderAnalysis = PF.render;
