"""LLM reharmonization: reharmonize a hummed melody in a requested style.

The client asks for "jazzier", "simpler for guitar", "sadder", etc. We send the model only
per-bar pitch-class profiles (note names + weights), the key, the time signature, the current
chords, and the style string - never audio, seconds, or a filename. The model proposes one
chord per measure from a fixed quality whitelist; we validate it locally, spell it, score its
fit against the melody bar by bar, and engrave it, so a clash is shown, not hidden.

B1's chords.QUALITIES is the shared vocabulary; B2's chords.measure_profiles / coverage_fit are
reused so the LLM path and the deterministic path agree on the maths. Transport is the shared
mouthtranscriber.llm (config per request from a gitignored .env; DEEPSEEK_CHORD_MODEL override).
"""

from __future__ import annotations

import json

from . import chords as chords_mod
from . import export as export_mod
from . import llm
from .chords import QUALITIES
from .model import Chord, NoteEvent, Score

_QUALITY_LINES = "\n".join(
    f"  {q}: {'-'.join(str(i) for i in v['intervals'])} semitones over the root"
    for q, v in QUALITIES.items()
)


def build_messages(
    profiles: list[dict],
    key: str | None,
    time_sig: tuple[int, int],
    current_chords: list[dict],
    style: str,
    language: str,
) -> list[dict]:
    """Build the chat messages for a reharmonization request. PURE (no I/O, no network).

    The model is a harmony arranger: propose exactly one chord per measure for the N measures,
    roots as note names, qualities from the whitelist, keeping strong-beat melody notes as chord
    tones unless the style asks for tension. `language` sets the summary language only.
    """
    n = len(profiles)
    lang = (language or "en").strip().lower()
    lang_line = (
        "Write the summary in Vietnamese." if lang.startswith("vi")
        else "Write the summary in English."
    )
    payload = {
        "key": key or "unknown",
        "time_signature": f"{time_sig[0]}/{time_sig[1]}",
        "measures": n,
        "current_chords": [
            {"measure": (c.get("measure", 0) + 1), "symbol": c.get("symbol", "")}
            for c in (current_chords or [])
        ],
        "melody_by_measure": [
            {"measure": p["measure"] + 1, "notes": p.get("weights", {})} for p in profiles
        ],
    }
    system = (
        "You are a harmony arranger reharmonizing a short monophonic melody. You are given, per "
        "measure, the melody's pitch-class content as note names with weights (higher weight = "
        "more prominent, on strong beats or held longer), the key, the time signature, and the "
        "current chords. Propose a new chord progression in the requested style.\n\n"
        "Return ONLY a JSON object: {\"chords\": [{\"measure\": 1, \"root\": \"G\", \"quality\": "
        "\"dom7\"}, ...], \"summary\": \"...\"}. Rules:\n"
        f"- Exactly one chord per measure, for all {n} measures, numbered 1 to {n} in order.\n"
        "- root is a note name like C, F#, Bb, Eb (sharps or flats both fine).\n"
        "- quality MUST be one of these exact strings:\n"
        f"{_QUALITY_LINES}\n"
        "- Keep the melody's strong-beat notes (the higher-weighted ones) as chord tones unless "
        "the requested style specifically calls for tension or color.\n"
        "- Make the progression musical: voice-lead by root motion, respect the key unless the "
        "style asks to leave it, and end on a chord that resolves.\n"
        "- summary is one or two plain sentences naming the main moves you made. Plain text only: "
        "no markdown, no emojis, and no em dashes or en dashes; use a hyphen if needed.\n"
        "- Output only the JSON object, nothing before or after it.\n"
        + lang_line
    )
    user = (
        "Reharmonize this melody in the style: " + str(style).strip() + "\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _roman_for(root_name: str, quality: str, key_str: str | None) -> str:
    """Roman numeral for a spelled chord in the key: music21's numeral + our quality suffix.

    Built in root position from the ChordSymbol pitches (ordered root-up) so an octaveless
    chord is not misread as an inversion. Returns "" on any failure (export tolerates it).
    """
    if not key_str:
        return ""
    try:
        from music21 import chord as m21chord
        from music21 import harmony
        from music21 import key as m21key
        from music21 import roman as m21roman

        cs = harmony.ChordSymbol(root=root_name, kind=QUALITIES[quality]["kind"])
        c = m21chord.Chord(cs.pitches)
        tonic, mode = key_str.split()
        rn = m21roman.romanNumeralFromChord(c, m21key.Key(tonic, mode))
        return rn.romanNumeralAlone + QUALITIES[quality]["roman"]
    except Exception:
        return ""


def _validate(obj: dict, n_measures: int) -> list[dict]:
    """Check the model's proposal shape and content. Raises llm.LLMBadOutput on any violation.

    One chord per measure, measures 1..N in order, quality in the whitelist. Root parsing is
    checked in _build_chords (which needs the parsed pitch anyway).
    """
    proposed = obj.get("chords")
    if not isinstance(proposed, list):
        raise llm.LLMBadOutput("the reharmonization had no chords list")
    if len(proposed) != n_measures:
        raise llm.LLMBadOutput(
            f"expected {n_measures} chords (one per measure), got {len(proposed)}"
        )
    for i, ch in enumerate(proposed):
        if not isinstance(ch, dict):
            raise llm.LLMBadOutput(f"chord {i + 1} is not an object")
        if ch.get("measure") != i + 1:
            raise llm.LLMBadOutput(f"chord {i + 1} has the wrong measure number")
        if ch.get("quality") not in QUALITIES:
            raise llm.LLMBadOutput(f"chord {i + 1} has an unknown quality {ch.get('quality')!r}")
        if not isinstance(ch.get("root"), str) or not ch.get("root").strip():
            raise llm.LLMBadOutput(f"chord {i + 1} has no root note name")
    return proposed


def _build_chords(proposed: list[dict], key: str | None, time_sig: tuple[int, int]) -> list[Chord]:
    """Spell the validated proposal into Chord objects. Raises llm.LLMBadOutput on a bad root."""
    from music21 import pitch as m21pitch

    bar_ql = time_sig[0] * (4.0 / time_sig[1])
    out: list[Chord] = []
    for ch in proposed:
        m = int(ch["measure"]) - 1
        quality = ch["quality"]
        try:
            p = m21pitch.Pitch(str(ch["root"]).strip())
        except Exception:
            raise llm.LLMBadOutput(f"chord {m + 1} has an unparseable root {ch.get('root')!r}")
        out.append(
            Chord(
                measure=m,
                start_ql=m * bar_ql,
                root_pc=p.pitchClass,
                root_name=p.name,
                quality=quality,
                symbol=chords_mod._pretty(p.name, quality),
                roman=_roman_for(p.name, quality, key),
            )
        )
    return out


def _chord_dict(c: Chord) -> dict:
    return {
        "measure": c.measure,
        "start_ql": round(c.start_ql, 3),
        "root_pc": c.root_pc,
        "root_name": c.root_name,
        "quality": c.quality,
        "symbol": c.symbol,
        "roman": c.roman,
    }


def reharmonize(
    notes: list[NoteEvent],
    key: str | None,
    time_sig: tuple[int, int],
    current_chords: list[dict],
    style: str,
    language: str,
    env_path: str = ".env",
) -> dict:
    """Return {chords, fit, summary, svg, model} for a reharmonization, or raise.

    Raises llm.LLMNotConfigured (503) with no key, llm.LLMBadOutput (502) when the proposal is
    malformed / unmusical to build, and llm.LLMUpstreamError (502) on a network / API failure.
    """
    profiles = chords_mod.measure_profiles(notes, time_sig)
    if not profiles:
        raise llm.LLMBadOutput("no quantized melody to reharmonize")

    messages = build_messages(profiles, key, time_sig, current_chords, style, language)
    # No max_tokens override: inherit llm._MAX_TOKENS, the one generous budget shared by every
    # feature (a ceiling billed on actual output, so it is free headroom). A non-reasoning
    # DEEPSEEK_CHORD_MODEL avoids the reasoning-token cost entirely and is faster.
    out = llm.chat(
        messages, env_path=env_path, model_key="DEEPSEEK_CHORD_MODEL",
        temperature=0.7, json_mode=True,
    )
    obj = llm.parse_json_object(out["content"])
    proposed = _validate(obj, len(profiles))
    built = _build_chords(proposed, key, time_sig)
    fit = chords_mod.coverage_fit(notes, time_sig, built)

    score = Score(notes=notes, key=key, time_sig=time_sig, chords=built)
    try:
        svg = export_mod.sheet_svg_string(score)
    except Exception:
        svg = ""   # a render failure should not sink the whole reharmonization

    summary = obj.get("summary")
    return {
        "chords": [_chord_dict(c) for c in built],
        "fit": fit,
        "summary": summary if isinstance(summary, str) else "",
        "svg": svg,
        "model": out["model"],
    }
