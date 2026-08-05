"use strict";

// Headless unit tests for the Transposer's pure transposition math (transposer.js).
//
// The controller (createTransposer) is DOM-bound and not tested here; these cover the
// deterministic core: key / note / chord transposition, the destination-key spelling,
// the minimal-shift helper, and Roman-numeral invariance.
//
// Run: node tests/manual/transposer.test.cjs

const path = require("path");
const TR = require(path.join(__dirname, "..", "..", "server", "static", "transposer.js"));

let failures = 0;
const fail = (msg) => { console.error("  FAIL: " + msg); failures++; };
const eq = (got, want, msg) => {
  if (got !== want) fail(`${msg}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
};
const deepEq = (got, want, msg) => {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    fail(`${msg}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
  }
};

// ---- midiName ----------------------------------------------------------------
eq(TR.midiName(60), "C4", "midiName C4");
eq(TR.midiName(69), "A4", "midiName A4");
eq(TR.midiName(61), "C#4", "midiName C#4");

// ---- transposeKey ------------------------------------------------------------
eq(TR.transposeKey("C major", 2), "D major", "C major up 2 -> D major");
eq(TR.transposeKey("C major", -1), "B major", "C major down 1 wraps to B major");
eq(TR.transposeKey("A minor", 3), "C minor", "A minor up 3 -> C minor (mode kept)");
eq(TR.transposeKey("G major", 12), "G major", "up an octave keeps the key");
eq(TR.transposeKey(null, 5), null, "null key stays null");
eq(TR.transposeKey("nonsense", 5), null, "unparseable key -> null");

// ---- minimalShift (nearest-register jump for the To-key dropdown) -------------
eq(TR.minimalShift(0, 2), 2, "C -> D is +2");
eq(TR.minimalShift(0, 11), -1, "C -> B is -1 (down, not +11)");
eq(TR.minimalShift(0, 6), 6, "tritone ties resolve upward");
eq(TR.minimalShift(9, 0), 3, "A -> C is +3");

// ---- transposeNotes ----------------------------------------------------------
const notes = [
  { midi: 60, start_ql: 0, dur_ql: 1, cents: 4, velocity: 80, name: "C4" },
  { midi: 62, start_ql: 1, dur_ql: 1, cents: -3, velocity: 80, name: "D4" },
];
const tn = TR.transposeNotes(notes, 4);
eq(tn[0].midi, 64, "note 0 midi +4");
eq(tn[0].name, "E4", "note 0 renamed E4");
eq(tn[0].start_ql, 0, "start_ql preserved");
eq(tn[0].dur_ql, 1, "dur_ql preserved");
eq(tn[0].cents, 4, "cents preserved");
eq(tn[1].midi, 66, "note 1 midi +4");
eq(notes[0].midi, 60, "source notes not mutated");

// ---- transposeChords: root moves, quality/measure/roman invariant -----------
const chords = [
  { measure: 0, start_ql: 0, root_pc: 0, root_name: "C", quality: "maj", symbol: "C", roman: "I" },
  { measure: 1, start_ql: 4, root_pc: 7, root_name: "G", quality: "maj", symbol: "G", roman: "V" },
  { measure: 2, start_ql: 8, root_pc: 9, root_name: "A", quality: "min", symbol: "Am", roman: "vi" },
];
const newKey = TR.transposeKey("C major", 2); // D major
const tc = TR.transposeChords(chords, 2, newKey);
eq(tc[0].root_pc, 2, "C -> D root_pc");
eq(tc[0].root_name, "D", "C -> D root_name");
eq(tc[0].symbol, "D", "C -> D symbol");
eq(tc[0].roman, "I", "roman invariant (I)");
eq(tc[1].root_pc, 9, "G -> A root_pc");
eq(tc[1].symbol, "A", "G -> A symbol");
eq(tc[1].roman, "V", "roman invariant (V)");
eq(tc[2].root_name, "B", "Am -> Bm root_name (B major uses sharps)");
eq(tc[2].symbol, "Bm", "Am -> Bm symbol keeps the minor suffix");
eq(tc[2].roman, "vi", "roman invariant (vi)");

// Destination key that spells with flats (F major): B natural chord -> "E-" (E flat).
const fKey = TR.transposeKey("C major", 5); // F major (flat key)
const tcF = TR.transposeChords(
  [{ measure: 0, start_ql: 0, root_pc: 11, root_name: "B", quality: "maj", symbol: "B", roman: "VII" }],
  5, fKey);
eq(tcF[0].root_pc, 4, "B -> E root_pc in F major");
eq(tcF[0].root_name, "E", "B -> E root_name");
eq(tcF[0].symbol, "E", "B -> E symbol");

// ---- shiftLabel --------------------------------------------------------------
eq(TR.shiftLabel(0), "no change", "shiftLabel 0");
eq(TR.shiftLabel(7), "up a perfect 5th", "shiftLabel +7");
eq(TR.shiftLabel(-3), "down a minor 3rd", "shiftLabel -3");
eq(TR.shiftLabel(12), "up an octave", "shiftLabel +12");

// ---- transpose composite -----------------------------------------------------
const src = { key: "C major", notes, chords, tempo_bpm: 100, time_sig: [4, 4], subdiv: 4 };
const out = TR.transpose(src, 2);
eq(out.shift, 2, "composite shift");
eq(out.key, "D major", "composite key");
eq(out.notes[0].midi, 62, "composite note midi");
eq(out.chords[0].root_name, "D", "composite chord root");
deepEq(out.chords.map((c) => c.roman), ["I", "V", "vi"], "composite romans invariant");

// ---- identity ----------------------------------------------------------------
const id = TR.transpose(src, 0);
eq(id.key, "C major", "identity key");
eq(id.notes[0].midi, 60, "identity note");
eq(id.chords[0].symbol, "C", "identity chord");

if (failures) {
  console.error(`\n${failures} assertion(s) failed.`);
  process.exit(1);
}
console.log("transposer.test.cjs: all assertions passed");
