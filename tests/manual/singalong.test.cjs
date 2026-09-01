"use strict";

// Headless unit tests for the Sing-Along pure core (singalong.js).
//
// The controller (createSingalong) is DOM/mic-bound and not tested here; these cover the
// deterministic math: cent folding, the active-note lookup, the lane geometry, and the
// full scoreTake grading matrix (perfect / octave / sharp takes, onset grace, stopQl).
//
// Run: node tests/manual/singalong.test.cjs

const path = require("path");
const SA = require(path.join(__dirname, "..", "..", "server", "static", "singalong.js"));

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

// ---- foldCents ---------------------------------------------------------------
near(SA.foldCents(60, 60, true), 0, "unison -> 0");
near(SA.foldCents(72, 60, true), 0, "octave up folds to 0 (agnostic)");
near(SA.foldCents(48, 60, true), 0, "octave down folds to 0 (agnostic)");
near(SA.foldCents(67, 60, true), -500, "fifth folds to nearest -500");
near(SA.foldCents(66, 60, true), -600, "tritone ties to -600");
near(SA.foldCents(72, 60, false), 1200, "octave up, enforced = 1200");
near(SA.foldCents(60.3, 60, true), 30, "30 cents sharp");
near(SA.foldCents(59.7, 60, true), -30, "30 cents flat");

// ---- activeIndex -------------------------------------------------------------
const mel = [
  { midi: 60, start_ql: 0, dur_ql: 1 },
  { midi: 62, start_ql: 1, dur_ql: 1 },
  { midi: 64, start_ql: 3, dur_ql: 1 },   // gap at ql 2..3
];
eq(SA.activeIndex(mel, 0), 0, "start of note 0 is active");
eq(SA.activeIndex(mel, 0.99), 0, "inside note 0");
eq(SA.activeIndex(mel, 1), 1, "boundary belongs to the next note");
eq(SA.activeIndex(mel, 2), -1, "gap between notes -> -1");
eq(SA.activeIndex(mel, 2.5), -1, "still in the gap");
eq(SA.activeIndex(mel, 3), 2, "note 2 starts at ql 3");
eq(SA.activeIndex(mel, 4), -1, "past the end -> -1");

// ---- pitchRange --------------------------------------------------------------
const pr = SA.pitchRange([{ midi: 60 }, { midi: 64 }]);
eq(pr.lo, 58, "pad 2 below the lowest");
eq(pr.hi, 66, "pad 2 above the highest");
const flat = SA.pitchRange([{ midi: 60 }]);          // span 4 after pad -> widened to 6
eq(flat.hi - flat.lo >= 6, true, "min span enforced");
const empty = SA.pitchRange([]);
eq(empty.lo, 55, "empty melody default lo");

// ---- barQl -------------------------------------------------------------------
near(SA.barQl([4, 4]), 4, "4/4 = 4 ql");
near(SA.barQl([2, 4]), 2, "2/4 = 2 ql");
near(SA.barQl([6, 8]), 3, "6/8 = 3 ql");
near(SA.barQl(null), 4, "default 4/4");

// ---- bandForDifficulty -------------------------------------------------------
eq(SA.bandForDifficulty("strict"), 25, "strict -> 25c");
eq(SA.bandForDifficulty("normal"), 50, "normal -> 50c");
eq(SA.bandForDifficulty("lenient"), 75, "lenient -> 75c");
eq(SA.bandForDifficulty("tonedeaf"), 100, "tone-deaf -> 100c");
eq(SA.bandForDifficulty("bogus"), 50, "unknown level falls back to normal");
eq(SA.bandForDifficulty(undefined), 50, "missing level falls back to normal");
eq(SA.DEFAULT_DIFFICULTY, "normal", "default difficulty is normal");

// ---- verdict -----------------------------------------------------------------
eq(SA.verdict({ hitPct: 0.9 }), "good", "0.9 -> good");
eq(SA.verdict({ hitPct: 0.8 }), "good", "0.8 boundary -> good");
eq(SA.verdict({ hitPct: 0.6 }), "ok", "0.6 -> ok");
eq(SA.verdict({ hitPct: 0.5 }), "ok", "0.5 boundary -> ok");
eq(SA.verdict({ hitPct: 0.2 }), "miss", "0.2 -> miss");
eq(SA.verdict({ hitPct: null }), "miss", "no data -> miss");

// ---- laneLayout --------------------------------------------------------------
{
  const lay = SA.laneLayout(mel, 0, { width: 1000, height: 200, pxPerQl: 100, playheadFrac: 0.3 });
  near(lay.playheadX, 300, "playhead at 30% of width");
  const r0 = lay.rects.find((r) => r.i === 0);
  near(r0.x, 300, "note 0 sits at the playhead at tQl 0");
  near(r0.w, 100, "note 0 width = dur * pxPerQl");
  eq(r0.active, true, "note 0 active at tQl 0");
  const r2 = lay.rects.find((r) => r.i === 2);
  near(r2.x, 600, "note 2 at ql 3 -> playhead + 3*100");
  // Far-future melody note is culled when off-screen.
  const far = SA.laneLayout([{ midi: 60, start_ql: 100, dur_ql: 1 }], 0,
    { width: 300, height: 100, pxPerQl: 50 });
  eq(far.rects.length, 0, "off-screen note culled");
}

// ---- scoreTake ---------------------------------------------------------------
// Two quarter notes at 120 BPM: note0 C4 sec[0,0.5), note1 D4 sec[0.5,1.0).
const SCORE_MEL = [
  { midi: 60, start_ql: 0, dur_ql: 1 },
  { midi: 62, start_ql: 1, dur_ql: 1 },
];
const OPTS = { bpm: 120, bandCents: 50, graceSec: 0.1, octaveAgnostic: true };

// Frames every 30 ms across the whole song, pitched by a supplied function of the active note.
function frames(pitchFn) {
  const out = [];
  for (let t = 0; t < 1.0; t += 0.03) {
    const spb = 0.5;
    const idx = SA.activeIndex(SCORE_MEL, t / spb);
    const ref = idx >= 0 ? SCORE_MEL[idx].midi : null;
    out.push({ t, midiFloat: pitchFn(ref, t) });
  }
  return out;
}

// Perfect take: sing exactly the target.
{
  const res = SA.scoreTake(SCORE_MEL, frames((ref) => ref), OPTS);
  near(res.inTunePct, 1, "perfect take: 100% in tune");
  eq(res.notesGood, 2, "both notes nailed");
  eq(res.scoredNotes, 2, "both notes scored");
  eq(res.notesTotal, 2, "two notes total");
  eq(res.perNote[0].verdict, "good", "note 0 good");
  near(res.perNote[0].meanAbsCents, 0, "note 0 dead on");
}

// Octave-down take: agnostic forgives it, enforcing the octave fails it.
{
  const down = frames((ref) => ref - 12);
  const agn = SA.scoreTake(SCORE_MEL, down, OPTS);
  near(agn.inTunePct, 1, "octave down forgiven when agnostic");
  eq(agn.notesGood, 2, "octave down: still good (agnostic)");
  const strictOct = SA.scoreTake(SCORE_MEL, down, Object.assign({}, OPTS, { octaveAgnostic: false }));
  near(strictOct.inTunePct, 0, "octave down fails when octave enforced");
  eq(strictOct.perNote[0].verdict, "miss", "octave down -> miss when enforced");
}

// 30-cent-sharp take: inside the normal band, outside the Strict band.
{
  const sharp = frames((ref) => ref + 0.3);
  const normal = SA.scoreTake(SCORE_MEL, sharp, OPTS);
  near(normal.inTunePct, 1, "30c sharp inside +-50c band");
  near(normal.meanAbsCents, 30, "mean abs cents ~30", 1e-6);
  const strict = SA.scoreTake(SCORE_MEL, sharp, Object.assign({}, OPTS, { bandCents: 25 }));
  near(strict.inTunePct, 0, "30c sharp outside +-25c strict band");
}

// Onset grace: a wild frame inside the first 100 ms must not count.
{
  const f = [
    { t: 0.03, midiFloat: 40 },   // way off, but within the 0.1 s grace of note 0
    { t: 0.2, midiFloat: 60 },    // on target, past the grace
    { t: 0.3, midiFloat: 60 },
  ];
  const res = SA.scoreTake(SCORE_MEL, f, OPTS);
  eq(res.perNote[0].verdict, "good", "grace frame excluded -> note 0 good");
  near(res.perNote[0].meanAbsCents, 0, "grace frame excluded from mean");
}

// stopQl: a manual stop before note 1 leaves it unscored.
{
  const res = SA.scoreTake(SCORE_MEL, frames((ref) => ref), Object.assign({}, OPTS, { stopQl: 1.0 }));
  eq(res.scoredNotes, 1, "only note 0 scored before the stop");
  eq(res.notesTotal, 2, "note count still reflects the whole song");
  eq(res.perNote[1].verdict, "unscored", "note 1 left unscored");
}

// Half-voiced note: silence lowers hitPct and voicedPct together.
{
  const f = frames((ref, t) => (t / 0.5 % 1) < 0.5 ? ref : null);  // voice first half of each note
  const res = SA.scoreTake(SCORE_MEL, f, OPTS);
  eq(res.perNote[0].voicedPct < 1, true, "note 0 not fully voiced");
  eq(res.perNote[0].hitPct <= res.perNote[0].voicedPct + 1e-9, true, "hitPct bounded by voiced coverage");
}

// ---- analyzeTake -------------------------------------------------------------
const AN_OPTS = { bpm: 120, bandCents: 50, graceSec: 0.1, octaveAgnostic: true, timeSig: [4, 4] };

// A frame generator over an arbitrary melody (spb = seconds per quarter).
function framesFor(melody, spb, pitchFn) {
  const out = [];
  let total = 0;
  for (const n of melody) total = Math.max(total, (n.start_ql + n.dur_ql) * spb);
  for (let t = 0; t < total; t += 0.03) {
    const idx = SA.activeIndex(melody, t / spb);
    const ref = idx >= 0 ? melody[idx].midi : null;
    out.push({ t, midiFloat: pitchFn(ref, t, idx) });
  }
  return out;
}

// Flat by 30c the whole way: signed bias is negative, absolute magnitude unchanged.
{
  const flat = frames((ref) => ref - 0.3);
  const a = SA.analyzeTake(SCORE_MEL, flat, AN_OPTS);
  near(a.signedBiasCents, -30, "flat take: signed bias ~ -30c", 1);
  near(a.meanAbsCents, 30, "flat take: mean abs ~30c (unchanged)", 1);
}

// In tune, then flat in the second half: drift is clearly negative.
{
  const drifting = frames((ref, t) => (t < 0.5 ? ref : ref - 0.4));
  const a = SA.analyzeTake(SCORE_MEL, drifting, AN_OPTS);
  near(a.drift.firstCents, 0, "drift: first third ~0c", 5);
  near(a.drift.lastCents, -40, "drift: last third ~ -40c", 5);
  eq(a.drift.driftCents < -20, true, "drift is negative (started fine, went flat)");
}

// One note sung a full octave down -> one octave slip (agnostic still forgives the pitch).
{
  const oct = frames((ref, t) => (t < 0.5 ? ref : ref - 12));
  const a = SA.analyzeTake(SCORE_MEL, oct, AN_OPTS);
  eq(a.octaveSlips, 1, "one note an octave down -> octaveSlips == 1");
}

// Leap accuracy below step accuracy when only the leap landing is missed.
{
  const LEAP_MEL = [
    { midi: 60, start_ql: 0, dur_ql: 1 },   // start
    { midi: 62, start_ql: 1, dur_ql: 1 },   // step (+2)
    { midi: 69, start_ql: 2, dur_ql: 1 },   // leap (+7)
  ];
  const lf = framesFor(LEAP_MEL, 0.5, (ref, t, idx) => (ref == null ? null : idx === 2 ? ref - 2 : ref));
  const a = SA.analyzeTake(LEAP_MEL, lf, AN_OPTS);
  eq(a.leaps.leap.n, 1, "one leap landing classified");
  eq(a.leaps.step.n, 1, "one step note classified");
  eq(a.leaps.leap.hitPct < a.leaps.step.hitPct, true, "leap accuracy below step accuracy");
  eq(a.leaps.missedLeapLandings, 1, "the missed note was the leap landing");
}

// worstNotes: capped at 5, worst (most off) first, correct name + bar.
{
  const MANY = [];
  for (let i = 0; i < 6; i++) MANY.push({ midi: 60 + i, start_ql: i, dur_ql: 1 });
  const mf = framesFor(MANY, 0.5, (ref, t, idx) => (ref == null ? null : ref + idx * 0.3)); // sharper each note
  const a = SA.analyzeTake(MANY, mf, AN_OPTS);
  eq(a.worstNotes.length, 5, "worst notes capped at 5");
  eq(a.worstNotes[0].name, "F4", "worst note is the most-off one (F4, midi 65)");
  eq(a.worstNotes[0].bar, 2, "F4 at ql 5 falls in bar 2 (4/4)");
  for (let i = 1; i < a.worstNotes.length; i++) {
    eq(a.worstNotes[i].hitPct >= a.worstNotes[i - 1].hitPct, true, "worst notes ordered by hitPct ascending");
  }
  eq(a.lowConfidence, false, "6 scored notes -> not low confidence");
}

// lowConfidence flips when very few notes are scored.
{
  const one = [{ midi: 60, start_ql: 0, dur_ql: 1 }];
  const of = framesFor(one, 0.5, (ref) => ref);
  const a = SA.analyzeTake(one, of, AN_OPTS);
  eq(a.lowConfidence, true, "1 scored note -> low confidence");
}

// ---- buildCoachReport --------------------------------------------------------
{
  const oct = frames((ref, t) => (t < 0.5 ? ref : ref - 12));
  const a = SA.analyzeTake(SCORE_MEL, oct, AN_OPTS);
  const ref = { key: "C major", tempo_bpm: 120, time_sig: [4, 4], n_notes: 2, duration_ql: 2 };
  const rep = SA.buildCoachReport(a, ref, { bandCents: 50, octaveAgnostic: true });
  eq(rep.context.key, "C major", "report carries the key");
  eq(rep.context.tempo_bpm, 120, "report carries the tempo");
  eq(rep.context.band_cents, 50, "report carries the difficulty band");
  eq(rep.context.octave_mode, "agnostic", "report carries the octave mode");
  eq("frames" in rep, false, "report never carries frames");
  eq("filename" in rep, false, "report never carries a filename");
  eq(Array.isArray(rep.worstNotes), true, "report has a worstNotes array");
  eq(rep.worstNotes.length <= 5, true, "worstNotes capped at 5");
  eq(rep.context.octave_mode, "agnostic", "octave mode agnostic by default");
  const rep2 = SA.buildCoachReport(a, ref, { bandCents: 25, octaveAgnostic: false });
  eq(rep2.context.octave_mode, "enforced", "octave mode reflects the enforce toggle");
}

// ---- summary -----------------------------------------------------------------
if (failures) {
  console.error(`\n${failures} assertion(s) FAILED`);
  process.exit(1);
} else {
  console.log("singalong.test.cjs: all assertions passed");
}
