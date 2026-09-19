"""Metamorphic correctness properties (EVALUATION UPGRADE §2).

These need NO reference melody and NO precise performer: they assert *relations* the
transcriber must obey between a take and a transformed version of the same take. That makes
them a de-saturated, ground-truth-free correctness signal - complementary to the synthetic
F1 gate, and they hold on real hums too (a transformation the ear cannot hear must not change
the notes).

Properties:
  * Transpose invariance - shift the pitch by k semitones -> every output note shifts by k,
    the count is unchanged.
  * Gain invariance - scale the amplitude -> the Score is identical.
  * Additive-noise robustness - add noise to a moderate SNR -> the notes are unchanged.
  * Silence-pad invariance - prepend/append silence -> the same notes at the same values.
  * Tempo / time-scale invariance - re-synthesise the same melody at BPM x s and pass BPM x s
    -> the note VALUES (start_ql / dur_ql) are unchanged.

The synthetic cases re-synthesise exactly (no DSP artefacts). The real-take cases use the
recorded .webm files: gain invariance is exact, and transpose invariance uses
librosa.effects.pitch_shift and is checked leniently (a single tracker wobble is tolerated),
because a real voice is not a pure tone.
"""

from __future__ import annotations

import glob
import os
import shutil
from collections import Counter

import numpy as np
import pytest

from mouthtranscriber.config import Params
from mouthtranscriber.pipeline import transcribe_array
from tests.make_synthetic import FIXTURES, build

# The synthetic properties use the deterministic DSP default (pyin): installed, no model
# download, reproducible. Keep the fixture set small - pyin is not fast.
_SYNTH_FIXTURE = "c_major_scale"


def _midis(notes):
    return [int(n.midi) for n in notes]


def _values(notes):
    return [(int(n.midi), round(float(n.start_ql), 4), round(float(n.dur_ql), 4)) for n in notes]


def _rel_onsets(notes):
    """Onsets relative to the first note (robust to how quantise anchors absolute time)."""
    if not notes:
        return []
    o0 = float(notes[0].start_ql)
    return [round(float(n.start_ql) - o0, 4) for n in notes]


# --- synthetic, exact re-synthesis ------------------------------------------------------

@pytest.mark.parametrize("k", [2, -3, 5])
def test_transpose_invariance(k):
    """Rendering k semitones higher shifts every output note by k, count unchanged."""
    y0, sr, _ = build(_SYNTH_FIXTURE)
    yk, _, _ = build(_SYNTH_FIXTURE, detune_semitones=float(k))
    base = _midis(transcribe_array(y0, Params(sr=sr)).score.notes)
    shifted = _midis(transcribe_array(yk, Params(sr=sr)).score.notes)
    assert len(shifted) == len(base), f"count changed under transpose: {len(base)} -> {len(shifted)}"
    assert shifted == [m + k for m in base], f"k={k}: {base} -> {shifted}"


@pytest.mark.parametrize("g", [0.25, 4.0])
def test_gain_invariance(g):
    """Scaling the amplitude leaves the Score identical (analysis is peak-relative)."""
    y, sr, _ = build(_SYNTH_FIXTURE)
    base = _values(transcribe_array(y, Params(sr=sr)).score.notes)
    scaled = _values(transcribe_array((y * g).astype(np.float32), Params(sr=sr)).score.notes)
    assert scaled == base, f"gain x{g} changed the Score"


def test_additive_noise_robustness():
    """Adding white noise down to a moderate SNR does not change the notes."""
    y, sr, _ = build(_SYNTH_FIXTURE, noise_db=None)  # clean tone, add our own noise
    base = _midis(transcribe_array(y, Params(sr=sr)).score.notes)
    rng = np.random.default_rng(1)
    sig_pow = float(np.mean(y ** 2)) + 1e-12
    snr_db = 30.0
    noise_amp = float(np.sqrt(sig_pow / (10 ** (snr_db / 10.0))))
    noisy = (y + rng.normal(0, noise_amp, len(y))).astype(np.float32)
    got = _midis(transcribe_array(noisy, Params(sr=sr)).score.notes)
    assert got == base, f"noise at {snr_db} dB SNR changed the notes: {base} -> {got}"


def test_silence_pad_invariance():
    """Prepending/appending silence keeps the same notes at the same relative values."""
    y, sr, _ = build(_SYNTH_FIXTURE)
    bpm = FIXTURES[_SYNTH_FIXTURE][0]
    base = transcribe_array(y, Params(sr=sr), tempo_bpm=bpm).score.notes
    pad = np.zeros(int(0.5 * sr), dtype=np.float32)
    padded_y = np.concatenate([pad, y, pad]).astype(np.float32)
    padded = transcribe_array(padded_y, Params(sr=sr), tempo_bpm=bpm).score.notes
    assert _midis(padded) == _midis(base)
    assert [v[2] for v in _values(padded)] == [v[2] for v in _values(base)]  # durations
    assert _rel_onsets(padded) == _rel_onsets(base)                          # relative onsets


@pytest.mark.parametrize("s", [0.75, 1.5])
def test_time_scale_invariance(s):
    """The same melody performed at BPM x s and read at BPM x s notates the same values."""
    for fixture in ("c_major_scale", "mixed_rhythm"):
        bpm = FIXTURES[fixture][0]
        y0, sr, _ = build(fixture, bpm=bpm)
        ys, _, _ = build(fixture, bpm=bpm * s)
        base = _values(transcribe_array(y0, Params(sr=sr), tempo_bpm=bpm).score.notes)
        scaled = _values(transcribe_array(ys, Params(sr=sr), tempo_bpm=bpm * s).score.notes)
        assert scaled == base, f"{fixture} at x{s}: values changed {base} -> {scaled}"


# --- real recordings (hold regardless of hum accuracy) ----------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))
_RECORDED = sorted(glob.glob(os.path.join(_HERE, "data", "recorded", "*.webm")))


def _decode_recorded(path):
    """Decode a recorded take with the same ffmpeg path the server/diagnosis use."""
    from tests.diagnose_recorded import decode
    return decode(path, 22050)


def _backend_for(webm):
    """The backend the take's JSON asks for, falling back to pyin if it isn't installed."""
    import json
    jpath = os.path.splitext(webm)[0] + ".json"
    backend = "pyin"
    if os.path.exists(jpath):
        with open(jpath, encoding="utf-8") as f:
            backend = json.load(f).get("backend", "pyin")
    try:
        __import__({"pesto": "pesto", "crepe": "torchcrepe", "fcnf0": "penn",
                    "basic_pitch": "basic_pitch"}.get(backend, "numpy"))
    except Exception:
        return "pyin"
    return backend


def _pick_take():
    """Prefer a scale-type take: distinct pitches segment stably, so both count and pitch
    hold under transformation. A repeated-note take's segmentation is deliberately fragile."""
    for want in ("scale", "twinkle"):
        for f in _RECORDED:
            if want in os.path.basename(f).lower():
                return f
    return _RECORDED[0] if _RECORDED else None


@pytest.mark.skipif(not _RECORDED, reason="no recorded .webm takes present")
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")
def test_real_take_gain_invariance():
    """Scaling a real take's amplitude leaves its transcription identical (any backend)."""
    webm = _pick_take()
    p = Params(backend=_backend_for(webm))
    y = _decode_recorded(webm)
    base = _values(transcribe_array(y, p).score.notes)
    scaled = _values(transcribe_array((y * 2.0).astype(np.float32), p).score.notes)
    assert scaled == base, f"{os.path.basename(webm)}: gain changed the transcription"


@pytest.mark.skipif(not _RECORDED, reason="no recorded .webm takes present")
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")
def test_real_take_transpose_invariance():
    """Pitch-shifting a real take by k semitones shifts its notes by k (majority vote).

    A real voice is not a pure tone and librosa's phase-vocoder shift adds artefacts, so we
    check the MODAL per-note shift over the aligned prefix, not bit-exactness: the property
    under test is that the pitch transposes by k. Count-stability under a DSP artefact is a
    separate (segmentation) concern, covered by the synthetic cases, so a count change is
    tolerated rather than asserted.
    """
    import librosa

    webm = _pick_take()
    p = Params(backend=_backend_for(webm))
    y = _decode_recorded(webm)
    k = 2
    y_up = librosa.effects.pitch_shift(y, sr=p.sr, n_steps=k).astype(np.float32)
    base = _midis(transcribe_array(y, p).score.notes)
    shifted = _midis(transcribe_array(y_up, p).score.notes)
    aligned = min(len(base), len(shifted))
    if aligned < 3:
        pytest.skip("take produced too few notes to judge a modal shift on this backend")
    diffs = [shifted[i] - base[i] for i in range(aligned)]
    modal, n = Counter(diffs).most_common(1)[0]
    assert modal == k, f"modal per-note shift {modal} != {k} (diffs {diffs})"
    assert n >= (aligned + 1) // 2, f"shift not a clear majority: {diffs}"
