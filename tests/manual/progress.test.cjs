"use strict";

// Headless unit tests for the progress aggregator pure core (progress.js).
//
// hub.js owns the DOM + localStorage reads; these cover only the deterministic fold:
// trend delta / OLS slope, low-confidence gating and the "not enough" wording, recurring
// worst-note counting, range first/last, per-mode recent ear accuracy, empty-input safety,
// and the privacy invariant (no t / frames / audio / filename anywhere in the report).
//
// Run: node tests/manual/progress.test.cjs

const path = require("path");
const PG = require(path.join(__dirname, "..", "..", "server", "static", "progress.js"));

let failures = 0;
const fail = (msg) => { console.error("  FAIL: " + msg); failures++; };
const eq = (got, want, msg) => {
  if (got !== want) fail(`${msg}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
};
const ok = (cond, msg) => { if (!cond) fail(msg); };

const NOW = 1_700_000_000_000;
const DAY = 86400000;
// takes on consecutive recent days, oldest first.
const voiceTake = (pct, daysAgo, extra) =>
  Object.assign({ t: NOW - daysAgo * DAY, inTunePct: pct, bestSustainS: 3, steadinessMedianC: 15, vibrato: null, rangeLo: 48, rangeHi: 72 }, extra || {});

// ---- inTune trend: improving -> positive delta + slope -----------------------
{
  const voice = [];
  for (let i = 0; i < 10; i++) voice.push(voiceTake(50 + i * 3, 10 - i)); // 50..77
  const rep = PG.buildProgressReport({ voice }, NOW);
  ok(rep.inTune.delta > 0, "improving series -> positive delta");
  ok(rep.inTune.slopePerTake > 0, "improving series -> positive slope");
  eq(rep.inTune.best, 77, "best is the max");
  eq(rep.inTune.n, 10, "n counts non-null inTune takes");
  ok(!rep.lowConfidence.voice, "10 takes -> voice not low confidence");
}
// flat -> ~0
{
  const voice = [];
  for (let i = 0; i < 8; i++) voice.push(voiceTake(60, 8 - i));
  const rep = PG.buildProgressReport({ voice }, NOW);
  eq(rep.inTune.delta, 0, "flat series -> zero delta");
  eq(rep.inTune.slopePerTake, 0, "flat series -> zero slope");
}
// declining -> negative
{
  const voice = [];
  for (let i = 0; i < 8; i++) voice.push(voiceTake(80 - i * 4, 8 - i));
  const rep = PG.buildProgressReport({ voice }, NOW);
  ok(rep.inTune.delta < 0, "declining series -> negative delta");
  ok(rep.inTune.slopePerTake < 0, "declining series -> negative slope");
}

// ---- small n -> low confidence + "not enough" line ---------------------------
{
  const voice = [voiceTake(60, 2), voiceTake(65, 1)];
  const rep = PG.buildProgressReport({ voice }, NOW);
  ok(rep.lowConfidence.voice, "2 takes -> voice low confidence");
  const lines = PG.describeProgress(rep);
  ok(lines.some((l) => /In tune: not enough takes yet \(2 of 5\)/.test(l)), "emits the not-enough voice line");
  ok(!lines.some((l) => /->/.test(l)), "no trend arrow when low confidence");
}

// ---- recurringWorst counts across takes and drops singletons -----------------
{
  const mkSA = (worst) => ({
    t: NOW, report: {
      summary: { inTunePct: 0.7 }, signedBiasCents: -10,
      leaps: { leap: { n: 3, hitPct: 0.3 }, step: { n: 5, hitPct: 0.8 }, missedLeapLandings: 2 },
      register: { low: {}, mid: {}, high: {}, weakest: "high" },
      worstNotes: worst.map((name) => ({ name, bar: 1, hitPct: 0.2, meanCents: -30 })),
      octaveSlips: 1, lowConfidence: false,
    },
  });
  const singalong = [mkSA(["C5", "A4"]), mkSA(["C5", "F4"]), mkSA(["C5", "A4"])];
  const rep = PG.buildProgressReport({ singalong }, NOW);
  eq(rep.singalong.recurringWorst.length, 2, "C5 (3x) and A4 (2x) survive, F4 (1x) dropped");
  eq(rep.singalong.recurringWorst[0].name, "C5", "most frequent first");
  eq(rep.singalong.recurringWorst[0].count, 3, "C5 counted 3 times");
  eq(rep.singalong.octaveSlipsTotal, 3, "octave slips summed");
  eq(rep.singalong.weakestRegister, "high", "most frequent weakest register");
  ok(rep.singalong.leapMinusStepPct < 0, "leaps worse than steps -> negative pp");
  ok(!rep.lowConfidence.singalong, "3 takes -> singalong not low confidence");
}

// ---- range: first/last use earliest and latest; deltaST sign correct ---------
{
  const mkR = (t, lo, hiExt, extST) => ({ t, report: {
    lo, hiExt, extST, group: "male",
    tessitura: { p25: 50, p50: 55, p75: 60 },
    voiceTypes: [{ label: "Baritone" }, { label: "Tenor" }, { label: "Bass" }],
  } });
  const ranges = [mkR(NOW - 5 * DAY, 45, 64, 19), mkR(NOW - 1 * DAY, 43, 67, 24)];
  const rep = PG.buildProgressReport({ ranges }, NOW);
  eq(rep.range.tests, 2, "two range tests");
  eq(rep.range.first.extST, 19, "first = earliest test");
  eq(rep.range.last.extST, 24, "last = latest test");
  eq(rep.range.deltaST, 5, "deltaST = last - first");
  eq(rep.range.voiceTypes.length, 2, "at most two voice-type names");
  eq(rep.range.voiceTypes[0], "Baritone", "voice type label carried");
  ok(rep.range.tessitura && rep.range.tessitura.p50 === 55, "tessitura p25/p50/p75 carried");
  const lines = PG.describeProgress(rep);
  ok(lines.some((l) => /up 5 since your first test/.test(l)), "range line reports the +5 delta");
}

// ---- ear recentPct uses the last 20 answers of that mode ---------------------
{
  const ear = [];
  // 30 interval answers: first 10 all wrong, last 20 all right -> pct 66/67, recentPct 100.
  for (let i = 0; i < 10; i++) ear.push({ t: NOW - (30 - i) * 1000, mode: "interval", difficulty: "beginner", ok: 0 });
  for (let i = 0; i < 20; i++) ear.push({ t: NOW - (20 - i) * 1000, mode: "interval", difficulty: "beginner", ok: 1 });
  const rep = PG.buildProgressReport({ ear }, NOW);
  eq(rep.ear.interval.n, 30, "all interval answers counted");
  eq(rep.ear.interval.pct, 67, "overall pct = 20/30");
  eq(rep.ear.interval.recentPct, 100, "recentPct = last 20 answers");
  ok(!rep.lowConfidence.ear, "30 answers -> ear not low confidence");
}

// ---- empty inputs -> no throw, all zeros -------------------------------------
{
  const rep = PG.buildProgressReport({}, NOW);
  eq(rep.inTune.n, 0, "empty voice n = 0");
  eq(rep.singalong.takes, 0, "empty singalong takes = 0");
  eq(rep.range.tests, 0, "empty ranges tests = 0");
  eq(rep.window.firstTakeDaysAgo, null, "empty -> no first take");
  ok(rep.lowConfidence.voice && rep.lowConfidence.singalong && rep.lowConfidence.ear && rep.lowConfidence.range,
    "empty -> every lowConfidence true");
  eq(PG.describeProgress(rep).length, 0, "empty report -> no readout lines");
  eq(PG.buildProgressReport(null, NOW).inTune.n, 0, "null data does not throw");
}

// ---- privacy: no t / frames / audio / filename anywhere ----------------------
{
  const voice = [voiceTake(60, 1)];
  const ranges = [{ t: NOW, report: { lo: 45, hiExt: 64, extST: 19, group: "female", tessitura: null, voiceTypes: [] } }];
  const ear = [{ t: NOW, mode: "chord", difficulty: "beginner", ok: 1 }];
  const singalong = [{ t: NOW, report: {
    summary: { inTunePct: 0.6 }, signedBiasCents: -5,
    leaps: { leap: { n: 1, hitPct: 0.5 }, step: { n: 2, hitPct: 0.7 } },
    register: { weakest: "mid" }, worstNotes: [{ name: "G4", bar: 2 }], octaveSlips: 0,
    context: { key: "C major", filename: "SHOULD_NOT_LEAK.mid" },
  } }];
  const rep = PG.buildProgressReport({ voice, ranges, ear, singalong }, NOW);
  const forbidden = new Set(["t", "frames", "audio", "filename"]);
  const blob = JSON.stringify(rep);
  ok(blob.indexOf("SHOULD_NOT_LEAK") === -1, "singalong context/filename never copied into report");
  (function walk(o) {
    if (Array.isArray(o)) return o.forEach(walk);
    if (o && typeof o === "object") {
      Object.keys(o).forEach((k) => { if (forbidden.has(k)) fail("forbidden key present: " + k); walk(o[k]); });
    }
  })(rep);
}

if (failures) { console.error(`\nprogress.test.cjs: ${failures} failure(s)`); process.exit(1); }
console.log("progress.test.cjs: all passed");
