"""Natural-language editing for Manual mode: a plain-language request -> a JSON edit script.

The client sends a text LISTING of the melody (note numbers, pitches, beat lengths, bars -
built by manual.js's describeSeq) plus the typed instruction. We ask the LLM for a small JSON
edit script that the client's pure interpreter (MT.applyScript) applies as one undoable step.

Only note names and beat lengths leave the machine (never audio, frames, or a filename); the
listing is text and the instruction is the user's words. The LLM output is validated for SHAPE
here and for RANGE on the client (which holds the live melody and is headless-testable).

Transport is the shared mouthtranscriber.llm (config in a gitignored `.env`, read per request).
The optional DEEPSEEK_EDIT_MODEL override lets the user point this at a faster model than the
coaching default without touching code.
"""

from __future__ import annotations

import json

from . import llm

# The op vocabulary the client interpreter understands. Kept in sync with manual.js
# applyScript's switch; the client does the authoritative range validation.
KNOWN_OPS = {
    "pitch", "setPitch", "duration", "setDuration", "transpose",
    "delete", "merge", "split", "insert", "insertRest",
}


def build_messages(listing: str, instruction: str, language: str) -> list[dict]:
    """Build the chat messages for an edit-script request. PURE (no I/O, no network).

    The system prompt gives the full op vocabulary with one example each, pins the 1-based
    note numbering to the listing, and tells the model to return only a JSON object (and to
    return an empty ops list with a question when the request is ambiguous or impossible).
    `language` sets the language of the human-readable `summary` only; the op names are fixed.
    """
    lang = (language or "en").strip().lower()
    lang_line = (
        "Write the summary field in Vietnamese." if lang.startswith("vi")
        else "Write the summary field in English."
    )

    system = (
        "You convert a musician's plain-language request into a JSON edit script for a "
        "monophonic melody. You are given a text listing of the melody: notes are numbered "
        "1-based (rests are not numbered), each line shows the note number, its pitch, and its "
        "length in beats, grouped by bar, and one line says which note is selected.\n\n"
        "Return ONLY a JSON object of the form {\"ops\": [...], \"summary\": \"...\"}. Each op is "
        "one object. The op vocabulary, with an example of each:\n"
        "- pitch: shift a note by semitones. {\"op\":\"pitch\",\"note\":3,\"semitones\":12}\n"
        "- setPitch: set a note to an exact MIDI number (60 = C4). {\"op\":\"setPitch\",\"note\":3,\"midi\":64}\n"
        "- duration: change a note length by a whole number of ticks (see the tick legend in the "
        "listing). {\"op\":\"duration\",\"note\":3,\"ticks\":2}\n"
        "- setDuration: set a note length in beats (1 beat = 1 quarter note). "
        "{\"op\":\"setDuration\",\"note\":3,\"beats\":2}\n"
        "- transpose: shift a range of notes. {\"op\":\"transpose\",\"from\":4,\"to\":8,\"semitones\":-2}\n"
        "- delete: turn a note into a rest. {\"op\":\"delete\",\"note\":2}\n"
        "- merge: join notes from..to into one held note. {\"op\":\"merge\",\"from\":3,\"to\":5}\n"
        "- split: split a note into parts equal pieces (2 to 8). {\"op\":\"split\",\"note\":4,\"parts\":2}\n"
        "- insert: add a note after another. {\"op\":\"insert\",\"after\":8,\"midi\":67,\"beats\":1}\n"
        "- insertRest: add a rest after a note. {\"op\":\"insertRest\",\"after\":5,\"beats\":0.5}\n\n"
        "Rules:\n"
        "- Note numbers are 1-based and refer to the listing exactly. You may also use the string "
        "\"selected\" for the selected note and \"last\" for the final note, in any note / from / "
        "to / after field.\n"
        "- \"this note\" or \"the selected note\" means the note the listing marks as selected.\n"
        "- beats means quarter notes; the listing already shows each note's length in beats, so "
        "you never have to convert.\n"
        "- You CANNOT refer to notes this script creates. If the user needs that, ask for a second "
        "instruction instead.\n"
        "- If the request is ambiguous or impossible, return {\"ops\": [], \"summary\": \"<one short "
        "question or reason>\"} rather than guessing.\n"
        "- summary is one plain sentence describing what you did (or the question). Plain text only: "
        "no markdown, no emojis, and no em dashes or en dashes; use a hyphen if needed.\n"
        "- Output only the JSON object, nothing before or after it.\n"
        + lang_line
    )
    user = (
        "Here is the melody:\n\n" + str(listing) +
        "\n\nMy request: " + str(instruction) +
        "\n\nReturn the JSON edit script."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _validate_shape(obj: dict) -> dict:
    """Shape-only check: {ops: [ {op: <known str>, <fields: number|string>...} ], summary?}.

    Range validation (do the note numbers exist, is the split too short) is the client's job.
    Raises llm.LLMBadOutput on a structural problem so the route maps it to a 502 reword hint.
    An empty ops list is valid (the model asking a question / refusing).
    """
    if not isinstance(obj, dict):
        raise llm.LLMBadOutput("the edit script was not a JSON object")
    ops = obj.get("ops")
    if not isinstance(ops, list):
        raise llm.LLMBadOutput("the edit script has no ops list")
    if len(ops) > 32:
        raise llm.LLMBadOutput("the edit script has too many ops")
    for idx, op in enumerate(ops):
        if not isinstance(op, dict):
            raise llm.LLMBadOutput(f"op {idx + 1} is not an object")
        name = op.get("op")
        if not isinstance(name, str) or name not in KNOWN_OPS:
            raise llm.LLMBadOutput(f"op {idx + 1} has an unknown op name")
        for k, v in op.items():
            if k == "op":
                continue
            # bool is an int subclass in Python; a JSON true/false is not a valid field value.
            if isinstance(v, bool) or not isinstance(v, (int, float, str)):
                raise llm.LLMBadOutput(f"op {idx + 1} field '{k}' has an unexpected type")
    summary = obj.get("summary")
    if summary is not None and not isinstance(summary, str):
        raise llm.LLMBadOutput("the edit script summary was not text")
    return {"ops": ops, "summary": obj.get("summary", "")}


def edit_script(listing: str, instruction: str, language: str, env_path: str = ".env") -> dict:
    """Return {"script": dict, "model": str} for an instruction, or raise.

    Raises llm.LLMNotConfigured (HTTP 503) with no key, llm.LLMBadOutput (HTTP 502, reword)
    when the model output is not a valid edit script, and llm.LLMUpstreamError (HTTP 502) on
    network / API failure. Low temperature: this is a precise conversion, not prose.
    """
    messages = build_messages(listing, instruction, language)
    # No max_tokens override: inherit llm._MAX_TOKENS, the one generous budget shared by every
    # feature (a ceiling billed on actual output, so it is free headroom). A non-reasoning
    # DEEPSEEK_EDIT_MODEL avoids the reasoning-token cost entirely and is faster.
    out = llm.chat(
        messages, env_path=env_path, model_key="DEEPSEEK_EDIT_MODEL",
        temperature=0.2, json_mode=True,
    )
    obj = llm.parse_json_object(out["content"])
    script = _validate_shape(obj)
    return {"script": script, "model": out["model"]}
