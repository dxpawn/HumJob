---
title: HumJob
emoji: 🎙️
colorFrom: green
colorTo: blue
sdk: gradio
app_file: app.py
python_version: "3.12"
pinned: false
---

# HumJob

Hum a melody, get back the notes, key, and suggested chords as MIDI, MusicXML, and
engraved sheet music. Also includes a live pitch monitor, a guitar tuner, a guided
vocal range and tessitura test, a transposer, and a sing-along scorer.

This Space uses the Gradio SDK only as the free runtime: `app.py` starts the full
FastAPI app (uvicorn on port 7860), so the whole site is served at the root. The
pesto pitch backend is used.

Optional AI singing coaching (Sing-Along tab) is enabled only when a `DEEPSEEK_API_KEY`
secret is set on the Space; without it, that one button reports "not configured" and
everything else works.
