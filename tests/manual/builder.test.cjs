"use strict";

// Headless golden test for the Manual-mode MusicXML builder (Phase 1).
//
// Reads tests/data/generated/manual_golden.json (produced by gen_manual_golden.py
// from the real server engraver), runs MT.notesToMusicXML on the SAME seq, parses
// the resulting XML structurally, and asserts it matches music21 note-for-note:
// spelled pitch, quarter-length, tie flags, measure numbers, clef, key fifths, and
// time signature. Layout, ids, and beams are ignored. Also checks the noteheadMap.
//
// Run: node tests/manual/builder.test.cjs

const fs = require("fs");
const path = require("path");

const MT = require(path.join(__dirname, "..", "..", "server", "static", "manual.js"));
const GOLDEN = path.join(__dirname, "..", "data", "manual_golden.json");

let failures = 0;
const fail = (msg) => { console.error("  FAIL: " + msg); failures++; };
const near = (a, b) => Math.abs(a - b) < 1e-9;

function attr(re, xml) {
  const m = xml.match(re);
  return m ? m[1] : null;
}

function parseStructural(xml) {
  const divisions = Number(attr(/<divisions>(-?\d+)<\/divisions>/, xml));
  const fifthsRaw = attr(/<fifths>(-?\d+)<\/fifths>/, xml);
  const fifths = fifthsRaw === null ? null : Number(fifthsRaw);
  const beats = Number(attr(/<beats>(\d+)<\/beats>/, xml));
  const beatType = Number(attr(/<beat-type>(\d+)<\/beat-type>/, xml));
  const clef = attr(/<clef><sign>([A-G])<\/sign>/, xml);

  const events = [];
  const measureRe = /<measure number="(\d+)">([\s\S]*?)<\/measure>/g;
  let mm;
  while ((mm = measureRe.exec(xml)) !== null) {
    const measure = Number(mm[1]);
    const inner = mm[2];
    const noteRe = /<note>([\s\S]*?)<\/note>/g;
    let nm;
    while ((nm = noteRe.exec(inner)) !== null) {
      const body = nm[1];
      const dur = Number(attr(/<duration>(\d+)<\/duration>/, body));
      const ql = dur / divisions;
      if (/<rest\/>/.test(body)) {
        events.push({ measure, rest: true, ql });
        continue;
      }
      const step = attr(/<step>([A-G])<\/step>/, body);
      const alterRaw = attr(/<alter>(-?\d+)<\/alter>/, body);
      const alter = alterRaw === null ? 0 : Number(alterRaw);
      const octave = Number(attr(/<octave>(-?\d+)<\/octave>/, body));
      const hasStart = /<tie type="start"\/>/.test(body);
      const hasStop = /<tie type="stop"\/>/.test(body);
      const tie = hasStart && hasStop ? "continue" : hasStart ? "start" : hasStop ? "stop" : null;
      events.push({ measure, step, alter, octave, ql, tie });
    }
  }
  return { divisions, fifths, beats, beatType, clef, events };
}

function eqEvent(a, b) {
  if (!!a.rest !== !!b.rest) return false;
  if (a.rest) return a.measure === b.measure && near(a.ql, b.ql);
  return a.measure === b.measure && a.step === b.step && a.alter === b.alter &&
    a.octave === b.octave && near(a.ql, b.ql) && (a.tie || null) === (b.tie || null);
}

const goldenData = JSON.parse(fs.readFileSync(GOLDEN, "utf-8"));
const golden = goldenData.melodies;
console.log(`Golden builder test: ${golden.length} melodies`);

for (const g of golden) {
  console.log(`- ${g.name}`);
  const { xml, noteheadMap } = MT.notesToMusicXML(g.seq, {
    key: g.key, timeSig: g.timeSig, divisions: g.divisions,
  });
  const got = parseStructural(xml);

  if (got.divisions !== g.divisions) fail(`${g.name}: divisions ${got.divisions} != ${g.divisions}`);
  if (got.fifths !== g.fifths) fail(`${g.name}: fifths ${got.fifths} != ${g.fifths}`);
  if (got.clef !== g.clef) fail(`${g.name}: clef ${got.clef} != ${g.clef}`);
  if (got.beats !== g.timeSig[0] || got.beatType !== g.timeSig[1]) {
    fail(`${g.name}: time ${got.beats}/${got.beatType} != ${g.timeSig[0]}/${g.timeSig[1]}`);
  }

  if (got.events.length !== g.events.length) {
    fail(`${g.name}: ${got.events.length} events != golden ${g.events.length}`);
    console.error("    got:   " + JSON.stringify(got.events));
    console.error("    want:  " + JSON.stringify(g.events));
  } else {
    for (let i = 0; i < g.events.length; i++) {
      if (!eqEvent(got.events[i], g.events[i])) {
        fail(`${g.name}: event ${i} mismatch\n      got:  ${JSON.stringify(got.events[i])}\n      want: ${JSON.stringify(g.events[i])}`);
      }
    }
  }

  // noteheadMap: one seq index per emitted notehead (rests excluded), in order,
  // and every entry must point at a non-rest seq event.
  const noteheads = got.events.filter((e) => !e.rest).length;
  if (noteheadMap.length !== noteheads) {
    fail(`${g.name}: noteheadMap length ${noteheadMap.length} != noteheads ${noteheads}`);
  }
  for (const idx of noteheadMap) {
    if (idx < 0 || idx >= g.seq.length || g.seq[idx].rest) {
      fail(`${g.name}: noteheadMap points at bad seq index ${idx}`);
    }
  }
  // noteheadMap must be non-decreasing (document order follows seq order).
  for (let i = 1; i < noteheadMap.length; i++) {
    if (noteheadMap[i] < noteheadMap[i - 1]) fail(`${g.name}: noteheadMap not ordered`);
  }
}

// ---- chord-quality table drift guard (B1) ------------------------------------
(function chordQualities() {
  console.log("- chord qualities");
  // The browser copy must equal the Python source of truth (written into the golden).
  const want = goldenData.chordQualities;
  const got = MT.CHORD_QUALITIES;
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    fail("MT.CHORD_QUALITIES != golden chordQualities (run tests/gen_manual_golden.py)\n" +
      "      got:  " + JSON.stringify(got) + "\n      want: " + JSON.stringify(want));
  }
  // Every quality engraves with the same <kind> text music21 wrote (proves none is dropped).
  for (const [q, kindWanted] of Object.entries(goldenData.chordKinds)) {
    const seq = [{ midi: 60, durTicks: 4 }];
    const opts = { key: "C major", timeSig: [4, 4], divisions: 4,
      chords: [{ measure: 0, start_ql: 0, root_pc: 7, root_name: "G", quality: q, symbol: "G", roman: "" }] };
    const { xml } = MT.notesToMusicXML(seq, opts);
    const m = xml.match(/<kind[^>]*>([^<]*)<\/kind>/);
    const kindGot = m ? m[1] : null;
    if (kindGot !== kindWanted) fail(`chord ${q}: client <kind> ${JSON.stringify(kindGot)} != music21 ${JSON.stringify(kindWanted)}`);
  }
})();

// Extra unit checks on the pure helpers.
(function units() {
  console.log("- helper units");
  const eq = (label, a, b) => { if (JSON.stringify(a) !== JSON.stringify(b)) fail(`${label}: ${JSON.stringify(a)} != ${JSON.stringify(b)}`); };
  // spelling
  eq("spell 70 flat", MT.spell(70, true), { step: "B", alter: -1, octave: 4 });
  eq("spell 66 sharp", MT.spell(66, false), { step: "F", alter: 1, octave: 4 });
  eq("spell 60", MT.spell(60, false), { step: "C", alter: 0, octave: 4 });
  // key parsing / useFlats
  eq("F major useFlats", MT.parseKey("F major").useFlats, true);
  eq("C major useFlats", MT.parseKey("C major").useFlats, false);
  eq("D minor useFlats", MT.parseKey("D minor").useFlats, true);
  eq("A minor useFlats", MT.parseKey("A minor").useFlats, false);
  eq("G major fifths", MT.parseKey("G major").fifths, 1);
  eq("F major fifths", MT.parseKey("F major").fifths, -1);
  // clef
  eq("clef low", MT.bestClefSign([{ step: "E", octave: 2 }, { step: "G", octave: 2 }]), "F");
  eq("clef mid", MT.bestClefSign([{ step: "C", octave: 4 }, { step: "G", octave: 4 }]), "G");
  // decompose: dotted half kept single at offset 0 (D=4 -> 12 divisions)
  eq("decompose dotted half", MT.decompose(0, 12, 4).map((c) => [c.type, c.dots]), [["half", 1]]);
  // beat-4 half in 4/4 clipped to the bar is a quarter (barline split done upstream)
  eq("decompose quarter at off12", MT.decompose(12, 4, 4).map((c) => c.type), ["quarter"]);
})();

// Pure edit operations (Phase 4). Each returns a NEW seq + the sel to keep, and
// must not mutate its input.
(function editUnits() {
  console.log("- edit-op units");
  const eq = (label, a, b) => { if (JSON.stringify(a) !== JSON.stringify(b)) fail(`${label}: got ${JSON.stringify(a)} want ${JSON.stringify(b)}`); };
  const base = () => [
    { midi: 60, durTicks: 4, cents: -20 },
    { midi: 62, durTicks: 4, cents: 5 },
    { rest: true, durTicks: 2 },
    { midi: 64, durTicks: 8, cents: 0 },
  ];

  // pitch: shifts midi, clears cents, keeps sel; input untouched; clamps range.
  const src = base();
  const up = MT.EDITS.pitch(src, 0, 2);
  eq("pitch midi", up.seq[0].midi, 62);
  eq("pitch cents cleared", up.seq[0].cents, null);
  eq("pitch sel", up.sel, 0);
  eq("pitch immutable", src[0].midi, 60);
  eq("pitch clamp low", MT.EDITS.pitch(src, 0, -100).seq[0].midi, 12);
  eq("pitch on rest is no-op", MT.EDITS.pitch(src, 2, 3).seq[2].midi, undefined);

  // duration: adds ticks with a floor of 1.
  eq("duration longer", MT.EDITS.duration(base(), 0, 3).seq[0].durTicks, 7);
  eq("duration floor 1", MT.EDITS.duration(base(), 0, -99).seq[0].durTicks, 1);

  // mergeNext: fuse note 0 with the following event (summed duration, note 0 pitch).
  const merged = MT.EDITS.mergeNext(base(), 0);
  eq("merge length", merged.seq.length, 3);
  eq("merge dur", merged.seq[0].durTicks, 8);
  eq("merge midi kept", merged.seq[0].midi, 60);
  eq("merge sel", merged.sel, 0);
  eq("merge at last is no-op", MT.EDITS.mergeNext(base(), 3).seq.length, 4);

  // split: halve the note into two tied-length pieces.
  const split = MT.EDITS.split(base(), 3);   // durTicks 8 -> 4 + 4
  eq("split length", split.seq.length, 5);
  eq("split halves", [split.seq[3].durTicks, split.seq[4].durTicks], [4, 4]);
  eq("split too short no-op", MT.EDITS.split([{ midi: 60, durTicks: 1 }], 0).seq.length, 1);

  // deleteToRest: note -> rest, selection snaps to the nearest remaining note.
  const del = MT.EDITS.deleteToRest(base(), 0);
  eq("delete makes rest", del.seq[0].rest, true);
  eq("delete keeps dur", del.seq[0].durTicks, 4);
  eq("delete sel snaps to a note", del.seq[del.sel] && !del.seq[del.sel].rest, true);

  // insertAfter: new note after i (default = i's pitch), selection follows the new note.
  const ins = MT.EDITS.insertAfter(base(), 0, 4);
  eq("insert length", ins.seq.length, 5);
  eq("insert at index 1", [ins.seq[1].midi, ins.seq[1].durTicks], [60, 4]);
  eq("insert sel is new note", ins.sel, 1);
  eq("insert with no selection appends", MT.EDITS.insertAfter(base(), -1, 4).sel, 4);

  // snapSel: from a rest index, pick the nearest note.
  eq("snapSel from rest", MT.snapSel(base(), 2), 1);
  eq("snapSel already a note", MT.snapSel(base(), 3), 3);
  eq("snapSel no notes", MT.snapSel([{ rest: true, durTicks: 4 }], 0), -1);
})();

// ---- Path A: NL edit ops, interpreter, and describer -------------------------
(function () {
  const eq = (label, a, b) => { if (JSON.stringify(a) !== JSON.stringify(b)) fail(`${label}: got ${JSON.stringify(a)} want ${JSON.stringify(b)}`); };
  const ok = (label, cond) => { if (!cond) fail(label); };
  // note1 si0, note2 si1, rest si2, note3 si3, note4 si4, note5 si5
  const base = () => [
    { midi: 60, durTicks: 4, cents: 8 },
    { midi: 62, durTicks: 2, cents: 0 },
    { rest: true, durTicks: 2 },
    { midi: 64, durTicks: 4, cents: 0 },
    { midi: 64, durTicks: 4, cents: 0 },
    { midi: 67, durTicks: 8, cents: 0 },
  ];
  const mkLayout = (seq) => seq.map((e, i) => (e.rest ? null : { seqIndex: i, midi: e.midi, cents: e.cents }))
    .filter(Boolean);
  const OPTS = { key: "C major", timeSig: [4, 4], divisions: 4, tempo: 100 };

  // --- new EDITS ops ---
  const mr = MT.EDITS.mergeRange(base(), 1, 3);   // note2(2) + rest(2) + note3(4) -> one 8-tick note
  eq("mergeRange length", mr.seq.length, 4);
  eq("mergeRange dur swallows rest", mr.seq[1].durTicks, 8);
  eq("mergeRange keeps first-note pitch", mr.seq[1].midi, 62);

  const sp = MT.EDITS.splitInto(base(), 5, 3);    // dur 8 -> 2 + 2 + 4 (remainder on last)
  eq("splitInto length", sp.seq.length, 8);
  eq("splitInto pieces", [sp.seq[5].durTicks, sp.seq[6].durTicks, sp.seq[7].durTicks], [2, 2, 4]);
  eq("splitInto too short no-op", MT.EDITS.splitInto([{ midi: 60, durTicks: 2 }], 0, 3).seq.length, 1);

  const ir = MT.EDITS.insertRestAfter(base(), 5, 4);
  eq("insertRestAfter length", ir.seq.length, 7);
  eq("insertRestAfter is a rest", ir.seq[6].rest, true);
  ok("insertRestAfter sel snaps to a note", ir.seq[ir.sel] && !ir.seq[ir.sel].rest);

  // --- applyScript ---
  const run = (script, sel) => MT.applyScript(base(), mkLayout(base()), script, 4, sel == null ? -1 : sel);

  let r = run({ ops: [{ op: "pitch", note: 1, semitones: 12 }] });
  eq("pitch applies", r.seq[0].midi, 72);
  eq("pitch applied count", r.applied, 1);

  r = run({ ops: [{ op: "setPitch", note: 2, midi: 65 }] });
  eq("setPitch applies", r.seq[1].midi, 65);

  r = run({ ops: [{ op: "setDuration", note: 1, beats: 2 }] });
  eq("setDuration applies (2 beats = 8 ticks)", r.seq[0].durTicks, 8);

  r = run({ ops: [{ op: "transpose", from: 3, to: 5, semitones: -2 }] });
  eq("transpose range", [r.seq[3].midi, r.seq[4].midi, r.seq[5].midi], [62, 62, 65]);
  eq("transpose leaves earlier notes", r.seq[0].midi, 60);

  r = run({ ops: [{ op: "delete", note: 2 }] });
  eq("delete makes a rest", r.seq[1].rest, true);

  r = run({ ops: [{ op: "pitch", note: "last", semitones: 1 }] });
  eq("last resolves to the final note", r.seq[5].midi, 68);

  r = run({ ops: [{ op: "pitch", note: "selected", semitones: 1 }] }, 3);
  eq("selected resolves to selSeq", r.seq[3].midi, 65);

  // Mixed structural: split note 1 and merge notes 4-5, non-overlapping, both land.
  r = run({ ops: [{ op: "split", note: 1, parts: 2 }, { op: "merge", from: 4, to: 5 }] });
  ok("mixed script applies", !r.error);
  eq("mixed applied count", r.applied, 2);
  eq("mixed: note 1 split into two 2-tick pieces", [r.seq[0].durTicks, r.seq[1].durTicks], [2, 2]);
  // after split (+1 event) the merge target region collapses; net length 6 - 1 + 1 = 6
  eq("mixed length", r.seq.length, 6);
  eq("mixed: notes 4+5 merged to one 12-tick note", r.seq[r.seq.length - 1].durTicks, 12);

  // Rejections: nothing applied, an error message returned.
  r = run({ ops: [{ op: "pitch", note: 14, semitones: 1 }] });
  ok("out-of-range note rejects", !!r.error && r.seq === undefined);
  ok("rejection names the count", /12 notes|does not exist/.test(r.error));

  r = run({ ops: [{ op: "merge", from: 3, to: 5 }, { op: "split", note: 4, parts: 2 }] });
  ok("conflicting structural ops reject", !!r.error);

  r = run({ ops: [{ op: "frobnicate", note: 1 }] });
  ok("unknown op rejects", !!r.error);

  r = run({ ops: [] });
  ok("empty ops rejects", !!r.error);

  r = run({ ops: [{ op: "pitch", note: "selected", semitones: 1 }] }, -1);
  ok("selected with no selection rejects", !!r.error);

  // sel points at a real note after applying.
  r = run({ ops: [{ op: "pitch", note: 3, semitones: 1 }] });
  ok("sel is a note index", r.seq[r.sel] && !r.seq[r.sel].rest);

  // --- describeSeq ---
  const listing = MT.describeSeq(base(), OPTS, 3);
  const want =
    "Key: C major. Time: 4/4. Tempo: 100 bpm. 1 beat = 1 quarter note = 4 ticks.\n" +
    "5 notes. Selected: note 3.\n" +
    "Bar 1: 1: C4 1 beat (hummed +8c) | 2: D4 0.5 beat | rest 0.5 beat | 3: E4 1 beat | 4: E4 1 beat\n" +
    "Bar 2: 5: G4 2 beats";
  eq("describeSeq frozen format", listing, want);
  ok("describeSeq hides zero cents", listing.indexOf("2: D4 0.5 beat |") >= 0 && listing.indexOf("D4 0.5 beat (hummed") < 0);
  ok("describeSeq no-selection line", /Selected: none\./.test(MT.describeSeq(base(), OPTS, -1)));

  // --- parseLocalCommand (offline grammar) shares the script shape ---
  const lay = mkLayout(base());
  eq("local merge", MT.parseLocalCommand("merge notes 3 to 5", lay).ops[0], { op: "merge", from: 3, to: 5 });
  eq("local split into", MT.parseLocalCommand("split 4 into 3", lay).ops[0], { op: "split", note: 4, parts: 3 });
  eq("local octave", MT.parseLocalCommand("note 7 up an octave", lay).ops[0], { op: "pitch", note: 7, semitones: 12 });
  eq("local down semis", MT.parseLocalCommand("5 down 2", lay).ops[0], { op: "pitch", note: 5, semitones: -2 });
  eq("local delete", MT.parseLocalCommand("delete 2", lay).ops[0], { op: "delete", note: 2 });
  eq("local set half", MT.parseLocalCommand("3 = half", lay).ops[0], { op: "setDuration", note: 3, beats: 2 });
  ok("local no match falls through", MT.parseLocalCommand("make it jazzier", lay) === null);

  // --- round trip: describe, merge two slivers via a script, re-engrave stays valid ---
  const slivers = [{ midi: 64, durTicks: 4, cents: 0 }, { midi: 64, durTicks: 4, cents: 0 }];
  const rr = MT.applyScript(slivers, mkLayout(slivers), { ops: [{ op: "merge", from: 1, to: 2 }] }, 4, -1);
  ok("round trip merged to one note", rr.seq.length === 1 && rr.seq[0].durTicks === 8);
  const built = MT.notesToMusicXML(rr.seq, OPTS);
  ok("round trip re-engraves to valid MusicXML", typeof built.xml === "string" && built.xml.indexOf("<score-partwise") >= 0);
})();

if (failures) {
  console.error(`\n${failures} failure(s).`);
  process.exit(1);
}
console.log("\nAll builder golden + unit checks passed.");
