"""Load and resolve `config/models.yaml`.

Two jobs, both about not hardcoding model IDs (CLAUDE.md rule 7):

1. Expand `${VAR}` and `${VAR:-default}` references from the environment.
2. Drop providers whose `enabled_if` env var is unset, so a tier's chain
   contains only providers that could actually answer.

A tier whose chain empties out is not an error. It resolves to its declared
terminal fallback -- the deterministic rule engine, or the structured CV
descriptor for the vision tier -- and that is recorded as a degradation rather
than raised as an exception. Missing configuration should reduce capability
visibly, never stop the line.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from forge.domain.enums import ModelTier

# ${VAR} or ${VAR:-default}
_REF = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")

DEFAULT_CONFIG_PATH = Path("config/models.yaml")


class ModelConfigError(Exception):
    """The config file is missing or structurally invalid. Not recoverable."""


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    name: str
    kind: str                    # "openai_compatible" | "anthropic" | "fake"
    model: str
    base_url: str | None = None
    api_key: str | None = None

    @property
    def is_configured(self) -> bool:
        """Whether this entry has enough to attempt a call."""
        if self.kind == "fake":
            return True
        if not self.model:
            return False
        if self.kind == "openai_compatible":
            return bool(self.base_url)
        return bool(self.api_key)


@dataclass(frozen=True, slots=True)
class Budget:
    max_input_tokens: int = 12_000
    max_output_tokens: int = 2_000
    timeout_ms: int = 8_000
    max_cost_usd_per_call: float = 0.05


@dataclass(frozen=True, slots=True)
class TierConfig:
    tier: ModelTier
    chain: tuple[ProviderConfig, ...]
    terminal_fallback: str
    budget: Budget
    required_capability: str | None = None

    @property
    def has_provider(self) -> bool:
        return any(p.is_configured for p in self.chain)


@dataclass(frozen=True, slots=True)
class CacheConfig:
    enabled: bool = True
    ttl_seconds: int = 3_600
    demo_mode_ttl_seconds: int = 604_800
    backend: str = "memory"


@dataclass(frozen=True, slots=True)
class BreakerConfig:
    failure_threshold: int = 5
    success_threshold: int = 2
    open_duration_seconds: int = 30
    half_open_max_calls: int = 1


@dataclass(frozen=True, slots=True)
class ModelConfig:
    tiers: dict[ModelTier, TierConfig]
    cache: CacheConfig
    breaker: BreakerConfig
    probe_on_boot: bool = True
    probe_timeout_ms: int = 6_000
    # Providers named in the file but skipped because `enabled_if` was unset.
    # Surfaced on /admin so "why is Anthropic not being used?" has an answer.
    skipped_providers: tuple[tuple[str, str], ...] = field(default=())

    def tier(self, tier: ModelTier) -> TierConfig:
        if tier not in self.tiers:
            raise ModelConfigError(f"tier '{tier.value}' is not declared in models.yaml")
        return self.tiers[tier]


def _expand(value: Any) -> Any:
    """Resolve ${VAR} / ${VAR:-default} against the environment."""
    if not isinstance(value, str):
        return value

    def sub(m: re.Match[str]) -> str:
        name, default = m.group(1), m.group(2)
        return os.environ.get(name) or (default if default is not None else "")

    return _REF.sub(sub, value)


def load(path: Path | str = DEFAULT_CONFIG_PATH) -> ModelConfig:
    """Read, expand and validate the model configuration."""
    p = Path(path)
    if not p.exists():
        raise ModelConfigError(f"model config not found at {p.resolve()}")

    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ModelConfigError(f"{p}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ModelConfigError(f"{p}: expected a mapping at the top level")

    provider_defs: dict[str, dict[str, Any]] = raw.get("providers") or {}
    skipped: list[tuple[str, str]] = []

    def build(entry: dict[str, Any]) -> ProviderConfig | None:
        name = entry.get("provider", "")
        definition = provider_defs.get(name)
        if definition is None:
            raise ModelConfigError(f"tier references unknown provider '{name}'")

        gate = definition.get("enabled_if")
        if gate and not os.environ.get(str(gate)):
            skipped.append((name, f"{gate} is not set"))
            return None

        return ProviderConfig(
            name=name,
            kind=str(definition.get("kind", "openai_compatible")),
            model=str(_expand(entry.get("model", ""))).strip(),
            base_url=(_expand(definition.get("base_url")) or None),
            api_key=(_expand(definition.get("api_key")) or None),
        )

    tiers: dict[ModelTier, TierConfig] = {}
    for tier_name, spec in (raw.get("tiers") or {}).items():
        try:
            tier = ModelTier(tier_name)
        except ValueError:
            raise ModelConfigError(
                f"unknown tier '{tier_name}'; expected one of "
                f"{[t.value for t in ModelTier]}"
            ) from None

        chain = tuple(
            p for entry in (spec.get("chain") or []) if (p := build(entry)) and p.is_configured
        )
        b = spec.get("budget") or {}
        tiers[tier] = TierConfig(
            tier=tier,
            chain=chain,
            terminal_fallback=str(spec.get("terminal_fallback", "deterministic_rule_engine")),
            required_capability=spec.get("required_capability"),
            budget=Budget(
                max_input_tokens=int(b.get("max_input_tokens", 12_000)),
                max_output_tokens=int(b.get("max_output_tokens", 2_000)),
                timeout_ms=int(b.get("timeout_ms", 8_000)),
                max_cost_usd_per_call=float(b.get("max_cost_usd_per_call", 0.05)),
            ),
        )

    if not tiers:
        raise ModelConfigError(f"{p}: no tiers declared")

    c = raw.get("cache") or {}
    br = raw.get("breaker") or {}
    probe = raw.get("probe") or {}

    return ModelConfig(
        tiers=tiers,
        cache=CacheConfig(
            enabled=bool(c.get("enabled", True)),
            ttl_seconds=int(c.get("ttl_seconds", 3_600)),
            demo_mode_ttl_seconds=int(c.get("demo_mode_ttl_seconds", 604_800)),
            backend=str(_expand(c.get("backend", "memory"))),
        ),
        breaker=BreakerConfig(
            failure_threshold=int(br.get("failure_threshold", 5)),
            success_threshold=int(br.get("success_threshold", 2)),
            open_duration_seconds=int(br.get("open_duration_seconds", 30)),
            half_open_max_calls=int(br.get("half_open_max_calls", 1)),
        ),
        probe_on_boot=bool(probe.get("run_on_boot", True)),
        probe_timeout_ms=int(probe.get("timeout_ms", 6_000)),
        skipped_providers=tuple(dict.fromkeys(skipped)),
    )
