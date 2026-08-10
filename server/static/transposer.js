"use strict";

// Transposer tab - shift a score to a new key, then hear it and export it.
//
// Two source modes:
//   * FILE (primary): upload a MIDI or MusicXML file. It is transposed server-side by
//     music21 (POST /api/transpose-file), which moves every voice and the key signature
//     and returns an engraved SVG + transposed MusicXML/MIDI + a flat note list for
//     playback. Polyphony-safe; nothing to record first.
//   * HUM (secondary): transpose the last Transcriber result (window lastResult). This
//     path is monophonic and runs entirely client-side: it transposes notes/key/chords
//     here, re-engraves through the same MT + verovio path Manual mode uses, and exports
//     via the existing /api/export-edited.
//
// Transposing the entire piece by one fixed interval is a rigid shift: every scale degree
// and chord function is preserved, so a chord's Roman numeral is INVARIANT. We only move
// each chord root's pitch class and respell it for the destination key (hum mode; the file
// path lets music21 respell). That respelling reuses the same key-level flats/sharps rule
// the note builder uses, so chord roots read consistently with the notes. (One known
// nicety: the raised leading-tone chords of a flat minor key, V and vii, can show an
// enharmonic variant; the melody is always authoritative and unaffected.)
//
// The transpose math is pure and exposed on window.TR (and as a node module) so
// tests/manual/transposer.test.cjs can drive it. No em/en dashes or emojis per CLAUDE.md.

const TR = (() => {
  const SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  const FLAT = ["C", "D-", "D", "E-", "E", "F", "G-", "G", "A-", "A", "B-", "B"];
  const SUFFIX = { maj: "", min: "m", dim: "dim" };

  // Which keys the note builder spells with flats (mirrors manual.js MAJOR/MINOR_FLAT_PCS,
  // themselves baked from music21). Used to respell transposed chord roots consistently.
  const MAJOR_FLAT_PCS = new Set([5]);            // F major
  const MINOR_FLAT_PCS = new Set([0, 2, 5, 7]);   // C, D, F, G minor

  const mod12 = (n) => ((n % 12) + 12) % 12;

  function midiName(m) {
    return SHARP[mod12(m)] + (Math.floor(m / 12) - 1);
  }

  // Camelot wheel code for a key, e.g. (0,"major") -> "8B", (9,"minor") -> "8A".
  // Ported verbatim from analyze.py's _MAJOR_CAMELOT_NUM / to_camelot so the file-mode
  // presets read the same codes the Pitch Finder shows.
  const CAMELOT_NUM = { 0: 8, 1: 3, 2: 10, 3: 5, 4: 12, 5: 7,
    6: 2, 7: 9, 8: 4, 9: 11, 10: 6, 11: 1 };

  function toCamelot(pc, modeStr) {
    if (modeStr === "minor") return `${CAMELOT_NUM[mod12(pc + 3)]}A`;
    return `${CAMELOT_NUM[mod12(pc)]}B`;
  }

  // "C major" / "F minor" -> { pc, mode }. null when there is no usable key.
  function parseKey(keyStr) {
    if (!keyStr) return null;
    const parts = String(keyStr).trim().split(/\s+/);
    const pc = SHARP.indexOf(parts[0]);
    const mode = (parts[1] || "").toLowerCase();
    if (pc < 0 || (mode !== "major" && mode !== "minor")) return null;
    return { pc, mode };
  }

  function useFlatsFor(pc, mode) {
    return mode === "major" ? MAJOR_FLAT_PCS.has(pc) : MINOR_FLAT_PCS.has(pc);
  }

  // The smallest-magnitude shift (in [-6, 6]) that moves fromPc onto toPc, keeping the
  // melody as close as possible to its original register. Ties (a tritone) go up.
  function minimalShift(fromPc, toPc) {
    let d = mod12(toPc - fromPc);
    if (d > 6) d -= 12;
    return d;
  }

  const IVL = ["unison", "minor 2nd", "major 2nd", "minor 3rd", "major 3rd",
    "perfect 4th", "tritone", "perfect 5th", "minor 6th", "major 6th",
    "minor 7th", "major 7th", "octave"];

  // Human label for a signed semitone shift, e.g. -7 -> "down a perfect 5th".
  function shiftLabel(shift) {
    if (!shift) return "no change";
    const dir = shift > 0 ? "up" : "down";
    const mag = Math.abs(shift);
    const name = mag <= 12 ? IVL[mag] : `${mag} semitones`;
    const article = /^[aeiou]/i.test(name) ? "an" : "a";
    return `${dir} ${article} ${name}`;
  }

  // "C major" shifted by `shift` semitones -> "D major" (mode preserved). null in, null out.
  function transposeKey(keyStr, shift) {
    const k = parseKey(keyStr);
    if (!k) return null;
    return `${SHARP[mod12(k.pc + shift)]} ${k.mode}`;
  }

  // Copy each note up/down `shift` semitones. start_ql / dur_ql / cents / velocity ride
  // along unchanged; only midi (and its display name) move.
  function transposeNotes(notes, shift) {
    return (notes || []).map((n) => ({
      ...n,
      midi: n.midi + shift,
      name: midiName(n.midi + shift),
    }));
  }

  // Move each chord's root by `shift` and respell root_name / symbol for the destination
  // key. quality, measure, start_ql are unchanged; roman is invariant under transposition.
  function transposeChords(chords, shift, newKeyStr) {
    const nk = parseKey(newKeyStr);
    const useFlats = nk ? useFlatsFor(nk.pc, nk.mode) : true;
    const table = useFlats ? FLAT : SHARP;
    return (chords || []).map((c) => {
      const rootPc = mod12(c.root_pc + shift);
      const rootName = table[rootPc];
      const symbol = rootName.replace("-", "♭").replace("#", "♯") +
        (SUFFIX[c.quality] || "");
      return { ...c, root_pc: rootPc, root_name: rootName, symbol };
    });
  }

  // Everything the export/playback need for one transposition, computed purely (hum mode).
  function transpose(src, shift) {
    const key = transposeKey(src.key, shift);
    return {
      shift,
      key,
      notes: transposeNotes(src.notes, shift),
      chords: transposeChords(src.chords, shift, key),
    };
  }

  // ---- controller (browser only) --------------------------------------------

  const CHORD_QUAL = { maj: [0, 4, 7], min: [0, 3, 7], dim: [0, 3, 6] };
  const FILE_DEBOUNCE_MS = 180;

  function createTransposer() {
    const $ = (id) => document.getElementById(id);
    const refs = {
      source: $("trSource"), drop: $("trDrop"), file: $("trFile"),
      humRow: $("trHumRow"), useHum: $("trUseHum"), sourceStatus: $("trSourceStatus"),
      panel: $("trPanel"), summary: $("trSummary"),
      shift: $("trShift"), shiftOut: $("trShiftOut"), down: $("trDown"), up: $("trUp"),
      reset: $("trReset"), targetKey: $("trTargetKey"), chords: $("trChords"),
      camelot: $("trCamelot"), camelotCode: $("trCamelotCode"), camelotPresets: $("trCamelotPresets"),
      play: $("trPlay"), playChords: $("trPlayChords"), playChordsRow: $("trPlayChordsRow"),
      exportBtn: $("trExport"), sheet: $("trSheet"), downloads: $("trDownloads"),
      status: $("trStatus"), humNote: $("trHumNote"),
    };

    let mode = null;      // "hum" | "file"
    let file = null;      // uploaded File (file mode)
    let src = null;       // source context: hum -> lastResult; file -> { tempo_bpm, time_sig }
    let cur = null;       // current transposed result (both modes carry notes + key + shift)
    let base = null;      // source (shift 0) key: { pc, mode, keyDisplay }
    let shift = 0;
    let player = null;    // our own playback handle, independent of app.js's
    let fileTimer = null; // debounce for the file transpose request
    let fileReqToken = 0; // drops stale file responses
    let engToken = 0;     // drops stale hum engravings
    let camelotSig = null; // "pc:mode" the Camelot chips were last built for

    const MIN_SHIFT = -12, MAX_SHIFT = 12;
    const clampShift = (v) => Math.max(MIN_SHIFT, Math.min(MAX_SHIFT, v | 0));
    const setStatus = (t) => { if (refs.status) refs.status.textContent = t || ""; };
    const setSourceStatus = (t) => { if (refs.sourceStatus) refs.sourceStatus.textContent = t || ""; };
    const hide = (el, yes) => { if (el) el.hidden = yes; };

    function currentHum() {
      // lastResult is app.js's module-level binding (shared script scope, like the audio
      // helpers realtime.js reuses). Guarded so a missing/empty result degrades cleanly.
      const lr = typeof lastResult !== "undefined" ? lastResult : null;
      if (!lr || !lr.notes || !lr.notes.length) return null;
      return lr;
    }

    // ---- shared UI bits -----------------------------------------------------

    function populateTargetKeys(pc, modeStr) {
      const sel = refs.targetKey;
      if (!sel) return;
      sel.innerHTML = "";
      if (pc == null || !modeStr) { sel.disabled = true; return; }
      sel.disabled = false;
      for (let p = 0; p < 12; p++) {
        const opt = document.createElement("option");
        opt.value = String(p);
        opt.textContent = `${SHARP[p]} ${modeStr}`;
        sel.appendChild(opt);
      }
    }

    // File mode only: show the source key's Camelot code and its two perfect-fifth
    // neighbors (adjacent on the wheel, same mode) as one-click transposition presets.
    // The relative major/minor neighbor is a mode change, not a rigid shift, so it is
    // deliberately not offered here. Hum mode hides this whole row.
    function renderCamelot() {
      const box = refs.camelot;
      if (!box) return;
      if (mode !== "file" || !base || base.pc == null || !base.mode) {
        hide(box, true);
        camelotSig = null;
        return;
      }
      hide(box, false);
      if (refs.camelotCode) {
        refs.camelotCode.textContent = `${SHARP[base.pc]} ${base.mode} (${toCamelot(base.pc, base.mode)})`;
      }

      const sig = `${base.pc}:${base.mode}`;
      if (sig !== camelotSig && refs.camelotPresets) {
        camelotSig = sig;
        refs.camelotPresets.innerHTML = "";
        // Down a fifth (7B side) then up a fifth (9B side), as pitch classes. The applied
        // shift is minimalShift, so playback stays in register; the label names the key.
        for (const delta of [5, 7]) {
          const tPc = mod12(base.pc + delta);
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "tr-camelot-chip";
          btn.dataset.pc = String(tPc);
          btn.textContent = `${SHARP[tPc]} ${base.mode} (${toCamelot(tPc, base.mode)})`;
          btn.addEventListener("click", () => setShift(minimalShift(base.pc, tPc)));
          refs.camelotPresets.appendChild(btn);
        }
      }
      // Highlight whichever preset the current shift has landed on.
      const curPc = mod12(base.pc + shift);
      if (refs.camelotPresets) {
        for (const btn of refs.camelotPresets.querySelectorAll(".tr-camelot-chip")) {
          btn.classList.toggle("active", parseInt(btn.dataset.pc, 10) === curPc);
        }
      }
    }

    function syncTargetKey() {
      if (!base || base.pc == null || !refs.targetKey || refs.targetKey.disabled) return;
      refs.targetKey.value = String(mod12(base.pc + shift));
    }

    function updateShiftReadout() {
      if (refs.shift) refs.shift.value = String(shift);
      if (refs.shiftOut) refs.shiftOut.value = `${shift > 0 ? "+" : ""}${shift}`;
    }

    function renderSummary(toKey, noteCount) {
      if (!refs.summary) return;
      const from = base ? base.keyDisplay : "unknown key";
      const to = toKey || "unknown key";
      refs.summary.innerHTML =
        `<div class="stat"><span class="val">${to}</span><span class="lbl">New key</span></div>` +
        `<div class="stat"><span class="val">${shift > 0 ? "+" : ""}${shift}</span>` +
        `<span class="lbl">Semitones</span></div>` +
        `<div class="stat"><span class="val">${src && src.tempo_bpm ? Math.round(src.tempo_bpm) : "-"}</span>` +
        `<span class="lbl">BPM</span></div>` +
        `<div class="stat"><span class="val">${noteCount}</span><span class="lbl">Notes</span></div>`;
      const p = document.createElement("p");
      p.className = "hint";
      p.textContent = shift === 0
        ? `Original key: ${from}. Drag the slider or pick a key to transpose.`
        : `${from} transposed ${shiftLabel(shift)} to ${to}.`;
      refs.summary.appendChild(p);
    }

    function renderChordStrip(chords) {
      const box = refs.chords;
      if (!box) return;
      box.innerHTML = "";
      if (!chords || !chords.length) return;
      const label = document.createElement("span");
      label.className = "chords-label";
      label.textContent = "Suggested chords";
      box.appendChild(label);
      const strip = document.createElement("div");
      strip.className = "chord-strip";
      for (const c of chords) {
        const cell = document.createElement("div");
        cell.className = "chord-cell";
        cell.innerHTML = `<span class="chord-sym">${c.symbol}</span>` +
          `<span class="chord-rn">${c.roman}</span>`;
        cell.title = `Measure ${c.measure + 1}`;
        strip.appendChild(cell);
      }
      box.appendChild(strip);
    }

    function showDownloads(midiB64, musicxml) {
      const box = refs.downloads;
      if (!box) return;
      box.innerHTML = "";
      if (!midiB64 && !musicxml) return;
      const tag = (cur && cur.key ? cur.key : "transposed").replace(/\s+/g, "-").toLowerCase();
      const link = (text, blob, filename) => {
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = filename;
        a.textContent = text;
        return a;
      };
      if (midiB64) box.appendChild(link("Download MIDI", b64ToBlob(midiB64, "audio/midi"), `${tag}.mid`));
      if (musicxml) box.appendChild(link("Download MusicXML",
        new Blob([musicxml], { type: "application/xml" }), `${tag}.musicxml`));
    }

    function syncModeUI() {
      const hum = mode === "hum";
      hide(refs.chords, !hum);
      hide(refs.playChordsRow, !hum);
      hide(refs.exportBtn, !hum);
      hide(refs.humNote, !hum);
      renderCamelot();   // hides itself for hum; shows once a file's key is known
    }

    function showPanel() { hide(refs.panel, false); }

    // ---- hum mode (client-side) ---------------------------------------------

    function useHum() {
      const lr = currentHum();
      if (!lr) return;
      mode = "hum";
      file = null;
      src = lr;
      const k = parseKey(lr.key);
      base = { pc: k ? k.pc : null, mode: k ? k.mode : null, keyDisplay: lr.key || "unknown key" };
      shift = 0;
      populateTargetKeys(base.pc, base.mode);
      if (refs.downloads) refs.downloads.innerHTML = "";
      setStatus("");
      setSourceStatus("Transposing your last hum.");
      showPanel();
      syncModeUI();
      applyHum();
    }

    function engraveHum() {
      if (typeof MT === "undefined" || !refs.sheet) {
        if (refs.sheet) refs.sheet.innerHTML = "<p class='hint'>Notation engine unavailable.</p>";
        return;
      }
      const subdiv = src.subdiv || 4;
      const seq = MT.seqFromNotes(cur.notes, subdiv);
      const opts = { divisions: subdiv, timeSig: src.time_sig || [4, 4], key: cur.key, chords: cur.chords };
      const token = ++engToken;
      MT.renderSeq(seq, opts).then((out) => {
        if (token !== engToken) return;
        refs.sheet.innerHTML = out.svg;
      }).catch(() => {
        if (token !== engToken) return;
        refs.sheet.innerHTML = "<p class='hint'>Could not engrave this transposition.</p>";
      });
    }

    function applyHum() {
      stopPlayback();
      cur = transpose(src, shift);
      updateShiftReadout();
      renderSummary(cur.key, cur.notes.length);
      renderChordStrip(cur.chords);
      syncTargetKey();
      engraveHum();
    }

    // ---- file mode (server-side music21) ------------------------------------

    function loadFile(f) {
      if (!f) return;
      mode = "file";
      file = f;
      src = null;
      base = null;
      cur = null;
      shift = 0;
      if (refs.downloads) refs.downloads.innerHTML = "";
      updateShiftReadout();
      setSourceStatus(`Loaded ${f.name}. Reading and analyzing...`);
      showPanel();
      syncModeUI();
      transposeFile(0, true);
    }

    function scheduleFileTranspose() {
      clearTimeout(fileTimer);
      fileTimer = setTimeout(() => transposeFile(shift, false), FILE_DEBOUNCE_MS);
    }

    function transposeFile(sh, initial) {
      if (!file) return;
      stopPlayback();
      setStatus("transposing...");
      const fd = new FormData();
      fd.append("file", file, file.name);
      fd.append("semitones", String(sh));
      const token = ++fileReqToken;
      fetch("/api/transpose-file", { method: "POST", body: fd })
        .then(async (r) => {
          if (!r.ok) throw new Error(await r.text());
          return r.json();
        })
        .then((data) => {
          if (token !== fileReqToken) return;   // a newer request superseded this one
          src = { tempo_bpm: data.tempo_bpm, time_sig: data.time_sig };
          cur = {
            shift: sh, key: data.key, notes: data.notes || [],
            musicxml: data.musicxml, midi_b64: data.midi_b64,
          };
          if (initial || !base) {
            base = { pc: data.key_pc, mode: data.key_mode, keyDisplay: data.key || "unknown key" };
            populateTargetKeys(base.pc, base.mode);
            renderCamelot();
            setSourceStatus(`Loaded ${file.name}.`);
          }
          renderSummary(data.key, cur.notes.length);
          if (refs.sheet) {
            refs.sheet.innerHTML = data.svg || "<p class='hint'>Preview unavailable for this file.</p>";
          }
          showDownloads(data.midi_b64, data.musicxml);
          syncTargetKey();
          setStatus("ready");
        })
        .catch(() => {
          if (token !== fileReqToken) return;
          setStatus("transpose failed");
          setSourceStatus("Could not read this file. Is it a valid MIDI or MusicXML?");
        });
    }

    // Predicted new-key label for instant feedback while the file request is in flight.
    function predictedKey() {
      if (base && base.pc != null && base.mode) return `${SHARP[mod12(base.pc + shift)]} ${base.mode}`;
      return cur ? cur.key : null;
    }

    // ---- shift controls (both modes) ----------------------------------------

    function setShift(v) {
      shift = clampShift(v);
      updateShiftReadout();
      if (mode === "hum") {
        applyHum();
      } else if (mode === "file") {
        renderSummary(predictedKey(), cur ? cur.notes.length : 0);
        renderCamelot();   // move the active-preset highlight to the new shift
        setStatus("transposing...");
        scheduleFileTranspose();
      }
    }

    // ---- playback (our own handle; reuses app.js voices) --------------------

    function stopPlayback() {
      if (!player) return;
      clearTimeout(player.timer);
      const m = player.master;
      try { m.gain.setTargetAtTime(0.0001, m.context.currentTime, 0.02); } catch (e) {}
      setTimeout(() => { try { m.disconnect(); } catch (e) {} }, 120);
      player = null;
      if (refs.play) { refs.play.textContent = "▶ Play"; refs.play.classList.remove("playing"); }
    }

    async function togglePlayback() {
      if (player) { stopPlayback(); return; }
      if (!cur || !cur.notes || !cur.notes.length) return;
      if (typeof ensureAudio !== "function" || typeof sampleVoice !== "function") return;

      const ctx = ensureAudio();
      if (refs.play) refs.play.textContent = "...";
      if (typeof loadPiano === "function") { try { await loadPiano(ctx); } catch (e) {} }
      if (player) return;

      const spb = 60 / ((src && src.tempo_bpm) || 100);
      const master = ctx.createGain();
      master.gain.value = 0.8;
      master.connect(ctx.destination);
      const t0 = ctx.currentTime + 0.08;
      let endT = t0;

      // Hum mode voices the suggested triads as a separate accompaniment; file mode's
      // note list already contains every voice, so it plays as-is.
      const ts = (src && src.time_sig) || [4, 4];
      if (mode === "hum" && refs.playChords && refs.playChords.checked && cur.chords && cur.chords.length) {
        const barQl = ts[0] * (4 / ts[1]);
        for (const c of cur.chords) {
          const start = t0 + c.start_ql * spb;
          const dur = barQl * spb;
          const rootMidi = 48 + c.root_pc;
          for (const iv of CHORD_QUAL[c.quality] || CHORD_QUAL.maj) {
            sampleVoice(ctx, master, rootMidi + iv, start, dur * 0.9, 0.09);
          }
          endT = Math.max(endT, start + dur);
        }
      }
      const peak = mode === "file" ? 0.32 : 0.42;   // polyphony is denser; ease the level
      for (const n of cur.notes) {
        if (n.start_ql == null || n.dur_ql == null) continue;
        const start = t0 + n.start_ql * spb;
        const dur = Math.max(0.08, n.dur_ql * spb);
        sampleVoice(ctx, master, n.midi, start, dur, peak);
        endT = Math.max(endT, start + dur);
      }

      if (refs.play) { refs.play.textContent = "■ Stop"; refs.play.classList.add("playing"); }
      const timer = setTimeout(stopPlayback, (endT - ctx.currentTime + 0.15) * 1000);
      player = { master, timer };
    }

    // ---- hum-mode export (reuses /api/export-edited) ------------------------

    async function exportEdited() {
      if (mode !== "hum" || !cur || !cur.notes.length) return;
      setStatus("building files...");
      const payload = {
        notes: cur.notes.map((n) => ({
          midi: n.midi, start_ql: n.start_ql, dur_ql: n.dur_ql, velocity: n.velocity || 80,
        })),
        key: cur.key, chords: cur.chords, tempo: src.tempo_bpm, time_sig: src.time_sig || [4, 4],
      };
      try {
        const res = await fetch("/api/export-edited", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
        });
        if (!res.ok) throw new Error(await res.text());
        const data = await res.json();
        showDownloads(data.midi_b64, data.musicxml);
        setStatus("ready");
      } catch (e) {
        setStatus("export failed");
      }
    }

    // ---- wiring -------------------------------------------------------------

    if (refs.file) refs.file.addEventListener("change", (e) => {
      const f = e.target.files && e.target.files[0];
      if (f) loadFile(f);
    });
    if (refs.drop) {
      refs.drop.addEventListener("dragover", (e) => { e.preventDefault(); refs.drop.classList.add("drag"); });
      refs.drop.addEventListener("dragleave", () => refs.drop.classList.remove("drag"));
      refs.drop.addEventListener("drop", (e) => {
        e.preventDefault();
        refs.drop.classList.remove("drag");
        const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
        if (f) loadFile(f);
      });
    }
    if (refs.useHum) refs.useHum.addEventListener("click", useHum);

    if (refs.shift) refs.shift.addEventListener("input", () => setShift(parseInt(refs.shift.value, 10) || 0));
    if (refs.down) refs.down.addEventListener("click", () => setShift(shift - 1));
    if (refs.up) refs.up.addEventListener("click", () => setShift(shift + 1));
    if (refs.reset) refs.reset.addEventListener("click", () => setShift(0));
    if (refs.targetKey) refs.targetKey.addEventListener("change", () => {
      if (!base || base.pc == null) return;
      setShift(minimalShift(base.pc, parseInt(refs.targetKey.value, 10) || 0));
    });
    if (refs.play) refs.play.addEventListener("click", togglePlayback);
    if (refs.exportBtn) refs.exportBtn.addEventListener("click", exportEdited);

    // ---- lifecycle ----------------------------------------------------------

    function enter() {
      // Offer "transpose my last hum" only when a hum exists; never require it.
      hide(refs.humRow, !currentHum());
      if (!mode) hide(refs.panel, true);   // no source chosen yet -> just the source card
    }

    function exit() { stopPlayback(); }

    return {
      enter, exit,
      // exposed for debugging / tests
      _get: () => cur, _setShift: setShift, _useHum: useHum,
    };
  }

  return {
    parseKey, minimalShift, shiftLabel, transposeKey, transposeNotes,
    transposeChords, transpose, midiName, toCamelot, createTransposer,
  };
})();

if (typeof window !== "undefined") window.TR = TR;
if (typeof module !== "undefined" && module.exports) module.exports = TR;
