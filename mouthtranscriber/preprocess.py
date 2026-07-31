"""Signal conditioning before pitch analysis (PLAN §5.2).

Deliberately light: remove DC, high-pass out room rumble, and normalize level.
We avoid aggressive denoising because it distorts pitch — the confidence + RMS
voicing gate (§5.4) is what rejects noise, not spectral subtraction.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import butter, sosfilt


def preprocess(y: np.ndarray, sr: int, highpass_hz: float = 70.0) -> np.ndarray:
    """DC-remove, high-pass, and peak-normalize a mono signal."""
    y = np.asarray(y, dtype=np.float32)

    # DC removal
    y = y - float(np.mean(y))

    # High-pass to kill HVAC / desk thumps / handling rumble.
    nyq = sr / 2.0
    cutoff = min(highpass_hz, nyq * 0.99) / nyq
    sos = butter(4, cutoff, btype="highpass", output="sos")
    y = sosfilt(sos, y).astype(np.float32)

    # Peak-normalize with a little headroom.
    peak = float(np.max(np.abs(y)))
    if peak > 1e-9:
        y = (y / peak) * 0.98

    return y.astype(np.float32)
