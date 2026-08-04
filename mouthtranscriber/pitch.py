"""Fundamental-frequency (f0) estimation — the heart of the pipeline (PLAN §5.3).

The tracker is hidden behind a small ``PitchTracker`` interface so pYIN and CREPE
are interchangeable (a config flag, not a rewrite). pYIN is the default: no heavy
ML dependency, it returns a per-frame voiced probability, and it runs anywhere.
The f0 search is constrained to a human-hum range to suppress octave errors.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np

from .config import Params
from .model import Frame


class PitchTracker(Protocol):
    def track(self, y: np.ndarray, sr: int) -> list[Frame]: ...


class PyinTracker:
    """pYIN via librosa. Returns f0 + voiced probability + RMS per frame."""

    def __init__(self, params: Params):
        self.p = params

    def track(self, y: np.ndarray, sr: int) -> list[Frame]:
        import librosa

        p = self.p
        f0, _voiced_flag, voiced_prob = librosa.pyin(
            y,
            fmin=p.fmin,
            fmax=p.fmax,
            sr=sr,
            frame_length=p.frame_length,
            hop_length=p.hop_length,
            fill_na=np.nan,
        )
        times = librosa.times_like(f0, sr=sr, hop_length=p.hop_length)
        rms = librosa.feature.rms(
            y=y, frame_length=p.frame_length, hop_length=p.hop_length
        )[0]

        n = min(len(f0), len(times), len(rms), len(voiced_prob))
        frames: list[Frame] = []
        for i in range(n):
            val = f0[i]
            frames.append(
                Frame(
                    t=float(times[i]),
                    f0=float(val) if not np.isnan(val) else float("nan"),
                    confidence=float(voiced_prob[i]) if not np.isnan(voiced_prob[i]) else 0.0,
                    rms=float(rms[i]),
                )
            )
        return frames


class CrepeTracker:
    """Optional CREPE backend via torchcrepe (PyTorch, CPU-friendly).

    Not installed by default. torchcrepe gives no voiced probability of its own,
    so we reuse its periodicity as confidence and compute RMS separately. The model
    size ("full" vs "tiny") comes from ``Params.crepe_model``; "full" is the accurate
    default. Decoding is torchcrepe's default Viterbi, which smooths the contour.
    """

    def __init__(self, params: Params, model: str | None = None):
        self.p = params
        self.model = model or params.crepe_model

    def track(self, y: np.ndarray, sr: int) -> list[Frame]:
        import librosa
        import torch
        import torchcrepe

        p = self.p
        audio = torch.tensor(y, dtype=torch.float32).unsqueeze(0)
        # torchcrepe wants 16 kHz; resample just for the tracker.
        if sr != 16000:
            audio = torch.tensor(
                librosa.resample(y, orig_sr=sr, target_sr=16000), dtype=torch.float32
            ).unsqueeze(0)
        hop_16k = int(round(p.hop_s * 16000))
        f0, periodicity = torchcrepe.predict(
            audio, 16000, hop_length=hop_16k, fmin=p.fmin, fmax=p.fmax,
            model=self.model, decoder=torchcrepe.decode.viterbi,
            return_periodicity=True, batch_size=512,
        )
        f0 = f0.squeeze(0).cpu().numpy()
        periodicity = periodicity.squeeze(0).cpu().numpy()

        rms = librosa.feature.rms(
            y=y, frame_length=p.frame_length, hop_length=p.hop_length
        )[0]
        n = min(len(f0), len(rms))
        frames: list[Frame] = []
        for i in range(n):
            conf = float(periodicity[i])
            frames.append(
                Frame(
                    t=i * p.hop_s,
                    f0=float(f0[i]) if conf > 0.1 else float("nan"),
                    confidence=conf,
                    rms=float(rms[i]),
                )
            )
        return frames


class PestoTracker:
    """PESTO backend via pesto-pitch (PyTorch, CPU-fast).

    PESTO (Riou et al., 2023) is a self-supervised, transposition-equivariant pitch
    estimator that matches or beats CREPE on singing voice while being far lighter and
    faster. Its default model ``mir-1k_g7`` is trained on the MIR-1K singing-voice set,
    which fits humming well. Like CREPE it emits a per-frame pitch + confidence; we reuse
    the confidence as our voiced probability and compute RMS separately. PESTO builds its
    CQT front end for the given sample rate, so no resampling is needed.
    """

    def __init__(self, params: Params, model: str | None = None):
        self.p = params
        self.model = model or params.pesto_model

    def track(self, y: np.ndarray, sr: int) -> list[Frame]:
        import librosa
        import torch
        import pesto

        p = self.p
        x = torch.from_numpy(np.ascontiguousarray(y, dtype=np.float32))
        step_ms = p.hop_s * 1000.0  # match the pipeline hop (~11.6 ms)
        timesteps, f0, conf, _act = pesto.predict(
            x, sr, step_size=step_ms, model_name=self.model
        )
        f0 = f0.squeeze().cpu().numpy()
        conf = conf.squeeze().cpu().numpy()
        times = timesteps.squeeze().cpu().numpy() / 1000.0  # ms -> s

        rms = librosa.feature.rms(
            y=y, frame_length=p.frame_length, hop_length=p.hop_length
        )[0]
        n = min(len(f0), len(conf), len(times), len(rms))
        frames: list[Frame] = []
        for i in range(n):
            c = float(conf[i])
            frames.append(
                Frame(
                    t=float(times[i]),
                    f0=float(f0[i]) if c > 0.1 else float("nan"),
                    confidence=c,
                    rms=float(rms[i]),
                )
            )
        return frames


def ensure_penn_importable() -> None:
    """Make ``import penn`` succeed even where its Viterbi extension is unbuildable.

    penn does ``import torbi`` at module load, and torbi ships no wheel for some torch
    builds (raising at import). We always decode with argmax, which never touches torbi,
    so a stub is enough to let penn load. No-op if torbi is genuinely importable.
    """
    import sys
    import types

    if "torbi" in sys.modules:
        return
    try:
        import torbi  # noqa: F401
    except Exception:
        stub = types.ModuleType("torbi")
        stub.from_probabilities = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("torbi Viterbi unavailable; penn uses decoder='argmax'")
        )
        sys.modules["torbi"] = stub


class PennTracker:
    """FCNF0++ backend via penn (Pitch-Estimating Neural Networks; Morrison 2023).

    FCNF0++ is a fully-convolutional f0 + periodicity model that matches/beats CREPE and
    is a precision peer to PESTO. Two integration notes:
      * penn's default decoder is Viterbi via the compiled ``torbi`` extension, which ships
        no wheel for our torch build. We decode with ``argmax`` instead - penn refines the
        argmax bin with a local expected value, so cents precision is preserved - and we
        stub ``torbi`` before importing penn (only if the real one fails to load) so penn's
        hard top-level ``import torbi`` does not blow up.
      * penn downloads its checkpoint from the HuggingFace Hub on first use, then caches it
        locally. Only the model weights are fetched; user audio never leaves the machine.

    penn returns a per-frame pitch + periodicity; we reuse periodicity as the voiced
    probability (its entropy scale separates voiced ~0.58 from unvoiced ~0.05, cleanly
    astride the voiced_enter/voiced_exit thresholds) and compute RMS separately.
    """

    def __init__(self, params: Params):
        self.p = params

    def track(self, y: np.ndarray, sr: int) -> list[Frame]:
        import librosa
        import torch

        ensure_penn_importable()  # stub torbi if needed; we decode with argmax
        import penn

        p = self.p
        audio = torch.from_numpy(np.ascontiguousarray(y, dtype=np.float32)).unsqueeze(0)
        pitch, periodicity = penn.from_audio(
            audio, sr, hopsize=p.hop_s, fmin=p.fmin, fmax=p.fmax, decoder="argmax"
        )
        f0 = pitch.squeeze(0).cpu().numpy()
        conf = periodicity.squeeze(0).cpu().numpy()

        rms = librosa.feature.rms(
            y=y, frame_length=p.frame_length, hop_length=p.hop_length
        )[0]
        lo, span = p.penn_conf_lo, max(p.penn_conf_hi - p.penn_conf_lo, 1e-6)
        n = min(len(f0), len(conf), len(rms))
        frames: list[Frame] = []
        for i in range(n):
            # Stretch penn's compressed entropy periodicity into a [0,1] confidence so the
            # voicing thresholds keep their margin (see Params.penn_conf_lo/hi).
            c = (float(conf[i]) - lo) / span
            c = 0.0 if c < 0.0 else 1.0 if c > 1.0 else c
            frames.append(
                Frame(
                    t=i * p.hop_s,
                    f0=float(f0[i]) if c > 0.05 else float("nan"),
                    confidence=c,
                    rms=float(rms[i]),
                )
            )
        return frames


def make_tracker(params: Params) -> PitchTracker:
    if params.backend == "crepe":
        return CrepeTracker(params)
    if params.backend == "pesto":
        return PestoTracker(params)
    if params.backend == "fcnf0":
        return PennTracker(params)
    return PyinTracker(params)
