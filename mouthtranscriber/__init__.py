"""MouthTranscriber — hum a melody, get back notes, key, and chords.

Local-first Python pipeline. See PROJECT PLAN.md for the full design.
"""

from .config import Params
from .model import Frame, NoteEvent, Score

__all__ = ["Params", "Frame", "NoteEvent", "Score"]
__version__ = "0.1.0"
