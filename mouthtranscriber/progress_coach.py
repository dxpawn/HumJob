"""Hub progress coach: turn a longitudinal practice report into a one-week plan.

The mirror of coach.py, one level up: coach.py reviews a single Sing-Along take, this
reviews the whole practice log (aggregated by progress.js into weeks of counts and trends,
never a single recording). Opt-in and PII-free like every LLM feature here: only the compact
numeric report leaves the machine, never audio, frames, filenames, or timestamps.

Transport is shared (mouthtranscriber.llm): config in a gitignored `.env`, read per request.
Keys are the same DEEPSEEK_* set coach.py uses; no new key is needed.
"""

from __future__ import annotations

import json

from . import llm


def build_messages(report: dict, language: str) -> list[dict]:
    """Build the chat messages for the practice-plan request. PURE (no I/O, no network).

    The model is cast as a vocal + musicianship coach reviewing a student's practice LOG:
    the numbers are aggregates over weeks, not a single take. It must (1) say what improved,
    (2) say what is stuck, and (3) give a one-week plan whose items name the app's own drills
    so the advice is actionable inside HumJob. Honesty guardrails match coach.py: interpret,
    do not recite; invent no measurements; wherever a section's lowConfidence is true, say
    there is not enough data and do not extrapolate. Plain-text house style (no markdown, no
    em/en dashes, no emojis); the client renders it verbatim. `language`: "vi" -> Vietnamese,
    anything else -> English.
    """
    lang = (language or "en").strip().lower()
    lang_line = "Respond in Vietnamese." if lang.startswith("vi") else "Respond in English."

    system = (
        "You are an experienced vocal and musicianship coach reviewing a student's practice "
        "log. You receive a JSON report that AGGREGATES many practice sessions over the last "
        "few weeks: it is trends and counts, not a single take, and you did NOT hear any "
        "audio. In the report: inTune is the percent of each Realtime take sung in the in-tune "
        "band (first / last / delta / slopePerTake show the trend across takes); steadiness is "
        "pitch wobble in cents (lower is steadier); sustain is the longest held note in "
        "seconds; range is the measured vocal range (lo to hiExt in note names, extST in "
        "semitones) with tessitura and a likely voice type; singalong aggregates Sing-Along "
        "takes (signedBiasCents negative = flat, positive = sharp; leapMinusStepPct negative = "
        "leaps land worse than steps; recurringWorst = notes that keep coming up weak; "
        "octaveSlipsTotal = octave errors); ear is Ear Trainer accuracy per mode (interval, "
        "chord, scale, key), pct overall and recentPct on the last answers. Every section also "
        "carries a lowConfidence flag.\n\n"
        "Coach, do not recite. Structure your reply in three parts:\n"
        "1. What improved. Name the specific trends that got better and what that suggests "
        "about the student's technique or ear.\n"
        "2. What is stuck. Name what is flat or getting worse, and interpret the likely cause "
        "in plain language using real pedagogy (for example, steady flatness that worsens "
        "points to fading breath support; weak leaps mean the pitch was not pre-heard; weak "
        "high register can be a registration or support problem).\n"
        "3. A one-week plan. Give day-by-day or session-by-session practice for the next week. "
        "Each item MUST name one of the app's own drills so the student knows exactly where to "
        "go, using these exact names: Realtime > Match game, Realtime > Scale trainer, "
        "Realtime > Range test, Sing-Along at Strict difficulty, Ear Trainer > Chords (or the "
        "other Ear Trainer modes: Intervals, Scales, Key). Tie each drill to a weakness the "
        "numbers showed.\n\n"
        "Honesty: base everything on the numbers given plus standard technique. Do not invent "
        "measurements you were not given, and do not claim to have heard tone or vibrato. "
        "Wherever a section has lowConfidence true, say plainly there is not enough data yet "
        "for that area and do not extrapolate a trend from it; tell the student how many more "
        "takes or answers would make it meaningful.\n\n"
        "Format: plain running text with simple numbered points only. No markdown symbols (no "
        "#, no *, no backticks, no bold), no headings beyond the three plain labels, no emojis. "
        "Do not use em dashes or en dashes; use a hyphen if you need one. Keep it focused, "
        "roughly 250 to 400 words.\n"
        + lang_line
    )
    user = (
        "Here is my aggregated practice report as JSON. Give me the review and a one-week plan "
        "that names the drills I should use.\n\n"
        + json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def progress_feedback(report: dict, language: str, env_path: str = ".env") -> dict:
    """Return {"feedback": str, "model": str} for a practice report, or raise.

    Raises llm.LLMNotConfigured when no key is set (HTTP 503) and llm.LLMUpstreamError on any
    network / API failure or an unusable response (HTTP 502). Prose output, so no json_mode.
    """
    messages = build_messages(report, language)
    out = llm.chat(messages, env_path=env_path, temperature=0.7)
    return {"feedback": out["content"], "model": out["model"]}
