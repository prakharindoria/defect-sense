"""LLM port.

Callers ask for a **tier**, never a model. `CLAUDE.md` rule 7: never hardcode a
model ID. Which provider and which model serve a tier is decided by
`config/models.yaml` and the boot-time capability probe, so swapping providers
is a config change and the agents never learn about it.

The vision tier is **optional by design**. `capabilities()` reports what is
actually available and callers branch on that rather than assuming. On the build
machine today the only provider is Ollama, which has no vision model, so
`supports_vision` is false and the structured CV descriptor path runs. That is
the designed path, not a degradation (ADR-0003).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

# Imported at runtime, not under TYPE_CHECKING, because these appear in
# dataclass FIELD annotations. Anything that introspects these types at runtime
# -- serialisation, FastAPI response models, typing.get_type_hints -- needs the
# names to resolve. Only types used exclusively in method signatures belong in a
# TYPE_CHECKING block. tests/architecture/test_layering.py asserts this holds.
from forge.domain.enums import DegradationKind, ModelTier
from forge.domain.provenance import TokenUsage


class LLMError(Exception):
    """Base for provider failures. Distinguishing these drives retry policy."""


class LLMTimeoutError(LLMError):
    """Retryable."""


class LLMRateLimitedError(LLMError):
    """Retryable with backoff."""


class LLMUnavailableError(LLMError):
    """Not retryable on this provider; the chain should fall through."""


class LLMSchemaError(LLMError):
    """The model returned something that does not match the requested schema.

    Retried once with a repair prompt, then failed closed. We never coerce a
    malformed structured output into a verdict.
    """


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    """What an endpoint can actually do, measured rather than assumed.

    Rendered on /admin. A judge asking "what if your model provider changes?"
    gets a live matrix instead of a slide.
    """

    provider: str
    model: str
    reachable: bool
    supports_vision: bool = False
    supports_function_calling: bool = False
    supports_json_mode: bool = False
    supports_streaming: bool = False
    max_context: int = 0
    measured_p50_latency_ms: int = 0
    error: str | None = None


@dataclass(frozen=True, slots=True)
class Message:
    role: str          # "system" | "user" | "assistant"
    content: str
    # Base64 data URIs. Ignored by providers without vision; callers must check
    # capabilities() first rather than hoping.
    images: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Completion:
    """A model response plus everything needed to attribute it.

    `tier_used` and `provider_used` may differ from what was requested when the
    chain fell through or the budget forced a downgrade. `degradations` says so,
    and that list ends up in the trace and on the provenance strip -- a demoted
    response is still returned, but never as though it were the nominal one.
    """

    text: str
    tier_requested: ModelTier
    tier_used: ModelTier
    provider_used: str
    model_used: str
    latency_ms: int
    tokens: TokenUsage
    degradations: tuple[DegradationKind, ...] = ()
    from_cache: bool = False
    cache_age_seconds: int | None = None
    finish_reason: str = "stop"

    @property
    def was_truncated(self) -> bool:
        """Truncation is a degradation, not a detail. Surfaced, never swallowed."""
        return self.finish_reason == "length"


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    tier: ModelTier
    messages: tuple[Message, ...]
    # JSON Schema. When set, the adapter requests structured output if the
    # provider supports it and validates the result either way.
    response_schema: dict[str, object] | None = None
    max_output_tokens: int | None = None
    temperature: float = 0.0
    # Cache key component: identical prompts under different packs are different
    # questions and must not share a cached answer.
    pack_id: str = ""
    # Correlation ID, propagated into logs and the trace.
    correlation_id: str = ""
    stop: tuple[str, ...] = field(default=())


class LLMPort(ABC):
    """Every LLM call in the system goes through this."""

    @abstractmethod
    async def generate(self, request: GenerationRequest) -> Completion:
        """Run a completion, applying the tier's fallback chain.

        Must never raise for a provider-level failure that the chain can absorb:
        fall through to the next provider, record a `DegradationKind`, and return
        a Completion. Raise only when the whole chain including the terminal
        fallback is exhausted.
        """

    @abstractmethod
    async def capabilities(self, tier: ModelTier) -> ModelCapabilities:
        """Measured capabilities of whichever provider currently serves `tier`."""

    @abstractmethod
    async def probe(self) -> tuple[ModelCapabilities, ...]:
        """Probe every configured endpoint. Run at boot, cached, shown on /admin."""

    @abstractmethod
    def is_tier_available(self, tier: ModelTier) -> bool:
        """Whether any provider can serve this tier.

        Callers branch on this instead of catching an exception -- notably the
        vision tier, whose absence is an expected configuration rather than an
        error condition.
        """
