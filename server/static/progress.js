/* ============================================================================
   HumJob - progress aggregator (window.PG).

   Pure, DOM-free math (Pattern B, like vocal.js): the hub reads the four practice
   localStorage keys and hands the raw arrays here; this folds them into one compact,
   rounded, PII-free report and a set of deterministic readout lines. No timestamps
   leave beyond day buckets, and no field is named t / frames / audio / filename, so
   the whole report is safe to POST to the progress coach.

   Inputs (exactly as stored by the writers):
     voice     humjob.voice.history       {t,key,camelot,inTunePct(0-100|null),
                                            bestSustainS,steadinessMedianC,
                                            vibrato:{rateHz,depthCents}|null,rangeLo,rangeHi}
     ranges    humjob.voice.rangeTest     {t, report:{lo,hiExt,extST,tessitura,voiceTypes,group,...}}
     ear       humjob.eartrainer.history  {t, mode, difficulty, ok(0|1)}
     singalong humjob.singalong.history   {t, report:<buildCoachReport output>}

   History is already capped by each writer (50 / 10 / 300 / 50), so we fold all of
   it rather than day-filtering; small demo installs keep every data point they have.
   ========================================================================= */
(function () {
  "use strict";

  var WINDOW_DAYS = 28;          // nominal reporting window (label only; writers cap the data)
  var DAY = 86400000;
  var RECENT_EAR = 20;           // "recent" per-mode ear accuracy uses the last this-many answers
  var MIN = { voice: 5, singalong: 3, ear: 20, range: 2 }; // low-confidence thresholds

  // ---- tiny stats helpers ----------------------------------------------------
  function nums(a) { return (a || []).filter(function (v) { return typeof v === "number" && isFinite(v); }); }
  function sum(a) { return a.reduce(function (s, v) { return s + v; }, 0); }
  function mean(a) { return a.length ? sum(a) / a.length : null; }
  function median(a) {
    if (!a.length) return null;
    var s = a.slice().sort(function (x, y) { return x - y; });
    var m = Math.floor(s.length / 2);
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  }
  function r0(x) { return x == null ? null : Math.round(x); }
  function r2(x) { return x == null ? null : Math.round(x * 100) / 100; }

  // First-half vs second-half means, the same split hub.js uses for its delta:
  // first = mean of series.slice(0, floor(n/2)), last = mean of the rest.
  function firstLast(series) {
    if (!series.length) return { first: null, last: null };
    if (series.length === 1) return { first: series[0], last: series[0] };
    var half = Math.floor(series.length / 2);
    return { first: mean(series.slice(0, half)), last: mean(series.slice(half)) };
  }

  // Ordinary least squares slope of y over take index 0..n-1.
  function slope(y) {
    var n = y.length;
    if (n < 2) return 0;
    var xbar = (n - 1) / 2;
    var ybar = mean(y);
    var num = 0, den = 0;
    for (var i = 0; i < n; i++) { num += (i - xbar) * (y[i] - ybar); den += (i - xbar) * (i - xbar); }
    return den === 0 ? 0 : num / den;
  }

  function dayStamp(t) { var d = new Date(t); d.setHours(0, 0, 0, 0); return d.getTime(); }

  // Consecutive-day streak ending today or yesterday, over a set of day buckets.
  function streakFrom(times, now) {
    if (!times.length) return 0;
    var days = new Set(times.map(dayStamp));
    var today = dayStamp(now);
    var cursor = days.has(today) ? today : (days.has(today - DAY) ? today - DAY : null);
    if (cursor == null) return 0;
    var n = 0;
    while (days.has(cursor)) { n++; cursor -= DAY; }
    return n;
  }

  var NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  function midiName(m) {
    if (m == null || !isFinite(m)) return "?";
    var r = Math.round(m);
    return NOTE_NAMES[((r % 12) + 12) % 12] + (Math.floor(r / 12) - 1);
  }

  // ---- the aggregator --------------------------------------------------------
  function buildProgressReport(data, now) {
    data = data || {};
    now = now || Date.now();
    var voice = Array.isArray(data.voice) ? data.voice.slice() : [];
    var ranges = Array.isArray(data.ranges) ? data.ranges.slice() : [];
    var ear = Array.isArray(data.ear) ? data.ear.slice() : [];
    var singalong = Array.isArray(data.singalong) ? data.singalong.slice() : [];

    var byT = function (a, b) { return (a.t || 0) - (b.t || 0); };
    voice.sort(byT); ranges.sort(byT); ear.sort(byT); singalong.sort(byT);

    // ---- window / activity (all sources) ----
    var allTimes = []
      .concat(voice.map(function (r) { return r.t; }))
      .concat(ranges.map(function (r) { return r.t; }))
      .concat(ear.map(function (r) { return r.t; }))
      .concat(singalong.map(function (r) { return r.t; }))
      .filter(function (t) { return typeof t === "number"; });
    var activeDays = new Set(allTimes.map(dayStamp)).size;
    var firstTakeDaysAgo = allTimes.length
      ? Math.floor((now - Math.min.apply(null, allTimes)) / DAY) : null;

    // ---- voice: in tune / steadiness / sustain / vibrato ----
    var inTuneSeries = nums(voice.map(function (r) { return r.inTunePct; }));
    var itFL = firstLast(inTuneSeries);
    var inTune = {
      n: inTuneSeries.length,
      mean: r0(mean(inTuneSeries)),
      first: r0(itFL.first),
      last: r0(itFL.last),
      delta: (itFL.first == null || itFL.last == null) ? 0 : Math.round(itFL.last - itFL.first),
      best: inTuneSeries.length ? Math.round(Math.max.apply(null, inTuneSeries)) : null,
      slopePerTake: r2(slope(inTuneSeries)),
    };

    var steadySeries = nums(voice.map(function (r) { return r.steadinessMedianC; }));
    var stFL = firstLast(steadySeries);
    var steadiness = {
      n: steadySeries.length,
      medianCents: r0(median(steadySeries)),
      first: r0(stFL.first),
      last: r0(stFL.last),
    };

    var sustainSeries = nums(voice.map(function (r) { return r.bestSustainS; }));
    var suFL = firstLast(sustainSeries);
    var sustain = {
      bestS: sustainSeries.length ? Math.round(10 * Math.max.apply(null, sustainSeries)) / 10 : null,
      first: r2(suFL.first),
      last: r2(suFL.last),
    };

    var vibTakes = voice.filter(function (r) { return r.vibrato && typeof r.vibrato.rateHz === "number"; });
    var vibrato = {
      takesWithVibrato: vibTakes.length,
      rateHzMedian: r2(median(nums(vibTakes.map(function (r) { return r.vibrato.rateHz; })))),
      depthCentsMedian: r0(median(nums(vibTakes.map(function (r) { return r.vibrato.depthCents; })))),
    };

    // ---- range tests (earliest vs latest) ----
    var rangeReports = ranges.map(function (r) { return r.report; }).filter(Boolean);
    var range;
    if (rangeReports.length) {
      var firstR = rangeReports[0], lastR = rangeReports[rangeReports.length - 1];
      var pick = function (rep) {
        return { lo: r0(rep.lo), hiExt: r0(rep.hiExt), extST: r0(rep.extST) };
      };
      var tess = lastR.tessitura;
      range = {
        tests: rangeReports.length,
        first: pick(firstR),
        last: pick(lastR),
        deltaST: (firstR.extST == null || lastR.extST == null) ? null : Math.round(lastR.extST - firstR.extST),
        voiceTypes: (Array.isArray(lastR.voiceTypes) ? lastR.voiceTypes : [])
          .slice(0, 2).map(function (v) { return v && v.label; }).filter(Boolean),
        tessitura: (tess && tess.p50 != null)
          ? { p25: r0(tess.p25), p50: r0(tess.p50), p75: r0(tess.p75) } : null,
        group: lastR.group || null,
      };
    } else {
      range = { tests: 0, first: null, last: null, deltaST: null, voiceTypes: [], tessitura: null, group: null };
    }

    // ---- Sing-Along takes ----
    var saReports = singalong.map(function (r) { return r.report; }).filter(Boolean);
    var singalongBlk;
    if (saReports.length) {
      var itPct = saReports.map(function (rep) { return rep.summary && rep.summary.inTunePct; })
        .filter(function (v) { return typeof v === "number"; });
      var bias = nums(saReports.map(function (rep) { return rep.signedBiasCents; }));
      var biasFL = firstLast(bias);
      // Weakest register: most frequent report.register.weakest.
      var regCount = {};
      saReports.forEach(function (rep) {
        var w = rep.register && rep.register.weakest;
        if (w) regCount[w] = (regCount[w] || 0) + 1;
      });
      var weakestRegister = null, weakestC = 0;
      Object.keys(regCount).forEach(function (k) { if (regCount[k] > weakestC) { weakestC = regCount[k]; weakestRegister = k; } });
      // Leap minus step in percentage points, averaged over takes where both exist.
      var lms = [];
      saReports.forEach(function (rep) {
        var lp = rep.leaps && rep.leaps.leap, sp = rep.leaps && rep.leaps.step;
        if (lp && sp && typeof lp.hitPct === "number" && typeof sp.hitPct === "number") {
          lms.push((lp.hitPct - sp.hitPct) * 100);
        }
      });
      // Recurring worst notes across all takes: top 3 with count >= 2.
      var noteCount = {};
      saReports.forEach(function (rep) {
        (rep.worstNotes || []).forEach(function (n) { if (n && n.name) noteCount[n.name] = (noteCount[n.name] || 0) + 1; });
      });
      var recurringWorst = Object.keys(noteCount).map(function (name) { return { name: name, count: noteCount[name] }; })
        .filter(function (x) { return x.count >= 2; })
        .sort(function (a, b) { return b.count - a.count || (a.name < b.name ? -1 : 1); })
        .slice(0, 3);
      singalongBlk = {
        takes: saReports.length,
        inTunePct: {
          first: itPct.length ? Math.round(itPct[0] * 100) : null,
          last: itPct.length ? Math.round(itPct[itPct.length - 1] * 100) : null,
        },
        signedBiasCents: { mean: r0(mean(bias)), first: r0(biasFL.first), last: r0(biasFL.last) },
        weakestRegister: weakestRegister,
        leapMinusStepPct: lms.length ? Math.round(mean(lms)) : null,
        recurringWorst: recurringWorst,
        octaveSlipsTotal: sum(nums(saReports.map(function (rep) { return rep.octaveSlips; }))),
      };
    } else {
      singalongBlk = {
        takes: 0, inTunePct: { first: null, last: null },
        signedBiasCents: { mean: null, first: null, last: null },
        weakestRegister: null, leapMinusStepPct: null, recurringWorst: [], octaveSlipsTotal: 0,
      };
    }

    // ---- Ear Trainer per mode ----
    var earBlk = {};
    var earModes = ["interval", "chord", "scale", "key"];
    var earTotal = 0;
    earModes.forEach(function (mode) {
      var set = ear.filter(function (r) { return r.mode === mode; });
      var ok = set.filter(function (r) { return r.ok; }).length;
      var recent = set.slice(-RECENT_EAR);
      var recentOk = recent.filter(function (r) { return r.ok; }).length;
      earTotal += set.length;
      earBlk[mode] = {
        n: set.length,
        pct: set.length ? Math.round((100 * ok) / set.length) : null,
        recentPct: recent.length ? Math.round((100 * recentOk) / recent.length) : null,
      };
    });

    var window_ = {
      days: WINDOW_DAYS,
      takes: voice.length,
      activeDays: activeDays,
      streakDays: streakFrom(allTimes, now),
      firstTakeDaysAgo: firstTakeDaysAgo,
    };

    var lowConfidence = {
      voice: inTune.n < MIN.voice,
      singalong: singalongBlk.takes < MIN.singalong,
      ear: earTotal < MIN.ear,
      range: range.tests < MIN.range,
    };

    // Fixed key order so JSON.stringify(report) hashes stably across renders.
    return {
      window: window_,
      inTune: inTune,
      steadiness: steadiness,
      sustain: sustain,
      vibrato: vibrato,
      range: range,
      singalong: singalongBlk,
      ear: earBlk,
      lowConfidence: lowConfidence,
    };
  }

  // ---- deterministic readout lines (rendered offline, no API key needed) -----
  function describeProgress(report) {
    if (!report) return [];
    var lines = [];
    var lc = report.lowConfidence || {};
    var sgn = function (x) { return (x > 0 ? "+" : "") + x; };

    // In tune
    var it = report.inTune;
    if (lc.voice) {
      if (it.n > 0) lines.push("In tune: not enough takes yet (" + it.n + " of " + MIN.voice + ")");
    } else {
      lines.push("In tune: " + it.first + "% -> " + it.last + "% over your last " + it.n +
        " takes (" + sgn(it.delta) + ")");
      if (report.steadiness.medianCents != null) {
        var st = report.steadiness;
        var was = (st.first != null && st.first !== st.medianCents) ? " (was " + st.first + ")" : "";
        lines.push("Steadiness: median " + st.medianCents + " cents" + was);
      }
      if (report.sustain.bestS != null && report.sustain.bestS > 0) {
        lines.push("Sustain: best " + report.sustain.bestS + " seconds");
      }
    }

    // Range
    var rg = report.range;
    if (rg.tests > 0 && rg.last) {
      var line = "Range: " + midiName(rg.last.lo) + " to " + midiName(rg.last.hiExt) +
        ", " + rg.last.extST + " semitones";
      if (rg.tests >= 2 && rg.deltaST != null && rg.deltaST !== 0) {
        line += "; " + (rg.deltaST > 0 ? "up " : "down ") + Math.abs(rg.deltaST) + " since your first test";
      } else if (rg.tests < MIN.range) {
        line += " (1 test so far)";
      }
      lines.push(line);
    }

    // Sing-Along
    var sa = report.singalong;
    if (lc.singalong) {
      if (sa.takes > 0) lines.push("Sing-Along: not enough takes yet (" + sa.takes + " of " + MIN.singalong + ")");
    } else {
      var parts = [];
      var b = sa.signedBiasCents.mean;
      if (b != null) {
        parts.push(Math.abs(b) < 5 ? "you sing on pitch on average"
          : "you average " + Math.abs(b) + " cents " + (b < 0 ? "flat" : "sharp"));
      }
      if (sa.leapMinusStepPct != null && sa.leapMinusStepPct < 0) {
        parts.push("leaps land " + Math.abs(sa.leapMinusStepPct) + " points worse than steps");
      }
      if (sa.recurringWorst.length) {
        var w = sa.recurringWorst[0];
        parts.push(w.name + " shows up in your worst notes in " + w.count + " takes");
      }
      if (parts.length) lines.push("Sing-Along: " + parts.join("; "));
    }

    // Ear Trainer
    var earTotal = ["interval", "chord", "scale", "key"].reduce(function (s, m) { return s + (report.ear[m].n || 0); }, 0);
    if (lc.ear) {
      if (earTotal > 0) lines.push("Ear: not enough answers yet (" + earTotal + " of " + MIN.ear + ")");
    } else {
      var present = ["interval", "chord", "scale", "key"].filter(function (m) { return report.ear[m].n > 0; });
      var weakest = present.slice().sort(function (a, b) { return report.ear[a].pct - report.ear[b].pct; })[0];
      var label = { interval: "intervals", chord: "chords", scale: "scales", key: "key" };
      var seg = present.map(function (m) {
        return label[m] + " " + report.ear[m].pct + "%" + (m === weakest && present.length > 1 ? " (weakest)" : "");
      });
      if (seg.length) lines.push("Ear: " + seg.join(", "));
    }

    return lines;
  }

  var PG = {
    buildProgressReport: buildProgressReport,
    describeProgress: describeProgress,
    midiName: midiName,
    WINDOW_DAYS: WINDOW_DAYS,
    MIN: MIN,
  };

  if (typeof window !== "undefined") window.PG = PG;
  if (typeof module !== "undefined" && module.exports) module.exports = PG;
})();
