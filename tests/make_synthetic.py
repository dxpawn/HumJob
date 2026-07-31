"""Synthetic "da-da-da" humming generator (PLAN §6.1).

Lets us test and iterate on the whole pipeline *today*, before any real
recordings exist, and gives the eval harness (M2) exact ground truth. Each note
is a voice-like tone (fundamental + a few harmonics) with a quick attack, a
subtle vibrato, an optional onset scoop, and a short silent gap after it — the
consonant stop that "da" articulation creates.

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


def _synth_note(midi: float, dur: float, sr: int, vibrato: bool, scoop: bool) -> np.ndarray:
    n = int(round(dur * sr))
    t = np.arange(n) / sr
    base = midi_to_hz(midi)

    # pitch contour: optional onset scoop rising to the target
    semis = np.zeros(n)
    if scoop:
        rise = max(1, int(0.03 * sr))
        semis[:rise] = np.linspace(-0.5, 0.0, rise)
    if vibrato:
        semis = semis + 0.08 * np.sin(2 * np.pi * 5.0 * t)  # ~±8 cents at 5 Hz
    freq = base * (2.0 ** (semis / 12.0))
    phase = 2 * np.pi * np.cumsum(freq) / sr

    wave = np.zeros(n)
    for h, amp in enumerate(HARMONICS, start=1):
        wave += amp * np.sin(h * phase)
    wave /= sum(HARMONICS)

    # amplitude envelope: fast attack, gentle decay, short release
    env = np.ones(n)
    a = max(1, int(0.008 * sr))
    r = max(1, int(0.02 * sr))
    env[:a] = np.linspace(0, 1, a)
    env[-r:] = np.linspace(1, 0, r)
    env *= np.linspace(1.0, 0.85, n)  # slight decay
    return (wave * env).astype(np.float32)


def build(
    name: str,
    sr: int = SR,
    detune_semitones: float = 0.0,
    vibrato: bool = False,
    scoop: bool = False,
    noise_db: float = -48.0,
    seed: int = 0,
) -> tuple[np.ndarray, int, list[RefNote]]:
    """Render a named fixture. Returns (audio, sr, reference_notes).

    ``detune_semitones`` shifts the *audio* pitch but not the reference — used to
    test tuning correction (the "hums flat" case).
    """
    bpm, sequence = FIXTURES[name]
    beat_s = 60.0 / bpm

    rng = np.random.default_rng(seed)
    chunks = [np.zeros(int(LEAD_IN_S * sr), dtype=np.float32)]
    refs: list[RefNote] = []
    clock = LEAD_IN_S

    for midi, beats in sequence:
        slot = beats * beat_s
        sound = max(0.05, slot - GAP_S)
        if midi is None:  # an explicit rest
            chunks.append(np.zeros(int(slot * sr), dtype=np.float32))
            clock += slot
            continue
        tone = _synth_note(midi + detune_semitones, sound, sr, vibrato, scoop)
        gap = np.zeros(int(GAP_S * sr), dtype=np.float32)
        chunks.append(tone)
        chunks.append(gap)
        refs.append(RefNote(start=clock, end=clock + sound, midi=int(midi)))
        clock += len(tone) / sr + len(gap) / sr

    y = np.concatenate(chunks)
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


def write_all(outdir: str) -> list[str]:
    """Render every fixture (plus a flat/vibrato variant) to WAV + reference MIDI."""
    import pretty_midi
    import soundfile as sf

    os.makedirs(outdir, exist_ok=True)
    written = []

    variants = [(name, {}) for name in FIXTURES]
    variants.append(("c_major_scale_flat", {"name": "c_major_scale", "detune_semitones": -0.4}))
    variants.append(("twinkle_expressive", {"name": "twinkle", "vibrato": True, "scoop": True}))

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
