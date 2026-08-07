"""Probe every candidate TCS model and report what it can actually do.

Choosing a model from a vendor list is guessing. This measures: reachability,
JSON mode, genuine vision, and latency — then you pick from numbers.

It exists because guessing already cost us twice on this endpoint. The model
TCS designates for vision returned HTTP 410 while gpt-4o quietly had vision all
along, and a status-code-only vision check reported a text-only model as
multimodal. Both were caught by measuring.

    python -m forge.ops.model_sweep            # the shortlist
    python -m forge.ops.model_sweep --all      # every catalogued model
"""

from __future__ import annotations

import asyncio
import sys
import time

from forge.bootstrap import init as bootstrap_init
from forge.infrastructure.llm.config import ProviderConfig
from forge.infrastructure.llm.openai_compat import OpenAICompatAdapter

# Candidates by the role we would use them for. Ordered newest-first within a
# group so the sweep reads as "is the newer one actually better here?".
SHORTLIST: dict[str, tuple[str, ...]] = {
    "reasoning": (
        "genailab-maas-gpt-5.4",
        "genailab-maas-gpt-5.2",
        "genailab-maas-gpt-5.0",
        "azure/genailab-maas-gpt-4.1",
        "azure/genailab-maas-gpt-4o",           # incumbent
        "gemini-3.1-pro-preview",
        "gemini-2.5-pro",
        "azure_ai/genailab-maas-DeepSeek-R1",
    ),
    "fast": (
        "genailab-maas-gpt-5.4-mini",
        "genailab-maas-gpt-5.4-nano",
        "azure/genailab-maas-gpt-5-mini",
        "azure/genailab-maas-gpt-4.1-mini",
        "azure/genailab-maas-gpt-4.1-nano",
        "azure/genailab-maas-gpt-4o-mini",      # incumbent
        "gemini-3-flash-preview",
        "gemini-2.5-flash",
        "gemini-2.0-flash-001",
    ),
    "vision": (
        "azure_ai/genailab-maas-Llama-3.2-90B-Vision-Instruct",
        "azure_ai/genailab-maas-Phi-3.5-vision-instruct",
        "gemini-2.5-flash",
        "azure/genailab-maas-gpt-4o",           # incumbent
        "azure/genailab-maas-gpt-4.1",
    ),
}

EXTRA = (
    "genailab-maas-gpt-5.1",
    "genailab-maas-gpt-5.2-codex",
    "genailab-maas-gpt-5.3-codex",
    "azure/genailab-maas-gpt-35-turbo",
    "azure_ai/genailab-maas-DeepSeek-V3-0324",
    "azure_ai/genailab-maas-Llama-3.3-70B-Instruct",
    "azure_ai/genailab-maas-Llama-4-Maverick-17B-128E-Instruct-FP8",
)

# Enough parallelism to finish quickly, low enough not to trip a rate limit and
# have every model report a spurious failure.
CONCURRENCY = 4


async def probe_one(model: str, base_url: str, api_key: str, sem: asyncio.Semaphore):  # noqa: ANN201
    async with sem:
        adapter = OpenAICompatAdapter(
            ProviderConfig(
                name="tcs", kind="openai_compatible", model=model,
                base_url=base_url, api_key=api_key,
            ),
            timeout_ms=45_000,
        )
        started = time.perf_counter()
        caps = await adapter.probe(timeout_ms=45_000)
        return model, caps, int((time.perf_counter() - started) * 1000)


async def run(models: list[str]) -> int:
    import os  # noqa: PLC0415

    bootstrap_init()
    api_key = os.environ.get("TCS_API_KEY", "").strip()
    base_url = os.environ.get("TCS_BASE_URL", "https://genailab.tcs.in")
    if not api_key:
        print("TCS_API_KEY is not set. Put it in .env first.", file=sys.stderr)
        return 1

    sem = asyncio.Semaphore(CONCURRENCY)
    print(f"Probing {len(models)} models at {base_url} ...\n")
    results = await asyncio.gather(*(probe_one(m, base_url, api_key, sem) for m in models))

    header = f"{'model':<52}{'ok':>5}{'vision':>8}{'json':>6}{'stream':>8}{'ms':>8}"
    print(header)
    print("-" * len(header))
    for model, caps, elapsed in results:
        ok = "  ok" if caps.reachable else "FAIL"
        y = lambda b: " yes" if b else "  - "  # noqa: E731
        print(
            f"{model:<52}{ok:>5}{y(caps.supports_vision):>8}"
            f"{y(caps.supports_json_mode):>6}{y(caps.supports_streaming):>8}{elapsed:>8}"
        )
        if caps.error:
            print(f"{'':<52}   {caps.error[:110]}")

    reachable = [(m, c, e) for m, c, e in results if c.reachable]
    print(f"\n{len(reachable)}/{len(results)} reachable")
    if reachable:
        fastest = min(reachable, key=lambda r: r[2])
        print(f"fastest reachable: {fastest[0]} at {fastest[2]}ms")
        vision = [m for m, c, _ in reachable if c.supports_vision]
        print(f"genuine vision:    {', '.join(vision) if vision else 'none'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if "--all" in argv:
        models = list(dict.fromkeys([m for g in SHORTLIST.values() for m in g] + list(EXTRA)))
    else:
        models = list(dict.fromkeys([m for g in SHORTLIST.values() for m in g]))
    return asyncio.run(run(models))


if __name__ == "__main__":
    raise SystemExit(main())
