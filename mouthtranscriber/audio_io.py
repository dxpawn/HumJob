"""Audio loading and (optional) live recording.

Loading always yields a mono float32 signal at the canonical sample rate so
every downstream stage can assume a single, known format (PLAN §5.1).
"""

from __future__ import annotations

import numpy as np


def load_audio(path: str, target_sr: int) -> tuple[np.ndarray, int]:
    """Load ``path`` as mono float32 at ``target_sr``.

    Uses librosa (soundfile + audioread), so it handles WAV/FLAC/OGG natively and
    falls back to ffmpeg-backed decoding for other formats. Resamples and downmixes
    to mono as needed.
    """
    import librosa  # imported lazily; librosa is heavy

    y, sr = librosa.load(path, sr=target_sr, mono=True)
    return y.astype(np.float32), sr


def record(seconds: float, sr: int) -> np.ndarray:
    """Record ``seconds`` of mono audio from the default input device.

    Only used by the CLI's ``record`` convenience path; the web UI records in the
    browser instead. Requires ``sounddevice`` (PortAudio).
    """
    import sounddevice as sd

    frames = int(round(seconds * sr))
    audio = sd.rec(frames, samplerate=sr, channels=1, dtype="float32")
    sd.wait()
    return audio.reshape(-1)
