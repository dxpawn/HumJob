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

const golden = JSON.parse(fs.readFileSync(GOLDEN, "utf-8"));
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

if (failures) {
  console.error(`\n${failures} failure(s).`);
  process.exit(1);
}
console.log("\nAll builder golden + unit checks passed.");
