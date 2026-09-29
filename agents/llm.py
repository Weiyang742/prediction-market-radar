"""LLM access layer with two backends.

``OpenAICompatLLM`` calls an OpenAI-compatible chat/completions endpoint with
json_object structured output (DeepSeek does not support strict json_schema).
``MockLLM`` is a deterministic, key-free backend used by ``--mock``.  Both obey
the no-silent-failure rule: any call error raises ``LLMError`` (never returns a
fabricated "empty" result).
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import re
from typing import Any

import httpx

from data.config import Config

log = logging.getLogger(__name__)


def _stable_seed(text: str) -> int:
    return int(hashlib.md5(text.encode()).hexdigest()[:8], 16)


class LLMError(RuntimeError):
    pass


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _extract_json(text: Any) -> dict[str, Any]:
    """Parse a model response into a JSON object.

    Free/domestic models sometimes ignore ``response_format`` and emit markdown
    fences or prose around the object; tolerate those instead of failing.
    """
    if isinstance(text, dict):
        return text
    raw = str(text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        parsed = json.loads(raw)
    except ValueError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end <= start:
            raise
        parsed = json.loads(raw[start:end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("LLM response is not a JSON object")
    return parsed


class OpenAICompatLLM:
    def __init__(self, config: Config, model: str | None = None,
                 base_url: str | None = None, api_key: str | None = None) -> None:
        self.config = config
        self.model_name = model or config.model
        self.base_url = (base_url or config.llm_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else config.llm_api_key
        self._client = httpx.Client(timeout=config.llm_timeout_s)

    def close(self) -> None:
        self._client.close()

    def is_available(self) -> bool:
        return bool(self.api_key)

    def complete(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise LLMError("LLM_API_KEY is not set")
        # DeepSeek (and OpenAI) support json_object; strict json_schema is not
        # available on DeepSeek.  Enforce the shape via the prompt and retry
        # once on malformed output (V3.1 §6.3: one retry, then rule_fallback).
        # Some free/domestic models reject ``response_format``; drop it on the
        # retry and rely on _extract_json to recover the object from the text.
        keys = sorted(schema.get("properties", {}))
        user_prompt = (user + "\n\nRespond with a JSON object only (no markdown, no prose)."
                       f" Required keys: {keys}")
        for attempt in range(2):
            payload: dict[str, Any] = {
                "model": self.model_name,
                "temperature": self.config.llm_temperature,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_prompt},
                ],
            }
            if attempt == 0:
                payload["response_format"] = {"type": "json_object"}
            try:
                resp = self._client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
                return _extract_json(content)
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
                if attempt == 1:
                    raise LLMError(f"LLM call failed: {exc}") from exc
        raise LLMError("unreachable")


class MockLLM:
    """Deterministic mock.  Produces P(YES) around a base price embedded in the
    prompt as ``BASE_P_YES=<float>``; other schema fields get deterministic
    placeholder values.  Clearly not a real model."""

    model_name = "mock"

    def is_available(self) -> bool:
        return True

    def close(self) -> None:
        pass

    def complete(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        props = schema.get("properties", {})
        base_match = re.search(r"BASE_P_YES=([0-9.]+)", user)
        base = float(base_match.group(1)) if base_match else 0.5
        rng = random.Random(_stable_seed(user))
        out: dict[str, Any] = {}
        p_yes = _clamp(base + rng.uniform(-0.06, 0.06))
        if "p_yes" in props:
            out["p_yes"] = round(p_yes, 4)
        if "reasoning" in props:
            out["reasoning"] = f"[MOCK] reasoning for base {base:.2f}"
        if "direction" in props:
            out["direction"] = "YES" if p_yes >= 0.5 else "NO"
        if "confidence" in props:
            out["confidence"] = round(rng.uniform(50, 90), 1)
        if "abstain" in props:
            out["abstain"] = False
        if "abstain_reason" in props:
            out["abstain_reason"] = None
        if "thesis" in props:
            out["thesis"] = f"[MOCK] thesis for base {base:.2f}"
        if "key_risks" in props:
            out["key_risks"] = ["[MOCK] risk placeholder"]
        if "invalidated_if" in props:
            out["invalidated_if"] = None
        if "same_event" in props:
            out["same_event"] = True
        if "lesson" in props:
            out["lesson"] = "[MOCK] lesson placeholder"
        if "premise" in props:
            out["premise"] = f"[MOCK] premise for base {base:.2f}"
        if "decisive_point" in props:
            out["decisive_point"] = "[MOCK] decisive point"
        if "summary" in props:
            out["summary"] = f"[MOCK] summary for base {base:.2f}"
        if "verdict" in props:
            out["verdict"] = "support"
        if "rationale" in props:
            out["rationale"] = "[MOCK] rationale"
        return out


def make_llm(config: Config) -> Any:
    if config.mock:
        return MockLLM()
    return OpenAICompatLLM(config)


def make_second_llm(config: Config) -> Any | None:
    """Second-model anchor for dual-model abstention (DeepFund trading-personality
    finding).

    Returns None when unavailable: mock mode, no API key, or the second model
    plus endpoint are identical to the primary (which would be a no-op double
    spend).  The second model may live on its own endpoint/key via
    ``second_model_base_url`` / ``second_model_api_key``.
    """
    if config.mock:
        return None
    name = (config.second_model or "").strip()
    if not name:
        return None
    api_key = config.second_model_api_key or config.llm_api_key
    if not api_key:
        log.warning("dual-model anchor disabled: no API key for %s", name)
        return None
    base = config.second_model_base_url or config.llm_base_url
    if name == config.model and base.rstrip("/") == config.llm_base_url.rstrip("/"):
        log.warning("dual-model anchor disabled: second model identical to primary (%s)", name)
        return None
    return OpenAICompatLLM(config, model=name, base_url=base, api_key=api_key)
