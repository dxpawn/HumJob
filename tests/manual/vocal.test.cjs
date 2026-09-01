"use strict";

// Headless unit tests for the vocal range/tessitura pure core (vocal.js).
//
// realtime.js owns the mic + wizard; these cover only the deterministic math:
// the time-weighted histogram + quantiles, tessitura, the normal-model percentile,
// stable-range + extreme-support glitch guards, the fach classifier, the report
// builder, and the exact wording of describeModel (incl. the no-dash house rule).
//
// Run: node tests/manual/vocal.test.cjs

const path = require("path");
const VA = require(path.join(__dirname, "..", "..", "server", "static", "vocal.js"));

let failures = 0;
const fail = (msg) => { console.error("  FAIL: " + msg); failures++; };
const eq = (got, want, msg) => {
  if (got !== want) fail(`${msg}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
};
const near = (got, want, msg, tol) => {
  tol = tol == null ? 1e-6 : tol;
  if (typeof got !== "number" || Math.abs(got - want) > tol) {
    fail(`${msg}: got ${JSON.stringify(got)}, want ~${want}`);
  }
};
const ok = (cond, msg) => { if (!cond) fail(msg); };

// Build N frames at a fixed midi/clarity, each ~30 ms apart (dt 30).
const frames = (midi, n, clarity, dt) => {
  const c = clarity == null ? 1 : clarity;
  const d = dt == null ? 30 : dt;
  const out = [];
  for (let i = 0; i < n; i++) out.push({ m: midi, c, dt: i === 0 ? 0 : d });
  return out;
};
const concat = (...arrs) => [].concat(...arrs);

// ---- timeWeightedHistogram ---------------------------------------------------
{
  // 10 frames at 30 ms each; first frame dt 0 -> 9*0.03 = 0.27 s at midi 60.
  const h = VA.timeWeightedHistogram(frames(60, 10));
  near(h.totalS, 0.27, "histogram totalS = 9*0.03");
  near(h.bins[60], 0.27, "all time lands in the round(60) bin");
  eq(Object.keys(h.bins).length, 1, "one bin for a single held pitch");
}
{
  // dt is clamped to 100 ms so a post-silence gap adds no more than 0.1 s.
  const h = VA.timeWeightedHistogram([{ m: 60, c: 1, dt: 5000 }]);
  near(h.totalS, 0.1, "dt clamped to 100 ms");
}
{
  // clarity below the gate contributes nothing.
  const h = VA.timeWeightedHistogram(frames(60, 10, 0.3));
  near(h.totalS, 0, "low-clarity frames are gated out");
}

// ---- weightedQuantile --------------------------------------------------------
{
  const pairs = [{ v: 1, w: 1 }, { v: 2, w: 1 }, { v: 3, w: 1 }, { v: 4, w: 1 }];
  eq(VA.weightedQuantile(pairs, 0), 1, "q0 -> min");
  eq(VA.weightedQuantile(pairs, 1), 4, "q1 -> max");
  eq(VA.weightedQuantile(pairs, 0.5), 2, "q0.5 inverse-CDF");
  eq(VA.weightedQuantile([], 0.5), null, "empty -> null");
  // weight dominates: almost all mass at v=5 pulls the median up.
  eq(VA.weightedQuantile([{ v: 1, w: 1 }, { v: 5, w: 99 }], 0.5), 5, "weighted median follows mass");
}

// ---- tessituraFromFrames -----------------------------------------------------
{
  // ~15 s split evenly across midi 55 and 65 -> median between, band spans them.
  const f = concat(frames(55, 250), frames(65, 250)); // 2*249*0.03 ~= 14.9 s voiced
  const t = VA.tessituraFromFrames(f);
  ok(t != null, "enough voiced time -> tessitura present");
  ok(t.voicedS > 10, "voicedS over the 10 s floor");
  ok(t.p25 >= 55 && t.p25 <= 56, "p25 near the lower cluster");
  ok(t.p75 >= 64 && t.p75 <= 65, "p75 near the upper cluster");
}
eq(VA.tessituraFromFrames(frames(60, 20)), null, "under 10 s voiced -> null tessitura");

// ---- normalCdf ---------------------------------------------------------------
near(VA.normalCdf(38, 38, 4), 0.5, "cdf at the mean", 1e-6);
near(VA.normalCdf(42, 38, 4), 0.8413, "cdf at +1 SD", 1e-3);
near(VA.normalCdf(34, 38, 4), 0.1587, "cdf at -1 SD", 1e-3);
ok(VA.normalCdf(30, 38, 4) < VA.normalCdf(40, 38, 4), "cdf is monotonic increasing");

// ---- rangePercentile ---------------------------------------------------------
{
  eq(VA.rangePercentile(38, "male").pct, 50, "38 ST male -> 50th pct");
  eq(VA.rangePercentile(42, "male").pct, 84, "42 ST male -> ~84th pct");
  eq(VA.rangePercentile(30, "male").pct, 2, "30 ST male -> ~2nd pct");
  eq(VA.rangePercentile(10, "male").pct, 1, "tiny range clamps to 1");
  eq(VA.rangePercentile(60, "male").pct, 99, "huge range clamps to 99");
  eq(VA.rangePercentile(38, "nonsense").group, "combined", "unknown group -> combined");
  eq(VA.rangePercentile(37, "female").pct, 50, "37 ST female -> 50th pct");
  eq(VA.rangePercentile(NaN, "male"), null, "NaN -> null");
}

// ---- stableRange (glitch rejection) ------------------------------------------
{
  const r = VA.stableRange(frames(60, 10));
  near(r.lo, 60, "stable held pitch -> lo");
  near(r.hi, 60, "stable held pitch -> hi");
}
{
  // A single octave-glitch frame in the middle must not widen the range: it never
  // holds for stableMinFrames, and it resets the run.
  const f = concat(frames(60, 10), [{ m: 72, c: 1, dt: 30 }], frames(60, 10));
  const r = VA.stableRange(f);
  ok(r.hi < 61, "lone glitch frame does not widen hi");
}
eq(VA.stableRange(frames(60, 10, 0.3)), null, "all-low-clarity -> null range");

// ---- extremeSupported --------------------------------------------------------
ok(VA.extremeSupported(frames(64, 30), 64), "0.87 s held at the extreme is supported");
ok(!VA.extremeSupported(frames(64, 5), 64), "0.12 s held is not supported (< 0.4 s)");
ok(!VA.extremeSupported(frames(64, 30), 72), "no time near 72 -> unsupported");

// ---- classifyVoice -----------------------------------------------------------
{
  // Tenor-centred singer (C3-C5, tessitura median ~C4=60, the tenor centre).
  const rank = VA.classifyVoice({ lo: 48, hi: 72, tessMedian: 60 }, "male");
  eq(rank[0].id, "tenor", "male C3-C5 with C4 tessitura -> tenor top");
  eq(rank.length, 3, "male group restricts to 3 candidates");
}
{
  // Same range but tessitura sitting a fourth lower reads as baritone: the
  // tessitura median, not the range, separates the overlapping fachs.
  const rank = VA.classifyVoice({ lo: 48, hi: 72, tessMedian: 57 }, "male");
  eq(rank[0].id, "baritone", "same range, low tessitura -> baritone top");
}
{
  const rank = VA.classifyVoice({ lo: 60, hi: 84, tessMedian: 72 }, "female");
  eq(rank[0].id, "soprano", "female C4-C6 high tessitura -> soprano top");
}
{
  const all = VA.classifyVoice({ lo: 48, hi: 72, tessMedian: 57 }, "combined");
  eq(all.length, 6, "combined ranks all six fachs");
}
{
  // Missing tessitura -> coverage-only scoring, still returns a ranking.
  const rank = VA.classifyVoice({ lo: 48, hi: 72 }, "male");
  eq(rank[0].centerFit, null, "no tessitura -> centerFit null");
  ok(rank.length === 3, "still ranks on coverage alone");
}
eq(VA.classifyVoice({ lo: 72, hi: 48 }, "male").length, 0, "inverted range -> empty");

// ---- buildRangeReport --------------------------------------------------------
{
  // Full run: lo C3(48), full-voice hi C5(72), falsetto hi E5(76), tessitura ~A3.
  const rep = VA.buildRangeReport({
    group: "male",
    steps: {
      comfort: frames(57, 80),
      low: frames(48, 30),
      highFull: frames(72, 30),
      highFalsetto: frames(76, 30),
      tessitura: concat(frames(60, 250), frames(62, 250)), // median ~C4 -> tenor
    },
  });
  ok(!rep.error, "full run builds without error");
  eq(rep.lo, 48, "reported lo");
  eq(rep.hiFull, 72, "reported full-voice hi");
  eq(rep.hiExt, 76, "extended hi is the falsetto note");
  eq(rep.extST, 28, "extended span 76-48 = 28 ST");
  eq(rep.fullST, 24, "full-voice span 72-48 = 24 ST");
  eq(rep.falsettoUsed, true, "falsetto extended the range");
  eq(rep.protocolComparable, true, "falsetto step attempted -> protocol comparable");
  eq(rep.percentile.basis, "extended", "percentile uses the extended range");
  ok(rep.tessitura != null, "tessitura measured");
  eq(rep.voiceTypes[0].id, "tenor", "top voice type is tenor");
}
{
  // Falsetto skipped: percentile basis is full voice and a lower-bound caveat appears.
  const rep = VA.buildRangeReport({
    group: "combined",
    steps: {
      comfort: frames(57, 80), low: frames(48, 30), highFull: frames(72, 30),
      highFalsetto: null, tessitura: concat(frames(55, 250), frames(60, 250)),
    },
  });
  eq(rep.hiExt, 72, "no falsetto -> extended hi is full-voice hi");
  eq(rep.protocolComparable, false, "skipped falsetto -> not protocol comparable");
  eq(rep.percentile.basis, "fullVoice", "percentile basis full voice when skipped");
  ok(rep.caveats.some((c) => /lower bound/i.test(c)), "a lower-bound caveat is present");
}
{
  // Incomplete: no usable high step.
  const rep = VA.buildRangeReport({ group: "male", steps: { low: frames(48, 30), highFull: frames(72, 2) } });
  eq(rep.error, "incomplete", "too few high frames -> incomplete");
}
{
  // Quality flags: a briefly-touched extreme is flagged.
  const rep = VA.buildRangeReport({
    group: "male",
    steps: {
      comfort: frames(57, 80), low: frames(40, 6), highFull: frames(72, 30),
      highFalsetto: null, tessitura: concat(frames(55, 250), frames(60, 250)),
    },
  });
  ok(!rep.error, "brief low still builds");
  ok(rep.quality.low.flags.length > 0, "brief/low-limit low note raises a quality flag");
}

// ---- describeModel -----------------------------------------------------------
{
  const lines = VA.describeModel("male", true);
  ok(lines.some((l) => /Hollien/.test(l)), "cites Hollien 1971");
  ok(lines.some((l) => /assumption/i.test(l) && /SD/.test(l)), "states the SD assumption");
  ok(!lines.some((l) => /lower bound/i.test(l)), "no lower-bound line when protocol comparable");
  const combined = VA.describeModel("combined", true);
  ok(combined.some((l) => /37\.6/.test(l)), "combined derivation shown");
  const skipped = VA.describeModel("male", false);
  ok(skipped.some((l) => /lower bound/i.test(l)), "lower-bound line when falsetto skipped");
  // House style: no em/en dashes anywhere in printed copy.
  for (const l of combined.concat(skipped)) {
    ok(!/[–—]/.test(l), "no em/en dash in model copy: " + l);
  }
}

// ---- summary -----------------------------------------------------------------
if (failures) {
  console.error(`\n${failures} assertion(s) FAILED`);
  process.exit(1);
} else {
  console.log("vocal.test.cjs: all assertions passed");
}
