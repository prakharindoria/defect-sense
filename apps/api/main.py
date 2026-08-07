"""FORGE API.

Run it:
    python tasks.py api        ->  http://localhost:8000
    docs                       ->  http://localhost:8000/docs
"""

from __future__ import annotations

import asyncio
import contextlib
import random
from collections import deque
from datetime import UTC, datetime
from typing import Any, Literal

from data.generators.torque_curve import (
    CurveClass,
    CurveSpec,
    generate_wheel,
    learn_baseline,
)
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from apps.api.routers import auth as auth_router
from apps.api.security import require, require_any
from forge.application.usecases.inspect import InspectionResult, InspectionService
from forge.bootstrap import init as bootstrap_init
from forge.domain.cost import CostModel
from forge.infrastructure.auth import User, load_rbac
from forge.infrastructure.llm.config import load as load_models

bootstrap_init()

# Demo plant economics. Synthetic but internally coherent; every figure is an
# assumption the UI displays alongside the number it produced.
WHEEL_COST_MODEL = CostModel(
    unit_material_cost=8_000.0,
    rework_minutes=16.0,
    labour_rate_per_hour=2_250.0,
    rework_parts_cost=2_600.0,
    line_rate_units_per_hour=60.0,
    margin_per_unit=3_500.0,
    field_failure_rate=0.08,
    warranty_cost_per_failure=45_000.0,
    recall_exposure_per_unit=120_000.0,
    inspection_cost_per_unit=150.0,
    currency="INR",
)

SPEC = CurveSpec()
SCENARIOS: dict[str, tuple[CurveClass, float]] = {
    "clean": (CurveClass.CLEAN, 0.0),
    "thread_contamination": (CurveClass.THREAD_CONTAMINATION, 0.8),
    "cross_threading": (CurveClass.CROSS_THREADING, 0.8),
    "over_torque": (CurveClass.OVER_TORQUE, 0.9),
    "under_torque": (CurveClass.UNDER_TORQUE, 0.8),
    "missing_fastener": (CurveClass.CLEAN, 0.0),   # verifier-detected, not signature
}

app = FastAPI(
    title="FORGE",
    version="1.0.0",
    description=(
        "Factory Operations Reasoning & Governance Engine.\n\n"
        "Multi-agent wheel assembly defect detection. The verdict path is fully "
        "deterministic and runs in milliseconds; LLM narrative is streamed "
        "separately so a safety decision never waits on, or depends on, a "
        "language model.\n\n"
        "**All data is synthetic.**"
    ),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router.router)


class _State:
    """Process state. Replaced by SQLite + the pack loader in v1.3/v1.1."""

    def __init__(self) -> None:
        self.baseline = learn_baseline(SPEC, runs=120, sigma=3.0)
        self.service = InspectionService(self.baseline, WHEEL_COST_MODEL, "wheel_assembly")
        self.results: dict[str, InspectionResult] = {}
        self.recent: deque[str] = deque(maxlen=200)
        self.subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self.unit_counter = 0

    def record(self, result: InspectionResult) -> None:
        self.results[result.correlation_id] = result
        self.recent.appendleft(result.correlation_id)

    async def broadcast(self, message: dict[str, Any]) -> None:
        for queue in list(self.subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(message)


state = _State()

_llm_singleton: Any = None


def _llm_service() -> Any:
    """Lazily build the LLM service.

    Constructed on first use rather than at import so a cold model load cannot
    add seconds to API startup for a page most sessions never open.
    """
    global _llm_singleton  # noqa: PLW0603
    if _llm_singleton is None:
        from forge.infrastructure.llm.service import LLMService  # noqa: PLC0415

        _llm_singleton = LLMService(load_models())
    return _llm_singleton


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class InspectRequest(BaseModel):
    scenario: Literal[
        "clean", "thread_contamination", "cross_threading",
        "over_torque", "under_torque", "missing_fastener",
    ] = Field("clean", description="Which synthetic condition to generate.")
    position: int = Field(3, ge=1, le=5, description="Fastener position to affect.")
    containment_scope: int = Field(
        12, ge=1, description="Units since the last known-good verdict."
    )
    seed: int | None = None


class FastenerView(BaseModel):
    position: int
    final_torque_nm: float
    knee_angle_deg: float
    elastic_slope_nm_per_deg: float
    anomaly_score: float
    endpoint_in_spec: bool
    signature_anomalous: bool
    fusion_only: bool
    likely_class: str | None
    deviations: list[str]
    curve: list[list[float]] = Field(description="[[angle_deg, torque_nm], ...]")


class SpanView(BaseModel):
    agent: str
    duration_ms: float
    summary: str
    ok: bool


class InspectionView(BaseModel):
    correlation_id: str
    unit_id: str
    pack_id: str
    verdict: str
    severity: str
    confidence: float
    fusion_only: bool
    primary_signal: str
    reasoning: str
    disposition: str
    expected_cost: float
    cost_low: float
    cost_high: float
    currency: str
    cost_assumptions: list[str]
    requires_human: bool
    human_reason: str
    data_quality: str
    data_quality_reasons: list[str]
    fasteners: list[FastenerView]
    spans: list[SpanView]
    total_ms: float
    is_synthetic: bool
    created_at: datetime
    baseline: dict[str, float]


def _view(result: InspectionResult, curves_by_pos: dict[int, list[list[float]]]) -> InspectionView:
    b = state.baseline
    return InspectionView(
        correlation_id=result.correlation_id,
        unit_id=result.unit_id,
        pack_id=result.pack_id,
        verdict=result.verdict.value,
        severity=result.severity.value,
        confidence=round(result.confidence, 3),
        fusion_only=result.fusion_only,
        primary_signal=result.primary_signal.value,
        reasoning=result.reasoning,
        disposition=result.disposition.value,
        expected_cost=round(result.expected_cost, 2),
        cost_low=round(result.cost_low, 2),
        cost_high=round(result.cost_high, 2),
        currency=result.currency,
        cost_assumptions=list(result.cost_assumptions),
        requires_human=result.requires_human,
        human_reason=result.human_reason,
        data_quality=result.data_quality.value,
        data_quality_reasons=list(result.data_quality_reasons),
        fasteners=[
            FastenerView(
                position=f.position,
                final_torque_nm=round(f.signature.features.final_torque_nm, 2),
                knee_angle_deg=round(f.signature.features.knee_angle_deg, 2),
                elastic_slope_nm_per_deg=round(
                    f.signature.features.elastic_slope_nm_per_deg, 3
                ),
                anomaly_score=round(f.signature.anomaly_score, 3),
                endpoint_in_spec=f.signature.endpoint_in_spec,
                signature_anomalous=f.signature.signature_anomalous,
                fusion_only=f.fusion_only,
                likely_class=(
                    f.signature.ranked_classes[0][0] if f.signature.ranked_classes else None
                ),
                deviations=[d.statement for d in f.signature.deviations],
                curve=curves_by_pos.get(f.position, []),
            )
            for f in result.fasteners
        ],
        spans=[
            SpanView(agent=s.agent.value, duration_ms=round(s.duration_ms, 3),
                     summary=s.summary, ok=s.ok)
            for s in result.spans
        ],
        total_ms=round(result.total_ms, 2),
        is_synthetic=result.is_synthetic,
        created_at=datetime.now(UTC),
        baseline={
            "knee_angle_deg": round(b.knee_angle_deg, 2),
            "knee_tolerance_deg": round(b.knee_angle_tolerance_deg, 2),
            "elastic_slope_nm_per_deg": round(b.elastic_slope_nm_per_deg, 3),
            "elastic_slope_tolerance": round(b.elastic_slope_tolerance, 3),
            "spec_lo_nm": b.spec_lo_nm,
            "spec_hi_nm": b.spec_hi_nm,
            "derived_from_runs": b.derived_from_runs,
            "sigma": b.sigma_multiplier,
        },
    )


_VIEWS: dict[str, InspectionView] = {}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health", tags=["ops"])
async def health() -> dict[str, Any]:
    b = state.baseline
    return {
        "status": "ok",
        "baseline_runs": b.derived_from_runs,
        "sigma": b.sigma_multiplier,
        "inspections": len(state.results),
        "ws_subscribers": len(state.subscribers),
    }


@app.get("/api/v1/scenarios", tags=["inspections"])
async def scenarios() -> dict[str, Any]:
    """Conditions the generator can produce, for the UI's scenario picker."""
    return {
        "scenarios": [
            {
                "id": "clean",
                "label": "Nominal run",
                "note": "All five fasteners in spec, signature nominal.",
            },
            {
                "id": "thread_contamination",
                "label": "Contaminated threads",
                "note": (
                    "Torque endpoint lands IN SPEC and vision passes. Caught only "
                    "by signature shape. This is NHTSA 24V237000's failure mode."
                ),
            },
            {"id": "cross_threading", "label": "Cross-threading",
             "note": "Erratic run-down, high residual variance."},
            {"id": "over_torque", "label": "Over-torque",
             "note": "Driven past yield; elastic slope flattens."},
            {"id": "under_torque", "label": "Under-torque",
             "note": "Endpoint below spec. Caught by the endpoint, not the shape."},
            {"id": "missing_fastener", "label": "Missing fastener",
             "note": "Geometric verifier failure. Deterministic and decisive."},
        ]
    }


@app.post("/api/v1/inspections", response_model=InspectionView, tags=["inspections"])
async def create_inspection(
    request: InspectRequest,
    user: User = Depends(require("inspection:create")),
) -> InspectionView:
    """Generate a unit, run the deterministic pipeline, return the verdict.

    Latency here is the honest end-to-end number: no model call is involved.
    """
    state.unit_counter += 1
    unit_id = f"VIN-SYN-{state.unit_counter:05d}"
    seed = request.seed if request.seed is not None else random.randint(0, 2**31 - 1)  # noqa: S311

    label, severity = SCENARIOS[request.scenario]
    labels = {} if label is CurveClass.CLEAN else {request.position: (label, severity)}
    generated = generate_wheel(labels, spec=SPEC, seed=seed, unit_id=unit_id)

    # missing_fastener is a VISION defect: the curves are all nominal because
    # the bolt that is absent was never run. The verifier is what catches it.
    missing = request.scenario == "missing_fastener"
    curves = [g.curve for g in generated]
    if missing:
        curves = [c for c in curves if c.fastener_position != request.position]

    result = state.service.inspect(
        unit_id,
        curves,
        containment_scope=request.containment_scope,
        verifiers_passed=not missing,
        verifier_summary=(
            f"fastener count 4, expected 5 - position {request.position} absent"
            if missing
            else "fastener count 5, bolt-circle 120.0mm, spacing uniform, TPMS present"
        ),
    )
    state.record(result)

    view = _view(
        result,
        {c.fastener_position: [[round(s.angle_deg, 3), round(s.torque_nm, 3)]
                               for s in c.samples] for c in curves},
    )
    _VIEWS[result.correlation_id] = view

    await state.broadcast({"type": "inspection", "data": view.model_dump(mode="json")})
    return view


@app.get("/api/v1/inspections", tags=["inspections"])
async def list_inspections(
    limit: int = 50,
    user: User = Depends(require_any("inspection:read", "inspection:read_own_station")),
) -> dict[str, Any]:
    ids = list(state.recent)[:limit]
    return {
        "total": len(state.results),
        "items": [
            {
                "correlation_id": v.correlation_id,
                "unit_id": v.unit_id,
                "verdict": v.verdict,
                "severity": v.severity,
                "confidence": v.confidence,
                "fusion_only": v.fusion_only,
                "disposition": v.disposition,
                "expected_cost": v.expected_cost,
                "currency": v.currency,
                "requires_human": v.requires_human,
                "total_ms": v.total_ms,
                "created_at": v.created_at,
            }
            for cid in ids
            if (v := _VIEWS.get(cid))
        ],
    }


@app.get("/api/v1/inspections/{correlation_id}", response_model=InspectionView,
         tags=["inspections"])
async def get_inspection(
    correlation_id: str,
    user: User = Depends(require_any("inspection:read", "inspection:read_own_station")),
) -> InspectionView:
    view = _VIEWS.get(correlation_id)
    if view is None:
        raise HTTPException(status_code=404, detail=f"no inspection '{correlation_id}'")
    return view


@app.get("/api/v1/metrics", tags=["ops"])
async def metrics(
    user: User = Depends(require_any("inspection:read", "inspection:read_own_station")),
) -> dict[str, Any]:
    views = [_VIEWS[c] for c in state.recent if c in _VIEWS]
    if not views:
        return {"inspected": 0}
    defects = [v for v in views if v.verdict == "defect"]
    fusion = [v for v in views if v.fusion_only]
    latencies = sorted(v.total_ms for v in views)
    return {
        "inspected": len(views),
        "defects": len(defects),
        "fusion_only": len(fusion),
        "escalated": len([v for v in views if v.requires_human]),
        "first_pass_yield": round(1 - len(defects) / len(views), 4),
        "p50_ms": round(latencies[len(latencies) // 2], 2),
        "p95_ms": round(latencies[min(int(len(latencies) * 0.95), len(latencies) - 1)], 2),
        "cost_at_risk": round(sum(v.expected_cost for v in defects), 2),
        "currency": "INR",
    }


@app.get("/api/v1/admin/system", tags=["admin"])
async def admin_system(
    user: User = Depends(require("admin:model_config")),
) -> dict[str, Any]:
    """Platform state for the Admin page.

    The capability matrix is measured at boot, not declared. "What if your model
    provider changes?" is answered with numbers rather than a claim -- and it is
    how we discovered the endpoint's designated vision model was returning HTTP
    410 while gpt-4o quietly had vision all along.
    """
    matrix = load_rbac()
    service = _llm_service()
    caps = await service.probe()
    stats = service.stats()

    return {
        "tiers": stats["tiers"],
        "skipped_providers": stats["skipped_providers"],
        "capabilities": [
            {
                "provider": c.provider,
                "model": c.model,
                "reachable": c.reachable,
                "supports_vision": c.supports_vision,
                "supports_json_mode": c.supports_json_mode,
                "supports_streaming": c.supports_streaming,
                "measured_p50_latency_ms": c.measured_p50_latency_ms,
                "error": c.error,
            }
            for c in caps
        ],
        "roles": {
            role.value: {
                "label": cfg.label,
                "default_page": cfg.default_page,
                "permissions": sorted(cfg.permissions),
            }
            for role, cfg in matrix.roles.items()
        },
        "health": {
            "inspections": len(state.results),
            "baseline_runs": state.baseline.derived_from_runs,
            "ws_subscribers": len(state.subscribers),
            "cache_entries": stats["cache"].get("entries", 0),  # type: ignore[union-attr]
        },
    }


@app.websocket("/ws/live")
async def live(websocket: WebSocket) -> None:
    """Streams every new inspection to the Command Center."""
    await websocket.accept()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)
    state.subscribers.add(queue)
    try:
        await websocket.send_json({"type": "hello", "data": {"connected": True}})
        while True:
            await websocket.send_json(await queue.get())
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        state.subscribers.discard(queue)
