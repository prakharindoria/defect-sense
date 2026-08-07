# CLAUDE.md — standing rules for every session in this repo

**Project:** FORGE — Factory Operations Reasoning & Governance Engine
**Context:** 36-hour judged hackathon build. Manufacturing quality control, agentic AI.
**Read before writing any code:** `docs/CONTRACTS.md`, `docs/ARCHITECTURE.md`, `docs/DEMO_SCRIPT.md`.

---

## The one thing that matters

Every feature exists to serve a step in `docs/DEMO_SCRIPT.md`. If you cannot name the demo step a change serves, do not make the change. Write the idea to `IDEAS.md` and move on.

The product loop is **DETECT → EXPLAIN → DECIDE → ACT → LEARN**. Every module sits at one of those five stations.

---

## Hard rules

1. **Contracts are frozen.** Do not edit `docs/CONTRACTS.md`, shared Pydantic models, `QCState`, the event schema, the RBAC matrix, or design tokens. Need a change? Append to `docs/CONTRACT_CHANGE_REQUESTS.md` and keep working around it.
2. **Stay in your workstream.** Do not modify files outside your assigned directories. If you're blocked on another workstream, stub against the frozen contract.
3. **No placeholder code.** No `# TODO: implement`. No function that returns invented data while claiming to compute it. If it isn't real, it doesn't merge.
4. **No silent failure.** Every fallback, retry, degradation, and truncation is recorded in the trace and surfaced in the UI.
5. **Provenance on every AI output.** Source, confidence, latency, tokens, model ID. No exceptions.
6. **Fail closed on safety.** Guardrails, authorization, and autonomy limits deny by default. Escalating to a human is a correct outcome, never an error.
7. **Never hardcode a model ID.** Read from `config/models.yaml` via env. Three tiers — `reasoning`, `fast`, `vision` — each with a declared fallback.
8. **Prompts live in `agents/prompts/*.md`**, versioned and loaded at runtime. Never inline in Python.
9. **Feature flags for anything risky.** Default off. A broken flag costs nothing; a broken feature costs the demo.
10. **After hour 30, no new features.** After hour 32, `main` is frozen and we demo from the `demo-candidate` tag.

---

## Architecture rules (enforced by `tests/architecture/`)

```
domain/         imports NOTHING (no framework, no infrastructure, no I/O)
application/    imports domain only — defines ports as ABCs
infrastructure/ implements ports; may import inward
interfaces/     HTTP/WS layer; may import inward
```

Dependencies point inward, always. Every external system (MES, Slack, LLM, weather, object store, vector store) sits behind a port defined in `application/ports/` with at least two implementations: real and fake. The fakes are what make tests fast and the demo reliable.

SOLID is not decoration here — a judge will ask to see a swap. Make sure swapping the LLM provider or the vector store is a config change, and be able to show it live on the admin page.

---

## Code standards

- Python 3.11, full type hints, `from __future__ import annotations`. Pydantic v2 for every boundary.
- Async throughout the API and agent layers. No blocking I/O in an async path — if a library is sync, wrap it in a thread executor and say so.
- Ruff + mypy clean before merge.
- React: TypeScript strict, no `any`, functional components, TanStack Query for all server state. No `localStorage` for auth tokens — memory + httpOnly refresh cookie.
- Errors: typed domain exceptions mapped to RFC 7807 problem responses. Never leak stack traces to the client.
- Logs: `structlog`, JSON, `correlation_id` on every line. Never log PII, secrets, or raw prompts containing plant data.
- Tests alongside features, not after. A feature without a test on the demo path is not done.

---

## Commit and merge discipline

- Conventional commits: `feat(agents): add adjudicator reflection loop`
- One workstream per branch: `ws/data`, `ws/agents`, `ws/platform`, `ws/web`, `ws/analytics`
- The integration owner merges to `main` every 2 hours from hour 7. Never let branches diverge overnight.
- `main` must always boot. If your merge breaks `make demo`, revert first and fix on your branch.

---

## Definition of done

A change is done when all of these are true:

- [ ] It works end to end from the UI, not just from a test
- [ ] It has a test covering the demo path
- [ ] It logs with a correlation ID and emits a metric
- [ ] It fails gracefully with a visible, honest degradation
- [ ] It shows provenance if it displays an AI-derived value
- [ ] It respects the RBAC matrix
- [ ] It works after `make reset && make demo` on a clean machine
- [ ] If it involved a real choice, `docs/DECISIONS.md` has an ADR with the alternatives and honest cons

---

## Data honesty

All demo data is synthetic. Never present it as real plant data. Every synthetic record carries `is_synthetic: true` and the UI shows a `SYNTHETIC` chip. Report metrics from the held-out golden set only — never from data the memory bank has seen. If a number is uncertain, show the uncertainty. Judges reward honest measurement and punish inflated claims.

---

## When you're unsure

Prefer the option that is **demonstrable in 20 seconds** over the option that is more sophisticated. Prefer **narrow and finished** over broad and half-built. Prefer **honest degradation** over hidden failure.

If a task would take more than 90 minutes and isn't on the MUST list in the build prompt, stop and flag it before starting.
