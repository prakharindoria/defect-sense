# FORGE v2 — Master Build Prompt for Claude Code
### Wheel Assembly Defect Detection Agent · Multi-Agent · Multi-Page Enterprise Web App

> **Supersedes v1.** Domain is now locked to automotive wheel assembly, with brake assembly and robotic body welding as live-switchable Use Case Packs.
>
> **Confirmed constraints:** 36 hours · 5–6 engineers, 2 CV-capable · laptop GPU only · TCS-hosted LLM endpoints · synthetic visual data · real open-source MES (ERPNext) · image + video + sensors + MES all live in the demo · 10–15 minute pitch slot.

---

## §1. Competitive benchmark — what the leaders actually do today

You asked to ground this in reality first. Here is the state of the art, and precisely where the gaps are.

### BMW — the most advanced public deployment

BMW runs a proprietary platform called **AIQX (Artificial Intelligence Quality Next)**: network cameras and sensors positioned along the conveyor, synchronised with real-time vehicle localisation, feeding deep-learning models that flag defects in fractions of a second and push results to line staff on handheld devices. Regensburg became the first automotive plant in the world to run end-to-end automated surface inspection in series production, live since March 2023. For reflective painted surfaces — where conventional machine vision struggles — they use deflectometry rather than standard imaging.

A second pilot, **GenAI4Q**, uses generative AI to produce an **individualised inspection catalogue per vehicle**, because every car is built to customer spec and virtually no two are alike (~1,400 vehicles/day at Regensburg, one every 57 seconds).

They also solved a specific, expensive problem: **pseudo-errors**. Metal chips and oil residue left on formed body parts look like fine cracks. Their fix required roughly 100 real images per characteristic — 100 clean, 100 with dust, 100 with oil drops, 100 with actual cracks — to teach the network the difference. On the welding side, robots at Spartanburg place 300–400 studs per SUV frame, around half a million studs a day; an AI stud-correction system saved over $1M/year. BMW also built **SORDI**, a large synthetic reference dataset for manufacturing AI. One European plant reported a ~30% defect-rate reduction within a year.

*Sources: press.bmwgroup.com (GenAI4Q), axis.com customer story (AIQX), intel.com BMW/OpenVINO story, claritypoints.com iFACTORY analysis.*

### Tesla

Industrial cameras at every major station, NVIDIA edge GPUs, CNN and YOLO-family object detection trained on millions of labelled factory images, wired into the MES for real-time production monitoring. Inference runs **at the edge** — each station processes locally, keeping latency matched to line pace. Models are continuously retrained as new production data arrives. Vision-based inspection robots handle paint and panel alignment on the Gigafactory floor.

*Sources: kodytechnolab.com, ifactoryapp.com, digitaldefynd.com.*

### The market context for your business-impact slide

Rockwell's 2025 State of Smart Manufacturing report surveyed 1,560 decision-makers: 95% had invested or planned to invest in AI within five years, and **half named quality control as a primary target**. Machine vision is projected to grow from $20.4B (2024) to $41.7B (2030). State-of-the-art surface inspection reaches 99.5–99.8% on defects down to 0.1mm, against human inspectors at 70–80%.

The most important line in that research, and you should say it on stage: *what determines who captures the value is no longer access to algorithms — those are available to anyone.*

### The four gaps — this is your entire technical thesis

| # | Gap | Why the leaders still have it | What FORGE does |
|---|---|---|---|
| **1** | **Supervised models need labelled defects that don't exist yet** | BMW needed ~100 real images *per pseudo-error characteristic*. A new line, a new SKU, or a rare defect means weeks of collection and labelling before you have coverage. | Memory-bank anomaly detection — model *normal*, catch everything else by construction. New variant onboarded in minutes. |
| **2** | **Single-modality inspection misses cross-modal defects** | Vision sees the surface. The torque gun sees the torque number. Neither sees the failure mode where *both individually pass.* See §2. | Multi-agent fusion with explicit adjudication of conflicting signals. |
| **3** | **Detection is reactive — the bad part already exists** | AIQX and Tesla both detect defects on parts that have been built. BMW is explicitly trying to move upstream. | Process Sentinel forecasts defect risk from sensor drift *before* the next unit is assembled. |
| **4** | **Every use case is a new engineering project** | BMW's own material notes their systems must adapt across line configurations; in practice each inspection task is bespoke. | Use Case Packs — wheel → brake → welding is a config load, zero code change. Demonstrated live. §4. |

**Do not claim you have out-engineered BMW.** You have not. Claim something narrower and true: *BMW's approach requires labelled defect data and per-task engineering that most manufacturers cannot afford. FORGE reaches useful accuracy on day one with zero labelled defects, and generalises to a new inspection task by loading a config pack. That's the difference between a capability available to BMW and a capability available to their tier-2 suppliers.* That framing is defensible under hostile questioning and it opens a much larger market than the OEM story.

---

## §2. The killer insight — build the whole demo around this

Real recall, verifiable, and it is the single best argument for multi-agent fusion in existence.

**GM Safety Recall N232431480** (2023 Chevrolet Colorado / GMC Canyon): front wheel hub bolts were **over-torqued and damaged during installation**. Deformed bolts loosen over time. A bolt fractures in service, load shifts to the remaining bolts, and the outcome is partial loss of vehicle control. Ford has parallel recalls on rear axle hub bolt breakage across 100K+ trucks.

Now the physics that makes this a *multi-agent* problem. Wheel torque specifications assume **dry, clean threads**. Torque is a proxy for clamp load, and the relationship between them is governed by thread friction. Oil, grit, or corrosion on the threads changes that friction — so the torque gun reads a perfectly in-spec value while the actual clamp force is wrong.

**Therefore:**

```
Torque sensor alone      →  reads 118 Nm, spec is 110–125 Nm  →  PASS ✓
Vision alone             →  bolt is present, seated, correct count  →  PASS ✓
Reality                  →  contaminated threads, clamp load ~30% under target  →  FAIL ✗
```

Neither signal catches it. Both are individually correct. **Only fusion catches it.**

FORGE catches it with three signals adjudicated together:
1. **Torque-angle signature analysis** — the torque-vs-rotation-angle curve of a clean dry fastener has a characteristic slope through the elastic region. Contaminated or cross-threaded fasteners produce a measurably different curve even when the final torque value lands in spec. The Process Sentinel scores the *shape*, not the endpoint.
2. **Vision on the bolt seat and thread entry** — anomaly heatmap over the fastener region catches residue, witness marks, and seating gaps.
3. **MES context from ERPNext** — tool age, last calibration date, batch, material lot, operator, station.

The Adjudicator reasons over the disagreement and escalates. That is a **defect that no camera and no torque gun can catch alone**, tied to a real recall, in a safety-critical assembly. Build your demo around this and you have the best five minutes in the room.

---

## §3. Product definition

**FORGE** — *Factory Operations Reasoning & Governance Engine*

**MVP use case:** Wheel Assembly Station (hub mount → bolt placement → torque sequence → final verification)
**Switchable packs:** Brake Assembly · Robotic Body Welding

**Product spine — every module sits on one of five stations:**

```
DETECT  →  EXPLAIN  →  DECIDE  →  ACT  →  LEARN
```

**Wheel assembly defect taxonomy** (this is your `taxonomy.yaml`):

| Class | Detected by | Severity | Why it matters |
|---|---|---|---|
| `missing_fastener` | vision (count verifier) | critical | Load redistributes to remaining studs |
| `under_torque` | torque sensor | critical | Clamp force loss → wheel-off risk |
| `over_torque` | torque sensor + angle signature | critical | **The GM recall failure mode** |
| `thread_contamination` | **vision + angle signature fusion** | critical | **The signature demo — invisible to either alone** |
| `cross_threading` | angle signature (early slope anomaly) | critical | Stud damage, progressive loosening |
| `wrong_torque_sequence` | temporal sensor pattern | major | Uneven seating, hub distortion |
| `hub_seating_gap` | vision (geometric) | critical | Wheel not flush; catastrophic |
| `wrong_wheel_variant` | vision + MES order spec | major | Build-to-order mismatch (the BMW problem) |
| `missing_tpms` | vision | major | Regulatory non-compliance |
| `rim_surface_damage` | vision (anomaly) | minor | Cosmetic, warranty claims |
| `missing_wheel_weight` | vision | minor | Balance / NVH complaints |
| `HOLDOUT: valve_stem_damage` | — | major | **Never seeded. This is the unseen-defect demo.** |

**Personas** (drive RBAC, default dashboards, notification channel, and AI explanation depth):

| Persona | Role | Primary page | Autonomy they grant |
|---|---|---|---|
| Ravi — Line Operator | `OPERATOR` | Command Center | View + acknowledge only |
| Priya — QC Inspector | `INSPECTOR` | Defect Workbench | Approve/override verdicts, name new classes |
| Arjun — Line Supervisor | `SUPERVISOR` | Approval Queue | Approve rework/scrap, halt line |
| Meera — Plant Quality Manager | `QUALITY_MANAGER` | Analytics | Policy thresholds, unmask PII (audited) |
| Sam — Platform Engineer | `ADMIN` | Admin & Health | Models, flags, packs, rate limits |

---

## §4. Use Case Packs — the central architectural idea

This is what makes "live switch on stage" possible, and it is your answer to rubric #9 (feasibility and scale). Build the second and third packs from day one, even if thinner than wheel.

```
packs/
├── wheel_assembly/
│   ├── manifest.yaml          # id, name, stations, inspection points, cycle time
│   ├── taxonomy.yaml          # defect classes, severity, cost model, spec limits
│   ├── sensors.yaml           # channel defs, units, spec bands, causal graph
│   ├── normals/               # normal image set → memory bank (200–500 images)
│   ├── exemplars/             # one labelled example per known class
│   ├── knowledge/             # SOPs, torque standards, incident history → RAG corpus
│   ├── mes_mapping.yaml       # → ERPNext Item / BOM / Operation / QI Template
│   ├── prompts/               # pack-specific prompt overrides
│   ├── verifiers.yaml         # geometric checks: fastener count=5, PCD=120mm, ...
│   └── ui.yaml                # KPI defs, dashboard layout, station diagram
├── brake_assembly/
└── body_welding/
```

**Runtime behaviour:** `POST /api/v1/packs/{id}/activate` swaps the active pack. The system reloads the memory bank, taxonomy, sensor schema, RAG collection, prompt overrides, cost model, and UI config. **Under 10 seconds, zero code change, no restart.**

**Pack loading must be an application-layer port** (`UseCasePackRepository`) so this reads as architecture, not a hack. Write `tests/test_pack_isolation.py` asserting that no pack-specific string exists anywhere in `domain/` or `application/`. An executable architectural rule is worth more to a judge than a slide claiming modularity.

**Demo line to rehearse:** *"BMW needs a new engineering project per inspection task. We need a folder."*

---

## §5. Non-negotiable constraints

### Hackathon checklist — each demonstrable in <20 seconds

Multi-page enterprise app (14 pages) · multi-agent LangGraph (10 agents, real handoffs) · Hybrid RAG (BM25 + dense + RRF + rerank) over ChromaDB · LangChain tools · HITL approval gates (brief says "HIIT") · guardrails (PII, injection, unsafe recommendation, unauthorized tool exec) · JWT auth + 5-role RBAC · glassmorphism UI + distinctive product-wide typeface · circuit breaker, retry, bulkhead, fallback chain, DLQ · structured logs + Prometheus + traces · rate limiting · Slack alerts **and** interactive approvals · OpenAPI + Redoc + Postman · dashboards throughout · ≥3 live public APIs, load-bearing · Docker one-command deploy · profiling and persona creation · SOLID + clean architecture · test suite · **every capability shown with 2–3 distinct examples** · header and footer with live use-case-relevant content.

### Engineering rules

1. **No placeholder code.** No `# TODO`. No function returning invented data while claiming to compute.
2. **Every AI output carries provenance:** source, confidence, latency, tokens, model ID. This is the signature UI element.
3. **Feature flags on anything risky**, default off. `FEATURE_WEBCAM_INGEST`, `FEATURE_SLACK_INTERACTIVE`, `FEATURE_VOICE`.
4. **Seed on boot** — clean machine to demo-ready in under 4 minutes, zero manual steps.
5. **`DEMO_MODE=true`** pins seeds, fixes the event sequence, and serves LLM responses from cache. Non-negotiable given TCS endpoint latency is unknown.
6. **Latency budget:** p95 verdict < 4s. Exceed it → degrade to fallback and *say so in the trace*.
7. **Contracts frozen after Phase 0.** Change requests go to `docs/CONTRACT_CHANGE_REQUESTS.md`.
8. **Integration owner merges to `main` every 2 hours** from hour 7.

---

## §6. LLM provider abstraction — TCS-hosted models

> **Highest-risk unknown in the build. Do this in Phase 0, before anything depends on it.**

You do not control these endpoints. They may be OpenAI-compatible or not; they may lack vision, function calling, JSON mode, or streaming; rate limits and latency are unknown. **Design so that none of that can kill the demo.**

### Required: capability probe on boot

```python
class ModelCapabilities(BaseModel):
    supports_vision: bool
    supports_function_calling: bool
    supports_json_mode: bool
    supports_streaming: bool
    max_context: int
    measured_p50_latency_ms: int
```

Probe every configured endpoint at startup, cache the result, and **render it on the Admin page**. A judge asking "what if your model provider changes?" gets shown a live capability matrix. That is a better answer than any slide.

### Required: three tiers, each with a declared fallback chain

```yaml
# config/models.yaml — read from env, NEVER hardcode a model string
tiers:
  reasoning:
    primary:   {provider: tcs, base_url: ${TCS_BASE_URL}, model: ${TCS_REASONING_MODEL}}
    fallback:  [tier:fast, deterministic_rule_engine]
    budget:    {max_tokens: 2000, timeout_ms: 8000}
  fast:
    primary:   {provider: tcs, model: ${TCS_FAST_MODEL}}
    fallback:  [deterministic_rule_engine]
    budget:    {max_tokens: 600, timeout_ms: 3000}
  vision:
    primary:   {provider: tcs, model: ${TCS_VISION_MODEL}, required_capability: supports_vision}
    fallback:  [structured_cv_descriptor]     # ← see below, CRITICAL
    budget:    {max_tokens: 800, timeout_ms: 6000}
```

Use **LiteLLM** or a thin `LLMPort` ABC with adapters. Two implementations minimum: `TCSAdapter` and `FakeAdapter` (deterministic, for tests and offline demo).

### Critical: the no-vision-model fallback

If the TCS endpoint has no vision model, your "VLM describes the defect" step dies. **Build the fallback first, treat the VLM as the enhancement.**

The `structured_cv_descriptor` computes classical features over the anomalous region and renders them into structured text that a *text-only* model then reasons over:

```
Anomalous region at (412, 288), 34×29 px, 2.1% of inspection ROI.
Location: bolt seat, position 3 of 5 (clockwise from valve stem).
Intensity: 41% below local normal mean. Edge density 2.8× baseline.
Hue shift: +12° toward amber. Specular response: diffuse (expected: specular).
Geometry: irregular boundary, aspect 1.17, no straight edges.
Nearest labelled exemplar: thread_contamination (cosine 0.71).
```

Feed that to the reasoning tier. You get a grounded natural-language explanation with **no vision model required** — and it is arguably *more* defensible than a VLM, because every input is a measured quantity rather than a model's impression. Put that argument in `DECISIONS.md`; it converts a constraint into a strength.

### Required guards

Response cache keyed on `(tier, prompt_hash, pack_id)` in Redis with long TTL in `DEMO_MODE` · per-request and per-session token ceilings · circuit breaker on the endpoint · **pre-warm the entire demo path during setup** so every stage response is already cached · every degradation visible in the trace and the UI.

---

## §7. Tech stack (pinned)

**Backend:** Python 3.11 · FastAPI + Uvicorn (async) · Pydantic v2 · SQLAlchemy 2.0 + Alembic · PostgreSQL 16 · Redis 7 (cache, pub/sub, rate limits, breaker state, idempotency) · ChromaDB (persistent) · MinIO

**AI:** LangGraph (orchestration, checkpointing, `interrupt()` for HITL) · LangChain (tools, retrievers) · LiteLLM (provider abstraction) · sentence-transformers `bge-small-en-v1.5` + `bge-reranker-base` · `rank_bm25` · PyTorch + timm · faiss-cpu · scikit-learn · ruptures · opencv-python-headless

**Video (laptop-GPU-safe):** decode with PyAV or OpenCV · **sample at 5 fps, not 30** · 256×256 ROI crops, not full frames · per-frame PatchCore scoring · ByteTrack-lite or centroid tracking for unit identity across frames · rolling temporal consistency filter

**Frontend:** React 18 + TypeScript + Vite · TailwindCSS + shadcn/ui · TanStack Query · Zustand · React Router v6 · Recharts · `@xyflow/react` (live agent graph) · framer-motion (restrained) · native WebSocket

**Ops:** Docker + compose · structlog · prometheus-client + Grafana · OpenTelemetry · pytest + pytest-asyncio + httpx · locust

**Explicitly not using — say so, it shows judgment:** Kafka (Redis Streams suffices at demo scale; document the migration) · Kubernetes · any fine-tuned model (no labelled defect data exists on day one — that is the entire point) · video transformer models (a laptop GPU cannot serve them at line rate, and per-frame + temporal aggregation is the correct engineering answer anyway).

---

## §8. Laptop-GPU vision engine

```
1. ROI extraction     Locate the wheel hub via Hough circle + template alignment.
                      Crop to a canonical 512×512, rotation-normalised by valve-stem position.
                      DO NOT run the network on the full frame — you cannot afford it.

2. Features           ResNet18 (NOT ResNet50 — you have a laptop GPU), layers 2+3, frozen.
                      Half precision. Batch frames. Inference only, never training.

3. Memory bank        Embed ONLY normal images from the active pack.
                      Coreset-subsample to 1%. FAISS IndexFlatL2 (exact — the bank is small).
                      Target: <150MB resident, <40ms per frame.

4. Anomaly score      Per-patch NN distance → anomaly map. Image score = 99th pct patch distance
                      (more robust than max on noisy frames).

5. Geometric          Deterministic verifiers from verifiers.yaml, running in PARALLEL:
   verifiers          fastener count (expect 5), bolt-circle diameter, angular spacing
                      uniformity, hub flushness, TPMS presence, wheel-weight presence.
                      These are cheap, exact, explainable, and they catch missing_fastener
                      with 100% reliability. Do not make a neural net do arithmetic.

6. Temporal           A defect fires only if it persists ≥3 of 5 consecutive frames on the
   aggregation        SAME tracked unit. This is your video story AND your false-positive
                      killer — single-frame glare and motion blur get filtered out.

7. Classification     Nearest labelled exemplar in the pack's class memory.
                      No match >0.6 → UNKNOWN → HITL. Never guess a class.

8. Description        vision tier if available, else structured_cv_descriptor (§6).

9. Learning           Inspector names the class → append embeddings to exemplars →
                      the next instance auto-classifies. Live, on stage.
```

**Performance targets on a laptop GPU:** 5 fps sustained on one station, <40ms per frame extraction, <15ms FAISS query, <200ms end-to-end vision verdict. **Measure and display these in the UI.** If you cannot hit 5 fps, drop to 3 and say so — an honest measured number beats an unverified claim every time.

**Write into `DECISIONS.md`:**

| | Memory bank + geometric verifiers (chosen) | Supervised CNN / YOLO (BMW & Tesla's approach) |
|---|---|---|
| Labelled defects needed | **Zero** | Hundreds per class (BMW: ~100 images per pseudo-error characteristic) |
| Unseen defects | Caught by construction | Silently missed — the dangerous failure mode |
| New variant onboarding | Minutes | Weeks |
| Explainability | Distance to nearest normal patch + exact geometric measurement | Gradient saliency, post-hoc and contested |
| Hardware | Laptop GPU | Edge GPU per station |
| **Cons** | ~150MB bank resident; weaker on subtle low-contrast texture; requires a clean normal set | Higher ceiling *given the data*; faster inference; smaller footprint |
| **Mitigation** | Coreset subsampling; fuse with sensor signal for low-contrast cases; data-quality gate on the normal set | — |

---

## §9. Synthetic data — wheel assembly

### 9.1 Images (procedural, no 3D assets)

Generate a canonical wheel hub face: rim annulus with brushed-metal texture, 5 bolt seats on a 120mm PCD, centre cap, valve stem, TPMS sensor, wheel weight. Randomise: lighting angle and intensity, specular hotspots, mild perspective, rotation, sensor noise, motion blur, partial occlusion by a robot arm.

Inject defects parametrically, severity 0.0–1.0, **with ground truth recorded per frame**:

```
missing_fastener       remove a bolt, render the empty seat with shadow
thread_contamination   dark irregular residue at thread entry + diffuse (not specular) response
hub_seating_gap        sub-pixel offset + shadow line between wheel and hub face
wrong_wheel_variant    change spoke count / finish, contradicting the MES order spec
missing_tpms           remove sensor
rim_surface_damage     bezier scratch aligned to machining direction
missing_wheel_weight   remove weight
[HOLDOUT] valve_stem_damage   NEVER SEEDED. Generator exists, output is quarantined
                              outside the memory bank and the exemplar set.
```

### 9.2 Video — 2.5D composite (recommended, decided)

Do **not** build 3D. Animate generated stills over a moving conveyor plate: horizontal translation at line speed, slight rotation, per-frame lighting jitter, occasional robot-arm occlusion, 30 fps output sampled at 5 fps for inference. Full parametric ground truth per frame, near-zero asset cost, and it swaps to brake/welding by changing the base plate render.

**Stage layer — `FEATURE_WEBCAM_INGEST`, feature-flagged:** a webcam or phone pointed at a physical prop (toy wheel or a plate with removable bolts) into the same frame-stream pipeline. Pull a bolt live, watch the count verifier fire and the andon go red. Near-zero build cost, enormous in the room. **It must be impossible for this to break the core demo** — if the webcam fails, the composite stream continues untouched.

### 9.3 Sensors — including the torque-angle signature

Per fastener, per station: `torque_nm`, `angle_deg`, `spindle_current_a`, `seating_time_ms`, `tool_age_cycles`, `tool_last_calibration_days`, plus ambient `temperature_c` and `humidity_pct`.

**The torque-angle curve is the crown jewel — generate it properly.** A fastener run produces a torque-vs-angle trace: a low-slope run-down phase, a knee at seating, then a steep near-linear elastic slope to target.

```
clean dry thread        knee at ~14°, elastic slope 3.2 Nm/deg, smooth
contaminated thread     knee delayed to ~22°, slope 2.1 Nm/deg  ← FINAL TORQUE STILL IN SPEC
cross-threaded          erratic run-down, early false knee, high variance
over-torqued            elastic slope continues past yield, curve flattens (plastic deformation)
under-torqued           run terminates before the elastic region completes
```

The Process Sentinel scores **curve shape** — knee angle, elastic slope, residual variance — not the endpoint. That is what makes the §2 demo work, and it is a real technique, not something invented for a hackathon.

### 9.4 Hidden causal graph

```
tool_age_cycles ─────────┐
tool_calibration_days ───┼──> torque_delivery_error ──┐
spindle_current ─────────┘                            ├──> P(over_torque), P(under_torque)
ambient_humidity ──> thread_surface_condition ────────┘
                              │
                              └──> P(thread_contamination) ──> clamp_load_deficit
material_lot_hardness ──> P(cross_threading)
shift == "C" (night)  ──> P(missed_manual_check)      ← bias-audit hook, keep it, report it
```

Agents never see the graph. `make eval` scores Root Cause top-1/top-3 against it. **Every team claims their agent explains root cause. You will show 87% top-1 accuracy and a confusion matrix.**

### 9.5 Dirty data — deliberately

NaNs, out-of-range spikes, stuck-at-value sensors, clock skew, dropped frames, blown-out exposure. Then show the data-quality gate rejecting ~3% and explain what it caught. "We tested on clean data" is a weak answer; a rejection log is a strong one.

### 9.6 Privacy and lineage

Operator IDs → deterministic tokens (`OP-7f3a`), reversible only for `QUALITY_MANAGER`+, always audited. Supplier and material-lot IDs masked for non-privileged roles. PII redaction runs **before** any text reaches an LLM, with `tests/redteam/test_pii_egress.py` failing CI on leak. Every record carries `source`, `generated_at`, `generator_version`, `is_synthetic: true`. The UI never shows synthetic data without a `SYNTHETIC` chip.

---

## §10. Real MES integration — ERPNext

> You asked for a real open-source MES, not a mock. **ERPNext (Frappe) is the right choice** and it is a genuine differentiator: nobody else in that room will have integrated with a real ERP/MES.

### Why ERPNext

It ships the exact doctypes this problem needs: `Item`, `BOM`, `Work Order`, `Job Card` (per-operation tracking), `Workstation`, `Operation`, `Batch`, `Serial No`, `Stock Entry`, and — critically — **`Quality Inspection`** with `Quality Inspection Template`, configurable inspection parameters and acceptance criteria. Inspection type supports **In Process (Manufacturing)**, and the reference document type can be a **Job Card**. Clean REST API (`/api/resource/{DocType}`) with API key/secret auth, filtering and pagination. Standard Docker deployment.

**The killer property:** ERPNext treats quality as a *transactional dependency*, not an informational add-on — a Delivery Note cannot be submitted while a required inspection is unapproved. So when FORGE writes a rejected Quality Inspection, **the shipment is genuinely blocked inside a real ERP.** That is not a demo animation. That is a real business consequence, live, and it is the strongest possible answer to "is this actually integrated?"

It also enforces role separation natively — QC managers approve, inspectors enter data — which maps onto your RBAC story.

### What FORGE writes back

| FORGE event | ERPNext write |
|---|---|
| Inspection verdict | `Quality Inspection` (In Process) against the `Job Card`, with per-parameter readings and accepted/rejected status |
| Defect confirmed | `Quality Inspection` rejected → downstream transaction blocked |
| Rework decision | `Job Card` update + rework `Stock Entry` |
| Line halt approved | `Work Order` status change + `Quality Inspection` on the batch |
| Root cause: tool wear | Maintenance request against the `Workstation` |

### What FORGE reads

Order spec for `wrong_wheel_variant` (compare the built wheel against the BOM variant — this is BMW's build-to-order problem, and you solve it), work order and job card context, batch and serial, workstation and operation metadata, QI template acceptance criteria as the source of truth for spec limits.

### Risk and mitigation — read this carefully

ERPNext is a heavy stack: MariaDB + Redis + gunicorn + background workers + scheduler + nginx. Running it alongside Postgres, Chroma, MinIO, your API, and a GPU workload on one laptop is genuinely tight.

1. Put ERPNext behind an `MESPort` ABC with **three** adapters: `ERPNextAdapter`, `InMemoryMESAdapter` (fast, for tests), and `RecordedERPNextAdapter` (replays captured real responses — your demo insurance).
2. Boot it on the highest-spec machine and pre-seed it in Phase 1: company, warehouse, wheel-assembly Item + BOM, Workstation, Operation, QI Templates per pack, 50 historical Work Orders and Quality Inspections.
3. Snapshot the seeded MariaDB volume. Restoring it must take under 60 seconds. Rehearse the restore.
4. If ERPNext is not stable by **hour 20, switch to `RecordedERPNextAdapter` and stop.** Note the hard checkpoint in §17. This is the one component allowed to be abandoned, because the adapter pattern means the rest of the system does not notice.

---

## §11. Agent architecture

### Topology — supervisor + specialists, two reflection loops, explicit adjudication

```
                          ┌──────────────────┐
                          │   ORCHESTRATOR   │  routes, enforces token/latency budgets
                          └────────┬─────────┘
        ┌──────────────────┬───────┴───────┬──────────────────┐
        ▼                  ▼               ▼                  ▼
 ┌─────────────┐   ┌──────────────┐ ┌──────────────┐  ┌──────────────┐
 │  INGESTION  │   │    VISION    │ │   PROCESS    │  │   CONTEXT    │
 │             │   │  INSPECTOR   │ │  SENTINEL    │  │  (ERPNext)   │
 │ frames,     │   │ patchcore +  │ │ torque-angle │  │ order spec,  │
 │ sensors,    │   │ verifiers +  │ │ signature,   │  │ BOM, tool,   │
 │ QC gate     │   │ temporal agg │ │ drift, risk  │  │ batch, lot   │
 └──────┬──────┘   └──────┬───────┘ └──────┬───────┘  └──────┬───────┘
        └─────────────────┴────────┬───────┴─────────────────┘
                                   ▼
                        ┌────────────────────┐
                        │    ADJUDICATOR     │◄─ reflection 1: disagreement → re-query
                        │  resolves conflict │   THIS IS THE §2 DEMO
                        └─────────┬──────────┘
                                  ▼
                        ┌────────────────────┐
                        │    ROOT CAUSE      │◄─ reflection 2: groundedness <0.7 → re-retrieve
                        │   (Hybrid RAG)     │
                        └─────────┬──────────┘
                                  ▼
                        ┌────────────────────┐
                        │      TRIAGE        │  cost model, risk engine, ₹/$ impact
                        └─────────┬──────────┘
                                  ▼
                        ┌────────────────────┐
                        │      GUARDIAN      │  PII, injection, unsafe rec, tool authz
                        └─────────┬──────────┘   FAIL CLOSED
                        ┌─────────┴──────────┐
                        ▼                    ▼
              ┌──────────────────┐   ┌───────────────────┐
              │    HITL GATE     │──►│      ACTION       │ ERPNext QI, Job Card, Slack
              │  interrupt()     │   └─────────┬─────────┘
              └──────────────────┘             ▼
                                     ┌───────────────────┐
                                     │  LEARNING AGENT   │ → memory bank, thresholds
                                     └───────────────────┘
```

Plus a standalone **ANALYST AGENT** (`Cmd+K` on every page) for conversational Q&A over live and historical data.

### Agent table — reproduce this in `docs/AGENTS.md` with prompt, budget, failure mode per agent

| # | Agent | Job | Tools | Fallback |
|---|---|---|---|---|
| 1 | **Ingestion** | Validate, denoise, normalise, ROI-extract, quality-gate, assign correlation ID | `frames.decode`, `image.preprocess`, `dq.gate` | Pass through flagged `data_quality: degraded` |
| 2 | **Vision Inspector** | Anomaly score + heatmap + geometric verifiers + temporal aggregation + description | `patchcore.score`, `verifiers.run`, `llm.vision` / `cv.describe` | Verifiers + heatmap only, no narrative |
| 3 | **Process Sentinel** | Torque-angle signature scoring, drift, change point, **forward risk forecast** | `signature.analyze`, `anomaly.score`, `changepoint.detect`, `risk.forecast` | Static spec-limit rules |
| 4 | **Context** | Pull order spec, BOM variant, job card, tool age, calibration, batch, lot from ERPNext | `mes.job_card`, `mes.bom_variant`, `mes.workstation` | Cached context + `context: stale` flag |
| 5 | **Adjudicator** | Reconcile vision vs. process vs. spec; reason about *why* each signal may be unreliable now | `llm.reason`, `history.similar_conflicts`, `reliability.profile` | Escalate to human — fail safe |
| 6 | **Root Cause** | Hybrid RAG over SOPs, torque standards, incident history + sensor correlation → ranked hypotheses **with citations** | `rag.hybrid_search`, `mes.machine_history`, `sensors.correlate`, `llm.reason` | Top-3 similar incidents, no synthesis |
| 7 | **Triage** | Cost model: continue / rework / scrap / halt, with ₹ and $ impact and confidence interval | `cost.model`, `risk.calculate`, `mes.wip_value`, `fx.convert` | Conservative default: flag for human. **Never auto-halt.** |
| 8 | **Guardian** | PII scan, injection detection, unsafe-recommendation block, tool authorization, schema validation | `pii.scan`, `injection.detect`, `policy.evaluate` | Block + escalate. **Fail closed.** |
| 9 | **Action** | Write ERPNext Quality Inspection / Job Card, Slack notify, publish events — idempotent | `mes.create_qi`, `mes.update_job_card`, `slack.notify`, `events.publish` | DLQ + backoff retry, surfaced in Integration Hub |
| 10 | **Learning** | Inspector verdict → memory bank append, threshold recalibration, new class registration | `memorybank.append`, `threshold.tune`, `metrics.recompute` | Log feedback, defer to batch |
| 11 | **Analyst** | Conversational Q&A with citations and generated charts | `sql.query` (allowlisted views only), `rag.hybrid_search`, `chart.generate` | "I can't answer that from available data." Never guess. |

### The three details that prove it is really multi-agent

**(a) Visible adjudication of genuine conflict.** The §2 thread-contamination case: Vision 0.58 (borderline), Torque endpoint PASS, angle signature ANOMALOUS. The Adjudicator must show its reasoning on the Agent Console: *"Final torque is in spec at 118 Nm. However the elastic slope is 2.1 Nm/deg against a 3.2 baseline for this fastener class, and the knee is delayed 8°. This signature is consistent with contaminated threads, under which torque is not a valid proxy for clamp load. Vision shows a diffuse response at the bolt seat. Escalating: predicted clamp load deficit."* **Rehearse reading that line out loud.**

**(b) Reflection with a measurable trigger.** Root Cause self-scores groundedness; below 0.7 it re-retrieves with relaxed metadata filters. Show the retry counter increment in the trace. Reflection that fires on a number, not on vibes.

**(c) Budgeted, degrading orchestration.** The Orchestrator holds per-request token and latency budgets. Exceed → downgrade that node to the fast tier and annotate `degraded: budget_exceeded`. No hackathon team implements graceful AI degradation. It takes two hours and it is unforgettable.

### State schema — freeze in Phase 0

```python
class QCState(TypedDict):
    correlation_id: str
    pack_id: str                        # active Use Case Pack
    unit_id: str                        # tracked across frames
    event: InspectionEvent
    frames: list[FrameVerdict]          # per-frame, for temporal aggregation
    vision: VisionVerdict | None
    process: ProcessVerdict | None
    context: MESContext | None
    fusion: FusionVerdict | None
    root_cause: RootCauseReport | None
    triage: TriageDecision | None
    guardrail: GuardrailReport
    actions: list[ActionRecord]
    hitl: HitlRequest | None
    trace: list[TraceSpan]              # every node appends — this IS the audit log
    retries: dict[str, int]
    budget: BudgetLedger
    degradations: list[str]
    status: Literal["ingesting","analyzing","adjudicating","awaiting_human","acting","complete","failed"]
```

Persist every transition. That one table is the audit log (#8), the Agent Console data source (#7), the eval harness input (#6), and the replay feature. **One table, four rubric points.**

### HITL gates

| Gate | Trigger | Approver | Timeout |
|---|---|---|---|
| Low confidence | fused confidence ∈ [0.45, 0.70] | Inspector | 5 min → conservative default |
| High consequence | halt line, or cost impact > ₹50,000 | Supervisor | 2 min → escalate |
| Novel class | anomalous, no exemplar match > 0.6 | Inspector | none — this is a learning event |
| Safety-critical override | inspector overrides a `critical` verdict to pass | Quality Manager | none — dual approval |

Answerable from **both** the UI and Slack (Block Kit buttons resolving the `interrupt()`). Approve / Reject / **Modify** — and Modify feeds the Learning Agent, closing the loop. All audit-logged with actor, timestamp, and the exact state snapshot they saw.

---

## §12. Hybrid RAG

```
Query
  ├─► BM25 (rank_bm25)                       → top 30      favours torque specs, part numbers
  ├─► Dense (bge-small-en-v1.5, Chroma)      → top 30      favours narrative incident reports
  └─► Metadata prefilter (pack, station, defect family, date window)
         ▼
  Reciprocal Rank Fusion (k=60)              → top 20
         ▼
  Cross-encoder rerank (bge-reranker-base)   → top 5
         ▼
  Context assembly (dedupe, token budget, citation IDs)
         ▼
  Generation with mandatory inline citations [DOC-id §chunk]
         ▼
  Groundedness self-check → <0.7 triggers re-retrieval
```

**Collections:** `qc_knowledge` (SOPs, torque standards, ISO extracts) · `incident_history` (past defects + resolutions, richly filtered) · `patch_memory` (image embeddings — FAISS actually; document why in the ADR).

**Corpus per pack, ~120 docs across four types**, because retrieval differences only show on a heterogeneous corpus: SOPs (structured, jargon → BM25 wins) · maintenance manuals (long, descriptive → dense wins) · incident narratives (hybrid wins) · torque spec tables (metadata filtering wins).

Build the **Retrieval Playground** page: judge types a query, sees BM25 / dense / fused / reranked side by side with scores. One page, three rubric points. And run the ablation on a golden question set — put the table in `DECISIONS.md`. **That table is the single highest-value artefact for rubric #4.**

---

## §13. Guardrails and Responsible AI

**Input:** PII detection + masking before any LLM call (regex + NER, with a CI-failing egress test) · prompt injection detection on all free text — **inspector notes, chat, and uploaded documents; judges will try the chat box, make sure it holds** · schema validation, size limits, upload content-type allowlist.

**Execution:** **tool authorization matrix (role × tool), enforced at the tool layer, not in the prompt** — an Operator's session physically cannot call `mes.create_qi`. Say that out loud: *prompts are not a security boundary.* · **Autonomy ceiling: no agent halts a production line without human approval. Ever. Hardcoded and tested.** · per-request and per-tenant cost ceilings.

**Output:** structured output validation with repair-retry then fail-closed · groundedness scoring, unsupported claims stripped · **confidence calibration with a reliability diagram in-product** — uncalibrated confidence is worse than none · unsafe-recommendation filter (block anything that would violate a safety SOP, e.g. "skip the re-torque check to hold takt time").

**Fairness:** slice detection rate and false-positive rate by **shift, station, tool age, operator cohort, wheel variant**. Surface it in-product. The night-shift gap is in your causal graph — **find it, show it, explain the mitigation.** Judges reward a team that audits itself far more than one that claims perfection.

**Model cards** for every AI component — purpose, inputs, reference data, metrics, known limits, out-of-scope uses, human oversight — shipped in-app at `/responsible-ai`, not just in markdown.

**Safety-critical framing to say on stage:** *"Wheel fastener defects cause wheel-off events. We designed the autonomy boundary around that. FORGE can reject a part autonomously. It cannot pass one, and it cannot halt a line, without a human."* That single sentence answers rubric #8 better than any dashboard.

---

## §14. API surface

Every route: JWT, RBAC dependency, rate limit, correlation ID, structured log, OpenAPI tag + example.

```
POST   /api/v1/auth/login | refresh | logout          GET /api/v1/auth/me

GET    /api/v1/packs                          list Use Case Packs
POST   /api/v1/packs/{id}/activate            ← THE LIVE SWITCH
GET    /api/v1/packs/{id}/manifest

POST   /api/v1/inspections                    submit (frames + sensors)
GET    /api/v1/inspections                    paginated, filterable
GET    /api/v1/inspections/{id}               full record + agent trace
GET    /api/v1/inspections/{id}/heatmap
GET    /api/v1/inspections/{id}/torque-curve  torque-angle trace + baseline overlay
POST   /api/v1/inspections/{id}/feedback      inspector verdict → Learning Agent

POST   /api/v1/stream/frames                  video frame ingest (composite or webcam)
GET    /api/v1/stream/status                  fps, latency, dropped frames, tracked units

GET    /api/v1/agents/runs/{correlation_id}   full trace: nodes, timings, tokens, cost
GET    /api/v1/agents/graph                   live topology for React Flow
GET    /api/v1/agents/metrics                 per-agent latency, cost, success rate
POST   /api/v1/agents/replay/{id}             re-run a past event

GET    /api/v1/hitl/queue                     pending for my role
POST   /api/v1/hitl/{id}/decide               approve | reject | modify

POST   /api/v1/knowledge/documents            upload → chunk → embed
POST   /api/v1/knowledge/search               hybrid search with PER-STAGE scores
GET    /api/v1/knowledge/documents/{id}       resolve a citation

GET    /api/v1/analytics/defect-rate | pareto | spc | cost-of-quality | first-pass-yield
POST   /api/v1/analytics/roi                  ROI calculator

GET    /api/v1/mes/health                     ERPNext breaker state, latency, error rate
GET    /api/v1/mes/events                     integration log + DLQ
POST   /api/v1/mes/dlq/{id}/retry
GET    /api/v1/mes/quality-inspections        read back from ERPNext — proves round trip

GET    /api/v1/governance/audit | guardrail-events | bias-report
GET    /api/v1/admin/users | roles | rate-limits | feature-flags | model-config | capabilities

POST   /api/v1/assistant/chat                 Analyst Agent (SSE)

WS     /ws/live                               frames, verdicts, agent steps, alerts, HITL
GET    /health  /ready  /metrics
```

---

## §15. Frontend

### Pages (14, role-gated)

| # | Route | Page | Must contain |
|---|---|---|---|
| 1 | `/login` | Sign in | Role-select demo shortcuts — judges will switch roles fast |
| 2 | `/` | **Command Center** | Andon bar, **live video panel with anomaly overlay**, station status, streaming verdict feed, throughput + FPY tickers, agent activity ribbon, **active pack switcher** |
| 3 | `/inspections/:id` | **Defect Workbench** | Frame + heatmap toggle/opacity, **torque-angle curve vs. baseline overlay**, verifier results table, agent reasoning trace, root cause with citations, triage with ₹ impact, Approve / Override / Reclassify |
| 4 | `/agents` | **Agent Console** | Live React Flow graph, nodes animating, per-node latency/tokens/cost, retry + degradation markers, click a node → prompt, tools called, raw output |
| 5 | `/root-cause` | **Root Cause Explorer** | Causal chain viz, ranked hypotheses + evidence, similar incidents, citation drill-through |
| 6 | `/analytics` | **Quality Analytics** | SPC chart with UCL/LCL + Nelson rules, Pareto by cost, defect rate by shift/station/variant, cost-of-quality waterfall, FPY trend |
| 7 | `/roi` | **Business Impact** | ROI calculator with sensitivity sliders, before/after, payback period |
| 8 | `/integrations` | **Integration Hub** | ERPNext health + breaker, event log, DLQ + retry, **fault injection panel**, live Quality Inspection records read back |
| 9 | `/knowledge` | **Knowledge Base** | Upload + ingest status, chunk viewer, **Retrieval Playground** |
| 10 | `/hitl` | **Approval Queue** | Pending decisions + SLA countdown, full context, approve/reject/modify, history |
| 11 | `/responsible-ai` | **Responsible AI Center** | Guardrail events, PII masking log, bias report by slice, calibration diagram, model cards, autonomy boundaries |
| 12 | `/packs` | **Use Case Packs** | Installed packs, manifest viewer, memory bank stats, **activate button** |
| 13 | `/admin` | **Admin & Governance** | Users, permission matrix, audit log, rate limits, feature flags, **model config + live capability probe results** |
| 14 | `/health` | **System Health** | Service status, metrics, FPS/latency, recent errors, trace explorer, load test results |

Plus `/profile` (persona, notification prefs, saved views) and a global **`Cmd+K` Assistant drawer** on every page.

Header: live plant clock, shift indicator, active pack, ambient conditions (live API), connection status, alert bell, role switcher. Footer: build SHA, active model tier, `SYNTHETIC DATA` notice, docs and API links.

### Design system — "Cleanroom at night"

Not the default near-black-plus-acid-accent dashboard. The reference is **anodized metal under vapour lamps, seen through polycarbonate machine guarding** — the material the product actually lives inside. Glass panels read as tinted safety shielding, not as a Dribbble effect.

```css
--surface-void:   #0B1017;   --surface-base:  #10161F;   --surface-raised: #161E29;

--glass-bg:     rgba(148, 176, 205, 0.055);
--glass-border: rgba(168, 199, 230, 0.11);
--glass-blur:   20px;
--glass-inner:  inset 0 1px 0 rgba(255,255,255,0.06);

--accent-molten: #FF7A1A;   /* primary action, hot process */
--accent-signal: #35D6E8;   /* telemetry, measurement, AI */

/* Andon status — real factory convention; operators read these instantly */
--state-nominal:  #21C97A;  --state-watch:    #F5B32C;  --state-alert: #FF7A1A;
--state-critical: #F0453F;  --state-unknown:  #A78BFA;  /* novel class — deliberately off-palette */

--text-primary: #E8EFF6;  --text-muted: #8CA0B6;  --text-faint: #5A6D82;
```

**Type** (self-host from Fontshare / Vercel — free, distinctive):
- **Clash Display** 500/600 — section titles, KPI numbers. Condensed, mechanical, nothing like Inter (Inter is the tell of a templated dashboard).
- **Satoshi** 400/500/700 — body and UI.
- **Geist Mono** — *every* number, ID, timestamp, torque value, confidence. Rule: if it came from a sensor or a model, it's mono. Tabular figures are how you read a control chart — that's function, not decoration.

**Signature element — the provenance strip.** Every panel showing an AI-derived value carries a hairline mono footer:

```
──────────────────────────────────────────────────────────────
VISION·patchcore-r18  conf 0.94  38ms  5.0fps  ⌁ live  pack:wheel
```

In a quality-control product, *where did this number come from* is the entire job. It encodes something true rather than decorating, it repeats on every panel so it becomes the app's visual rhythm, and it hands you rubric #8 free. **This is the one bold element — keep everything else disciplined.**

**Motion:** one orchestrated moment (the andon bar pulsing on state change; agent nodes lighting sequentially as the graph executes). Nowhere else. Scattered animation is the surest sign of AI-generated UI. Respect `prefers-reduced-motion`.

**Accessibility — glassmorphism's failure mode is contrast.** Mandate 4.5:1 on all text via a solid backing layer beneath text inside glass panels; never rely on blur. Visible focus rings (2px `--accent-signal`). Full keyboard nav. `aria-live="assertive"` on alerts. **Status never by colour alone** — always icon + label. Colour-blind operators exist, and so do colour-blind judges.

**Copy:** active voice, sentence case, operator vocabulary ("Halt line", not "Execute stop command"). Errors state what happened and what to do. Empty states invite action. "Approve rework" produces "Rework approved."

---

## §16. Resilience, observability, security

**Resilience — each demonstrable, not merely present.** Circuit breaker on ERPNext, Slack, weather, LLM (three states, half-open probing, Redis-backed state) · retry with exponential backoff + jitter + idempotency keys, retryable classes only · bulkhead semaphores separating vision, LLM, and MES pools · fallback chain reasoning → fast → cached → deterministic rule, **every level annotated in the trace so degradation is visible, never silent** · DLQ with inspect-and-retry UI · graceful degradation banner telling the user which capabilities are reduced right now.

**Observability.** structlog JSON with `correlation_id` propagated API → agent node → tool → ERPNext. Prometheus: request latency histograms, agent node duration, token cost counter, defect counter, breaker state gauge, HITL queue depth, **frames/sec and dropped frames**, WS connections. Grafana dashboard JSON in-repo. OTel spans per node. **Surface it all in-product** on `/health` — judges will not tab out to Grafana.

**Security.** JWT access 15min + rotating refresh 7d · argon2 · 5-role RBAC with the permission matrix in code and a test asserting it · rate limits per role (Operator 60/min, Manager 300/min, Admin 1000/min) via Redis token bucket with `X-RateLimit-*` headers · secrets from env only · CORS allowlist · security headers · audit log on every state-changing action (actor, action, resource, before/after, IP, correlation ID) · documented and implemented retention purge job.

**Live public APIs — load-bearing, not decorative.** **Open-Meteo** (no key): live ambient temperature and humidity at the plant, fed into the causal model — humidity genuinely drives thread surface condition, which is the §2 mechanism. Strongest possible answer to "why is this API here?" · **Nager.Date** (no key): public holidays → staffing patterns → correlates with the night/holiday-shift quality gap in the bias report · **Frankfurter** (no key): live FX so cost impact renders in ₹ and $. All three behind the same breaker + cache + fallback path as ERPNext.

---

## §17. Build plan — 36 hours

| Phase | Hours | Goal | Exit criteria (binary) |
|---|---|---|---|
| **0 · Contracts** | 0–1.5 | Freeze interfaces | `docs/CONTRACTS.md` committed: Pydantic models, `QCState`, pack manifest schema, `MESPort`, `LLMPort`, OpenAPI stub, RBAC matrix, design tokens. **TCS capability probe written and run — you must know today whether you have a vision model.** Compose boots empty. 5 worktrees created. |
| **1 · Foundation** | 1.5–8 | Skeletons + data | Image + video + sensor generators producing wheel-assembly data with ground truth. **ERPNext booted and seeded, volume snapshotted.** DB migrated. Auth + RBAC live. React shell with design system, routing, 3 pages stubbed. WebSocket echoes frames. |
| **2 · Intelligence** | 8–16 | The AI works | Memory bank built, AUROC measured on holdout. Geometric verifiers passing. **Torque-angle signature scoring working — the §2 case detected.** LangGraph runs end-to-end on one event. Hybrid RAG returns cited answers. Guardian blocks a red-team prompt. |
| **3 · Integration I** | 16–19 | **Happy path live** | 🔴 **HARD GATE:** video stream → agents → ERPNext Quality Inspection → Slack alert → UI update, live, zero manual steps. **Also: ERPNext go/no-go at hour 20 — if unstable, switch to `RecordedERPNextAdapter` and move on.** If the gate fails at 19, cut scope immediately; do not proceed to Phase 5. |
| **4 · Sleep** | 19–23 | Staggered | 2 people on light integration, everyone else ≥4h. **Non-negotiable.** Hour-30 decisions made by exhausted people lose hackathons. |
| **5 · Completion** | 23–30 | Everything else | HITL (UI + Slack) · Learning loop · **packs 2 and 3 + live switch** · all 14 pages · analytics · resilience demos · observability · ROI · admin. |
| **6 · FREEZE** | **30** | 🔴 **FEATURE FREEZE** | No new features. This is the single most common way good projects lose. |
| **7 · Harden** | 30–32 | Polish + prove | `make eval` produces the metrics table **including the baseline row**. Docs complete. UI polish, error/empty/loading states. `make reset && make demo` verified on a clean machine. **Pre-warm the LLM cache for the whole demo path.** |
| **8 · QA** | 32–34 | Break it | Demo path run 5× end-to-end. Fresh-eyes tester (the PM, no code) tries to break it. Fault injection rehearsed. Fix demo-path blockers only — log everything else, fix nothing else. |
| **9 · Pitch** | 34–35.5 | Rehearse | Deck done. Skit rehearsed. Demo narrated aloud 2× with a timer against the 10–15 min slot. 15 likely judge questions with prepared answers. **Backup video recorded.** |
| **10 · Buffer** | 35.5–36 | Submit | Submission, uploads, links. |

### Workstreams

| WS | People | Scope |
|---|---|---|
| **A · Data & Simulation** | 1 | Image/video/sensor generators, torque-angle curves, causal graph, **all 3 packs**, golden set, seeding |
| **B · Vision & ML** | 1 (CV) | ROI extraction, PatchCore on laptop GPU, verifiers, temporal aggregation, tracking, eval harness |
| **C · Agents & RAG** | 1 (CV/ML) | LangGraph, adjudication, reflection, Hybrid RAG, guardrails, **LLM provider abstraction + fallbacks** |
| **D · Platform & MES** | 1 | Auth, RBAC, API, **ERPNext integration**, resilience, observability, rate limits, Slack, Docker |
| **E · Frontend + INTEGRATION OWNER** | 1–2 | Design system, all pages, live video panel, agent console — **and merges to `main` every 2h** |
| **F · Business & Pitch** | shared/PM | ROI model, personas, `BUSINESS_CASE.md`, competitive benchmark slide, demo script, deck, skit, fresh-eyes QA |

**Two rules that decide the outcome:** the integration owner merges every 2 hours from hour 7, and from hour 30 one person writes zero code and only rehearses.

---

## §18. Evaluation harness — `make eval`

Run on a **holdout golden set the memory bank has never seen.**

| Metric | Target | Why it matters to a judge |
|---|---|---|
| Anomaly AUROC / PR-AUC | > 0.92 / > 0.85 | Threshold-independent — shows you understand evaluation |
| Precision / Recall / F1 @ operating point | > 0.90 / > 0.94 | Recall weighted higher: a missed wheel defect is a recall, a false alarm is a rework |
| **Fusion-only defect detection rate** | **> 0.80** | **The §2 case. Nothing else in the market catches it.** |
| Unseen-defect detection (`valve_stem_damage` holdout) | > 0.85 | The differentiator, measured |
| Geometric verifier accuracy | 1.00 | Deterministic checks should be exact — say so |
| Root-cause top-1 / top-3 | > 0.70 / > 0.90 | Scored against the hidden causal graph |
| Groundedness (citation-supported claims) | > 0.90 | Hallucination control, measurable |
| Guardrail catch rate on red-team set | > 0.95 | Responsible AI, measurable |
| Temporal filter FP reduction | report it | Proves the video pipeline earns its cost |
| p50 / p95 end-to-end | < 2s / < 4s | Real-time claim, verified |
| Sustained FPS on laptop GPU | ≥ 5 | Honest measured throughput |
| Cost per inspection | < $0.02 | Unit economics → feeds ROI directly |
| **Baseline: spec-limit rule inspector** | **F1 ~0.61** | **The row that proves AI earned its place** |
| **Baseline: vision-only (no fusion)** | **misses 100% of §2 cases** | **The row that proves multi-agent earned its place** |

Those last two rows matter more than every other. Teams who show a baseline are believed. Teams who show only their own accuracy are not.

### Test suite

```
tests/unit/           domain, cost model, risk engine, RRF, chunking, guardrail rules,
                      torque-angle signature scoring
tests/integration/    API + DB + Redis + Chroma, auth, RBAC matrix, rate limits
tests/agents/         each node isolated w/ fixtures; graph routing; interrupt/resume
tests/mes/            ERPNext round trip: write QI → read back → verify blocking behaviour
tests/e2e/            frame stream → verdict → ERPNext → learning update
tests/resilience/     breaker states, retry, DLQ, fallback chain, bulkhead
tests/redteam/        injection corpus, PII egress, unauthorized tool attempts,
                      unsafe recommendations, jailbreak-via-uploaded-document
tests/architecture/   import-graph rules; pack isolation (no pack strings in domain/)
tests/load/           locust: 100 concurrent inspections, p95 assertion
```

---

## §19. Demo script — 12 minutes, build backwards from this

Write `docs/DEMO_SCRIPT.md` in Phase 0. **Order: requirements → happy path → then everything else. Never open with architecture.**

**0:00 — Skit (60s), two people, no slides.** A supervisor ships a batch of wheels. Six months later: a recall notice for over-torqued hub bolts, exactly like GM's N232431480. Rewind. Same scene with FORGE: an alert at the station, before the wheel left. Land one line: *"The torque gun said it was fine. It was not fine."*

**1:00 — The problem and what the leaders do (90s).** BMW's AIQX, live since 2023, needs ~100 labelled images per defect characteristic. Tesla retrains continuously on millions of labelled factory images. Both are extraordinary — and both need labelled defect data and per-task engineering that a tier-2 supplier cannot fund. *"Algorithms aren't the moat any more. We built for the plants that can't afford BMW's data pipeline."*

**2:30 — Happy path (60s).** Live video, wheel arrives, verifiers green, five fasteners confirmed, torque signature nominal, verdict in under 2 seconds. Provenance strip: model, confidence, latency, FPS. **Show it working normally before you show it working hard.** This "boring" beat is what makes everything after it credible.

**3:30 — Prediction, not detection (60s).** Tool age crosses 1,200 cycles, torque delivery variance widens. Process Sentinel goes amber and forecasts elevated risk for the next 40 units — **before a single bad wheel exists.** *"AIQX finds the defect. We find the drift."*

**4:30 — The money shot: the defect nobody else can catch (2m).** Ignore the warning. Next wheel: torque reads 118 Nm, dead centre of spec — **PASS**. Vision: bolt present, seated, count correct — **PASS**. FORGE flags it anyway. Open the Agent Console and show the Adjudicator's reasoning live. Show the torque-angle curve against baseline: knee delayed 8°, elastic slope 2.1 vs 3.2. *"Torque specs assume clean dry threads. Contaminated threads change the friction, so the gun reads in-spec while the clamp load is 30% under. This is the GM recall failure mode. No camera catches it. No torque gun catches it. It only exists in the disagreement between them — and that's what a multi-agent system is actually for."*

**6:30 — Root cause with receipts (60s).** Ranked hypotheses, every claim cited and clickable: humidity 71% since 06:00 [Open-Meteo, live], thread condition threshold in SOP-WA-114 §4.2, tool 340 cycles past calibration [ERPNext], three similar incidents.

**7:30 — Decision in money, approved from Slack (60s).** Triage: rework 12 units ₹38,400 vs halt line ₹4,96,000. Crosses the consequence threshold → supervisor approval required. **Approve from Slack.** A **Quality Inspection record appears in real ERPNext** and the downstream transaction is genuinely blocked. *"That's not a mock. That's a real open-source MES, and that shipment cannot leave."*

**8:30 — The unseen defect (60s).** Inject `valve_stem_damage` — never seeded, no exemplar, no classifier trained on it. Flagged as anomalous, described, routed to HITL as UNKNOWN. Inspector names it. **The next one auto-classifies.** *"Eleven seconds. BMW needed a hundred labelled images per characteristic. And until they had them, every one of these passed."*

**9:30 — The pack switch (60s).** Header dropdown → **Brake Assembly**. Memory bank, taxonomy, sensors, knowledge base, cost model, dashboard all reconfigure in under 10 seconds. Run one inspection. Switch to **Body Welding**. Run one. *"Same engine. Same agents. A different folder. Adding an inspection task is a config change, not a project."*

**10:30 — Break it on purpose (45s).** Fault injection: kill ERPNext. Breaker opens on the Integration Hub. FORGE keeps inspecting, queues writes to the DLQ, degradation banner appears. Restore. DLQ drains. **Breaking your own system on stage is the strongest credibility move available.**

**11:15 — Governance and the numbers (45s).** Responsible AI Center: guardrail events, a caught prompt injection, PII masking log, calibration diagram, and the **night-shift bias finding with its mitigation — surfaced, not hidden.** Then the eval table: F1 vs. both baselines, unseen-defect rate, root-cause accuracy, p95 latency, cost per inspection. Close on ROI: payback in weeks.

**Q&A prep — have crisp answers ready:** *Why not YOLO? · What if your model provider changes? (show the live capability matrix) · How is this different from AIQX? · What breaks first at 100×? · Is the data real? · What's the false-positive rate and what does it cost? · Would you let this halt a line autonomously? (No. Here's why, in code.)*

**Record a backup video.** Hackathon Wi-Fi fails.

---

## §20. Scope discipline

**MUST — the demo dies without these:** generators (image, video, sensor, torque-angle) · causal graph · memory bank + verifiers + temporal aggregation · torque signature scoring · LangGraph with adjudication · Hybrid RAG root cause with citations · cost triage · HITL in UI · ERPNext write-back (or recorded adapter) · learning loop · **all 3 packs + live switch** · Command Center · Workbench · Agent Console · auth + RBAC · WebSocket · Docker one-command boot · eval harness with both baselines.

**SHOULD — strong points, cut if behind at hour 26:** Slack interactive approvals · SPC analytics · ROI calculator · Responsible AI Center · Integration Hub fault injection · Retrieval Playground · admin pages · Grafana · load test · webcam ingest.

**COULD — only if genuinely ahead at hour 28:** voice assistant · agent replay/time-travel · multi-plant tenancy · scheduled PDF reports · mobile view.

**WON'T — say this out loud, it demonstrates judgment:** Kubernetes · real PLC/OPC-UA integration · model fine-tuning · true multi-tenancy · SOC2 · edge deployment · deflectometry for painted surfaces. Have a one-line answer for each: *"Not in 36 hours, and here's the path."*

### Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| **TCS endpoint has no vision model** | **High** | Probe in Phase 0. `structured_cv_descriptor` fallback built **first**, VLM treated as enhancement. |
| **TCS endpoint slow / rate-limited on stage** | **High** | `DEMO_MODE` cache, pre-warm the entire demo path in Phase 7, breaker + fast-tier fallback |
| **ERPNext unstable or too heavy** | **High** | `MESPort` with 3 adapters; volume snapshot; **hard go/no-go at hour 20** → `RecordedERPNextAdapter` |
| Laptop GPU can't sustain 5 fps | Medium | ResNet18 + fp16 + 256px ROI crops + 3 fps floor; report the honest measured number |
| Video pipeline eats the whole build | Medium | 2.5D composite only. **No 3D.** Webcam is feature-flagged and strictly optional. |
| Integration hell at hour 30 | Medium | Contracts frozen Phase 0 · merge every 2h · hard gate at hour 19 |
| Feature creep past freeze | Medium | Hour-30 freeze is a team rule. New ideas go to `IDEAS.md` and ship in zero cases. |
| Someone breaks `main` at hour 33 | Medium | `main` frozen after hour 32. Tag `demo-candidate` at 32 and demo from the tag. |
| Demo Wi-Fi dies | Low | Everything local in Docker; public APIs behind breakers with cached fallbacks; backup video |

---

## §21. Appendix — agent prompts

Keep every prompt in `packs/{pack}/prompts/*.md` and `agents/prompts/*.md`, versioned, loaded at runtime, **never inline in Python**. Version them so you can show prompt iteration in the pitch.

### Adjudicator — the one judges will read

```
You reconcile disagreements between three independent inspection signals on a
wheel assembly station: a vision inspector, a process/torque monitor, and the
MES order specification.

Inputs: each signal's verdict with calibrated confidence, the station's historical
reliability profile per signal, current ambient conditions, tool age and days since
calibration, and the last 5 similar conflicts with their eventual ground truth.

Domain rules you must apply:
- Torque value alone is NOT a valid proxy for clamp load. Torque specs assume clean,
  dry threads. If thread surface condition is suspect — high ambient humidity,
  contamination detected by vision, or an anomalous torque-angle signature — an
  in-spec torque reading does NOT imply correct clamp load.
- The torque-angle signature is more informative than the final torque value.
  A delayed knee angle or reduced elastic slope indicates the fastener did not
  seat normally, even when the endpoint lands in spec.
- Vision degrades on low-contrast defects, high-gloss rim finishes, and when a
  robot arm partially occludes the ROI.
- Sensors degrade for 90 seconds after a tool change and during shift handover.
- NEVER average confidences. Reason about WHY each signal may be unreliable
  under THESE specific conditions.
- Fail safe: when uncertain, prefer the interpretation that risks a false alarm
  over the one that risks a missed defect. Wheel fastener defects cause wheel-off
  events.
- Escalating to a human is a VALID, CORRECT outcome — not a failure.

Return JSON only:
{
  "verdict": "pass" | "defect" | "escalate",
  "confidence": 0.0-1.0,
  "reasoning": "<= 3 sentences, specific to this case, cite the actual numbers>",
  "primary_signal": "vision" | "process" | "spec" | "fusion" | "none",
  "fusion_only": true | false,   // true if NO single signal would have caught this
  "reliability_notes": {"vision": "...", "process": "...", "spec": "..."},
  "escalation_reason": null | "<string>"
}
```

### Root Cause

```
You determine the most likely root cause of a wheel assembly defect.

You have: the fused verdict, the torque-angle trace and its baseline, a 10-minute
sensor window, MES context from ERPNext (work order, job card, tool age, days since
calibration, batch, material lot, operator shift, BOM variant), live ambient
conditions, and retrieved documents (SOPs, torque standards, maintenance manuals,
past incident reports).

Rules:
- Every causal claim MUST cite a retrieved document [DOC-id] or a specific sensor
  observation with its value and timestamp. Uncited claims will be stripped.
- Label FACT (observed or retrieved) separately from INFERENCE (your reasoning).
- Rank up to 3 hypotheses. For each, state what evidence would confirm or refute it.
- If no hypothesis exceeds 0.4 confidence, say so. "Insufficient evidence" is a
  correct and valuable answer.
- Never invent a sensor reading, a tool ID, a work order number, or a document.

Return JSON matching RootCauseReport.
```

---

## §22. Final checklist

- [ ] `make reset && make demo` works on a laptop that has never seen the repo
- [ ] Demo path run start-to-finish 5× with zero intervention
- [ ] `make eval` prints the table **including both baseline rows**
- [ ] The §2 fusion-only case fires reliably, every single time
- [ ] The pack switch completes in under 10 seconds, three times running
- [ ] A real Quality Inspection record is visible inside ERPNext's own UI
- [ ] Every AI value on screen shows source, confidence, latency, cost
- [ ] Judges can log in as 3 roles and see genuinely different products
- [ ] Every headline capability has 2–3 distinct working examples
- [ ] `DECISIONS.md` has ≥10 ADRs with real alternatives and honest cons
- [ ] The night-shift bias finding is in the product, not hidden
- [ ] LLM cache pre-warmed for the entire demo path
- [ ] Backup video recorded and playable offline
- [ ] One person has written no code for 6 hours and is fully rehearsed
- [ ] You can answer *"what breaks first at 100×?"* with a specific component and number
