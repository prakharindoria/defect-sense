"""The LLM service: fallback chain, response cache, circuit breakers.

This is the only class in the system that knows a model provider exists. Agents
ask for a tier and get a `Completion`; which provider answered, whether it was
the first choice, and whether the answer came from cache are recorded on the
result rather than hidden by it.

The contract that matters (`LLMPort.generate`): a provider-level failure the
chain can absorb must NOT raise. It falls through, records a `DegradationKind`,
and returns. Those degradations reach the trace and the provenance strip, so a
demoted answer is still shown -- but never as though it were the nominal one.
That is CLAUDE.md rule 4 made structural instead of remembered.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING

from forge.application.ports.llm import (
    Completion,
    GenerationRequest,
    LLMError,
    LLMPort,
    LLMUnavailableError,
    ModelCapabilities,
)
from forge.domain.enums import DegradationKind, ModelTier
from forge.infrastructure.llm.fake import FakeAdapter
from forge.infrastructure.llm.openai_compat import OpenAICompatAdapter
from forge.infrastructure.resilience.breaker import REGISTRY, CircuitOpenError

if TYPE_CHECKING:
    from forge.infrastructure.llm.config import ModelConfig, ProviderConfig

_log = logging.getLogger(__name__)

# Tier downgrade order when a budget is exceeded or a chain is exhausted.
_DOWNGRADE: dict[ModelTier, ModelTier | None] = {
    ModelTier.REASONING: ModelTier.FAST,
    ModelTier.FAST: None,
    ModelTier.VISION: None,
}


@dataclass(slots=True)
class _CacheEntry:
    completion: Completion
    stored_at: float


class ResponseCache:
    """Keyed on (tier, prompt_hash, pack_id).

    pack_id is part of the key deliberately: the same prompt asked under a
    different Use Case Pack is a different question, and sharing an answer
    across packs would be a correctness bug disguised as a cache hit.
    """

    def __init__(self, *, ttl_seconds: int) -> None:
        self._ttl = ttl_seconds
        self._entries: dict[str, _CacheEntry] = {}
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key(request: GenerationRequest) -> str:
        payload = "\n".join(f"{m.role}:{m.content}" for m in request.messages)
        digest = hashlib.sha256(payload.encode()).hexdigest()[:32]
        return f"{request.tier.value}:{request.pack_id}:{digest}"

    def get(self, request: GenerationRequest) -> Completion | None:
        entry = self._entries.get(self.key(request))
        if entry is None:
            self.misses += 1
            return None
        age = time.time() - entry.stored_at
        if age > self._ttl:
            del self._entries[self.key(request)]
            self.misses += 1
            return None
        self.hits += 1
        return replace(
            entry.completion,
            from_cache=True,
            cache_age_seconds=int(age),
            degradations=(*entry.completion.degradations, DegradationKind.CACHE_SERVED),
        )

    def put(self, request: GenerationRequest, completion: Completion) -> None:
        self._entries[self.key(request)] = _CacheEntry(completion, time.time())

    def stats(self) -> dict[str, int | float]:
        total = self.hits + self.misses
        return {
            "entries": len(self._entries),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 3) if total else 0.0,
        }


class LLMService(LLMPort):
    def __init__(self, config: ModelConfig, *, demo_mode: bool = False) -> None:
        self._config = config
        self._demo_mode = demo_mode
        ttl = (
            config.cache.demo_mode_ttl_seconds if demo_mode else config.cache.ttl_seconds
        )
        self._cache = ResponseCache(ttl_seconds=ttl) if config.cache.enabled else None
        self._fake = FakeAdapter()
        self._probed: dict[str, ModelCapabilities] = {}

    # -- adapters ----------------------------------------------------------
    def _adapter(self, provider: ProviderConfig, timeout_ms: int):  # noqa: ANN202
        if provider.kind == "fake":
            return self._fake
        return OpenAICompatAdapter(provider, timeout_ms=timeout_ms)

    def _breaker(self, provider: ProviderConfig):  # noqa: ANN202
        b = self._config.breaker
        return REGISTRY.get(
            f"llm:{provider.name}",
            failure_threshold=b.failure_threshold,
            success_threshold=b.success_threshold,
            open_duration_seconds=b.open_duration_seconds,
            half_open_max_calls=b.half_open_max_calls,
        )

    # -- generation --------------------------------------------------------
    async def generate(self, request: GenerationRequest) -> Completion:
        if self._cache and (hit := self._cache.get(request)):
            return hit

        completion, degradations = await self._run_chain(request, request.tier, [])

        if degradations:
            completion = replace(
                completion, degradations=(*completion.degradations, *degradations)
            )
        if self._cache:
            self._cache.put(request, completion)
        return completion

    async def _run_chain(
        self, request: GenerationRequest, tier: ModelTier, degradations: list[DegradationKind]
    ) -> tuple[Completion, list[DegradationKind]]:
        """Walk one tier's chain, then downgrade, then fail."""
        tier_config = self._config.tier(tier)
        budget = tier_config.budget

        for provider in tier_config.chain:
            breaker = self._breaker(provider)
            try:
                await breaker.before_call()
            except CircuitOpenError:
                degradations.append(DegradationKind.CIRCUIT_OPEN)
                _log.warning("llm.circuit_open", extra={"provider": provider.name})
                continue

            adapter = self._adapter(provider, budget.timeout_ms)
            call_request = (
                request
                if request.max_output_tokens
                else replace(request, max_output_tokens=budget.max_output_tokens)
            )
            try:
                if provider.kind == "fake":
                    completion = await adapter.generate(call_request)
                else:
                    caps = self._probed.get(provider.name)
                    completion = await adapter.generate(
                        call_request,
                        json_mode=caps.supports_json_mode if caps else True,
                    )
            except LLMError as exc:
                await breaker.on_failure(str(exc))
                _log.warning(
                    "llm.provider_failed",
                    extra={"provider": provider.name, "error": str(exc)},
                )
                continue
            else:
                await breaker.on_success()
                if completion.was_truncated:
                    degradations.append(DegradationKind.BUDGET_EXCEEDED)
                if tier is not request.tier:
                    degradations.append(DegradationKind.LLM_TIER_DOWNGRADE)
                    completion = replace(completion, tier_requested=request.tier)
                return completion, degradations

        # Chain exhausted for this tier. Try the declared downgrade.
        if (lower := _DOWNGRADE.get(tier)) is not None and lower in self._config.tiers:
            _log.warning("llm.tier_downgrade", extra={"from": tier.value, "to": lower.value})
            return await self._run_chain(request, lower, degradations)

        # Everything is exhausted. The terminal fallback is the CALLER's job --
        # a deterministic rule engine or the structured CV descriptor. We do not
        # fabricate prose here, because a plausible sentence with no model behind
        # it is exactly the invented output CLAUDE.md rule 3 forbids.
        degradations.append(DegradationKind.LLM_UNAVAILABLE)
        raise LLMUnavailableError(
            f"every provider for tier '{request.tier.value}' is exhausted; "
            f"caller must apply terminal fallback "
            f"'{self._config.tier(request.tier).terminal_fallback}'"
        )

    # -- capabilities ------------------------------------------------------
    async def capabilities(self, tier: ModelTier) -> ModelCapabilities:
        tier_config = self._config.tier(tier)
        for provider in tier_config.chain:
            if caps := self._probed.get(provider.name):
                if caps.reachable:
                    return caps
            else:
                return await self._probe_provider(provider)
        return ModelCapabilities(
            provider="none", model="", reachable=False,
            error=f"no configured provider for tier '{tier.value}'",
        )

    async def _probe_provider(self, provider: ProviderConfig) -> ModelCapabilities:
        if provider.kind == "fake":
            caps = await self._fake.capabilities(ModelTier.FAST)
            caps = replace(caps, provider=provider.name, model=provider.model)
        else:
            adapter = OpenAICompatAdapter(provider, timeout_ms=self._config.probe_timeout_ms)
            caps = await adapter.probe(timeout_ms=self._config.probe_timeout_ms)
        self._probed[provider.name] = caps
        return caps

    async def probe(self) -> tuple[ModelCapabilities, ...]:
        """Probe every distinct configured provider once."""
        seen: dict[str, ProviderConfig] = {}
        for tier_config in self._config.tiers.values():
            for provider in tier_config.chain:
                seen.setdefault(f"{provider.name}:{provider.model}", provider)
        return tuple([await self._probe_provider(p) for p in seen.values()])

    def is_tier_available(self, tier: ModelTier) -> bool:
        """Whether a tier can be served.

        For the vision tier this consults the probe result, because a configured
        vision model that is not actually installed must report unavailable --
        that is precisely the case on the current build machine.
        """
        if tier not in self._config.tiers:
            return False
        tier_config = self._config.tier(tier)
        if not tier_config.has_provider:
            return False
        if tier_config.required_capability == "supports_vision":
            return any(
                (caps := self._probed.get(p.name)) and caps.reachable and caps.supports_vision
                for p in tier_config.chain
            )
        return True

    # -- introspection for /admin -----------------------------------------
    def stats(self) -> dict[str, object]:
        return {
            "demo_mode": self._demo_mode,
            "cache": self._cache.stats() if self._cache else {"enabled": False},
            "breakers": [asdict(b) for b in REGISTRY.snapshots()],
            "skipped_providers": [
                {"provider": n, "reason": r} for n, r in self._config.skipped_providers
            ],
            "tiers": {
                t.value: {
                    "chain": [f"{p.name}:{p.model}" for p in tc.chain],
                    "terminal_fallback": tc.terminal_fallback,
                    "available": self.is_tier_available(t),
                }
                for t, tc in self._config.tiers.items()
            },
        }
