"use strict";

// Headless unit tests for the Ear Trainer pure core (eartrainer.js).
//
// The controller (createEarTrainer) is DOM/audio-bound and not tested here; these cover the
// deterministic question generation and answer checking: theory tables, per-mode question
// invariants (the played notes really encode the answer), difficulty pools, choice sets.
//
// Run: node tests/manual/eartrainer.test.cjs

const path = require("path");
const ET = require(path.join(__dirname, "..", "..", "server", "static", "eartrainer.js"));

let failures = 0;
const fail = (msg) => { console.error("  FAIL: " + msg); failures++; };
const eq = (got, want, msg) => {
  if (got !== want) fail(`${msg}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
};
const near = (got, want, msg, tol) => {
  tol = tol == null ? 1e-9 : tol;
  if (typeof got !== "number" || Math.abs(got - want) > tol) fail(`${msg}: got ${JSON.stringify(got)}, want ~${want}`);
};
const ok = (cond, msg) => { if (!cond) fail(msg); };

// A deterministic PRNG so question generation is reproducible.
function seeded(s) {
  let x = s >>> 0;
  return () => { x = (x * 1664525 + 1013904223) >>> 0; return x / 4294967296; };
}

// ---- theory tables -----------------------------------------------------------
eq(ET.INTERVAL_NAMES[7], "Perfect 5th", "P5 label");
eq(ET.INTERVAL_NAMES[12], "Octave", "octave label");
eq(ET.noteName(60), "C4", "noteName C4");
eq(ET.noteName(69), "A4", "noteName A4");
{
  const maj = ET.CHORD_QUALITIES.find((q) => q.id === "maj");
  eq(JSON.stringify(maj.intervals), JSON.stringify([0, 4, 7]), "maj triad intervals");
  const dom7 = ET.CHORD_QUALITIES.find((q) => q.id === "dom7");
  eq(JSON.stringify(dom7.intervals), JSON.stringify([0, 4, 7, 10]), "dom7 intervals");
  const major = ET.SCALE_TYPES.find((s) => s.id === "major");
  eq(JSON.stringify(major.steps), JSON.stringify([0, 2, 4, 5, 7, 9, 11]), "major scale steps");
}

// ---- shared choice-set invariant ---------------------------------------------
function checkChoices(q) {
  ok(q.choices.length >= 2, `${q.mode}: at least two choices`);
  const ids = q.choices.map((c) => c.id);
  eq(new Set(ids).size, ids.length, `${q.mode}: choices are distinct`);
  ok(ids.indexOf(String(q.answerId)) >= 0, `${q.mode}: the answer is among the choices`);
  ok(q.choices.every((c) => c.label && typeof c.label === "string"), `${q.mode}: every choice has a label`);
  ok(ET.checkAnswer(q, q.answerId), `${q.mode}: checkAnswer true for the answer`);
  const wrong = ids.find((i) => i !== String(q.answerId));
  if (wrong != null) ok(!ET.checkAnswer(q, wrong), `${q.mode}: checkAnswer false for a wrong id`);
}

// ---- interval questions ------------------------------------------------------
{
  const rand = seeded(11);
  for (let i = 0; i < 200; i++) {
    const q = ET.makeQuestion("interval", { difficulty: "advanced" }, rand);
    eq(q.notes.length, 2, "interval has two notes");
    const span = Math.abs(q.notes[1].midi - q.notes[0].midi);
    eq(span, Number(q.answerId), "interval span matches the answer semitones");
    eq(q.choices.find((c) => c.id === q.answerId).label, ET.INTERVAL_NAMES[Number(q.answerId)], "interval label matches semitone");
    checkChoices(q);
  }
  // pool respects difficulty
  const rb = seeded(3);
  for (let i = 0; i < 80; i++) {
    const q = ET.makeQuestion("interval", { difficulty: "beginner" }, rb);
    ok(ET.POOLS.interval.beginner.indexOf(Number(q.answerId)) >= 0, "beginner interval within pool");
  }
  // forced play styles still encode the same interval
  const harmonic = ET.makeQuestion("interval", { difficulty: "advanced", play: "harmonic" }, seeded(9));
  eq(harmonic.play, "harmonic", "harmonic play honored");
  eq(harmonic.notes[0].beat, harmonic.notes[1].beat, "harmonic notes share a beat");
}

// ---- chord questions ---------------------------------------------------------
{
  const rand = seeded(22);
  for (let i = 0; i < 200; i++) {
    const q = ET.makeQuestion("chord", { difficulty: "advanced" }, rand);
    const quality = ET.CHORD_QUALITIES.find((c) => c.id === q.answerId);
    ok(quality, "chord answer id is a known quality");
    eq(q.notes.length, quality.intervals.length, "chord voice count matches quality");
    const rel = q.notes.map((n) => n.midi - q.notes[0].midi);
    eq(JSON.stringify(rel), JSON.stringify(quality.intervals), "chord notes match the quality stack");
    ok(q.notes.every((n) => n.beat === 0), "chord notes are simultaneous");
    checkChoices(q);
  }
}

// ---- scale questions ---------------------------------------------------------
{
  const rand = seeded(33);
  for (let i = 0; i < 120; i++) {
    const q = ET.makeQuestion("scale", { difficulty: "advanced" }, rand);
    const s = ET.SCALE_TYPES.find((x) => x.id === q.answerId);
    ok(s, "scale answer id is a known scale");
    eq(q.notes.length, s.steps.length + 1, "scale plays each step plus the octave");
    const rel = q.notes.map((n) => n.midi - q.notes[0].midi);
    eq(JSON.stringify(rel), JSON.stringify(s.steps.concat([12])), "scale notes ascend by the step pattern");
    for (let k = 1; k < q.notes.length; k++) ok(q.notes[k].beat > q.notes[k - 1].beat, "scale notes are sequential");
    checkChoices(q);
  }
}

// ---- key / degree questions --------------------------------------------------
{
  const MAJOR = [0, 2, 4, 5, 7, 9, 11];
  const rand = seeded(44);
  for (let i = 0; i < 120; i++) {
    const q = ET.makeQuestion("key", { difficulty: "advanced" }, rand);
    const deg = Number(q.answerId);
    ok(deg >= 0 && deg <= 6, "key answer is a scale degree 0..6");
    // first note is the I-chord root = the tonic; the final note is the target degree.
    const tonic = q.notes[0].midi;
    const target = q.notes[q.notes.length - 1].midi;
    eq(target - tonic, MAJOR[deg], "final note is the claimed scale degree above the tonic");
    checkChoices(q);
  }
}

// ---- accuracy ----------------------------------------------------------------
near(ET.accuracy([1, 1, 0]), 2 / 3, "accuracy 2 of 3");
near(ET.accuracy([]), 0, "accuracy of nothing is 0");
eq(ET.checkAnswer(null, "x"), false, "checkAnswer null-safe");

// ---- summary -----------------------------------------------------------------
if (failures) {
  console.error(`\n${failures} assertion(s) FAILED`);
  process.exit(1);
} else {
  console.log("eartrainer.test.cjs: all assertions passed");
}
