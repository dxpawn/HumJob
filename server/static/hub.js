/* ============================================================================
   HumJob — hub (home) view.
   Read-only: renders practice numbers from the two keys the app already writes.
     humjob.voice.history        (realtime.js — buildSession records)
     humjob.eartrainer.history   ({t, mode, difficulty, ok})
   Nothing is written, nothing is uploaded. Loads after app.js so the tab router
   already exists; the hub navigates by clicking the real tab buttons.
   ========================================================================= */
(function () {
  "use strict";

  const VOICE_KEY = "humjob.voice.history";
  const EAR_KEY = "humjob.eartrainer.history";
  const RANGE_KEY = "humjob.voice.rangeTest";
  const SING_KEY = "humjob.singalong.history";
  const PLAN_CACHE_KEY = "humjob.coach.progress"; // {hash, t, feedback, model, language}
  const DAY = 86400000;
  const RECENT = 14; // takes folded into the headline number

  const $ = (id) => document.getElementById(id);
  const view = $("view-hub");
  if (!view) return;

  const el = {
    inTune: $("hubInTune"), sub: $("hubInTuneSub"), delta: $("hubDelta"),
    spark: $("hubSpark"), sparkEmpty: $("hubSparkEmpty"),
    streak: $("hubStreak"), takes: $("hubTakes"), week: $("hubWeek"),
    firstRun: $("hubFirstRun"), ear: $("hubEar"), mini: $("hubRtMini"),
    trend: $("hubTrend"), trendEmpty: $("hubTrendEmpty"),
    coach: $("hubCoach"), coachText: $("hubCoachText"),
    coachStatus: $("hubCoachStatus"), coachLang: $("hubCoachLang"),
  };

  function load(key) {
    try {
      const a = JSON.parse(localStorage.getItem(key));
      return Array.isArray(a) ? a : [];
    } catch (e) { return []; }
  }
  const cssVar = (name, fallback) =>
    (getComputedStyle(document.documentElement).getPropertyValue(name) || "").trim() || fallback;
  const mean = (a) => a.reduce((s, v) => s + v, 0) / a.length;
  const dayStamp = (t) => { const d = new Date(t); d.setHours(0, 0, 0, 0); return d.getTime(); };

  /* ---- canvas ------------------------------------------------------------ */
  // The CSS box is fluid, so size the bitmap at paint time rather than trusting
  // the width/height attributes.
  function fit(canvas) {
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(rect.width * dpr);
    canvas.height = Math.round(rect.height * dpr);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, rect.width, rect.height);
    return { ctx, w: rect.width, h: rect.height };
  }

  // Filled area + line, autoscaled with a little headroom.
  function drawSpark(canvas, values) {
    const c = fit(canvas);
    if (!c) return;
    const { ctx, w, h } = c;
    if (values.length < 2) return;
    const accent = cssVar("--accent", "#17B980");
    const lo = Math.max(0, Math.min.apply(null, values) - 6);
    const hi = Math.min(100, Math.max.apply(null, values) + 6);
    const span = Math.max(1, hi - lo);
    const pad = 3;
    const x = (i) => (i / (values.length - 1)) * w;
    const y = (v) => h - pad - ((v - lo) / span) * (h - pad * 2);

    ctx.beginPath();
    ctx.moveTo(0, h);
    values.forEach((v, i) => ctx.lineTo(x(i), y(v)));
    ctx.lineTo(w, h);
    ctx.closePath();
    const grad = ctx.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, hexA(accent, 0.28));
    grad.addColorStop(1, hexA(accent, 0));
    ctx.fillStyle = grad;
    ctx.fill();

    ctx.beginPath();
    values.forEach((v, i) => (i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v))));
    ctx.strokeStyle = accent;
    ctx.lineWidth = 1.75;
    ctx.lineJoin = "round";
    ctx.stroke();

    ctx.beginPath();
    ctx.arc(x(values.length - 1), y(values[values.length - 1]), 2.75, 0, Math.PI * 2);
    ctx.fillStyle = accent;
    ctx.fill();
  }

  function hexA(hex, a) {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
    if (!m) return hex;
    const n = parseInt(m[1], 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }

  // Decorative trace on the Realtime tool card: a pitch contour, not real data.
  function drawMini(canvas) {
    const c = fit(canvas);
    if (!c) return;
    const { ctx, w, h } = c;
    const accent = cssVar("--accent", "#17B980");
    const band = h * 0.34;
    ctx.fillStyle = hexA(accent, 0.1);
    ctx.fillRect(0, h / 2 - band / 2, w, band);
    ctx.beginPath();
    for (let i = 0; i <= 60; i++) {
      const t = i / 60;
      const yy = h / 2
        - Math.sin(t * Math.PI * 1.6) * h * 0.22
        - Math.sin(t * Math.PI * 9) * h * 0.05;
      i ? ctx.lineTo(t * w, yy) : ctx.moveTo(t * w, yy);
    }
    ctx.strokeStyle = accent;
    ctx.lineWidth = 1.75;
    ctx.lineJoin = "round";
    ctx.stroke();
  }

  /* ---- derived numbers --------------------------------------------------- */
  function streakDays(times) {
    if (!times.length) return 0;
    const days = new Set(times.map(dayStamp));
    const today = dayStamp(Date.now());
    // A streak stays alive if you sang today or yesterday.
    let cursor = days.has(today) ? today : (days.has(today - DAY) ? today - DAY : null);
    if (cursor == null) return 0;
    let n = 0;
    while (days.has(cursor)) { n++; cursor -= DAY; }
    return n;
  }

  const MODES = [
    ["interval", "Intervals"],
    ["chord", "Chords"],
    ["scale", "Scales"],
    ["key", "Key / degree"],
  ];

  function renderEar() {
    if (!el.ear) return;
    const hist = load(EAR_KEY);
    el.ear.replaceChildren();
    const rows = MODES.map(([mode, label]) => {
      const set = hist.filter((r) => r.mode === mode);
      const ok = set.filter((r) => r.ok).length;
      return { label, total: set.length, pct: set.length ? Math.round((100 * ok) / set.length) : null };
    });
    for (const r of rows) {
      const row = document.createElement("div");
      row.className = "acc-row";
      const head = document.createElement("div");
      head.className = "acc-head";
      const name = document.createElement("span");
      name.textContent = r.label;
      const val = document.createElement("span");
      val.className = "acc-val";
      val.textContent = r.pct == null ? "—" : r.pct + "%";
      head.append(name, val);
      const track = document.createElement("div");
      track.className = "acc-bar";
      const fill = document.createElement("i");
      fill.style.width = (r.pct || 0) + "%";
      if (r.pct != null && r.pct < 70) fill.classList.add("warn");
      track.append(fill);
      row.append(head, track);
      el.ear.append(row);
    }
  }

  function renderPractice() {
    const hist = load(VOICE_KEY);
    const times = hist.map((r) => r.t).filter((t) => typeof t === "number");
    const pcts = hist.map((r) => r.inTunePct).filter((v) => typeof v === "number");
    const recent = pcts.slice(-RECENT);
    const empty = recent.length === 0;

    if (el.inTune) {
      el.inTune.innerHTML = empty
        ? "&mdash;<small>%</small>"
        : Math.round(mean(recent)) + "<small>%</small>";
      el.inTune.classList.toggle("is-empty", empty);
    }
    if (el.sub) {
      el.sub.textContent = empty
        ? "in tune · no takes yet"
        : `in tune · last ${recent.length} take${recent.length === 1 ? "" : "s"}`;
    }
    if (el.delta) {
      let text = "";
      if (recent.length >= 4) {
        const half = Math.floor(recent.length / 2);
        const d = Math.round(mean(recent.slice(half)) - mean(recent.slice(0, half)));
        if (d !== 0) text = (d > 0 ? "+" : "") + d;
      }
      el.delta.textContent = text;
      el.delta.classList.toggle("down", text.charAt(0) === "-");
    }

    if (el.spark) el.spark.classList.toggle("hidden", empty);
    if (el.sparkEmpty) el.sparkEmpty.classList.toggle("hidden", !empty);
    if (el.spark && !empty) drawSpark(el.spark, recent);

    if (el.streak) el.streak.textContent = String(streakDays(times));
    if (el.takes) {
      const since = Date.now() - 7 * DAY;
      el.takes.textContent = String(times.filter((t) => t >= since).length);
    }

    if (el.week) {
      el.week.replaceChildren();
      const days = new Set(times.map(dayStamp));
      const today = dayStamp(Date.now());
      for (let i = 6; i >= 0; i--) {
        const d = document.createElement("i");
        if (days.has(today - i * DAY)) d.className = "on";
        el.week.append(d);
      }
    }
    if (el.firstRun) el.firstRun.classList.toggle("hidden", !empty);
  }

  /* ---- coach: offline trend lines + an opt-in practice plan -------------- */
  // progress.js (window.PG) folds the four practice logs into one PII-free report;
  // the trend lines render offline, and only pressing the button POSTs the report.
  const PLAN = { token: 0, report: null, hash: "" };

  function hashStr(s) {
    let h = 5381;
    for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
    return String(h >>> 0);
  }
  function buildReport() {
    if (typeof PG === "undefined") return null;
    return PG.buildProgressReport({
      voice: load(VOICE_KEY), ranges: load(RANGE_KEY),
      ear: load(EAR_KEY), singalong: load(SING_KEY),
    }, Date.now());
  }
  function loadPlanCache() {
    try { const c = JSON.parse(localStorage.getItem(PLAN_CACHE_KEY)); return c && typeof c === "object" ? c : null; }
    catch (e) { return null; }
  }

  function renderCoach() {
    if (typeof PG === "undefined" || !el.coach) return;
    const report = buildReport();
    PLAN.report = report;
    PLAN.hash = hashStr(JSON.stringify(report));

    // Trend lines: always offline, no key needed.
    if (el.trend) {
      const lines = PG.describeProgress(report);
      el.trend.replaceChildren();
      for (const line of lines) {
        const div = document.createElement("div");
        div.className = "hub-trend-line";
        div.textContent = line;            // deterministic strings, but keep it text
        el.trend.append(div);
      }
      if (el.trendEmpty) el.trendEmpty.classList.toggle("hidden", lines.length > 0);
    }

    // A cached plan for exactly this report shows without any request.
    const cache = loadPlanCache();
    if (cache && cache.hash === PLAN.hash && cache.feedback) {
      if (el.coachText) { el.coachText.textContent = cache.feedback; el.coachText.hidden = false; }
      if (el.coachStatus) el.coachStatus.textContent = cache.model ? "via " + cache.model : "";
      el.coach.textContent = "Refresh plan";
      if (el.coachLang && cache.language) el.coachLang.value = cache.language;
    } else {
      if (el.coachText) { el.coachText.textContent = ""; el.coachText.hidden = true; }
      if (el.coachStatus) el.coachStatus.textContent = "";
      el.coach.textContent = "Get a practice plan";
    }
  }

  // Only this button ever leaves the machine, and only a PII-free numeric report.
  // 503 = no API key (server hint); 502 = upstream error.
  function getPlan() {
    const report = PLAN.report || buildReport();
    if (!report || !el.coach) return;
    const hash = PLAN.hash || hashStr(JSON.stringify(report));
    const language = el.coachLang ? el.coachLang.value : "en";
    const token = ++PLAN.token;
    el.coach.disabled = true;
    if (el.coachStatus) el.coachStatus.textContent = "asking the coach...";
    if (el.coachText) el.coachText.hidden = true;
    fetch("/api/progress-coach", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ report, language }),
    })
      .then(async (r) => {
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.detail || r.statusText);
        return data;
      })
      .then((data) => {
        if (token !== PLAN.token) return;
        if (el.coachStatus) el.coachStatus.textContent = data.model ? "via " + data.model : "";
        if (el.coachText) { el.coachText.textContent = data.feedback || ""; el.coachText.hidden = false; }
        el.coach.disabled = false; el.coach.textContent = "Refresh plan";
        try {
          localStorage.setItem(PLAN_CACHE_KEY, JSON.stringify({ hash: hash, t: Date.now(), feedback: data.feedback, model: data.model, language: language }));
        } catch (e) { /* quota: the plan still shows, just is not cached */ }
      })
      .catch((e) => {
        if (token !== PLAN.token) return;
        if (el.coachStatus) el.coachStatus.textContent = e.message || "coaching failed";
        el.coach.disabled = false;
      });
  }

  if (el.coach) el.coach.addEventListener("click", getPlan);

  function render() {
    if (view.classList.contains("hidden")) return;
    renderPractice();
    renderEar();
    renderCoach();
    if (el.mini) drawMini(el.mini);
  }

  /* ---- navigation -------------------------------------------------------- */
  // Click the real tab so app.js's router does the view switch and lazy init.
  view.addEventListener("click", (e) => {
    const trigger = e.target.closest("[data-goto]");
    if (!trigger) return;
    const tab = document.querySelector('.tab[data-view="' + trigger.dataset.goto + '"]');
    if (tab) tab.click();
  });

  const tabs = document.getElementById("tabs");
  if (tabs) {
    tabs.addEventListener("click", (e) => {
      const btn = e.target.closest(".tab");
      if (btn && btn.dataset.view === "hub") requestAnimationFrame(render);
    });
  }

  let resizeTimer = 0;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(render, 120);
  });

  render();
})();
