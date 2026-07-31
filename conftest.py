"""Ensures the repo root is importable so ``import mouthtranscriber`` works
under pytest and when running helper scripts from the project root.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
