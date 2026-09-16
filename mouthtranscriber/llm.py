"""Shared LLM transport for HumJob's opt-in, off-machine features.

Every LLM-backed button in HumJob (Sing-Along coaching, and now the hub progress coach)
sends only a compact, PII-free numeric report to a DeepSeek chat model and renders the
reply. The transport is the same for all of them, so it lives here once instead of being
copied per feature.

Config lives in a gitignored `.env` at the project root and is read PER REQUEST (via
`load_env`), so the user can paste a key and use the feature without restarting the server.
Keys:
  DEEPSEEK_API_KEY   (required; no key -> LLMNotConfigured -> HTTP 503)
  DEEPSEEK_MODEL     (default "deepseek-flash")
  DEEPSEEK_BASE_URL  (default "https://api.deepseek.com"; OpenAI-compatible /chat/completions)

A feature may point `chat` at its own model override key (for example
DEEPSEEK_EDIT_MODEL / DEEPSEEK_CHORD_MODEL); it falls back to DEEPSEEK_MODEL and then the
default. Real environment variables override the file, so a shell export wins over `.env`.
"""

from __future__ import annotations

import json
import os
import pathlib

import httpx

DEFAULT_MODEL = "deepseek-flash"
DEFAULT_BASE_URL = "https://api.deepseek.com"
# Only these leak from the real environment into load_env's result. A feature-specific
# model override key is looked up in the parsed .env only (not overlaid from environ),
# which is fine: overrides are optional and .env is where they belong.
_CONFIG_KEYS = ("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL")
_TIMEOUT_S = 120.0
# deepseek-flash is a REASONING model: it spends completion tokens on hidden reasoning_content
# BEFORE the visible answer, and if max_tokens runs out mid-reasoning the answer comes back empty
# (finish_reason "length"). That hidden thinking is unrelated to input size, so a small task can
# still exhaust a small budget. `max_tokens` is only a CEILING - the model stops when it is done
# and you are billed on tokens actually generated, not on the cap - so we set it generously.
# 64000 is deepseek-flash's own thinking-mode default and is far above what any of these tasks
# (a coaching paragraph, a chord list, a melody JSON) ever needs, so the "length" failure
# effectively cannot happen. deepseek-flash allows up to 384000 if even more headroom is ever
# wanted; a non-reasoning override model needs almost none of this. One knob for every feature.
_MAX_TOKENS = 64000

_NOT_CONFIGURED_HINT = (
    "this feature is not configured: add DEEPSEEK_API_KEY to a .env file in the project "
    "root (copy .env.example), then try again. No restart needed."
)


class LLMNotConfigured(Exception):
    """No API key is configured; the feature is off until the user sets one."""


class LLMUpstreamError(Exception):
    """The upstream LLM API could not be reached or returned an unusable response."""


class LLMBadOutput(Exception):
    """The model returned text we could not parse into the expected structured shape."""


def load_env(path: str = ".env") -> dict:
    """Read KEY=VALUE lines from `path` into a dict; real os.environ wins over the file.

    Tiny on purpose (no python-dotenv dependency): blank lines and `#` comments are
    skipped, surrounding whitespace and one layer of matching quotes are stripped. Only
    the three DEEPSEEK_* config keys are overlaid from the environment, so the whole shell
    environment is not dragged into the result (feature-specific model overrides still come
    through because every `=` line in the file is parsed).
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


def _post_chat(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    *,
    max_tokens: int = _MAX_TOKENS,
    temperature: float = 0.7,
    json_mode: bool = False,
) -> dict:
    """POST an OpenAI-compatible chat completion and return the parsed JSON body.

    Isolated so tests can monkeypatch it (no real network in the suite). Raises
    httpx.HTTPError on transport / non-2xx responses, which callers map to LLMUpstreamError.
    The extra keyword args are keyword-only so existing positional callers (coach.py) keep
    working unchanged.
    """
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if json_mode:
        # Not all OpenAI-compatible endpoints honour this; the first real call tells you.
        # parse_json_object is the belt-and-braces fallback and is required regardless.
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    resp = httpx.post(url, json=payload, headers=headers, timeout=_TIMEOUT_S)
    resp.raise_for_status()
    return resp.json()


def chat(
    messages: list[dict],
    *,
    env_path: str = ".env",
    model_key: str = "DEEPSEEK_MODEL",
    max_tokens: int = _MAX_TOKENS,
    temperature: float = 0.7,
    json_mode: bool = False,
) -> dict:
    """Run one chat completion and return {"content": str, "model": str}, or raise.

    Loads config, resolves the model (the feature's `model_key` override, then
    DEEPSEEK_MODEL, then the default), posts, and applies the same empty-content /
    finish_reason "length" handling coach_feedback has. Raises LLMNotConfigured when no key
    is set (HTTP 503 upstream) and LLMUpstreamError on any network / API failure or an
    unusable response (HTTP 502).
    """
    cfg = load_env(env_path)
    api_key = (cfg.get("DEEPSEEK_API_KEY") or "").strip()
    if not api_key:
        raise LLMNotConfigured(_NOT_CONFIGURED_HINT)
    model = (
        (cfg.get(model_key) or "").strip()
        or (cfg.get("DEEPSEEK_MODEL") or "").strip()
        or DEFAULT_MODEL
    )
    base_url = (cfg.get("DEEPSEEK_BASE_URL") or "").strip() or DEFAULT_BASE_URL

    try:
        data = _post_chat(
            base_url, api_key, model, messages,
            max_tokens=max_tokens, temperature=temperature, json_mode=json_mode,
        )
    except httpx.HTTPError as e:
        raise LLMUpstreamError(f"could not reach the LLM API: {e}")

    try:
        choice = data["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise LLMUpstreamError("the LLM API returned an unexpected response shape")
    if not isinstance(content, str) or not content.strip():
        # A reasoning model that ran out of budget mid-thought returns empty content with
        # finish_reason "length"; say so, rather than a bare "empty response".
        if choice.get("finish_reason") == "length":
            raise LLMUpstreamError(
                "the model hit its token limit before writing a reply "
                "(raise max_tokens); please try again"
            )
        raise LLMUpstreamError("the LLM API returned an empty response")

    return {"content": content.strip(), "model": model}


def parse_json_object(text: str) -> dict:
    """Extract a single JSON object from model output, tolerant of fences and prose.

    Reasoning models sometimes wrap JSON in a ```json fence or a sentence of preamble.
    Take the substring from the first `{` to the last `}` and json.loads it. Raise
    LLMBadOutput (mapped to HTTP 502 by callers) if there is no object or it will not parse.
    """
    if not isinstance(text, str):
        raise LLMBadOutput("the model output was not text")
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0 or end < start:
        raise LLMBadOutput("no JSON object found in the model output")
    frag = text[start : end + 1]
    try:
        obj = json.loads(frag)
    except (json.JSONDecodeError, ValueError) as e:
        raise LLMBadOutput(f"could not parse JSON from the model output: {e}")
    if not isinstance(obj, dict):
        raise LLMBadOutput("the model output was not a JSON object")
    return obj
