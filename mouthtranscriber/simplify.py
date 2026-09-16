"""LLM melody simplification ("Reorganize"): rewrite a messy transcription into a clean,
easy-to-read melody.

Manual mode's per-note "Ask" box (edit_nl.py) does bounded, surgical edits. This does the
opposite: a WHOLE-melody rewrite. The client sends the current melody as a compact list of
note names + beat-lengths (never audio, seconds, or a filename); the model returns a NEW
sequential melody whose durations are snapped to simple note values (whole / half / quarter /
eighth ...), tied slivers merged, awkward rhythm straightened, while the tune's key and
contour are kept. We validate the shape locally, turn the sequential list back into notes with
grid positions (a rest becomes a gap), and hand it back for the client to apply as ONE
undoable step and re-engrave with its own verovio path.

Transport is the shared mouthtranscriber.llm (config per request from a gitignored .env). It
reuses DEEPSEEK_EDIT_MODEL, the same override the Ask box uses, since this is the other
Manual-mode structured-JSON feature.
"""

from __future__ import annotations

import json

from . import llm
from .model import midi_to_name

# The note values the model is told to snap to, in quarter-note beats. Whole down to
# sixteenth, plus the common dotted values. Kept as data so the prompt and the (loose)
# validation share one list.
_NOTE_VALUES = (4.0, 3.0, 2.0, 1.5, 1.0, 0.75, 0.5, 0.25)
_MAX_OUT_NOTES = 400   # a simplified melody should be shorter than the input, never huge


def _melody_listing(notes: list) -> str:
    """A compact, ordered 'pitch (midi), beats' listing of the melody for the prompt.

    Pure text: note names + numbers + beat lengths only. Notes are taken in onset order and
    each length is its quarter-note duration, so the model sees the same rhythm the staff does.
    """
    ordered = sorted(notes, key=lambda n: n.start_ql)
    lines = []
    for i, n in enumerate(ordered, 1):
        beats = round(float(n.dur_ql), 3)
        lines.append(f"{i}. {midi_to_name(int(n.midi))} ({int(n.midi)}), {beats} beats")
    return "\n".join(lines)


def build_messages(
    notes: list,
    key: str | None,
    time_sig: tuple[int, int],
    language: str,
) -> list[dict]:
    """Build the chat messages for a simplification request. PURE (no I/O, no network).

    The model is a copyist tidying a rough transcription into a readable lead sheet: keep the
    tune, fix the rhythm. `language` sets the summary language only.
    """
    lang = (language or "en").strip().lower()
    lang_line = (
        "Write the summary in Vietnamese." if lang.startswith("vi")
        else "Write the summary in English."
    )
    values = ", ".join(
        f"{v:g}" for v in _NOTE_VALUES
    )
    system = (
        "You are a music copyist. You are given a rough monophonic melody transcription: an "
        "ordered list of notes, each with its pitch (note name and MIDI number, 60 = middle C) "
        "and its length in beats (1 beat = 1 quarter note). Rewrite it as the SAME tune but "
        "easy to read.\n\n"
        "Return ONLY a JSON object: {\"notes\": [ ... ], \"summary\": \"...\"}. Each element of "
        "notes is either a note {\"midi\": 62, \"beats\": 1} or a rest {\"rest\": true, "
        "\"beats\": 0.5}, in playing order (do not give absolute positions; the order and the "
        "lengths define the timing). Rules:\n"
        f"- Snap every length to one simple note value from this set of beats: {values}.\n"
        "- Merge tied slivers and repeated fragments of the same pitch into one note, and drop "
        "tiny ornamental notes that only make the rhythm hard to read.\n"
        "- Keep the tune recognizable: same key, same overall pitch contour and phrasing. Do "
        "not transpose, and do not invent a new melody.\n"
        "- Keep it monophonic (one note at a time). Every midi is an integer from 12 to 108.\n"
        "- Keep the total length close to the original (within about one bar).\n"
        "- summary is one plain sentence naming what you cleaned up. Plain text only: no "
        "markdown, no emojis, and no em dashes or en dashes; use a hyphen if needed.\n"
        "- Output only the JSON object, nothing before or after it.\n"
        + lang_line
    )
    user = (
        f"Key: {key or 'unknown'}\n"
        f"Time signature: {time_sig[0]}/{time_sig[1]}\n"
        "Melody (pitch and length, in order):\n"
        + _melody_listing(notes)
        + "\n\nReturn the simplified melody as JSON."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _validate(obj: dict) -> list[dict]:
    """Shape + range check of the model's melody. Raises llm.LLMBadOutput on any violation.

    A note needs an integer midi 12..108 and beats > 0; a rest needs beats > 0. The list must
    be non-empty and not absurdly long. Beats are bounded so one bad number cannot blow up the
    grid. Returns the cleaned list of {"midi","beats"} / {"rest": True, "beats"} dicts.
    """
    items = obj.get("notes")
    if not isinstance(items, list) or not items:
        raise llm.LLMBadOutput("the simplified melody had no notes")
    if len(items) > _MAX_OUT_NOTES:
        raise llm.LLMBadOutput("the simplified melody was unexpectedly long")
    out: list[dict] = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            raise llm.LLMBadOutput(f"melody item {i + 1} is not an object")
        beats = it.get("beats")
        # bool is an int subclass; reject a JSON true/false where a number is expected.
        if isinstance(beats, bool) or not isinstance(beats, (int, float)):
            raise llm.LLMBadOutput(f"melody item {i + 1} has no numeric beats")
        if not (0 < float(beats) <= 16):
            raise llm.LLMBadOutput(f"melody item {i + 1} has an out-of-range length")
        if it.get("rest"):
            out.append({"rest": True, "beats": float(beats)})
            continue
        midi = it.get("midi")
        if isinstance(midi, bool) or not isinstance(midi, int):
            raise llm.LLMBadOutput(f"melody item {i + 1} has no integer midi")
        if midi < 12 or midi > 108:
            raise llm.LLMBadOutput(f"melody item {i + 1} has an out-of-range midi {midi}")
        out.append({"midi": int(midi), "beats": float(beats)})
    if not any("midi" in it for it in out):
        raise llm.LLMBadOutput("the simplified melody was all rests")
    return out


def _to_notes(items: list[dict]) -> list[dict]:
    """Turn the validated sequential melody into notes with grid positions.

    Onsets are the running sum of beats; a rest advances the cursor and emits nothing, so a gap
    reappears as a rest when the client rebuilds its sequence (seqFromNotes). Returns the
    transcribe `notes` shape the client already applies: {name, midi, start_ql, dur_ql}.
    """
    out: list[dict] = []
    cursor = 0.0
    for it in items:
        beats = it["beats"]
        if it.get("rest"):
            cursor += beats
            continue
        midi = it["midi"]
        out.append({
            "name": midi_to_name(midi),
            "midi": midi,
            "start_ql": round(cursor, 4),
            "dur_ql": round(beats, 4),
        })
        cursor += beats
    return out


def simplify(
    notes: list,
    key: str | None,
    time_sig: tuple[int, int],
    language: str,
    env_path: str = ".env",
) -> dict:
    """Return {"notes": [...], "summary": str, "model": str} for a simplified melody, or raise.

    Raises llm.LLMNotConfigured (503) with no key, llm.LLMBadOutput (502) when the model output
    is not a usable melody, and llm.LLMUpstreamError (502) on a network / API failure. Low-ish
    temperature: this is a precise rewrite, not free composition.
    """
    quantized = [n for n in notes if n.start_ql == n.start_ql and n.dur_ql == n.dur_ql]  # drop NaN
    if not quantized:
        raise llm.LLMBadOutput("no quantized melody to reorganize")

    messages = build_messages(quantized, key, time_sig, language)
    # No max_tokens override: inherit llm._MAX_TOKENS, the one generous budget shared by every
    # feature (a ceiling billed on actual output, so it is free headroom). A non-reasoning
    # DEEPSEEK_EDIT_MODEL avoids the reasoning-token cost entirely and is faster.
    out = llm.chat(
        messages, env_path=env_path, model_key="DEEPSEEK_EDIT_MODEL",
        temperature=0.3, json_mode=True,
    )
    obj = llm.parse_json_object(out["content"])
    items = _validate(obj)
    summary = obj.get("summary")
    return {
        "notes": _to_notes(items),
        "summary": summary if isinstance(summary, str) else "",
        "model": out["model"],
    }
