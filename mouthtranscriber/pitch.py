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
    so we reuse its periodicity as confidence and compute RMS separately.
    """

    def __init__(self, params: Params, model: str = "tiny"):
        self.p = params
        self.model = model

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
            model=self.model, return_periodicity=True, batch_size=512,
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


def make_tracker(params: Params) -> PitchTracker:
    if params.backend == "crepe":
        return CrepeTracker(params)
    return PyinTracker(params)
