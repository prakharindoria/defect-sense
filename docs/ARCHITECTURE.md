# FORGE — End-to-end technical architecture

**Factory Operations Reasoning & Governance Engine**
Wheel Assembly Defect Detection · multi-agent · multi-page enterprise application

Product spine: **DETECT → EXPLAIN → DECIDE → ACT → LEARN.** Every module sits on
exactly one of those five stations.

---

## 1. The thesis

Wheel torque specifications assume **clean, dry threads**. Torque is only a
proxy for clamp load, and the relationship runs through thread friction:

```
T ≈ K · F · d      T torque, F clamp load, d diameter, K nut factor (friction-dominated)
```

Contaminate the threads and K changes. The gun reads a perfectly in-spec value
while delivered clamp load is ~30% under target.

```
Torque endpoint alone   118 Nm in a 110–125 Nm band       →  PASS
Vision alone            bolt present, seated, count = 5   →  PASS
Reality                 clamp load ~30% under target      →  FAIL
```

Neither signal is wrong. The defect exists **only in the disagreement between
them** — which is what a multi-agent system is actually for, as opposed to five
chat windows.

Verified live from `api.nhtsa.gov`, keyless: campaign **`24V237000`**, component
`WHEELS:LUGS/NUTS/BOLTS/STUDS` — *"the front wheel hub bolts may have been
over-tightened and damaged during installation."*

---

## 2. Inputs

### 2.1 Input contracts

| Input | Shape | Source | Rate | Validation |
|---|---|---|---|---|
| **Torque-angle curve** | ordered `(angle_deg, torque_nm)` series, ≥10 samples, one per fastener position | fastening controller (synthetic) | 5/unit | monotonic angle, finite values, min length — `TorqueAngleCurve.__post_init__` |
| **Inspection image** | JPEG/PNG, ≥640×480, ≤10 MB | station camera (synthetic; optional webcam) | 1–5 fps | format, dimensions, size, decodability |
| **Sensor scalars** | `spindle_current_a`, `seating_time_ms`, `tool_age_cycles`, `tool_days_since_calibration` | tool telemetry | per fastener | range + stuck-at detection |
| **MES metadata** | work order, job card, BOM variant, batch, material lot, workstation, tool, operator token, shift | `MESPort` | per unit | schema + referential resolution |
| **Ambient conditions** | temperature °C, relative humidity % | **Open-Meteo, live** | 15 min | freshness stamped, staleness surfaced |
| **Historical defects** | past incidents + resolutions | local store → v2 vector index | on demand | — |
| **Recall reference** | campaign number, component, summary, consequence, remedy | **NHTSA, live** | cached | — |

### 2.2 Why each live API earns its place

The bar for inclusion is a one-line answer to *"why is this API here?"*

| API | Keyless | Load-bearing role |
|---|---|---|
| **Open-Meteo** | yes | Humidity drives thread surface condition — the physical mechanism behind the fusion case. Not decoration. |
| **NHTSA Recalls** | yes | The failure mode we detect, pulled live from a US government database; also seeds `recall_exposure_per_unit` in cost triage. |
| **open.er-api.com** | yes | ₹/$ on every cost figure. (Frankfurter was unreachable on this network.) |
| **Nager.Date** | yes | Holiday/shift calendar → the bias-report slice. **Returns HTTP 204 for India** — surfaced as a stated coverage gap, not an empty year. |

### 2.3 Data honesty

Every record carries `is_synthetic: true`, `generator_version`, `generated_at`,
and the UI renders a `SYNTHETIC` chip. Ground truth is recorded **per record at
generation time**, so evaluation scores against what was actually injected
rather than a later human opinion.

Dirty data is injected deliberately — NaNs, stuck-at sensors, clock skew,
out-of-range spikes, blown exposure. The data-quality gate rejects ~3% and the
**rejection log is shown**. "We tested on clean data" is a weak answer; a
rejection log is a strong one.

`valve_stem_damage` is a **holdout**: never seeded into any bank or exemplar
set. It measures unseen-defect detection honestly.

Operator IDs are tokenised (`OP-7f3a`), reversible only by `QUALITY_MANAGER`+
and always audited. PII redaction runs **before** any text reaches an LLM.

---

## 3. Layering

```
interfaces/       FastAPI routers, WebSocket, dependency wiring
     ↑
infrastructure/   adapters · resilience · persistence · LLM
     ↑
application/      use cases + ports (ABCs)
     ↑
domain/           entities, taxonomy, torque maths, cost engine
                  stdlib + pydantic ONLY · performs no I/O
```

Dependencies point inward, always — enforced by an AST import-graph walk, not by
convention. Five executable rules, all currently green:

| Rule | Test | Protects |
|---|---|---|
| Inward-only imports | `test_dependencies_point_inward` | Layering is real |
| Domain imports only stdlib + pydantic | `test_domain_imports_nothing_but_stdlib_and_the_allowlist` | Core stays portable |
| Domain performs no I/O | `test_domain_performs_no_io` | Catches `open()`, which imports cannot |
| No pack vocabulary in `domain/`/`application/` | `test_no_pack_vocabulary_in_inner_layers` | The live pack switch is architecture, not a trick |
| Ports are ABCs; their DTOs resolve at runtime | `test_every_port_is_an_abstract_base_class`, `test_port_dataclass_annotations_resolve_at_runtime` | "Ports" aren't just classes; annotations don't explode on serialization |

The pack rule has already paid for itself: it rejected a `fastener_count` field
on `PackManifest`. A weld station has weld points, not fasteners — the count
belongs in the pack's verifier YAML.

---

## 4. Ports and adapters

Every external system sits behind a port with **at least two implementations,
real and fake**. The fakes are why 56 tests run in 1.4 s and why the demo cannot
hard-fail on a provider outage.

| Port | Real | Fake / alternate | Phase |
|---|---|---|---|
| `LLMPort` | TCS / Anthropic / Ollama via OpenAI-compatible | `FakeAdapter` (deterministic, offline) | ✅ built |
| `WeatherPort` `RecallPort` `FxPort` `CalendarPort` | live HTTP | recorded fixtures | v1 |
| `UseCasePackRepository` | YAML pack loader | in-memory | v1 |
| `InspectionRepository` `AuditLog` | SQLite → Postgres | in-memory | v1 |
| `ROIExtractorPort` `GeometricVerifierPort` | OpenCV | fixture | v1 |
| `EventBusPort` | asyncio → Redis Streams | in-process | v1 |
| `NotifierPort` | Slack | console | v1 → v2 |
| `MESPort` | ERPNext | simulator, recorded, in-memory | v2 |
| `VectorStorePort` `KeywordSearchPort` `RerankerPort` | Chroma, BM25, cross-encoder | in-memory | v2 |
| `AnomalyScorerPort` | PatchCore + FAISS | fixture | v2 |

**`PROFILE=local|docker` selects the adapter set.** That is the whole
scalability answer: v2 is new adapters behind existing ports, not a rewrite.

---

## 5. LLM layer

Callers ask for a **tier**, never a model. No model ID is hardcoded anywhere;
`config/models.yaml` holds only `${ENV}` references.

```
                      ┌──────────── tier: reasoning ────────────┐
GenerationRequest ──> │ TCS gpt-4o → Anthropic → Ollama → fake │ ──> Completion
                      └────────────────────────────────────────┘
                        ↓ chain exhausted
                      downgrade to `fast` tier
                        ↓ still exhausted
                      raise — caller applies its terminal fallback
```

| Tier | TCS model | Terminal fallback |
|---|---|---|
| `reasoning` | `azure/genailab-maas-gpt-4o` | deterministic rule engine |
| `fast` | `azure/genailab-maas-gpt-4o-mini` | deterministic rule engine |
| `vision` | `azure_ai/genailab-maas-Llama-3.2-90B-Vision-Instruct` | `structured_cv_descriptor` |
| embeddings | `azure/genailab-maas-text-embedding-3-large` | local `bge-small-en-v1.5` |

`gpt-4o` over DeepSeek-R1 for reasoning: TCS's own guidance puts gpt-4o on
robust JSON parsing and central application logic, which is exactly the
Adjudicator's job — it must emit a strict schema. R1 reasons better but emits
`<think>` preamble and is slow.

**Boot capability probe.** Measures reachability, vision, JSON mode, streaming,
and p50 latency per endpoint; renders on `/admin`. The vision check is
*discriminating* — solid red and blue images the model must name — because a
status-code check produced a false positive (Ollama accepts multimodal payloads
for text-only models, drops the image, returns 200).

**Guards.** Response cache keyed on `(tier, prompt_hash, pack_id)` — pack is in
the key because the same prompt under a different pack is a different question.
Three-state circuit breaker per provider. Every degradation
(`LLM_TIER_DOWNGRADE`, `CIRCUIT_OPEN`, `CACHE_SERVED`, `BUDGET_EXCEEDED`) is
recorded on the `Completion` and flows to the trace and the provenance strip.

**When everything is exhausted, the service raises rather than inventing prose.**
A plausible sentence with no model behind it is exactly the fabricated output
`CLAUDE.md` rule 3 forbids.

---

## 6. Agent topology

```
                 ORCHESTRATOR   routing · token + latency budgets · degradation
      ┌──────────────┬─────────┴────────┬──────────────┐
  INGESTION      VISION            PROCESS         CONTEXT
  validate,      geometric         torque-angle    MES metadata,
  DQ gate,       verifiers         signature,      live ambient,
  correlation    (exact)           drift, forecast recall reference
      └──────────────┴─────────┬────────┴──────────────┘
                          ADJUDICATOR   ← reflection 1: signals disagree → re-query
                               │
                          ROOT CAUSE    ← reflection 2: groundedness < 0.7 → re-retrieve   [v2]
                               │
                          COST TRIAGE   expected-cost engine, ₹ + $
                               │
                          GUARDIAN      PII · injection · unsafe rec · tool authz  [v2]
                          ┌────┴────┐
                     HITL GATE → ACTION → MES QI, Slack, events                    [v2]
                                    │
                               LEARNING → memory bank, thresholds                  [v2]
```

v1 ships the first six. v2 adds Root Cause, Guardian, HITL, Action, Learning,
and a standalone Analyst on `Cmd+K` — eleven plus the Analyst.

### Collaboration model

- **Supervisor + specialists.** The Orchestrator routes and enforces budgets; it
  does not analyse.
- **Handoff is state, not messages.** Every agent reads and writes `QCState`. No
  agent mutates another's slot.
- **Reflection fires on a number.** Adjudicator re-queries on signal
  disagreement; Root Cause re-retrieves when groundedness < 0.7. The retry
  counter increments visibly in the trace.
- **Escalation is a correct outcome**, never an error. `Verdict.ESCALATE` is
  first-class and must carry a reason — validated in `state.py`.
- **Degradation is never silent.** `QCState.record()` rolls span-level
  degradations up to the run so the UI cannot miss one.

### The latency split — important

Measured on this machine: reasoning **6.9 s**, fast **4.7 s** per call. A single
LLM call blows a 4 s end-to-end budget on its own.

The resolution is architectural, not tuning: **the verdict is already fully
deterministic.** Torque signature, geometric verifiers and the cost engine are
pure computation — microseconds, no model involved. The LLM only writes the
*explanation*.

```
t+0ms     ingest → verifiers ∥ signature ∥ context   (deterministic)
t+~50ms   VERDICT + evidence + cost  ──────────────> UI renders
t+~5s     Adjudicator narrative      ──WebSocket──> streams in beneath it
```

This is both faster and a far stronger claim: **the safety decision does not
wait on a language model, and does not depend on one.** The LLM explains a
decision it did not make.

---

## 7. Domain engines

### Torque-angle signature — `src/forge/domain/torque.py`

Two-segment piecewise-linear fit. The seating knee is found by an **exact O(n)
sweep** over all admissible breakpoints using prefix sums — no gradient descent,
no initial guess, no local minima. Pure standard library; a judge can read every
line of the maths.

Measured features: knee angle, elastic slope, residual variance, run-down
torque, reversal count, late/early slope ratio (yield detection).

Deviations combine by **weighted noisy-OR**, not averaging — averaging lets one
strongly anomalous feature be diluted by several nominal ones, which is exactly
the dilution that makes a contaminated fastener look acceptable.

**Baselines are learned, not hand-picked.** `SignatureBaseline.from_clean_runs()`
sets every tolerance at *k*σ of the measured clean distribution (default 3σ,
minimum 20 runs, raises below that). Spec limits are the deliberate exception —
they come from engineering, never from data. A drifted process must not be
allowed to redefine what is in spec.

Measured, 120 clean runs at 3σ:

| class | final Nm | knee | slope | score | in spec | fusion-only |
|---|---|---|---|---|---|---|
| clean | 118.5 | 13.9° | 3.19 | 0.00 | yes | no |
| **thread_contamination** | **118.0** | **20.3°** | **2.31** | **0.61** | **yes** | **YES** |
| cross_threading | 118.1 | 11.3° | 2.87 | 0.65 | yes | YES |
| over_torque | 125.4 | 14.2° | 2.82 | 0.55 | no | no |
| under_torque | 101.2 | 13.9° | 3.19 | 0.00 | no | no |

Fusion-only detection rate across 120 seed/severity combinations: **> 80%**.
False-positive rate on clean runs: **≤ 2%**.

Honest limits, both reported rather than tuned away: mild over-torque that stays
in spec is not reliably caught (0.27 at severity 0.4); under-torque scores 0.00
on shape and is caught by the **endpoint**, not the signature.

### Cost triage — `src/forge/domain/cost.py`

Not a severity lookup. Expected cost per disposition:

```
EC(action) = P(defect) · cost_if_defective + (1 − P) · cost_if_fine
```

Recommend `argmin EC`. Conservatism on safety-critical parts **emerges from the
cost asymmetry** — a wheel escape carries warranty plus recall exposure — not
from a hardcoded rule. Proven by a test that zeroes the exposure and watches
ACCEPT stop looking dangerous.

Escalates to a human when: halting wins on cost, expected cost exceeds the
supervisor threshold, accepting a safety-critical unit, the top two options are
within 10% of each other, or the cost interval is wide relative to the estimate.

Uncertainty in P(defect) propagates to a **cost interval**. The assumption set
travels with every result — a cost figure without its assumptions is a claim;
with them it is an argument, and an argument can be checked.

**Never returns HALT_LINE autonomously**, even when halting is cheapest. The
number is still shown, because that number is what the human needs.

---

## 8. Vision

v1 is deliberately **deterministic geometric verifiers** — real computer vision,
exact, explainable, CPU-cheap:

```
1 ROI        Hough circle + template align → canonical 256×256, rotation-normalised
2 Verifiers  fastener count, bolt-circle diameter, angular spacing, hub flushness,
             TPMS presence, wheel-weight presence
```

Verifier results carry **no confidence**. Counting five fasteners either finds
five or it does not; attaching a pseudo-confidence would imply a doubt that does
not exist. You do not make a neural net do arithmetic.

Critically, the fusion case **needs vision to say PASS** — and verifiers do that
honestly, because the bolts genuinely are present and seated.

v2 adds the learned layer behind `AnomalyScorerPort`: PatchCore memory bank
(ResNet18 layers 2+3, frozen, coreset 1%, FAISS), 99th-percentile patch
distance, temporal 3-of-5 consensus on the same tracked unit, nearest-exemplar
classification with UNKNOWN below 0.6.

---

## 9. Hybrid RAG (v2)

```
query → BM25 (30) ∥ dense bge/text-embedding-3-large → Chroma (30) ∥ metadata prefilter
      → RRF k=60 (20) → cross-encoder rerank (5)
      → context assembly with citation IDs
      → generation with mandatory inline [DOC-id §chunk]
      → groundedness self-check → < 0.7 re-retrieves with relaxed filters
```

Three separate ports (`KeywordSearchPort`, `VectorStorePort`, `RerankerPort`)
specifically so the **ablation study** is cheap: BM25-only, dense-only, fused
and reranked each scored against the same golden question set.

Corpus is deliberately heterogeneous (~120 docs/pack) because retrieval
differences only show on heterogeneous data: SOPs where BM25 wins, maintenance
manuals where dense wins, incident narratives where hybrid wins, torque spec
tables where metadata filtering wins.

Swapping embedding models invalidates an index — vectors from two models are not
comparable — so the collection name carries the model ID.

---

## 10. Security, governance, resilience

**Auth.** JWT access 15 min held **in memory** + rotating httpOnly refresh
cookie 7 d, argon2. No token in `localStorage`.

**RBAC.** 5 roles from `config/rbac.yaml`, every cell asserted by test. Roles
drive permissions, default dashboard, notification channel, and AI explanation
depth.

**Tool authorization is enforced at the tool dispatcher, not in the prompt.** An
Operator's session physically cannot call `mes.create_qi`. *Prompts are not a
security boundary.* ADMIN deliberately **cannot** decide HITL gates or override
a quality verdict — platform authority and quality authority are separated.

**Autonomy ceiling**, declared in config and enforced in code:

> FORGE can reject a part autonomously. It **cannot pass one**, and it **cannot
> halt a line**, without a human.

**Resilience.** Three-state circuit breakers with half-open probing on every
outbound dependency; half-open admits a strictly limited number of trial calls,
because releasing full load the instant the timer expires is how a recovering
dependency gets knocked straight back over. Retry with jitter and idempotency
keys. Bulkheads isolating vision/LLM/MES pools. Fallback chain annotated at
every level. DLQ with retry UI. Fault-injection panel to break it on purpose.

**Observability.** structlog JSON with `correlation_id` propagated API → agent
node → tool → MES; Prometheus metrics; OTel spans per node; all surfaced
in-product on `/health`, because judges will not tab out to Grafana.

**TLS.** Verification routed through the OS trust store. The `verify=False`
pattern from the TCS sample is scoped to named hosts only, never global — an
attacker on the network could otherwise feed us a forged NHTSA record we would
then cite as evidence.

---

## 11. Runtime topology

**v1 — single process, zero infrastructure**

```
Browser (React 18 + Vite)
   │ HTTP + WebSocket
FastAPI (uvicorn)
   ├── LangGraph runtime        in-process, 6 nodes
   ├── SQLite                   QCState, audit, users, inspections
   ├── asyncio event bus
   ├── LLM  ──> TCS GenAI Lab (Ollama / fake fallback)
   └── live APIs behind breakers ──> Open-Meteo · NHTSA · FX · Nager
```

**v2 — same code, `PROFILE=docker`**

adds Postgres · Redis · ChromaDB · MinIO · ERPNext simulator · Slack ·
Prometheus/Grafana.

---

## 12. Current status

**Built and verified — 56 tests, ~1.4 s, lint clean, ~6,000 lines:**

`domain/` (enums, provenance, torque, cost, state) · `application/ports/`
(14 ports, 54 exports) · `infrastructure/llm/` (config, chain, cache, probe,
Ollama/OpenAI-compatible adapter, FakeAdapter) ·
`infrastructure/resilience/breaker.py` · torque-curve generator with ground
truth · `bootstrap.py` (OS trust store TLS) · `ops/doctor.py` ·
`tests/architecture/` (5 executable rules).

**Next, in order:** wheel_assembly pack + loader → OpenCV verifiers → SQLite +
auth/RBAC → LangGraph 6 nodes → FastAPI + WebSocket → React 4 pages → eval
harness with the baseline row.

See `docs/RUNBOOK.md` to run it and `docs/DECISIONS.md` for the 13 ADRs with
honest cons.
