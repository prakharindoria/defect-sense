# Architecture Decision Records

Every ADR states the alternatives we rejected and the **honest cons of what we
chose**. A decision record with no downsides listed is marketing, and a judge
asking "what are the trade-offs?" will find one immediately.

Status values: `accepted` · `superseded` · `revisit-if`

---

## ADR-0001 — Memory-bank anomaly detection over a supervised defect classifier

**Status:** accepted · **Date:** 2026-08-07 · **Owner:** WS-B

### Context

BMW's AIQX needs roughly 100 real images *per pseudo-error characteristic* —
100 clean, 100 with dust, 100 with oil drops, 100 with actual cracks — before it
can distinguish a metal chip from a crack. Tesla retrains continuously on
millions of labelled factory images. Both work. Both require labelled defect
data that does not exist on day one of a new line, a new SKU, or a rare defect.

### Decision

Model *normal* and flag departures from it. Embed only known-good images into a
PatchCore-style memory bank (ResNet18 layers 2+3, frozen, coreset-subsampled to
1%, FAISS exact search) and score each patch by distance to its nearest normal
neighbour. Pair it with deterministic geometric verifiers for anything countable.

### Alternatives rejected

| Option | Why not |
|---|---|
| YOLO / supervised CNN | Needs hundreds of labelled examples per class we do not have. Silently misses classes it was never trained on — the dangerous failure mode in a safety part. |
| Fine-tuned vision transformer | No labelled data, and a laptop GPU cannot serve it at line rate. |
| Pure classical CV | Robust for geometry, blind to texture and surface anomalies. We use it *alongside*, not instead. |

### Honest cons

- **~150 MB resident** for the memory bank. Real cost on a constrained edge box.
- **Weaker on subtle low-contrast texture** than a well-trained supervised model
  would be *given the data*. We mitigate by fusing with the sensor signal, which
  is exactly where the low-contrast cases show up anyway.
- **Requires a clean normal set.** A contaminated baseline poisons everything
  downstream, so a data-quality gate guards bank construction. This is a real
  operational burden we are moving onto the plant.
- A supervised model has a **higher ceiling** and a smaller footprint once the
  data exists. Our claim is about day one and about tier-2 suppliers, not about
  the asymptote.

### Revisit if

The plant accumulates >500 labelled examples for a class. Then a supervised head
on top of the same embeddings is strictly better for *that* class, and the memory
bank keeps covering the unseen ones.

---

## ADR-0002 — Torque-angle *signature* scoring, not endpoint checking

**Status:** accepted · **Date:** 2026-08-07 · **Owner:** WS-B
**Implementation:** `src/forge/domain/torque.py`

### Context

Torque specifications assume clean, dry threads, because torque is only a proxy
for clamp load and the relationship runs through thread friction. Contaminate the
threads and the gun reads in-spec while clamp load is materially low. This is the
failure mode in **NHTSA campaign 24V237000** (GM, 2023 Colorado / Canyon), which
we pull live from `api.nhtsa.gov` during the demo.

### Decision

Fit a two-segment piecewise-linear model to the torque-vs-angle trace and score
its **shape**: seating knee angle, elastic slope, residual variance, run-down
torque, reversal count, and late/early slope ratio for yield detection. The
breakpoint is found by an exact O(n) sweep over all admissible splits using
prefix sums — no gradient descent, no initial guess, no local minima.

### Measured result

Learned baseline from 120 clean runs at 3σ; contamination at severity 0.8:

| | final torque | knee | elastic slope | score | in spec | fusion-only |
|---|---|---|---|---|---|---|
| clean | 118.5 Nm | 13.9° | 3.19 Nm/deg | 0.00 | yes | no |
| **thread_contamination** | **118.0 Nm** | **20.3°** | **2.31 Nm/deg** | **0.61** | **yes** | **YES** |
| cross_threading | 118.1 Nm | 11.3° | 2.87 Nm/deg | 0.65 | yes | YES |
| over_torque | 125.4 Nm | 14.2° | 2.82 Nm/deg | 0.55 | no | no |
| under_torque | 101.2 Nm | 13.9° | 3.19 Nm/deg | 0.00 | no | no |

Fusion-only detection rate across 120 seeds/severities: **> 80%**
(`tests/unit/test_torque_signature.py::test_contamination_detected_across_severities`).

### Alternatives rejected

| Option | Why not |
|---|---|
| Spec-limit check on final torque | Misses 100% of contamination cases by construction. This is our published baseline row. |
| LSTM / 1-D CNN on the raw trace | Needs labelled failures; opaque to a quality engineer; unjustifiable for six measurable features. |
| DTW against a golden curve | Sensitive to run length and sampling rate, and produces a distance rather than a diagnosis. |

### Honest cons

- **Mild over-torque that stays in spec is not reliably caught** (severity 0.4
  scores 0.27, below threshold). The yield flattening is too small to separate
  from noise. Reported rather than tuned away.
- Under-torque scores 0.00 on shape — it is caught by the **endpoint**, not the
  signature. Knowing which mechanism catches which defect is the point of having
  both, but it means signature analysis alone is not sufficient.
- `yield_flattening_ratio` is a physical constant we set (0.60), not learned.
- The generator and the analyser were written by the same team. Detection rates
  on synthetic curves are an upper bound on real-world performance and we say so
  on stage.

---

## ADR-0003 — `structured_cv_descriptor` is the primary path; the VLM is an enhancement

**Status:** accepted · **Date:** 2026-08-07 · **Owner:** WS-C

### Context

We do not control the LLM endpoints and cannot assume a vision model exists.
**Confirmed on the build machine:** the only available local provider (Ollama)
has `devstral`, `qwen-2.5.1-coder-it` and `llama-3.2-3b-it` — **none vision
capable**. A design in which "the VLM describes the defect" is load-bearing was
already broken before it was written.

### Decision

Compute classical features over the anomalous region and render them as
structured text that a **text-only** model reasons over:

```
Anomalous region at (412, 288), 34x29 px, 2.1% of inspection ROI.
Location: bolt seat, position 3 of 5 (clockwise from valve stem).
Intensity: 41% below local normal mean. Edge density 2.8x baseline.
Specular response: diffuse (expected: specular).
Nearest labelled exemplar: thread_contamination (cosine 0.71).
```

Build this first. Treat any available VLM as an enhancement behind
`FEATURE_VLM_DESCRIPTION` (default off), enabled only if the boot probe reports
`supports_vision`.

### Why this is a strength, not a workaround

Every input is a **measured quantity** rather than a model's impression. The
description is reproducible, unit-testable, and defensible line by line to a
quality engineer. A VLM's prose is none of those things. We would argue for this
design even on an endpoint that had vision.

### Honest cons

- Loses genuinely open-ended description. A defect whose distinguishing feature
  is not in our feature set gets a **generically-worded** report — it is still
  *detected* (that is the memory bank's job), but described poorly.
- The feature set encodes our assumptions about what matters. Adding a feature is
  a code change, whereas a VLM would need no change at all.

---

## ADR-0004 — CPU-first vision, with honest measured throughput

**Status:** accepted · **Date:** 2026-08-07 · **Owner:** WS-B

### Context

`python tasks.py doctor` on the build machine reports **no CUDA GPU** (12 logical
cores, AMD64). The build prompt assumed a laptop GPU and a 5 fps target.

### Decision

Auto-detect: ONNX Runtime CPU by default, torch + CUDA fp16 when a GPU is
present. 256×256 ROI crops, never full frames. **Report the measured fps in the
UI, do not claim a number.** If we sustain 3 fps we say 3.

### Honest cons

- CPU inference is materially slower; the temporal filter has fewer frames to
  work with per unit, which weakens the false-positive suppression that filter
  exists to provide.
- A measured 3 fps is a less impressive slide than a claimed 5. It is also the
  only number we can defend when a judge asks us to run it again.

---

## ADR-0005 — Transparent rules for defect classification, not an LLM

**Status:** accepted · **Date:** 2026-08-07 · **Owner:** WS-C

### Decision

`forge.domain.torque.classify()` ranks defect classes with explicit rules over
measured deviations. The LLM's job is to *explain* a conclusion the deterministic
layer reached, never to reach it.

### Rationale

Claiming a language model independently discovered a fastening failure mode would
be neither true nor defensible. The rules are arguable, unit-testable, and
reviewable by a quality engineer — and when a judge asks "how do you know it is
not hallucinating the root cause?", the answer is that it did not produce the
root cause.

### Honest cons

- Rules do not generalise to failure modes we did not anticipate. A genuinely
  novel signature reaches HITL as `UNKNOWN` rather than being classified — which
  we consider correct, but it is a real capability limit.
- Rule maintenance is manual and grows with the taxonomy.

---

## ADR-0006 — Baselines derived from measured clean runs, not hand-picked

**Status:** accepted · **Date:** 2026-08-07 · **Owner:** WS-B

### Decision

`SignatureBaseline.from_clean_runs()` derives every tolerance as *k* standard
deviations of the distribution of known-good runs (default k=3, minimum 20 runs,
raises below that). No detector threshold is chosen by a developer.

**Spec limits are the deliberate exception** — they come from the engineering
specification and, in production, from the ERPNext Quality Inspection template.
A process that has drifted must never be allowed to redefine what is in spec.

### Honest cons

- Assumes the clean set really is clean. A contaminated baseline widens the
  tolerances and quietly hides the defect class it was contaminated with — the
  same failure mode as ADR-0001's poisoned memory bank, and the reason both go
  through the same data-quality gate.
- Assumes roughly Gaussian features. Heavy-tailed features would need a
  quantile-based bound instead.

---

## ADR-0007 — Expected-cost triage, not a severity lookup table

**Status:** accepted · **Date:** 2026-08-07 · **Owner:** WS-C
**Implementation:** `src/forge/domain/cost.py`

### Decision

Rank dispositions by expected cost, `EC = P(defect)·C_defective + (1−P)·C_fine`,
and recommend the minimum. Conservatism on safety-critical parts emerges from the
**cost asymmetry** (a wheel escape carries warranty + recall exposure), not from a
hardcoded rule. `tests/unit/test_cost_triage.py::test_recall_exposure_is_what_makes_escape_expensive`
proves it by zeroing the exposure and watching ACCEPT stop looking dangerous.

### Consequences we did not anticipate

QUARANTINE beats REWORK by ~₹700 on the demo case, because holding and inspecting
only pays for repair on the units that need it. The engine detects the near-tie
and **escalates rather than breaking it silently**. We updated the demo script to
say "contain" rather than "rework"; we did not tune the model to match the script.

### Honest cons

- `field_failure_rate` is the single most consequential input and is an estimate.
  We surface it in the assumption list on every decision rather than burying it.
- Cost figures are synthetic plant economics, labelled SYNTHETIC in the UI.
- Expected-value minimisation is risk-neutral. A real plant is risk-averse about
  recalls and would want a utility function, not an expectation.

---

## ADR-0008 — MES behind a port, with an ERPNext-faithful simulator first

**Status:** accepted · **Date:** 2026-08-07 · **Owner:** WS-D

### Decision

`MESPort` with three adapters: `InMemoryMESAdapter` (tests), `ERPNextSimulator`
(real ERPNext doctype routes, field names and *blocking semantics*, no Docker
required), and `ERPNextAdapter` (a real instance). Hard go/no-go at hour 20.

### Rationale

Docker is **not installed** on the build machine. ERPNext is MariaDB + Redis +
gunicorn + workers + scheduler + nginx. Putting the demo path's critical
dependency behind a heavyweight stack we have not yet installed is how a demo
dies at hour 30. The port means the rest of the system cannot tell the difference.

### Honest cons

- A simulator is **not** the differentiator a real ERP is. "A real Quality
  Inspection blocks a real Delivery Note" is a strictly stronger claim than ours
  unless we land `FEATURE_ERPNEXT_REAL`, and we will say which one we are showing.

---

## ADR-0009 — TLS through the OS trust store

**Status:** accepted · **Date:** 2026-08-07 · **Owner:** WS-D
**Implementation:** `src/forge/bootstrap.py`

### Context

The build network runs an intercepting TLS proxy. Measured: `curl` succeeded on
all four live APIs while `httpx` failed three of them with
`CERTIFICATE_VERIFY_FAILED` — because the proxy's CA is in the Windows trust
store but not in certifi's bundle. Per-domain interception makes it look flaky
rather than systematic.

### Decision

`truststore.inject_into_ssl()` at every process entry point. Verified: all four
APIs return HTTP 200.

### Rejected: `verify=False`

It disables certificate verification globally, it ships to whatever environment
runs the container, and "we turned off TLS verification to make the demo work" is
a question we do not want asked on stage.

---

## ADR-0010 — Pydantic is the domain layer's one permitted third-party import

**Status:** accepted · **Date:** 2026-08-07 · **Owner:** WS-C

### Decision

`domain/` may import the standard library and **pydantic only**, enforced by
`tests/architecture/test_layering.py::test_domain_imports_nothing_but_stdlib_and_the_allowlist`.
A second test forbids `open()`, `print()` and `input()` in the layer, because
import rules alone cannot catch builtins.

### Rationale

Pydantic declares shapes and validates them, performs no I/O, and binds us to no
framework. Self-validating boundary models are worth more than import purity. The
allowance is explicit and narrow on purpose — left to convention, `import
requests` appears in `domain/` by hour 20.

### Honest cons

- A pydantic 3 migration would touch the innermost layer, which is exactly what
  the dependency rule is meant to prevent.
- `torque.py` and `cost.py` deliberately use plain dataclasses, so the genuinely
  hot maths path stays pydantic-free.

---

## ADR-0011 — Pack isolation checked on code, not on prose

**Status:** accepted · **Date:** 2026-08-07 · **Owner:** WS-C

### Context

The first version of the pack-isolation test scanned raw lines. It produced two
false positives: `rim` matched inside `p**rim**ary`, and it flagged docstrings
explaining *why* a wheel-off event drives the autonomy boundary.

### Decision

Walk the AST and check identifiers, attributes, definitions, parameters, and
**non-docstring** string literals, with word-boundary matching. Docstrings are
exempt.

### Rationale

The property that must hold is that **no behaviour keys off pack vocabulary**. A
rule forcing us to describe a wheel-off failure mode without saying "wheel" would
buy compliance by making the code harder to understand — the test would be
passing at the expense of the thing it exists to protect.

### Honest cons

- A pack-specific value could reach runtime via a config file the test does not
  read. Mitigated by `tests/integration/test_pack_switch.py` exercising the swap
  end to end.

---

## ADR-0012 — Four live external APIs, each load-bearing

**Status:** accepted · **Date:** 2026-08-07 · **Owner:** WS-D

All four verified reachable and keyless on 2026-08-07.

| API | Role | Why it earns its place |
|---|---|---|
| **Open-Meteo** | live ambient temp/humidity | Humidity drives thread surface condition — the physical mechanism behind ADR-0002. Not decoration. |
| **NHTSA Recalls** | campaign `24V237000` | The failure mode we detect, pulled live from a US government database, on stage. Also seeds `recall_exposure_per_unit` in ADR-0007. |
| **open.er-api.com** | live FX | Cost impact rendered in ₹ and $. |
| **Nager.Date** | holiday calendar | Staffing patterns → the night/holiday shift slice in the bias report. |

### Corrections to the original plan

- The build prompt cites recall `N232431480`. The **real** NHTSA campaign number
  is `24V237000`; we use what the API returns.
- **Frankfurter is unreachable** from this network (301 / timeout on both hosts).
  Replaced with `open.er-api.com`.
- **Nager.Date has no data for India** (HTTP 204). We serve DE/US and surface the
  gap in the UI as a stated capability limit rather than silently returning an
  empty calendar. It doubles as a live graceful-degradation demo.

### Honest cons

- Four network dependencies on hackathon Wi-Fi. All sit behind the same breaker +
  cache + recorded-fixture path as the MES, and stale values render a `stale`
  chip rather than passing as fresh.

---

## ADR-0013 — `tasks.py` is the build interface; the Makefile delegates

**Status:** accepted · **Date:** 2026-08-07 · **Owner:** WS-D

`make` is **not installed** on the build machine. `CLAUDE.md` and the demo
checklist both reference `make demo` / `make reset`. Rather than pick one, the
real implementation is `tasks.py` and the Makefile is a thin wrapper, so both
invocations work and neither is a lie.

Targets a phase has not yet delivered report the owning phase and workstream and
exit non-zero. They do not print fabricated success (CLAUDE.md rule 3).

---

## Pending decisions

| # | Question | Blocked on | Default if unanswered |
|---|---|---|---|
| P1 | Which LLM provider on stage? | user | Provider-agnostic chain: TCS → Anthropic → Ollama → deterministic. Ollama works offline today. |
| P2 | Real ERPNext, or simulator? | Docker install + hour-20 gate | Simulator (ADR-0008) |
| P3 | Is the demo machine GPU-equipped? | user | CPU auto-detect (ADR-0004) |
