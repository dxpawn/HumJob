"""Debug visualization (PLAN §5.10) — the standard tool for iterating on the DSP.

One figure, three stacked panels sharing a time axis:
  1. f0 contour (as MIDI pitch) with detected note segments drawn on top
  2. RMS energy in dB with the voicing decision shaded
  3. confidence trace with the hysteresis thresholds marked

Saving this per clip is how we answer "why is this note wrong".
"""

from __future__ import annotations

import numpy as np

from .config import Params
from .model import Frame, NoteEvent, hz_to_midi


def plot_analysis(
    frames: list[Frame],
    voiced: np.ndarray,
    notes: list[NoteEvent],
    params: Params,
    path: str,
    title: str = "HumJob analysis",
) -> str:
    import matplotlib

    matplotlib.use("Agg")  # headless
    import matplotlib.pyplot as plt

    t = np.array([f.t for f in frames])
    f0 = np.array([f.f0 for f in frames])
    midi = np.array([hz_to_midi(v) for v in f0])
    rms = np.array([f.rms for f in frames])
    conf = np.array([f.confidence for f in frames])
    peak = float(rms.max()) + 1e-12
    rms_db = 20.0 * np.log10(rms / peak + 1e-12)

    fig, (ax0, ax1, ax2) = plt.subplots(
        3, 1, figsize=(12, 8), sharex=True, height_ratios=[3, 1, 1]
    )

    # Panel 1: pitch + detected notes
    ax0.plot(t, midi, ".", ms=2, color="#888", label="f0 (raw)")
    for n in notes:
        ax0.hlines(n.midi, n.start, n.end, color="#0a7", lw=4, alpha=0.9)
        ax0.plot([n.start, n.end], [n.raw_midi, n.raw_midi], color="#c33", lw=1)
    ax0.set_ylabel("pitch (MIDI)")
    ax0.set_title(title)
    ax0.grid(True, alpha=0.3)
    ax0.legend(loc="upper right", fontsize=8)

    # Panel 2: energy + voicing
    ax1.plot(t, rms_db, color="#37a", lw=1)
    ax1.axhline(params.rms_threshold_db, color="#a33", ls="--", lw=0.8)
    _shade(ax1, t, voiced)
    ax1.set_ylabel("RMS (dB)")
    ax1.grid(True, alpha=0.3)

    # Panel 3: confidence + thresholds
    ax2.plot(t, conf, color="#753", lw=1)
    ax2.axhline(params.voiced_enter, color="#3a3", ls="--", lw=0.8)
    ax2.axhline(params.voiced_exit, color="#aa3", ls="--", lw=0.8)
    ax2.set_ylabel("confidence")
    ax2.set_xlabel("time (s)")
    ax2.set_ylim(0, 1)
    ax2.grid(True, alpha=0.3)

    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path


def _shade(ax, t, voiced) -> None:
    """Shade the voiced regions on an axis."""
    in_run = False
    start = 0.0
    for i, v in enumerate(voiced):
        if v and not in_run:
            in_run, start = True, t[i]
        elif not v and in_run:
            ax.axvspan(start, t[i], color="#0a7", alpha=0.12)
            in_run = False
    if in_run:
        ax.axvspan(start, t[-1], color="#0a7", alpha=0.12)
