"""Deterministic offline LLM adapter.

Not a mock that returns lorem ipsum. It returns *schema-valid, plausible*
responses derived from the prompt, so it can stand in for a real provider in:

  - the whole test suite, which must run in under a second and never touch a network
  - `DEMO_MODE` cache misses, so the demo cannot hard-fail on a provider outage
  - CI, where no API key exists

Determinism is the point: the same request always yields the same response,
keyed by a hash of the prompt. That makes agent tests assertable rather than
approximately-assertable, and it makes a failure reproducible.

Honesty rule: every response is stamped with provider `fake`, and callers
propagate that into `Provenance`. The UI shows it. A judge must never be able to
mistake a fake response for a model's -- that would be exactly the kind of
invented output CLAUDE.md rule 3 forbids.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from forge.application.ports.llm import (
    Completion,
    GenerationRequest,
    LLMPort,
    ModelCapabilities,
)
from forge.domain.enums import ModelTier
from forge.domain.provenance import TokenUsage

FAKE_PROVIDER = "fake"


def _seed(request: GenerationRequest) -> int:
    payload = "|".join(m.content for m in request.messages)
    digest = hashlib.sha256(f"{request.tier.value}:{request.pack_id}:{payload}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _approx_tokens(text: str) -> int:
    """Rough token count. Good enough for budget accounting in tests."""
    return max(len(text) // 4, 1)


class FakeAdapter(LLMPort):
    """Deterministic, offline, schema-aware."""

    def __init__(self, *, latency_ms: int = 5) -> None:
        self._latency_ms = latency_ms
        self._calls = 0

    @property
    def call_count(self) -> int:
        """Lets tests assert how many model calls a graph run actually made."""
        return self._calls

    async def generate(self, request: GenerationRequest) -> Completion:
        started = time.perf_counter()
        self._calls += 1
        seed = _seed(request)

        text = (
            json.dumps(self._synthesize(request, seed), indent=2)
            if request.response_schema
            else self._prose(request, seed)
        )
        prompt_text = "".join(m.content for m in request.messages)

        return Completion(
            text=text,
            tier_requested=request.tier,
            tier_used=request.tier,
            provider_used=FAKE_PROVIDER,
            model_used=f"fake-{request.tier.value}-v1",
            latency_ms=max(int((time.perf_counter() - started) * 1000), self._latency_ms),
            tokens=TokenUsage(
                input_tokens=_approx_tokens(prompt_text),
                output_tokens=_approx_tokens(text),
                cost_usd=0.0,
            ),
        )

    def _synthesize(self, request: GenerationRequest, seed: int) -> dict[str, Any]:
        """Build an object satisfying the requested JSON Schema.

        Walks the schema rather than returning a canned dict, so a contract
        change surfaces here immediately instead of at integration time.
        """
        schema = request.response_schema or {}
        return self._from_schema(schema, seed, request)

    def _from_schema(
        self, schema: dict[str, Any], seed: int, request: GenerationRequest, depth: int = 0
    ) -> Any:
        if depth > 6:
            return None
        kind = schema.get("type")

        if enum := schema.get("enum"):
            return enum[seed % len(enum)]

        if kind == "object":
            props: dict[str, Any] = schema.get("properties") or {}
            required = set(schema.get("required") or props.keys())
            return {
                key: self._from_schema(sub, seed + i, request, depth + 1)
                for i, (key, sub) in enumerate(props.items())
                if key in required
            }
        if kind == "array":
            item = schema.get("items") or {"type": "string"}
            count = 1 + (seed % 2)
            return [self._from_schema(item, seed + i, request, depth + 1) for i in range(count)]
        if kind == "number":
            lo = float(schema.get("minimum", 0.0))
            hi = float(schema.get("maximum", 1.0))
            return round(lo + (hi - lo) * ((seed % 1000) / 1000.0), 3)
        if kind == "integer":
            lo = int(schema.get("minimum", 0))
            hi = int(schema.get("maximum", 10))
            return lo + (seed % max(hi - lo + 1, 1))
        if kind == "boolean":
            return bool(seed % 2)
        return self._string_for(schema, request)

    def _string_for(self, schema: dict[str, Any], request: GenerationRequest) -> str:
        """A string that reads like the field it fills, so fixtures stay legible."""
        hint = str(schema.get("description", "")).lower()
        if "reason" in hint or "explanation" in hint:
            return (
                "[FAKE ADAPTER] Deterministic stand-in response. No model was called. "
                f"Tier requested: {request.tier.value}."
            )
        return "[FAKE]"

    def _prose(self, request: GenerationRequest, seed: int) -> str:
        last = request.messages[-1].content if request.messages else ""
        return (
            "[FAKE ADAPTER] No model was called; this is a deterministic offline "
            f"response (tier={request.tier.value}, seed={seed % 100000}). "
            f"Prompt began: {last[:120]!r}"
        )

    async def capabilities(self, tier: ModelTier) -> ModelCapabilities:
        return ModelCapabilities(
            provider=FAKE_PROVIDER,
            model=f"fake-{tier.value}-v1",
            reachable=True,
            # Deliberately reports NO vision. The fake must not paper over the
            # capability gap the real environment has, or tests would pass on a
            # code path that cannot run in production.
            supports_vision=False,
            supports_function_calling=True,
            supports_json_mode=True,
            supports_streaming=False,
            max_context=32_000,
            measured_p50_latency_ms=self._latency_ms,
        )

    async def probe(self) -> tuple[ModelCapabilities, ...]:
        return tuple([await self.capabilities(t) for t in (ModelTier.REASONING, ModelTier.FAST)])

    def is_tier_available(self, tier: ModelTier) -> bool:
        return tier is not ModelTier.VISION
