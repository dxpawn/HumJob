"""Sing-Along LLM coaching: turn a numeric take report into spoken-language feedback.

This is the ONLY part of HumJob that sends anything off-machine, and only when the user
presses "Get coaching". The browser computes a compact, PII-free report (pitch bias, drift,
leap vs step accuracy, worst notes - never audio, never the recording, never a filename) and
POSTs it here; we ask a DeepSeek chat model to phrase it as concrete practice advice.

Config lives in a gitignored `.env` at the project root and is read PER REQUEST (via
`load_env`), so the user can paste their key and use the feature without restarting the
server. Keys:
  DEEPSEEK_API_KEY   (required; no key -> CoachNotConfigured -> HTTP 503)
  DEEPSEEK_MODEL     (default "deepseek-v4-flash")
  DEEPSEEK_BASE_URL  (default "https://api.deepseek.com"; OpenAI-compatible /chat/completions)

Real environment variables override the file, so a shell export wins over `.env`.
"""

from __future__ import annotations

import json
import os
import pathlib

import httpx

DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_BASE_URL = "https://api.deepseek.com"
_CONFIG_KEYS = ("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL")
_TIMEOUT_S = 60.0
# deepseek-v4-flash is a REASONING model: it spends completion tokens on hidden
# reasoning_content BEFORE the visible answer, and if max_tokens runs out mid-reasoning the
# answer comes back empty (finish_reason "length"). So the budget must cover the reasoning
# (a few thousand tokens for this task) PLUS the ~200-320 word reply, not just the reply.
_MAX_TOKENS = 4000


class CoachNotConfigured(Exception):
    """No API key is configured; the feature is off until the user sets one."""


class CoachUpstreamError(Exception):
    """The upstream LLM API could not be reached or returned an unusable response."""


def load_env(path: str = ".env") -> dict:
    """Read KEY=VALUE lines from `path` into a dict; real os.environ wins over the file.

    Tiny on purpose (no python-dotenv dependency): blank lines and `#` comments are
    skipped, surrounding whitespace and one layer of matching quotes are stripped. Only
    the three DEEPSEEK_* keys are overlaid from the environment, so the whole shell
    environment is not dragged into the result.
    """
    values: dict[str, str] = {}
    p = pathlib.Path(path)
    if p.is_file():
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key:
                values[key] = val
    for k in _CONFIG_KEYS:
        env_v = os.environ.get(k)
        if env_v is not None:
            values[k] = env_v
    return values


def build_messages(report: dict, language: str) -> list[dict]:
    """Build the chat messages for the coaching request. PURE (no I/O, no network).

    The system prompt casts the model as a real vocal coach: it uses the numbers as a
    DIAGNOSIS (what the pitch pattern implies about breath support, registration, ear /
    pre-hearing, larynx tension) and prescribes concrete exercises, rather than reciting the
    statistics back. The honesty guardrails stay (no invented measurements, no claiming it
    heard tone/vibrato it has no data for) as does the plain-text house style (no markdown,
    no em/en dashes) since the client renders it verbatim. `language` picks English or
    Vietnamese ("vi" -> Vietnamese; anything else -> English).
    """
    lang = (language or "en").strip().lower()
    lang_line = "Respond in Vietnamese." if lang.startswith("vi") else "Respond in English."

    system = (
        "You are an experienced vocal coach giving a one-on-one lesson. A student just sang one "
        "practice take against a reference melody, and you receive a JSON report of the "
        "pitch-tracking measurements from that take. You did NOT hear the audio; the report is "
        "all you have. In it: cents are pitch error (negative = flat, positive = sharp); hitPct "
        "is the fraction of a note sung inside the in-tune band; signedBiasCents is the average "
        "direction of error; drift compares the start of the take with the end; leaps vs step "
        "shows accuracy on jumps versus neighbouring notes; register shows accuracy low / mid / "
        "high; worstNotes lists the weakest notes with their bar numbers.\n\n"
        "Coach, do not recite. Your job:\n"
        "- Interpret the pattern like a teacher: say what it most likely means about the "
        "singer's TECHNIQUE, and explain the cause in plain language. Use real vocal pedagogy. "
        "For example, steady flatness that worsens across the take usually points to fading "
        "breath support or airflow; missed upward leaps often mean the pitch was not pre-heard "
        "or the larynx rises and the note is approached from below; weakness only in the high "
        "register can be a registration / passaggio or support problem. Draw the most probable "
        "cause from the actual numbers in this report.\n"
        "- Then give 3 to 5 concrete things to practise, each a SPECIFIC exercise the singer can "
        "do (for instance lip trills or sirens through the weak range, sustaining the target "
        "note against a held reference drone, staccato onsets to place a leap cleanly, or "
        "appoggio / breath-pacing work to stop the drift). Tie each exercise to what the data "
        "showed, and name the specific notes and bars from worstNotes where it helps (for "
        "example, the C5 in bar 2).\n"
        "- Be encouraging and specific, like a real lesson. Interpret and advise; do not just "
        "list the statistics.\n\n"
        "Honesty: base your diagnosis on the numbers given plus standard vocal technique. You "
        "MAY explain likely causes and prescribe exercises from your expertise, but do not "
        "invent specific measurements you were not given, and do not claim to have heard tone, "
        "breathiness, or vibrato, which are not in the data. If lowConfidence is true, say the "
        "take was short and keep the advice general.\n\n"
        "Format: plain running text and simple numbered points only. No markdown symbols (no "
        "#, no *, no backticks, no bold), no headings, no emojis. Do not use em dashes or en "
        "dashes; use a hyphen if you need one. Keep it focused, roughly 200 to 320 words.\n"
        + lang_line
    )
    user = (
        "Here is the pitch report for my take as JSON. Give me your coaching: what the numbers "
        "say about my technique, and exactly what to practise next.\n\n"
        + json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _post_chat(base_url: str, api_key: str, model: str, messages: list[dict]) -> dict:
    """POST an OpenAI-compatible chat completion and return the parsed JSON body.

    Isolated so tests can monkeypatch it (no real network in the suite). Raises
    httpx.HTTPError on transport / non-2xx responses, which the caller maps to
    CoachUpstreamError.
    """
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": _MAX_TOKENS,
        "temperature": 0.7,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    resp = httpx.post(url, json=payload, headers=headers, timeout=_TIMEOUT_S)
    resp.raise_for_status()
    return resp.json()


def coach_feedback(report: dict, language: str, env_path: str = ".env") -> dict:
    """Return {"feedback": str, "model": str} for a take report, or raise.

    Raises CoachNotConfigured when no DEEPSEEK_API_KEY is set (HTTP 503 upstream), and
    CoachUpstreamError on any network / API failure or an unparseable response (HTTP 502).
    """
    cfg = load_env(env_path)
    api_key = (cfg.get("DEEPSEEK_API_KEY") or "").strip()
    if not api_key:
        raise CoachNotConfigured(
            "coaching is not configured: add DEEPSEEK_API_KEY to a .env file in the "
            "project root (copy .env.example), then try again. No restart needed."
        )
    model = (cfg.get("DEEPSEEK_MODEL") or "").strip() or DEFAULT_MODEL
    base_url = (cfg.get("DEEPSEEK_BASE_URL") or "").strip() or DEFAULT_BASE_URL

    messages = build_messages(report, language)
    try:
        data = _post_chat(base_url, api_key, model, messages)
    except httpx.HTTPError as e:
        raise CoachUpstreamError(f"could not reach the coaching API: {e}")

    try:
        choice = data["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise CoachUpstreamError("the coaching API returned an unexpected response shape")
    if not isinstance(content, str) or not content.strip():
        # A reasoning model that ran out of budget mid-thought returns empty content with
        # finish_reason "length"; say so, rather than a bare "empty response".
        if choice.get("finish_reason") == "length":
            raise CoachUpstreamError(
                "the coaching model hit its token limit before writing a reply "
                "(raise _MAX_TOKENS in coach.py); please try again"
            )
        raise CoachUpstreamError("the coaching API returned an empty response")

    return {"feedback": content.strip(), "model": model}
