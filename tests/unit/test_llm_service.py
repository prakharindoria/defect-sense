"""LLM configuration, fallback chain, cache and circuit breaker.

The behaviour under test is mostly about *honesty under failure*: that a
degraded answer is still returned but is marked degraded, that a cached answer
reports its age, and that the service refuses to invent prose when every
provider is gone.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from forge.application.ports.llm import (
    GenerationRequest,
    LLMUnavailableError,
    Message,
)
from forge.domain.enums import BreakerState, DegradationKind, ModelTier
from forge.infrastructure.llm.config import ModelConfigError, load
from forge.infrastructure.llm.fake import FakeAdapter
from forge.infrastructure.llm.service import LLMService, ResponseCache
from forge.infrastructure.resilience.breaker import CircuitBreaker, CircuitOpenError

CONFIG = Path("config/models.yaml")


def req(text: str = "hello", *, tier: ModelTier = ModelTier.FAST, **kw) -> GenerationRequest:  # noqa: ANN003
    return GenerationRequest(tier=tier, messages=(Message("user", text),), **kw)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def test_unconfigured_providers_are_skipped_with_a_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider whose env var is unset drops out of the chain, and says why.

    "Why isn't it using Anthropic?" must have an answer on /admin rather than
    requiring someone to read YAML at 3am.
    """
    for var in ("TCS_API_KEY", "ANTHROPIC_API_KEY", "OLLAMA_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    config = load(CONFIG)

    skipped = dict(config.skipped_providers)
    assert "tcs" in skipped
    assert "anthropic" in skipped
    # TCS is gated on the KEY, not the URL. The URL has a working default, so
    # gating on it would put a guaranteed 401 at the head of every chain.
    assert "TCS_API_KEY" in skipped["tcs"]
    # Only the always-available fake survives.
    assert [p.name for p in config.tier(ModelTier.FAST).chain] == ["fake"]


def test_env_vars_expand_into_model_names(monkeypatch: pytest.MonkeyPatch) -> None:
    # Isolate from the developer's shell: with a real TCS key exported, TCS
    # heads the chain and this assertion would be about the wrong provider.
    monkeypatch.delenv("TCS_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("OLLAMA_FAST_MODEL", "my-custom-model:v9")
    config = load(CONFIG)
    assert config.tier(ModelTier.FAST).chain[0].model == "my-custom-model:v9"


def test_vision_tier_has_no_provider_unless_one_is_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unset vision model means no vision -- never an optimistic default.

    ADR-0003: claiming a capability the endpoint lacks is worse than lacking it,
    because the pipeline routes into a step that cannot run.
    """
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.delenv("OLLAMA_VISION_MODEL", raising=False)
    monkeypatch.delenv("TCS_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config = load(CONFIG)

    assert config.tier(ModelTier.VISION).chain == ()
    assert config.tier(ModelTier.VISION).terminal_fallback == "structured_cv_descriptor"
    assert not LLMService(config).is_tier_available(ModelTier.VISION)


def test_missing_config_file_is_an_error() -> None:
    with pytest.raises(ModelConfigError, match="not found"):
        load("config/does-not-exist.yaml")


def test_unknown_tier_is_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "models.yaml"
    bad.write_text(
        textwrap.dedent("""
            providers:
              fake: {kind: fake, enabled_if: null}
            tiers:
              telepathy:
                chain: [{provider: fake, model: x}]
        """),
        encoding="utf-8",
    )
    with pytest.raises(ModelConfigError, match="unknown tier"):
        load(bad)


# ---------------------------------------------------------------------------
# Fake adapter
# ---------------------------------------------------------------------------
async def test_fake_adapter_is_deterministic() -> None:
    """Same request, same answer -- this is what makes agent tests assertable."""
    adapter = FakeAdapter()
    a = await adapter.generate(req("analyse this fastener"))
    b = await adapter.generate(req("analyse this fastener"))
    assert a.text == b.text
    assert (await adapter.generate(req("a different prompt"))).text != a.text


async def test_fake_adapter_output_is_identifiable_as_fake() -> None:
    """A judge must never mistake a stand-in response for a model's."""
    completion = await FakeAdapter().generate(req())
    assert completion.provider_used == "fake"
    assert "FAKE" in completion.text


async def test_fake_adapter_satisfies_a_requested_schema() -> None:
    """It walks the schema rather than returning a canned dict, so a contract
    change surfaces here instead of at integration time."""
    schema = {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["pass", "defect", "escalate"]},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "fusion_only": {"type": "boolean"},
            "reasoning": {"type": "string", "description": "the reasoning"},
        },
        "required": ["verdict", "confidence", "fusion_only", "reasoning"],
    }
    import json  # noqa: PLC0415

    payload = json.loads((await FakeAdapter().generate(req(response_schema=schema))).text)
    assert payload["verdict"] in {"pass", "defect", "escalate"}
    assert 0.0 <= payload["confidence"] <= 1.0
    assert isinstance(payload["fusion_only"], bool)


async def test_fake_adapter_reports_no_vision() -> None:
    """The fake must not paper over the real environment's capability gap."""
    adapter = FakeAdapter()
    assert not (await adapter.capabilities(ModelTier.FAST)).supports_vision
    assert not adapter.is_tier_available(ModelTier.VISION)


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------
async def test_breaker_opens_after_threshold_failures() -> None:
    b = CircuitBreaker("t", failure_threshold=3, open_duration_seconds=60)
    for _ in range(3):
        await b.before_call()
        await b.on_failure("boom")
    assert b.state() is BreakerState.OPEN
    with pytest.raises(CircuitOpenError, match="is open"):
        await b.before_call()


async def test_breaker_half_opens_then_closes_on_success() -> None:
    clock = [0.0]
    b = CircuitBreaker("t", failure_threshold=1, success_threshold=2, open_duration_seconds=30)
    b._now = lambda: clock[0]  # noqa: SLF001

    await b.before_call()
    await b.on_failure()
    assert b.state() is BreakerState.OPEN

    clock[0] = 31.0
    assert b.state() is BreakerState.HALF_OPEN
    for _ in range(2):
        await b.before_call()
        await b.on_success()
    assert b.state() is BreakerState.CLOSED


async def test_half_open_failure_reopens_immediately() -> None:
    """A dependency that fails its trial does not get a second trial."""
    clock = [0.0]
    b = CircuitBreaker("t", failure_threshold=1, open_duration_seconds=30)
    b._now = lambda: clock[0]  # noqa: SLF001

    await b.before_call()
    await b.on_failure()
    clock[0] = 31.0
    await b.before_call()          # the one permitted probe
    await b.on_failure("still down")
    assert b.state() is BreakerState.OPEN
    assert b.retry_after_seconds > 0


async def test_half_open_admits_only_the_permitted_number_of_probes() -> None:
    """Releasing full load the instant the timer expires re-kills a recovering
    dependency. Half-open is a trickle, not a gate opening."""
    clock = [0.0]
    b = CircuitBreaker("t", failure_threshold=1, open_duration_seconds=10, half_open_max_calls=1)
    b._now = lambda: clock[0]  # noqa: SLF001

    await b.before_call()
    await b.on_failure()
    clock[0] = 11.0
    await b.before_call()
    with pytest.raises(CircuitOpenError):
        await b.before_call()


# ---------------------------------------------------------------------------
# Service: chain, cache, degradation
# ---------------------------------------------------------------------------
def _fake_only_config(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN202
    for var in ("TCS_API_KEY", "ANTHROPIC_API_KEY", "OLLAMA_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    return load(CONFIG)


async def test_service_falls_through_to_the_fake_and_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = LLMService(_fake_only_config(monkeypatch))
    completion = await service.generate(req("hello"))
    assert completion.provider_used == "fake"
    assert completion.text


async def test_cache_hit_reports_its_age_and_is_marked_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cached data is still served -- but never dressed up as fresh."""
    service = LLMService(_fake_only_config(monkeypatch))
    await service.generate(req("same question"))
    second = await service.generate(req("same question"))

    assert second.from_cache
    assert second.cache_age_seconds is not None
    assert DegradationKind.CACHE_SERVED in second.degradations


async def test_cache_key_separates_packs(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same prompt under a different pack is a different question."""
    a = req("what is the torque spec?", pack_id="pack_a")
    b = req("what is the torque spec?", pack_id="pack_b")
    assert ResponseCache.key(a) != ResponseCache.key(b)

    service = LLMService(_fake_only_config(monkeypatch))
    await service.generate(a)
    assert not (await service.generate(b)).from_cache


async def test_exhausted_chain_raises_rather_than_inventing_prose(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When every provider is gone the service must NOT make something up.

    A plausible sentence with no model behind it is exactly the invented output
    CLAUDE.md rule 3 forbids. The terminal fallback (rule engine / structured CV
    descriptor) belongs to the caller, which knows how to be honest about it.
    """
    cfg = tmp_path / "models.yaml"
    cfg.write_text(
        textwrap.dedent("""
            providers:
              ghost: {kind: openai_compatible, base_url: "", api_key: "", enabled_if: null}
            tiers:
              fast:
                chain: [{provider: ghost, model: ""}]
                terminal_fallback: deterministic_rule_engine
                budget: {timeout_ms: 100}
        """),
        encoding="utf-8",
    )
    service = LLMService(load(cfg))
    with pytest.raises(LLMUnavailableError, match="terminal fallback"):
        await service.generate(req())


async def test_stats_expose_what_admin_needs(monkeypatch: pytest.MonkeyPatch) -> None:
    service = LLMService(_fake_only_config(monkeypatch))
    await service.generate(req())
    stats = service.stats()

    assert "cache" in stats
    assert "tiers" in stats
    assert any(s["provider"] == "tcs" for s in stats["skipped_providers"])  # type: ignore[index]
