"""Synthetic "da-da-da" humming generator (PLAN §6.1).

Lets us test and iterate on the whole pipeline *today*, before any real
recordings exist, and gives the eval harness (M2) exact ground truth. Each note
is a voice-like tone (fundamental + a few harmonics) with a quick attack, a
subtle vibrato, an optional onset scoop, and a short silent gap after it — the
consonant stop that "da" articulation creates.

Two fidelity levels:

  * The bare ``build(name)`` renders a CLEAN take: gentle 8-cent vibrato, exact
    metronome timing, full silent "d" closures. The regression gate in
    ``test_pipeline.py`` runs these and expects F1 = 1.0, so their behaviour must
    not drift — every realism knob below defaults to the clean values.
  * ``build(name, expr=REALISTIC)`` renders an EXPRESSIVE take that models what a
    real voice actually does and what the segmenter actually struggles with: wide
    vibrato that crosses semitone lines, amplitude tremolo, slow pitch drift,
    onsets that wander off the grid, and *partial* "d" closures that dip the
    amplitude without fully devoicing (so two repeats of the same pitch are only
    separated by an energy valley, not a silence). This is the take that makes the
    eval bite; see tests/eval_report.py.

Real recordings will be dropped into tests/data/recorded/ later; this module
covers the deterministic, CI-friendly cases.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np

from mouthtranscriber.model import midi_to_hz

SR = 22050
LEAD_IN_S = 0.25          # silence before the first note (onset lead-in)
GAP_S = 0.08              # silent gap after each note (the consonant stop)
HARMONICS = (1.0, 0.5, 0.28, 0.14)  # relative amplitudes of harmonics 1..4


@dataclass
class RefNote:
    start: float
    end: float
    midi: int


@dataclass
class Expr:
    """An expressive-performance profile: how far the take departs from a clean grid.

    Every field defaults to the clean-take value, so ``Expr()`` reproduces the
    gentle fixture the regression gate expects. ``REALISTIC`` (below) dials each
    one up to real-voice territory.
    """

    vibrato_cents: float = 8.0        # vibrato depth (peak), cents. Real voice ~40-80.
    vibrato_rate: float = 5.0         # vibrato rate, Hz
    vibrato_onset_s: float = 0.0      # delay before vibrato ramps in (real vibrato is late)
    tremolo_db: float = 0.0           # amplitude tremolo depth, dB (0 = none)
    tremolo_rate: float = 4.5         # tremolo rate, Hz
    drift_cents: float = 0.0          # slow pitch random-walk amplitude, cents (0 = none)
    timing_jitter_s: float = 0.0      # per-note onset jitter off the metronome grid
    gap_jitter_s: float = 0.0         # per-note variation in the consonant-gap length
    closure_db: float | None = None   # None = full silent "d"; else a voiced dip to this
                                      # dB (relative to the note) — a *partial* closure


# The take that exercises the segmenter's real failure modes.
REALISTIC = Expr(
    vibrato_cents=55.0,
    vibrato_rate=5.3,
    vibrato_onset_s=0.12,
    tremolo_db=6.0,
    drift_cents=18.0,
    timing_jitter_s=0.0,   # off-grid timing stresses quantize, not note P/R; keep clean here
    gap_jitter_s=0.012,
    closure_db=-16.0,
)


def _vibrato_drift(n: int, sr: int, expr: Expr, rng) -> np.ndarray:
    """Semitone offset over a note: (late-onset) vibrato plus a slow pitch drift."""
    t = np.arange(n) / sr
    semis = np.zeros(n)

    if expr.vibrato_cents > 0:
        depth = expr.vibrato_cents / 100.0
        ramp = np.ones(n)
        if expr.vibrato_onset_s > 0:  # vibrato fades in over ~120 ms after its onset
            on = min(n, int(expr.vibrato_onset_s * sr))
            grow = min(n - on, max(1, int(0.12 * sr)))
            ramp[:on] = 0.0
            if grow > 0:
                ramp[on:on + grow] = np.linspace(0, 1, grow)
        semis = semis + depth * ramp * np.sin(2 * np.pi * expr.vibrato_rate * t)

    if expr.drift_cents > 0:  # smoothed random walk, normalised to +-drift_cents
        walk = np.cumsum(rng.normal(0, 1, n))
        walk -= np.linspace(walk[0], walk[-1], n)  # remove net slope (stay near centre)
        span = np.max(np.abs(walk)) + 1e-9
        semis = semis + (expr.drift_cents / 100.0) * (walk / span)

    return semis


def _synth_note(
    midi: float,
    dur: float,
    sr: int,
    expr: Expr,
    scoop: bool,
    rng,
    attack_from: float = 0.0,
    release_to: float = 0.0,
    attack_s: float = 0.008,
    release_s: float = 0.02,
) -> np.ndarray:
    """One sounded note. The attack ramps the amplitude from ``attack_from`` up to 1 and
    the release ramps it back down to ``release_to``. A *partial* "d" closure is modelled
    by leaving those levels ABOVE zero (``lvl`` = ``10**(closure_db/20)``): the voice never
    devoices, so voicing can't split here and the energy-valley splitter must — but the
    amplitude still dips to a clean V-valley right at the note boundary, so the onset cue
    lands on the true onset time (no lag). A full silent "d" uses levels of 0.
    """
    n = int(round(dur * sr))
    if n <= 0:
        return np.zeros(0, dtype=np.float32)
    t = np.arange(n) / sr
    base = midi_to_hz(midi)

    semis = _vibrato_drift(n, sr, expr, rng)
    if scoop:  # a quick rise into the target pitch at the onset
        rise = max(1, int(0.03 * sr))
        semis[:rise] = semis[:rise] + np.linspace(-0.5, 0.0, min(rise, n))

    freq = base * (2.0 ** (semis / 12.0))
    phase = 2 * np.pi * np.cumsum(freq) / sr

    wave = np.zeros(n)
    for h, amp in enumerate(HARMONICS, start=1):
        wave += amp * np.sin(h * phase)
    wave /= sum(HARMONICS)

    # amplitude envelope: attack from `attack_from`, gentle decay, release to `release_to`.
    env = np.ones(n)
    a = min(n, max(1, int(attack_s * sr)))
    env[:a] = np.linspace(attack_from, 1.0, a)
    r = min(n, max(1, int(release_s * sr)))
    env[-r:] = np.minimum(env[-r:], np.linspace(1.0, release_to, r))
    env = env * np.linspace(1.0, 0.85, n)  # slight decay
    if expr.tremolo_db > 0:
        trem = 10 ** ((-expr.tremolo_db * (0.5 - 0.5 * np.cos(2 * np.pi * expr.tremolo_rate * t))) / 20)
        env = env * trem

    return (wave * env).astype(np.float32)


def _place(buf: np.ndarray, chunk: np.ndarray, start_s: float, sr: int) -> None:
    i = int(round(start_s * sr))
    j = min(len(buf), i + len(chunk))
    if i < len(buf) and j > i:
        buf[i:j] += chunk[: j - i]


def build(
    name: str,
    sr: int = SR,
    detune_semitones: float = 0.0,
    vibrato: bool = False,
    scoop: bool = False,
    noise_db: float = -48.0,
    seed: int = 0,
    expr: Expr | None = None,
) -> tuple[np.ndarray, int, list[RefNote]]:
    """Render a named fixture. Returns (audio, sr, reference_notes).

    ``detune_semitones`` shifts the *audio* pitch but not the reference — used to
    test tuning correction (the "hums flat" case). ``expr`` selects an expressive
    profile; when None, the legacy clean take is rendered (``vibrato`` adds the old
    8-cent wobble). Reference onsets always reflect the *actual* rendered timing.
    """
    bpm, sequence = FIXTURES[name]
    beat_s = 60.0 / bpm
    rng = np.random.default_rng(seed)

    if expr is None:  # legacy clean take: gentle vibrato via the bool, exact grid
        expr = Expr(vibrato_cents=8.0 if vibrato else 0.0)

    # Nominal (on-grid) onset of every event.
    onsets: list[float] = []
    clk = LEAD_IN_S
    for _midi, beats in sequence:
        onsets.append(clk)
        clk += beats * beat_s
    total = clk + 0.30  # trailing silence
    y = np.zeros(int(round(total * sr)), dtype=np.float32)

    lvl = 10 ** (expr.closure_db / 20.0) if expr.closure_db is not None else 0.0

    refs: list[RefNote] = []
    prev_closure = False  # did the previous note dip into a *voiced* closure at this onset?
    for i, (midi, beats) in enumerate(sequence):
        if midi is None:  # explicit rest
            prev_closure = False
            continue
        slot = beats * beat_s
        gap = GAP_S + (rng.uniform(-1, 1) * expr.gap_jitter_s if expr.gap_jitter_s else 0.0)
        gap = float(np.clip(gap, 0.03, slot * 0.6))
        jit = rng.uniform(-1, 1) * expr.timing_jitter_s if expr.timing_jitter_s else 0.0
        start = max(LEAD_IN_S, onsets[i] + jit)

        nxt = sequence[i + 1][0] if i + 1 < len(sequence) else None
        voiced_closure = expr.closure_db is not None and nxt is not None
        m = midi + detune_semitones

        # A voiced "d" closure dips to `lvl` (never silence) right at the note boundary and
        # fills the whole slot, so the valley lands on the true onset time. A full silent
        # "d" leaves a real gap (levels of 0). The attack rises FROM the closure the prior
        # note left (lvl if it dipped, else 0), giving a clean energy onset either way.
        length = slot if voiced_closure else max(0.05, slot - gap)
        tone = _synth_note(
            m, length, sr, expr, scoop, rng,
            attack_from=lvl if prev_closure else 0.0,
            release_to=lvl if voiced_closure else 0.0,
            attack_s=max(0.02, gap * 0.5) if prev_closure else 0.008,
            release_s=max(0.02, gap * 0.5) if voiced_closure else 0.02,
        )
        _place(y, tone, start, sr)
        refs.append(RefNote(start=start, end=start + max(0.05, slot - gap), midi=int(midi)))
        prev_closure = voiced_closure

    if noise_db is not None:
        amp = 10 ** (noise_db / 20.0)
        y = y + rng.normal(0, amp, len(y)).astype(np.float32)
    peak = float(np.max(np.abs(y))) + 1e-9
    y = (y / peak * 0.9).astype(np.float32)
    return y, sr, refs


# --- fixtures: (bpm, [(midi_or_None, beats), ...]) --------------------------
# midi None = rest.  C4 = 60.
_C = 60
FIXTURES: dict[str, tuple[float, list[tuple[int | None, float]]]] = {
    # ascending C major scale, quarter notes
    "c_major_scale": (100, [(m, 1) for m in [60, 62, 64, 65, 67, 69, 71, 72]]),
    # descending A minor scale
    "a_minor_scale": (100, [(m, 1) for m in [69, 67, 65, 64, 62, 60, 59, 57]]),
    # C major arpeggio up and down
    "arpeggio": (110, [(m, 1) for m in [60, 64, 67, 72, 67, 64, 60]]),
    # five repeats of the SAME pitch (stresses onset-based splitting)
    "repeated_notes": (120, [(60, 1) for _ in range(5)]),
    # phrase, rest, phrase (stresses silence handling)
    "with_silence": (100, [(60, 1), (62, 1), (None, 1), (64, 1), (65, 1), (None, 1), (67, 2)]),
    # octave leaps (stresses octave-error robustness)
    "octave_leaps": (100, [(60, 1), (72, 1), (60, 1), (72, 1), (60, 2)]),
    # Twinkle Twinkle Little Star (first phrase)
    "twinkle": (
        110,
        [(60, 1), (60, 1), (67, 1), (67, 1), (69, 1), (69, 1), (67, 2),
         (65, 1), (65, 1), (64, 1), (64, 1), (62, 1), (62, 1), (60, 2)],
    ),
    # mixed durations (half, quarter, eighths)
    "mixed_rhythm": (100, [(60, 2), (62, 1), (64, 0.5), (65, 0.5), (67, 2)]),
}


def intended_grid(name: str) -> tuple[list[float], list[float]]:
    """The intended (start_ql, dur_ql) of each SOUNDED note, anchored so the first is 0.

    Ground truth for rhythm scoring (tests/eval_report.py, evaluate.rhythm_scores). A
    fixture entry ``(midi, beats)`` is one grid slot: its notated length is the FULL
    slot (``beats`` quarter-notes) - the "da" gap that clips the sounded tail is
    articulation, not a rest, and the quantizer folds it back - so the quantizer should
    recover exactly these positions from a jittered performance. Rests advance the clock
    without emitting a note.
    """
    _bpm, seq = FIXTURES[name]
    starts: list[float] = []
    durs: list[float] = []
    clk = 0.0
    first: float | None = None
    for midi, beats in seq:
        if midi is not None:
            if first is None:
                first = clk
            starts.append(clk - first)
            durs.append(float(beats))
        clk += beats
    return starts, durs


def write_all(outdir: str) -> list[str]:
    """Render every fixture (plus a flat/vibrato variant) to WAV + reference MIDI."""
    import pretty_midi
    import soundfile as sf

    os.makedirs(outdir, exist_ok=True)
    written = []

    variants = [(name, {}) for name in FIXTURES]
    variants.append(("c_major_scale_flat", {"name": "c_major_scale", "detune_semitones": -0.4}))
    variants.append(("twinkle_expressive", {"name": "twinkle", "vibrato": True, "scoop": True}))
    # realistic (expressive) renders of a few melodies, for ear-checking the hard cases
    variants.append(("twinkle_realistic", {"name": "twinkle", "expr": REALISTIC}))
    variants.append(("repeated_notes_realistic", {"name": "repeated_notes", "expr": REALISTIC}))

    for label, opts in variants:
        base = opts.pop("name", label)
        y, sr, refs = build(base, **opts)

        wav_path = os.path.join(outdir, f"{label}.wav")
        sf.write(wav_path, y, sr)
        written.append(wav_path)

        pm = pretty_midi.PrettyMIDI()
        inst = pretty_midi.Instrument(program=54)
        for rn in refs:
            inst.notes.append(
                pretty_midi.Note(velocity=90, pitch=rn.midi, start=rn.start, end=rn.end)
            )
        pm.instruments.append(inst)
        pm.write(os.path.join(outdir, f"{label}.mid"))

    return written


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "data", "generated")
    files = write_all(out)
    print(f"wrote {len(files)} fixtures to {out}")
