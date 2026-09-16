"""Per-measure diatonic chord suggestion (PLAN §5.9).

The melody is one voice; here we propose a triad under each measure. Two ideas,
straight from the plan:

  1. **Coverage** — score each diatonic triad by how much of the measure's melody
     it "explains", weighting strong-beat and long notes higher so a short off-beat
     passing tone can't force a weird chord.
  2. **Progression prior** — the raw best-fit chord per measure is often choppy, so
     we smooth the sequence with a Viterbi pass over a root-motion prior (circle-of-
     fifths motion like V→I is cheap; retrogressions are dear), with a gentle
     tonic bias at the start and a cadential tonic bias at the end.

v1 is triads only (no 7ths / borrowed chords) — flagged as a later enhancement in
the plan. Diatonic set = the seven scale-degree triads; minor keys also get the
harmonic-minor **V** (major dominant) and **vii°** so authentic cadences are
available.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import Params
from .model import Chord, NoteEvent

# Single source of truth for chord qualities. The diatonic suggester (`suggest`) still
# uses only the three triads, but the exporter, the reharmonizer, and the browser copy
# (manual.js MT.CHORD_QUALITIES, kept in sync by the golden drift-guard) all read this
# table, so a seventh chord spells, engraves, plays, and transposes everywhere.
#   intervals: pitch classes relative to the root
#   kind:      the MusicXML / music21 ChordSymbol kind (verified to construct, 2026-09-16)
#   suffix:    pretty display suffix appended to the root (e.g. "Gm7")
#   roman:     suffix appended to a Roman numeral (used by the reharmonizer)
QUALITIES: dict[str, dict] = {
    "maj":    {"intervals": (0, 4, 7),      "kind": "major",              "suffix": "",         "roman": ""},
    "min":    {"intervals": (0, 3, 7),      "kind": "minor",              "suffix": "m",        "roman": ""},
    "dim":    {"intervals": (0, 3, 6),      "kind": "diminished",         "suffix": "dim",      "roman": "°"},
    "aug":    {"intervals": (0, 4, 8),      "kind": "augmented",          "suffix": "aug",      "roman": "+"},
    "maj7":   {"intervals": (0, 4, 7, 11),  "kind": "major-seventh",      "suffix": "maj7",     "roman": "maj7"},
    "min7":   {"intervals": (0, 3, 7, 10),  "kind": "minor-seventh",      "suffix": "m7",       "roman": "7"},
    "dom7":   {"intervals": (0, 4, 7, 10),  "kind": "dominant",           "suffix": "7",        "roman": "7"},
    "min7b5": {"intervals": (0, 3, 6, 10),  "kind": "half-diminished",    "suffix": "m7♭5", "roman": "ø7"},
    "dim7":   {"intervals": (0, 3, 6, 9),   "kind": "diminished-seventh", "suffix": "dim7",     "roman": "°7"},
    "sus4":   {"intervals": (0, 5, 7),      "kind": "suspended-fourth",   "suffix": "sus4",     "roman": "sus4"},
    "sus2":   {"intervals": (0, 2, 7),      "kind": "suspended-second",   "suffix": "sus2",     "roman": "sus2"},
}

# Derived views, so the rest of the module (and export.py, which imports _KIND) keep their
# existing names. The three triads sort first, preserving prior behaviour and tests.
_INTERVALS = {q: v["intervals"] for q, v in QUALITIES.items()}
_SUFFIX = {q: v["suffix"] for q, v in QUALITIES.items()}
_KIND = {q: v["kind"] for q, v in QUALITIES.items()}

# Diatonic triad qualities + Roman numerals per scale degree (1..7).
_MAJOR_QUAL = ["maj", "min", "min", "maj", "maj", "min", "dim"]
_MAJOR_ROMAN = ["I", "ii", "iii", "IV", "V", "vi", "vii°"]
_MINOR_QUAL = ["min", "dim", "maj", "min", "min", "maj", "maj"]
_MINOR_ROMAN = ["i", "ii°", "III", "iv", "v", "VI", "VII"]

# Preference for a root motion of ``d`` semitones (to_root - from_root, mod 12).
# Strong functional moves (down a 5th / 3rd, up a 2nd) are cheap; retrogressions
# and tritone jumps are dear. Repeating the same chord is allowed but not favored.
_ROOT_MOTION = {
    0: 0.30,   # same chord
    5: 1.00,   # down a perfect 5th (= up a 4th): V->I, ii->V, the strongest move
    2: 0.70,   # up a 2nd: IV->V, ii->iii
    8: 0.75,   # down a major 3rd: I->vi(♭)/deceptive-ish
    9: 0.75,   # down a minor 3rd: I->vi, vi->IV
    7: 0.55,   # up a 5th: I->V
    3: 0.40,   # up a minor 3rd
    4: 0.40,   # up a major 3rd
    10: 0.45,  # down a 2nd (whole)
    11: 0.45,  # down a 2nd (half)
    1: 0.30,   # up a half step
    6: 0.30,   # tritone
}

_EMIT_W = 3.0       # weight on melody coverage vs. the progression prior
_START_TONIC = 0.25  # gentle "start on tonic harmony" bias
_END_TONIC = 0.45    # cadential "end on tonic" bias


@dataclass(frozen=True)
class _Template:
    """A key-level chord option (before it's assigned to a measure)."""
    root_pc: int
    root_name: str
    quality: str
    roman: str
    pcs: frozenset[int]
    is_tonic: bool


def _pretty(root_name: str, quality: str) -> str:
    root = root_name.replace("-", "♭").replace("#", "♯")
    return root + _SUFFIX[quality]


def _templates(key: str) -> list[_Template]:
    """Diatonic triad options for ``key`` (e.g. 'F minor'), correctly spelled."""
    from music21 import key as m21key

    tonic, mode = key.split()
    mode = mode.lower()
    k = m21key.Key(tonic, mode)
    quals = _MAJOR_QUAL if mode == "major" else _MINOR_QUAL
    romans = _MAJOR_ROMAN if mode == "major" else _MINOR_ROMAN

    out: list[_Template] = []

    def add(root_pitch, quality: str, roman: str, is_tonic: bool) -> None:
        pc = root_pitch.pitchClass
        pcs = frozenset((pc + iv) % 12 for iv in _INTERVALS[quality])
        out.append(_Template(pc, root_pitch.name, quality, roman, pcs, is_tonic))

    for deg in range(1, 8):
        add(k.pitchFromDegree(deg), quals[deg - 1], romans[deg - 1], deg == 1)

    if mode == "minor":
        # Harmonic-minor dominant + leading-tone diminished, for real cadences.
        add(k.pitchFromDegree(5), "maj", "V", False)
        leading = k.pitchFromDegree(7).transpose("A1")  # raise the subtonic
        add(leading, "dim", "vii°", False)

    return out


def _bar_ql(time_sig: tuple[int, int]) -> float:
    """Length of one measure in quarter-note units."""
    beats, unit = time_sig
    return beats * (4.0 / unit)


def _beat_strength(pos_ql: float, bar_ql: float) -> float:
    """Metrical weight of an onset at ``pos_ql`` into its measure."""
    if pos_ql < 1e-6:
        return 2.0                                   # downbeat
    if abs(pos_ql - bar_ql / 2.0) < 1e-6:
        return 1.5                                   # mid-bar (e.g. beat 3 of 4/4)
    if abs(pos_ql - round(pos_ql)) < 1e-6:
        return 1.0                                   # on a beat
    return 0.4                                        # off-beat subdivision


def _pc_weights(
    notes: list[NoteEvent], n_measures: int, bar_ql: float
) -> tuple[list[dict[int, float]], list[float]]:
    """Per-measure {pitch-class -> weight} and total weight (beat-strength x duration)."""
    pc_weight: list[dict[int, float]] = [{} for _ in range(n_measures)]
    total = [0.0] * n_measures
    for n in notes:
        if math.isnan(n.start_ql) or math.isnan(n.dur_ql):
            continue
        m = int((n.start_ql + 1e-6) // bar_ql)
        if m < 0 or m >= n_measures:
            continue
        pos = n.start_ql - m * bar_ql
        w = _beat_strength(pos, bar_ql) * max(n.dur_ql, 1e-3)
        pc = int(n.midi) % 12
        pc_weight[m][pc] = pc_weight[m].get(pc, 0.0) + w
        total[m] += w
    return pc_weight, total


def _emissions(
    notes: list[NoteEvent],
    templates: list[_Template],
    n_measures: int,
    bar_ql: float,
) -> list[list[float]]:
    """Coverage fraction of each template in each measure (rows: measures)."""
    pc_weight, total = _pc_weights(notes, n_measures, bar_ql)
    emis: list[list[float]] = []
    for m in range(n_measures):
        row = []
        for t in templates:
            if total[m] <= 0:
                row.append(0.0)
            else:
                covered = sum(w for pc, w in pc_weight[m].items() if pc in t.pcs)
                row.append(covered / total[m])
        emis.append(row)
    return emis


def _viterbi(
    emis: list[list[float]], templates: list[_Template]
) -> list[int]:
    """Best chord index per measure: max Σ emission·w + Σ root-motion pref."""
    n_measures = len(emis)
    n_states = len(templates)

    def start_score(s: int) -> float:
        return _EMIT_W * emis[0][s] + (_START_TONIC if templates[s].is_tonic else 0.0)

    dp = [start_score(s) for s in range(n_states)]
    back = [[0] * n_states for _ in range(n_measures)]

    for m in range(1, n_measures):
        prev = dp
        dp = [0.0] * n_states
        last = m == n_measures - 1
        for s in range(n_states):
            node = _EMIT_W * emis[m][s]
            if last and templates[s].is_tonic:
                node += _END_TONIC
            best_p, best_v = 0, -1e18
            for p in range(n_states):
                d = (templates[s].root_pc - templates[p].root_pc) % 12
                v = prev[p] + _ROOT_MOTION.get(d, 0.3)
                if v > best_v:
                    best_v, best_p = v, p
            dp[s] = node + best_v
            back[m][s] = best_p

    # Backtrack from the best final state.
    s = max(range(n_states), key=lambda i: dp[i])
    path = [s]
    for m in range(n_measures - 1, 0, -1):
        s = back[m][s]
        path.append(s)
    path.reverse()
    return path


def suggest(
    notes: list[NoteEvent],
    key: str | None,
    time_sig: tuple[int, int],
    params: Params | None = None,
) -> list[Chord]:
    """One diatonic chord per measure spanned by the (quantized) melody.

    Returns ``[]`` when there is no key or no quantized notes to harmonize.
    """
    if not key or not notes:
        return []
    quantized = [n for n in notes if not math.isnan(n.start_ql)]
    if not quantized:
        return []

    bar_ql = _bar_ql(time_sig)
    extent = max(n.start_ql + max(n.dur_ql, 0.0) for n in quantized)
    n_measures = max(1, int(math.ceil(extent / bar_ql - 1e-6)))

    try:
        templates = _templates(key)
    except Exception:
        return []
    if not templates:
        return []

    emis = _emissions(quantized, templates, n_measures, bar_ql)
    path = _viterbi(emis, templates)

    chords: list[Chord] = []
    for m, s in enumerate(path):
        t = templates[s]
        chords.append(
            Chord(
                measure=m,
                start_ql=m * bar_ql,
                root_pc=t.root_pc,
                root_name=t.root_name,
                quality=t.quality,
                symbol=_pretty(t.root_name, t.quality),
                roman=t.roman,
            )
        )
    return chords


# ---------------------------------------------------------------------------
# Deterministic alternatives (B2): richer chord options per measure, ranked by
# how much of that measure's melody they cover. No LLM. `reharm.py` reuses the
# profiles and the same coverage maths to score the model's proposals.
# ---------------------------------------------------------------------------

_SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_NUMERALS = ["I", "II", "III", "IV", "V", "VI", "VII"]

# Diatonic seventh-chord quality per scale degree (1..7).
_MAJOR_SEVENTH_QUAL = ["maj7", "min7", "min7", "maj7", "dom7", "min7", "min7b5"]
_MINOR_SEVENTH_QUAL = ["min7", "min7b5", "maj7", "min7", "min7", "maj7", "dom7"]


def _roman(degree: int, quality: str) -> str:
    """Roman numeral for a chord on `degree` with `quality`: numeral case from the third,
    plus the quality's roman suffix (from QUALITIES). Reproduces the diatonic triad romans."""
    num = _NUMERALS[degree - 1]
    if 4 not in QUALITIES[quality]["intervals"]:   # no major third -> lowercase numeral
        num = num.lower()
    return num + QUALITIES[quality]["roman"]


def _template_from_pitch(root_pitch, quality: str, roman: str, is_tonic: bool) -> _Template:
    pc = root_pitch.pitchClass
    pcs = frozenset((pc + iv) % 12 for iv in QUALITIES[quality]["intervals"])
    return _Template(pc, root_pitch.name, quality, roman, pcs, is_tonic)


def _seventh_templates(key: str) -> list[_Template]:
    """Diatonic seventh chords for `key`, plus the harmonic-minor V7 and vii°7 in minor."""
    from music21 import key as m21key

    tonic, mode = key.split()
    mode = mode.lower()
    k = m21key.Key(tonic, mode)
    quals = _MAJOR_SEVENTH_QUAL if mode == "major" else _MINOR_SEVENTH_QUAL
    out = [
        _template_from_pitch(k.pitchFromDegree(deg), quals[deg - 1], _roman(deg, quals[deg - 1]), deg == 1)
        for deg in range(1, 8)
    ]
    if mode == "minor":
        out.append(_template_from_pitch(k.pitchFromDegree(5), "dom7", "V7", False))
        leading = k.pitchFromDegree(7).transpose("A1")
        out.append(_template_from_pitch(leading, "dim7", "vii°7", False))
    return out


def _secondary_dominants(key: str) -> list[_Template]:
    """V7 of each usable diatonic degree (a dom7 a perfect fifth above the target root)."""
    from music21 import key as m21key

    tonic, mode = key.split()
    mode = mode.lower()
    k = m21key.Key(tonic, mode)
    base = _MAJOR_ROMAN if mode == "major" else _MINOR_ROMAN
    targets = [2, 3, 4, 5, 6] if mode == "major" else [3, 4, 5, 6, 7]  # skip tonic + diminished degree
    out = []
    for deg in targets:
        dom_root = k.pitchFromDegree(deg).transpose("P5")
        out.append(_template_from_pitch(dom_root, "dom7", "V7/" + base[deg - 1], False))
    return out


def _style_templates(key: str, style: str) -> list[_Template]:
    """Chord options for a style set: triads, +diatonic sevenths, or +secondary dominants."""
    templates = _templates(key)
    if style in ("sevenths", "extended"):
        templates = templates + _seventh_templates(key)
    if style == "extended":
        templates = templates + _secondary_dominants(key)
    # De-duplicate on (root_pc, quality), keeping the first (triads precede sevenths).
    seen: set[tuple[int, str]] = set()
    out: list[_Template] = []
    for t in templates:
        kid = (t.root_pc, t.quality)
        if kid in seen:
            continue
        seen.add(kid)
        out.append(t)
    return out


def _measure_span(notes: list[NoteEvent], bar_ql: float) -> int:
    extent = max(n.start_ql + max(n.dur_ql, 0.0) for n in notes)
    return max(1, int(math.ceil(extent / bar_ql - 1e-6)))


def measure_profiles(
    notes: list[NoteEvent], time_sig: tuple[int, int]
) -> list[dict]:
    """Per-measure weighted pitch-class content (note name -> normalized weight).

    The same beat-strength x duration accumulation `_emissions` uses, spelled with sharps and
    rounded, so it is compact and PII-free for the reharmonizer's prompt and fit scoring.
    """
    quantized = [n for n in notes if not math.isnan(n.start_ql)]
    if not quantized:
        return []
    bar_ql = _bar_ql(time_sig)
    n_measures = _measure_span(quantized, bar_ql)
    pc_weight, total = _pc_weights(quantized, n_measures, bar_ql)
    out = []
    for m in range(n_measures):
        if total[m] <= 0:
            weights = {}
        else:
            weights = {
                _SHARP_NAMES[pc]: round(w / total[m], 3)
                for pc, w in sorted(pc_weight[m].items(), key=lambda kv: -kv[1])
            }
        out.append({"measure": m, "start_ql": m * bar_ql, "weights": weights})
    return out


def _option(t: _Template, m: int, bar_ql: float, fit: float) -> dict:
    """One ranked alternative, as a JSON-ready dict (Chord fields + coverage fit)."""
    return {
        "measure": m,
        "start_ql": m * bar_ql,
        "root_pc": t.root_pc,
        "root_name": t.root_name,
        "quality": t.quality,
        "symbol": _pretty(t.root_name, t.quality),
        "roman": t.roman,
        "fit": round(max(0.0, fit), 3),
    }


def alternatives(
    notes: list[NoteEvent],
    key: str | None,
    time_sig: tuple[int, int],
    style: str = "sevenths",
    params: Params | None = None,
) -> list[dict]:
    """Top three chord options per measure within a style set, ranked by melody coverage.

    Coverage is the `_emissions` fraction; a flat `chord_complexity_penalty` per chord tone
    beyond a triad is subtracted so a triad wins a tie and a seventh is chosen only when it
    covers genuinely more of the melody. Returns [] with no key or no quantized notes.
    """
    params = params or Params()
    penalty = params.chord_complexity_penalty
    quantized = [n for n in notes if not math.isnan(n.start_ql)]
    if not key or not quantized:
        return []
    bar_ql = _bar_ql(time_sig)
    n_measures = _measure_span(quantized, bar_ql)
    try:
        templates = _style_templates(key, style)
    except Exception:
        return []
    if not templates:
        return []

    emis = _emissions(quantized, templates, n_measures, bar_ql)
    out = []
    for m in range(n_measures):
        scored = []
        for si, t in enumerate(templates):
            fit = emis[m][si] - penalty * max(0, len(t.pcs) - 3)
            # Rank by fit desc; break ties toward fewer chord tones (a triad).
            scored.append((-fit, len(t.pcs), si, t, fit))
        scored.sort(key=lambda x: (x[0], x[1]))
        options = [_option(t, m, bar_ql, fit) for _, _, _, t, fit in scored[:3]]
        out.append({"measure": m, "start_ql": m * bar_ql, "options": options})
    return out


def coverage_fit(
    notes: list[NoteEvent], time_sig: tuple[int, int], chord_list: list[Chord]
) -> list[float]:
    """Coverage fraction of each chord over its own measure's melody (the `_emissions` maths).

    Used by the reharmonizer to score a proposed progression bar by bar, so the UI can flag a
    chord that clashes with the melody. A chord whose measure has no notes gets 0.0.
    """
    quantized = [n for n in notes if not math.isnan(n.start_ql)]
    if not quantized:
        return [0.0] * len(chord_list)
    bar_ql = _bar_ql(time_sig)
    n_measures = _measure_span(quantized, bar_ql)
    pc_weight, total = _pc_weights(quantized, n_measures, bar_ql)
    out = []
    for c in chord_list:
        m = c.measure
        if m < 0 or m >= n_measures or total[m] <= 0:
            out.append(0.0)
            continue
        pcs = frozenset((c.root_pc + iv) % 12 for iv in QUALITIES[c.quality]["intervals"])
        covered = sum(w for pc, w in pc_weight[m].items() if pc in pcs)
        out.append(round(covered / total[m], 3))
    return out
