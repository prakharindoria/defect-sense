"""Capability probe and its CLI.

Answers, with measurements rather than assumptions: *what can our model
endpoints actually do right now?* Run at boot, cached, and rendered on /admin.

A judge asking "what happens if your model provider changes?" gets a live matrix
showing which providers are reachable, which support vision, JSON mode and
streaming, and what the measured latency is. That is a better answer than any
slide, and it is the reason the vision tier's absence is a designed-for
configuration rather than a surprise.

    python tasks.py probe
"""

from __future__ import annotations

import asyncio
import os
import sys

from forge.bootstrap import init as bootstrap_init
from forge.domain.enums import ModelTier
from forge.infrastructure.llm.config import ModelConfigError, load
from forge.infrastructure.llm.service import LLMService


def _tick(value: bool) -> str:
    return "\x1b[32myes\x1b[0m" if value else "\x1b[90m no\x1b[0m"


async def run() -> int:
    bootstrap_init()
    try:
        config = load()
    except ModelConfigError as exc:
        print(f"\x1b[31mmodel config error:\x1b[0m {exc}", file=sys.stderr)
        return 1

    service = LLMService(config, demo_mode=os.environ.get("DEMO_MODE") == "true")

    print("\n\x1b[1mLLM capability matrix\x1b[0m")
    if config.skipped_providers:
        print("\nSkipped (env var not set):")
        for name, reason in config.skipped_providers:
            print(f"  - {name}: {reason}")

    results = await service.probe()
    if not results:
        print(
            "\n\x1b[33mNo providers configured.\x1b[0m Every tier resolves to its "
            "terminal fallback.\n"
            "Copy .env.example to .env and set one provider block."
        )
        return 0

    header = (
        f"{'provider':<12} {'model':<28} {'reach':>6} {'vision':>7} "
        f"{'json':>6} {'stream':>7} {'p50 ms':>7}"
    )
    print(f"\n{header}")
    print("-" * len(header))
    for c in results:
        reach = "\x1b[32m  ok\x1b[0m" if c.reachable else "\x1b[31mfail\x1b[0m"
        print(
            f"{c.provider:<12} {c.model[:28]:<28} {reach:>6} {_tick(c.supports_vision):>7} "
            f"{_tick(c.supports_json_mode):>6} {_tick(c.supports_streaming):>7} "
            f"{c.measured_p50_latency_ms:>7}"
        )
        if c.error:
            print(f"{'':>13}\x1b[90m{c.error[:100]}\x1b[0m")

    print("\n\x1b[1mTier resolution\x1b[0m")
    for tier in ModelTier:
        if tier not in config.tiers:
            continue
        tc = config.tier(tier)
        available = service.is_tier_available(tier)
        chain = " -> ".join(f"{p.name}:{p.model}" for p in tc.chain) or "(no provider)"
        mark = "\x1b[32mavailable\x1b[0m" if available else "\x1b[33m fallback\x1b[0m"
        print(f"  {tier.value:<10} {mark}  {chain}")
        if not available:
            print(f"{'':>14}\x1b[90m-> {tc.terminal_fallback}\x1b[0m")

    if not service.is_tier_available(ModelTier.VISION):
        print(
            "\n\x1b[33mNo vision model.\x1b[0m This is expected and designed for: the "
            "structured CV descriptor\nis the primary description path (ADR-0003), and "
            "it feeds measured features to a\ntext-only model rather than a model's "
            "impression of an image."
        )

    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
