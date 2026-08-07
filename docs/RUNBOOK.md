# FORGE — How to run

Everything here is verified working on the build machine as of 2026-08-07.
Commands that are not yet implemented say so explicitly rather than failing
strangely (`CLAUDE.md` rule 3).

---

## 0. TL;DR

```bash
python tasks.py doctor
```

That single command tells you what this machine can and cannot run, separates
**blockers** from **degradations**, and gives a remedy for each. Run it first on
any new box. It exits non-zero only for a real blocker.

---

## 1. Prerequisites

| Need | Why | Check |
|---|---|---|
| Python 3.11+ | Backend (3.12.8 in use here) | `python --version` |
| Node 20+ / npm | Web app | `npm --version` |
| ~6 GB free disk | Model weights, memory bank, indexes | `python tasks.py doctor` |

**Optional, each degrades gracefully rather than blocking:**

| Optional | Without it |
|---|---|
| Docker | `PROFILE=local` runs the whole stack on SQLite + local files |
| NVIDIA GPU | Vision falls back to ONNX Runtime CPU; report the measured fps |
| Ollama | One fewer provider in the chain |
| TCS API key | Every tier resolves to the deterministic fake adapter |

---

## 2. Install

```bash
python -m pip install -e ".[dev]"
```

The heavy AI group (LangGraph, ChromaDB, OpenCV, ONNX, sentence-transformers)
is separate so the API and the whole test suite install fast:

```bash
python -m pip install -e ".[dev,ai]"
```

---

## 3. Configure

```bash
cp .env.example .env
```

Then set **one thing** — the TCS key issued at the event:

```
TCS_API_KEY=<key from the organisers>
```

Everything else already has a working default. With that key set the chain
resolves to:

| Tier | Model |
|---|---|
| `reasoning` | `azure/genailab-maas-gpt-4o` |
| `fast` | `azure/genailab-maas-gpt-4o-mini` |
| `vision` | `azure_ai/genailab-maas-Llama-3.2-90B-Vision-Instruct` |
| embeddings | `azure/genailab-maas-text-embedding-3-large` |

Never commit `.env`. Secrets come from the environment only; `config/*.yaml`
holds `${VAR}` references, never values.

### If TLS fails against the TCS endpoint

The official TCS sample uses `httpx.Client(verify=False)`. **Do not copy that
pattern** — it disables certificate verification for *every* outbound call in
the process, including the public APIs we cite as evidence on stage. An attacker
on the network could then feed us a forged NHTSA recall record.

We already route TLS through the OS trust store (`truststore`), which is what
made three of four public APIs work on this network. If TCS still fails, scope
the exemption to that one host:

```
FORGE_TLS_INSECURE_HOSTS=genailab.tcs.in
```

Everything else keeps full verification.

---

## 4. Verify the environment

```bash
python tasks.py doctor
```

Expected on this machine — no blockers, four degradations, all four live APIs
green:

```
  [ ok ] python          3.12.8 (CPython)
  [ ok ] cpu             12 logical cores, AMD64
  [FAIL] gpu             no nvidia-smi on PATH; CUDA unavailable
  [ ok ] disk            342.0 GB free
  [ ok ] node            npm 10.9.2
  [FAIL] docker          not installed
  [ ok ] deps:core       5 modules importable
  [ ok ] tls             active (OS trust store)
  [ ok ] api:open-meteo  HTTP 200 in 1786ms
  [ ok ] api:nhtsa       HTTP 200 in 440ms
  [ ok ] api:fx          HTTP 200 in 495ms
  [ ok ] api:nager       HTTP 200 in 516ms
```

---

## 5. Probe the models

```bash
python tasks.py probe
```

Prints the live capability matrix — which providers are reachable, which
genuinely support vision, JSON mode and streaming, and the measured latency.
This is what renders on `/admin`.

The vision check is **discriminating**, not a status-code check: it sends solid
red and blue images and requires the model to name both. Ollama happily accepts
a multimodal payload for a text-only model, drops the image, and returns HTTP
200 — which made `llama-3.2-3b` report `vision: yes` until this was fixed. A
capability probe that reports a false positive is worse than no probe.

Measured on this machine with Ollama only:

```
provider     model                         reach  vision   json  stream  p50 ms
ollama       qwen-2.5.1-coder-it:latest      ok     no      no    yes     6935
ollama       llama-3.2-3b-it:latest          ok     no     yes    yes     4749
fake         fake-fast-v1                    ok     no     yes     no        5
```

---

## 6. Run the checks

```bash
python tasks.py verify
```

Runs lint + types + the full suite. This is the gate for any merge to `main`.
Currently **56 tests, ~1.4 s**, lint clean.

Individually:

| Command | What |
|---|---|
| `python tasks.py test` | full suite, network tests excluded |
| `python tasks.py test -m live` | only the tests that hit real external APIs |
| `python tasks.py test -m demo_path` | only tests covering a demo step |
| `python tasks.py arch` | the executable architecture rules alone |
| `python tasks.py lint` / `fmt` / `types` | individually |

### See the core engine work right now

```bash
python -m data.generators.torque_curve
```

Learns a baseline from 120 clean runs at 3σ, then scores every defect class.
The row that matters is `thread_contamination`: final torque **118.0 Nm, inside
the 110–125 spec band**, and still flagged — `FUSION-ONLY: YES`.

---

## 7. Run the product

**Two terminals.** Backend first, then the UI.

**Terminal 1 — backend (port 8000):**

```bash
python tasks.py api
```

**Terminal 2 — frontend (port 5173):**

```bash
python tasks.py web
```

First run installs npm dependencies automatically. Then open:

| URL | What |
|---|---|
| **http://localhost:5173** | **The product.** Command Center + Defect Workbench |
| http://localhost:8000/docs | Interactive OpenAPI (Swagger) |
| http://localhost:8000/health | Liveness + baseline stats |

The UI proxies `/api` and `/ws` to the backend, so the browser talks to one
origin and there is no CORS to configure.

### What to click

1. **Nominal run** → ▶ Run inspection. Verdict **PASS**, disposition ACCEPT,
   ~3 ms. Show the system working normally first — that boring beat is what
   makes the next one credible.
2. **Contaminated threads** → ▶ Run inspection. Verdict **DEFECT** with a
   **⚠ FUSION-ONLY** badge, while the fastener table shows the endpoint
   *in spec* and the verifiers passing. Both signals individually say PASS.
3. Read the Torque-Angle Signature panel: the curve **ends inside the green spec
   band** but its shape departs from the dashed learned baseline — knee delayed,
   elastic slope shallow.
4. **Missing fastener** → caught by the deterministic geometric verifier
   instead, at confidence 0.99. Different mechanism, same pipeline.

### Verified end to end

```
clean                  pass     conf=0.94 fusion=False accept          0 INR   2.6ms
thread_contamination   defect   conf=0.81 fusion=TRUE  quarantine 34,838 INR   2.6ms
cross_threading        defect   conf=0.82 fusion=TRUE  quarantine 35,153 INR   2.7ms
over_torque            defect   conf=0.95 fusion=False rework     38,400 INR   2.5ms
under_torque           defect   conf=0.95 fusion=False rework     38,400 INR   2.4ms
missing_fastener       defect   conf=0.99 fusion=False rework     38,400 INR   1.9ms
```

Those latencies are the honest end-to-end number: **no model call is on the
verdict path.**

### Still to come

| Command | Status |
|---|---|
| `python tasks.py seed` | v1.1 — pack loader + seeding |
| `python tasks.py eval` | v1.7 — eval harness + baseline row |

`make` is not installed here, so **`tasks.py` is the real implementation** and
the `Makefile` delegates to it. `make api` and `python tasks.py api` are the
same thing.

---

## 8. Reset

```bash
python tasks.py reset
```

Deletes all generated state — `.forge/`, `data/generated/`, `*.sqlite3`,
`.pytest_cache`. Everything it removes is reproducible from `seed`, so this is
always safe.

---

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `CERTIFICATE_VERIFY_FAILED` on some hosts but `curl` works | Intercepting TLS proxy; its CA is in the OS store, not certifi's | `pip install truststore` (already a dependency). Last resort: `FORGE_TLS_INSECURE_HOSTS=<host>` |
| Probe says a model is unreachable, but it exists | Cold model load exceeds the probe timeout | Already set to 45 s. Warm it: `ollama run <model> ""` |
| `model requires more system memory` | Model larger than free RAM | Pick a smaller one. `devstral` needs 16 GiB; this box has 9.3 |
| Every tier resolves to `fake` | No provider configured | Set `TCS_API_KEY` in `.env` |
| Tests pass locally, fail for a teammate | Their shell exports `TCS_API_KEY`, changing chain order | Tests isolate via `monkeypatch.delenv`; verified green both ways |
| `unknown target` from `tasks.py` | Typo, or a target from a later phase | `python tasks.py help` |
