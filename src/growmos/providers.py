"""Optional headless mode: call an LLM API directly (cron, CI, overnight loops).

growmos is agent-native — normally the CLI agent you're already running (Claude Code,
Codex, Grok…) *is* the model and no API key is needed. This module exists for unattended
runs. It is stdlib-only (urllib) and provider-neutral:

  GROWMOS_PROVIDER = anthropic | openai | xai | <any OpenAI-compatible base URL via GROWMOS_BASE_URL>
  ANTHROPIC_API_KEY / OPENAI_API_KEY / XAI_API_KEY / GROWMOS_API_KEY
  GROWMOS_EXTRACT_MODEL, GROWMOS_REASON_MODEL   (override per-stage models)

Model split follows the playbook's Table IV: a fast, cheap model for high-volume
extraction; a stronger reasoning model for resolution, summarization and querying.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

DEFAULTS: Dict[str, Dict[str, str]] = {
    "anthropic": {"base": "https://api.anthropic.com", "extract": "claude-haiku-4-5", "reason": "claude-sonnet-5",
                  "key_env": "ANTHROPIC_API_KEY"},
    "openai": {"base": "https://api.openai.com/v1", "extract": "gpt-4o-mini", "reason": "gpt-4o",
               "key_env": "OPENAI_API_KEY"},
    "xai": {"base": "https://api.x.ai/v1", "extract": "grok-3-mini", "reason": "grok-3",
            "key_env": "XAI_API_KEY"},
}


class ProviderError(Exception):
    pass


def resolve_provider(config: Optional[Dict[str, Any]] = None) -> Tuple[str, str, str, str, str]:
    """Return (provider, base_url, api_key, extract_model, reason_model) or raise ProviderError."""
    cfg = (config or {}).get("provider") or {}
    name = os.environ.get("GROWMOS_PROVIDER") or cfg.get("name") or ""
    if not name:
        for cand in ("anthropic", "openai", "xai"):
            if os.environ.get(DEFAULTS[cand]["key_env"]):
                name = cand
                break
    if not name:
        raise ProviderError(
            "no provider configured. Set GROWMOS_PROVIDER=anthropic|openai|xai (and the matching API key), "
            "or just let your CLI agent run `growmos next` — no key needed in agent-native mode."
        )
    d = DEFAULTS.get(name, DEFAULTS["openai"])
    base = os.environ.get("GROWMOS_BASE_URL") or cfg.get("base_url") or d["base"]
    key = (os.environ.get("GROWMOS_API_KEY") or os.environ.get(d["key_env"]) or cfg.get("api_key") or "")
    if not key:
        raise ProviderError(f"missing API key: set {d['key_env']} (or GROWMOS_API_KEY)")
    extract = os.environ.get("GROWMOS_EXTRACT_MODEL") or cfg.get("extract_model") or d["extract"]
    reason = os.environ.get("GROWMOS_REASON_MODEL") or cfg.get("reason_model") or d["reason"]
    return name, base.rstrip("/"), key, extract, reason


def _post(url: str, headers: Dict[str, str], body: Dict[str, Any], timeout: int = 180) -> Dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"content-type": "application/json", **headers})
    last_err: Optional[Exception] = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            txt = e.read().decode("utf-8", "replace")
            if e.code in (408, 409, 429, 500, 502, 503, 529) and attempt < 3:
                time.sleep(2 ** attempt)
                last_err = ProviderError(f"HTTP {e.code}: {txt[:300]}")
                continue
            raise ProviderError(f"HTTP {e.code}: {txt[:600]}")
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            time.sleep(2 ** attempt)
    raise ProviderError(f"request failed after retries: {last_err}")


def call_structured(prompt: str, schema: Dict[str, Any], stage: str = "reason",
                    config: Optional[Dict[str, Any]] = None, max_tokens: int = 4096) -> Dict[str, Any]:
    """Call the configured provider and return a parsed JSON object matching `schema`.

    stage: 'extract' (cheap/fast model) or 'reason' (stronger model).
    """
    name, base, key, extract_model, reason_model = resolve_provider(config)
    model = extract_model if stage == "extract" else reason_model
    if name == "anthropic":
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {"format": {"type": "json_schema", "schema": schema}},
        }
        resp = _post(f"{base}/v1/messages", {"x-api-key": key, "anthropic-version": "2023-06-01"}, body)
        if resp.get("stop_reason") == "refusal":
            raise ProviderError("model refused the request")
        text = next((b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text"), "")
        return _parse_json(text)
    # OpenAI-compatible (openai, xai, local servers)
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Respond only with JSON matching the provided schema."},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "growmos_" + stage, "schema": schema, "strict": True}},
    }
    resp = _post(f"{base}/chat/completions", {"authorization": f"Bearer {key}"}, body)
    try:
        text = resp["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise ProviderError(f"unexpected response shape: {json.dumps(resp)[:300]}")
    return _parse_json(text)


def call_text(prompt: str, config: Optional[Dict[str, Any]] = None, max_tokens: int = 2048) -> str:
    name, base, key, _, reason_model = resolve_provider(config)
    if name == "anthropic":
        body = {"model": reason_model, "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}]}
        resp = _post(f"{base}/v1/messages", {"x-api-key": key, "anthropic-version": "2023-06-01"}, body)
        return "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
    body = {"model": reason_model, "messages": [{"role": "user", "content": prompt}]}
    resp = _post(f"{base}/chat/completions", {"authorization": f"Bearer {key}"}, body)
    return resp["choices"][0]["message"]["content"]


def _parse_json(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise ProviderError("model did not return JSON")
