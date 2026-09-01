"use strict";

// ===== Vocal range + tessitura analysis (pure math) =========================
// The number-crunching behind the Realtime tab's guided Range test. Kept in its
// own DOM-free module so it is node-testable (realtime.js touches the DOM at load
// and cannot be require()d). realtime.js owns the mic, the wizard state machine
// and all rendering; this file only turns frame arrays into a report.
//
// Frame shape used throughout: { m: midiFloat, c: clarity, dt: msSinceLastVoicedFrame }.
// dt is already computed per frame in realtime.js updateVoice(); the first frame
// after a silence has dt 0 (so it adds no time weight, which is what we want).
//
// GROUNDING RULE (project style): every population figure below traces to a cited
// study. Nothing here is invented; the one modelling assumption (the SD) is marked
// ASSUMPTION and printed in the UI via describeModel().

const VA = (() => {
  // ---- cited population norms ----------------------------------------------
  // Maximum phonational frequency range (lowest modal note to highest falsetto),
  // in semitones. Source: Hollien, Dew and Philips 1971, "Phonational Frequency
  // Ranges of Adults", Journal of Speech and Hearing Research 14:755. n=332 male
  // and n=202 female, ages 18-36. Reported means: 38 ST (male), 37 ST (female).
  const NORMS = {
    male: { meanST: 38.0, n: 332 },
    female: { meanST: 37.0, n: 202 },
    // n-weighted average of the two groups: (332*38 + 202*37) / 534 = 37.62 -> 37.6
    combined: { meanST: 37.6, n: 534 },
    // ASSUMPTION: Hollien 1971 does not report an SD. Voice range profile studies
    // cluster near 4 ST (young-female normative VRP, J Voice 2021: 34.7 +/- 3.9 ST),
    // so we model SD = 4.0 and say so in the UI. Not a measured value.
    sdST: 4.0,
    source: "Hollien, Dew and Philips 1971, JSHR 14:755",
  };

  // ---- approximate vocal fach (voice-type) ranges --------------------------
  // MIDI note ranges, classical-pedagogy convention. Approximate by nature: true
  // fach also depends on timbre and passaggio, which we do not measure. Group tags
  // let an optional male/female selector restrict the candidates.
  const FACHS = [
    { id: "bass", label: "Bass", group: "male", lo: 40, hi: 64 },        // E2-E4
    { id: "baritone", label: "Baritone", group: "male", lo: 45, hi: 69 }, // A2-A4
    { id: "tenor", label: "Tenor", group: "male", lo: 48, hi: 72 },       // C3-C5
    { id: "contralto", label: "Contralto (alto)", group: "female", lo: 53, hi: 77 }, // F3-F5
    { id: "mezzo", label: "Mezzo-soprano", group: "female", lo: 57, hi: 81 },        // A3-A5
    { id: "soprano", label: "Soprano", group: "female", lo: 60, hi: 84 },            // C4-C6
  ];

  // Stable-hold gate. Numerically identical to realtime.js RANGE_STABLE_* (lines
  // ~240-242); duplicated here on purpose because realtime.js is not importable.
  // Keep the two in sync if either changes.
  const RANGE_GATE = { stableCents: 40, stableMinFrames: 5, minClarity: 0.6 };

  const timeWeight = (dt) => Math.min(Number.isFinite(dt) ? dt : 0, 100) / 1000; // seconds, clamp gaps

  // Time-weighted per-semitone histogram of voiced frames. Weight is the frame's
  // dt in seconds (clamped to 100 ms so a post-silence gap cannot dump time onto
  // one note). Clarity-gated. Returns { bins: {midiInt: seconds}, totalS }.
  function timeWeightedHistogram(frames, gate) {
    gate = gate || RANGE_GATE;
    const bins = {};
    let totalS = 0;
    for (const f of (frames || [])) {
      if (!f || !Number.isFinite(f.m)) continue;
      const c = f.c != null ? f.c : 1;
      if (c < gate.minClarity) continue;
      const w = timeWeight(f.dt);
      if (w <= 0) continue;
      const k = Math.round(f.m);
      bins[k] = (bins[k] || 0) + w;
      totalS += w;
    }
    return { bins, totalS };
  }

  // Weighted quantile (inverse-CDF, no interpolation) over [{v, w}] pairs.
  function weightedQuantile(pairs, q) {
    const a = (pairs || []).filter((p) => p && Number.isFinite(p.v) && p.w > 0).sort((x, y) => x.v - y.v);
    if (!a.length) return null;
    const total = a.reduce((s, p) => s + p.w, 0);
    if (total <= 0) return null;
    const target = q * total;
    let cum = 0;
    for (const p of a) { cum += p.w; if (cum >= target) return p.v; }
    return a[a.length - 1].v;
  }

  // Tessitura: the time-weighted pitch distribution of a comfortable melody. The
  // band p25..p75 is where the middle 50% of singing time sits; p50 is the median
  // placement; p10/p90 are shown as context. null under 10 s of voiced time (too
  // little to characterise). Uses raw midiFloat (sub-semitone) for the quantiles.
  function tessituraFromFrames(frames, gate) {
    gate = gate || RANGE_GATE;
    const pairs = [];
    let voicedS = 0;
    for (const f of (frames || [])) {
      if (!f || !Number.isFinite(f.m)) continue;
      const c = f.c != null ? f.c : 1;
      if (c < gate.minClarity) continue;
      const w = timeWeight(f.dt);
      if (w <= 0) continue;
      pairs.push({ v: f.m, w });
      voicedS += w;
    }
    if (voicedS < 10) return null;
    return {
      p10: weightedQuantile(pairs, 0.10),
      p25: weightedQuantile(pairs, 0.25),
      p50: weightedQuantile(pairs, 0.50),
      p75: weightedQuantile(pairs, 0.75),
      p90: weightedQuantile(pairs, 0.90),
      voicedS,
    };
  }

  function voicedSeconds(frames, gate) {
    gate = gate || RANGE_GATE;
    let s = 0;
    for (const f of (frames || [])) {
      if (!f || !Number.isFinite(f.m)) continue;
      const c = f.c != null ? f.c : 1;
      if (c < gate.minClarity) continue;
      s += timeWeight(f.dt);
    }
    return s;
  }

  function meanClarity(frames) {
    let s = 0, n = 0;
    for (const f of (frames || [])) {
      if (!f || !Number.isFinite(f.m) || f.c == null) continue;
      s += f.c; n++;
    }
    return n ? s / n : null;
  }

  // erf via Abramowitz and Stegun 7.1.26 (max error 1.5e-7). Deterministic and
  // testable, so we avoid pulling in a stats library for one CDF.
  function erf(x) {
    const sign = x < 0 ? -1 : 1;
    const ax = Math.abs(x);
    const t = 1 / (1 + 0.3275911 * ax);
    const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-ax * ax);
    return sign * y;
  }
  function normalCdf(x, mean, sd) {
    if (!(sd > 0)) return x < mean ? 0 : 1;
    return 0.5 * (1 + erf((x - mean) / (sd * Math.SQRT2)));
  }

  // "Wider than X% of people", from the normal model above. pct clamped to 1..99
  // (we make no parametric-tail claim of 0 or 100). Unknown group -> combined.
  function rangePercentile(semitones, group) {
    if (!Number.isFinite(semitones)) return null;
    const g = NORMS[group] && group !== "sdST" && group !== "source" ? group : "combined";
    const norm = NORMS[g];
    const pct = Math.max(1, Math.min(99, Math.round(100 * normalCdf(semitones, norm.meanST, NORMS.sdST))));
    return { pct, meanST: norm.meanST, sdST: NORMS.sdST, n: norm.n, group: g };
  }

  // Stable pitch range of a run of {m, c} frames: a pitch counts only once held
  // within stableCents for stableMinFrames at sufficient clarity, so glitches and
  // octave slips do not widen it. Mirrors realtime.js rangeFromFrames semantics.
  function stableRange(frames, gate) {
    gate = gate || RANGE_GATE;
    let ref = null, count = 0, lo = null, hi = null;
    for (const f of (frames || [])) {
      const midi = f && f.m;
      const clarity = f && f.c != null ? f.c : 1;
      if (!Number.isFinite(midi) || clarity < gate.minClarity) { ref = null; count = 0; continue; }
      if (ref != null && Math.abs(midi - ref) * 100 <= gate.stableCents) { count++; ref = ref * 0.7 + midi * 0.3; }
      else { ref = midi; count = 1; }
      if (count >= gate.stableMinFrames) {
        if (lo == null || midi < lo) lo = midi;
        if (hi == null || midi > hi) hi = midi;
      }
    }
    return lo == null ? null : { lo, hi };
  }

  // Second guard against octave-glitch extremes: the candidate extreme must have
  // been held for at least minS seconds within tolST of it (time-weighted).
  function extremeSupported(frames, extremeMidi, tolST, minS) {
    tolST = tolST == null ? 1.0 : tolST;
    minS = minS == null ? 0.4 : minS;
    if (!Number.isFinite(extremeMidi)) return false;
    let s = 0;
    for (const f of (frames || [])) {
      if (!f || !Number.isFinite(f.m)) continue;
      const c = f.c != null ? f.c : 1;
      if (c < RANGE_GATE.minClarity) continue;
      if (Math.abs(f.m - extremeMidi) <= tolST) s += timeWeight(f.dt);
    }
    return s >= minS;
  }

  const overlapST = (aLo, aHi, bLo, bHi) => Math.max(0, Math.min(aHi, bHi) - Math.max(aLo, bLo));

  // Rank the fachs by fit to the measured { lo, hi, tessMedian }. coverage = how
  // much of the fach's textbook span the singer covers; centerFit = how close the
  // tessitura median sits to the fach centre (dominant when available, since range
  // alone cannot separate overlapping fachs). group restricts the candidate set.
  function classifyVoice(measured, group) {
    const lo = measured && measured.lo, hi = measured && measured.hi;
    if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi < lo) return [];
    const tess = measured && Number.isFinite(measured.tessMedian) ? measured.tessMedian : null;
    const cands = FACHS.filter((f) => (group === "male" || group === "female") ? f.group === group : true);
    const out = cands.map((f) => {
      const span = f.hi - f.lo;
      const coverage = span > 0 ? overlapST(lo, hi, f.lo, f.hi) / span : 0;
      let score, centerFit = null;
      if (tess != null) {
        const center = (f.lo + f.hi) / 2;
        centerFit = Math.max(0, 1 - Math.abs(tess - center) / 6);
        score = 0.45 * coverage + 0.55 * centerFit;
      } else {
        score = coverage;
      }
      return { id: f.id, label: f.label, group: f.group, score, coverage, centerFit };
    });
    out.sort((a, b) => b.score - a.score);
    return out;
  }

  function stepQuality(frames, extremeMidi) {
    const vs = voicedSeconds(frames);
    const mc = meanClarity(frames);
    const flags = [];
    if (vs < 1.5) flags.push("short: little voiced audio captured");
    if (mc != null && mc < 0.75) flags.push("weak or breathy signal");
    if (extremeMidi != null && !extremeSupported(frames, extremeMidi)) flags.push("extreme note held only briefly");
    if (extremeMidi != null && extremeMidi <= 33) flags.push("near the detector's low-frequency limit");
    return { voicedS: +vs.toFixed(1), meanClarity: mc != null ? +mc.toFixed(2) : null, flags };
  }

  // Turn the five steps' frame arrays into the full report. steps = { comfort,
  // low, highFull, highFalsetto (null if skipped), tessitura }. Errors surface as
  // { error } rather than throwing, so the UI can prompt a retry.
  function buildRangeReport(input) {
    const group = (NORMS[input && input.group] && input.group !== "sdST" && input.group !== "source") ? input.group : "combined";
    const steps = (input && input.steps) || {};
    const low = stableRange(steps.low);
    const highFull = stableRange(steps.highFull);
    const highFalsetto = steps.highFalsetto ? stableRange(steps.highFalsetto) : null;

    const lo = low ? low.lo : null;
    const hiFull = highFull ? highFull.hi : null;
    const hiFalsetto = highFalsetto ? highFalsetto.hi : null;
    const hiCandidates = [hiFull, hiFalsetto].filter((v) => v != null);
    const hiExt = hiCandidates.length ? Math.max(...hiCandidates) : null;

    if (lo == null || hiExt == null) {
      return { error: "incomplete", group, low, highFull, highFalsetto };
    }
    if (hiExt < lo) {
      return { error: "inconsistent", group, lo, hi: hiExt };
    }

    const rnd = Math.round;
    const attemptedFalsetto = steps.highFalsetto != null && highFalsetto != null;
    const falsettoUsed = hiFalsetto != null && (hiFull == null || hiFalsetto > hiFull);
    const protocolComparable = attemptedFalsetto; // explored the top including falsetto register

    const fullST = hiFull != null ? rnd(hiFull) - rnd(lo) : null;
    const extST = rnd(hiExt) - rnd(lo);
    const basisST = extST; // == fullST when falsetto was skipped (hiExt == hiFull)
    const percentile = rangePercentile(basisST, group);
    if (percentile) percentile.basis = protocolComparable ? "extended" : "fullVoice";

    const tessitura = tessituraFromFrames(steps.tessitura);
    const voiceTypes = classifyVoice({ lo, hi: hiExt, tessMedian: tessitura ? tessitura.p50 : null }, group);

    const quality = {
      comfort: stepQuality(steps.comfort, null),
      low: stepQuality(steps.low, lo),
      highFull: stepQuality(steps.highFull, hiFull),
      highFalsetto: steps.highFalsetto ? stepQuality(steps.highFalsetto, hiFalsetto) : null,
      tessitura: stepQuality(steps.tessitura, null),
    };

    // Semantic caveats only. Per-step signal-quality flags live in `quality` and
    // are surfaced by the UI's Signal-quality section, so they are not repeated here.
    const caveats = [];
    if (!protocolComparable) caveats.push("Falsetto step skipped, so the percentile uses your full-voice range and is a lower bound.");
    if (falsettoUsed) caveats.push("Your highest note used falsetto or head voice.");
    if (tessitura == null) caveats.push("Not enough steady singing in the melody step to characterise your tessitura.");
    const flaggedSteps = Object.keys(quality).filter((k) => quality[k] && quality[k].flags.length);
    if (flaggedSteps.length) caveats.push("Some steps had weak or brief audio (see Signal quality); the affected numbers are less reliable.");

    return {
      group,
      lo: rnd(lo), hiFull: hiFull != null ? rnd(hiFull) : null,
      hiFalsetto: hiFalsetto != null ? rnd(hiFalsetto) : null, hiExt: rnd(hiExt),
      loRaw: lo, hiExtRaw: hiExt,
      fullST, fullOct: fullST != null ? +(fullST / 12).toFixed(1) : null,
      extST, extOct: +(extST / 12).toFixed(1),
      falsettoUsed, protocolComparable,
      tessitura, percentile, voiceTypes, quality, caveats,
    };
  }

  // The exact assumption lines the UI prints below the percentile. Built here (not
  // in realtime.js) so a test can pin the wording and the no-dash rule.
  function describeModel(group, protocolComparable) {
    const g = (NORMS[group] && group !== "sdST" && group !== "source") ? group : "combined";
    const norm = NORMS[g];
    const lines = [];
    lines.push("Percentile model: a normal distribution over maximum phonational range in semitones.");
    lines.push(`Mean ${norm.meanST} ST, n=${norm.n}, from ${NORMS.source} (adults 18-36, lowest modal note to highest falsetto).`);
    if (g === "combined") {
      lines.push("Combined mean is the n-weighted average of the male (38 ST, n=332) and female (37 ST, n=202) groups: (332*38 + 202*37) / 534 = 37.6 ST.");
    }
    lines.push(`SD ${NORMS.sdST} ST is an assumption, not measured by that study. Voice range profile studies report SDs near 4 ST (young-female normative VRP, J Voice 2021: 34.7 +/- 3.9 ST).`);
    if (!protocolComparable) {
      lines.push("You skipped the falsetto step, so your measured range excludes falsetto while the reference protocol includes it. The percentile is a lower bound.");
    }
    return lines;
  }

  return {
    NORMS, FACHS, RANGE_GATE,
    timeWeightedHistogram, weightedQuantile, tessituraFromFrames, voicedSeconds, meanClarity,
    normalCdf, rangePercentile, stableRange, extremeSupported, classifyVoice,
    stepQuality, buildRangeReport, describeModel,
  };
})();

if (typeof window !== "undefined") window.VA = VA;
if (typeof module !== "undefined" && module.exports) module.exports = VA;
