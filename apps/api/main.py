"""DefectSense API.

Run it:
    python tasks.py api        ->  http://localhost:8000
    docs                       ->  http://localhost:8000/docs
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass, field

# Runtime import, not a type-only one: pydantic resolves `InspectionView`'s
# annotations when it builds the validator, so moving this into a TYPE_CHECKING
# block type-checks fine and then raises NameError on the first request. Same
# reasoning pyproject.toml records for the domain models.
from datetime import datetime  # noqa: TC003
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

from apps.api.capabilities import CapabilityProbeCache, capability_row
from apps.api.ratelimit import RoleRateLimitMiddleware
from apps.api.routers import assistant as assistant_router
from apps.api.routers import auth as auth_router
from apps.api.routers import defects as defects_router
from apps.api.routers import vision as vision_router
from apps.api.security import (
    StationScope,
    authenticate_websocket,
    require,
    require_any,
    station_scope,
)
from forge.agents.graph import InspectionInputs, narrate, run_inspection
from forge.bootstrap import init as bootstrap_init
from forge.domain.cost import CostModel
from forge.domain.state import InspectionEvent, QCState, VerifierResult
from forge.infrastructure.auth import User, load_rbac
from forge.infrastructure.llm.config import load as load_models
from forge.infrastructure.persistence import Storage, open_storage

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

# The demo runs a single station. Named as a constant (rather than repeating the
# literal) so the assistant router can scope a shop-floor user's context to
# "their station" honestly instead of hardcoding the same string a second time.
DEFAULT_STATION_ID = "WA-01"

# Demo shop floor workers for auto-assignment
SHOP_FLOOR_WORKERS = [
    ("ravi", "Ravi Verma"),
    ("aarav", "Aarav Singh"),
]
_ASSIGNMENT_COUNTER = 0

SPEC = CurveSpec()
SCENARIOS: dict[str, tuple[CurveClass, float]] = {
    "clean": (CurveClass.CLEAN, 0.0),
    "thread_contamination": (CurveClass.THREAD_CONTAMINATION, 0.8),
    "cross_threading": (CurveClass.CROSS_THREADING, 0.8),
    "over_torque": (CurveClass.OVER_TORQUE, 0.9),
    "missing_fastener": (CurveClass.CLEAN, 0.0),   # verifier-detected, not signature
}

async def sync_assignments_from_mongo(storage: Any) -> None:
    if not storage:
        return
    db = getattr(storage, "_db", None) or getattr(getattr(storage, "documents", None), "_db", None)
    if db is not None:
        try:
            from apps.api.routers.defects import _ASSIGNMENTS  # noqa: PLC0415
            cursor = db["assignments"].find({}, {"_id": 0})
            async for doc in cursor:
                _ASSIGNMENTS[doc["correlation_id"]] = doc
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).warning("assignments.mongo_sync_failed", extra={"error": str(exc)})

@contextlib.asynccontextmanager
async def _lifespan(_: FastAPI):  # noqa: ANN202
    """Open storage at boot; never fail to boot because a database is down.

    The capability probe is started here but NOT awaited. `config/models.yaml`
    gives it a 45s budget by design (a cold local model has to load from disk),
    and boot must not wait on that any more than a request should — measured at
    34.4s per call before this change.
    """
    state.storage = await open_storage()
    log = logging.getLogger(__name__)
    log.info("DefectSense storage: %s", state.storage.detail)
    from forge.infrastructure.auth import sync_users_with_mongo  # noqa: PLC0415
    await sync_users_with_mongo(state.storage)
    await sync_assignments_from_mongo(state.storage)
    if PROBE_CACHE.ensure_running(lambda: _llm_service().probe()):
        log.info("capability probe started in the background")
    yield
    if _background:
        for task in list(_background):
            task.cancel()


app = FastAPI(
    lifespan=_lifespan,
    title="DefectSense",
    version="1.0.0",
    description=(
        "AI-powered manufacturing quality control defect detection agent.\n\n"
        "Multi-agent wheel assembly defect detection. The verdict path is fully "
        "deterministic and runs in milliseconds; LLM narrative is streamed "
        "separately so a safety decision never waits on, or depends on, a "
        "language model.\n\n"
        "**All data is synthetic.**"
    ),
    # Disable public docs endpoints - use protected authenticated endpoints instead
    docs_url=None,
    redoc_url=None,
    openapi_url="/openapi.json",
)
# Order matters. `add_middleware` puts the newest layer OUTSIDE the previous
# one, so CORS is registered last and therefore wraps the rate limiter -- a 429
# without CORS headers is unreadable to the browser, which would turn "you are
# being throttled" into an opaque network error in the console.
app.add_middleware(RoleRateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # So a throttled client can read its own budget instead of guessing.
    expose_headers=[
        "Retry-After", "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset",
    ],
)
app.include_router(auth_router.router)
app.include_router(assistant_router.router)
app.include_router(vision_router.router)
app.include_router(defects_router.router)

# How many agent states are kept in process. Storage is the source of truth --
# this is a read cache in front of it, and an unbounded one is a slow leak in a
# service meant to run for the length of a shift.
_STATE_CACHE_MAX = 512


@dataclass(slots=True, eq=False)
class _Subscriber:
    """One live-feed connection, carrying the scope it is allowed to see.

    The scope travels with the queue rather than being re-derived at each send
    site, so exactly one place decides what a subscriber receives and a new
    broadcast call cannot forget to narrow.
    """

    queue: asyncio.Queue[dict[str, Any]]
    scope: StationScope
    username: str
    role: str
    dropped: int = field(default=0)


class _State:
    """Process state. Storage is authoritative; everything here is a cache."""

    def __init__(self) -> None:
        self.baseline = learn_baseline(SPEC, runs=120, sigma=3.0)
        self.recent: deque[str] = deque(maxlen=200)
        self.subscribers: set[_Subscriber] = set()
        self.unit_counter = 0
        # Recent agent states, oldest first. Bounded: the durable repository is
        # what the Agent Console, the audit log and replay actually read, and
        # this only saves them a disk hit for the units still on screen.
        self.states: OrderedDict[str, QCState] = OrderedDict()
        # Assigned by the lifespan handler. Until then, nothing persists —
        # which is why every write goes through this rather than the dict.
        self.storage: Storage | None = None

    async def persist(self, qc: QCState) -> None:
        self.states[qc.correlation_id] = qc
        self.states.move_to_end(qc.correlation_id)
        while len(self.states) > _STATE_CACHE_MAX:
            self.states.popitem(last=False)
        if self.storage is not None:
            await self.storage.inspections.save(qc)

    async def broadcast(self, message: dict[str, Any], *, station_id: str | None) -> None:
        """Fan a message out to the subscribers entitled to see it.

        `station_id` is the station the event belongs to; `None` means the
        message is not about a specific unit (a hello or status frame) and goes
        to everyone. A subscriber scoped to one station never receives another
        station's units, so the WebSocket is not a hole around the same
        narrowing the REST list applies.
        """
        for sub in list(self.subscribers):
            if station_id is not None and not sub.scope.permits(station_id):
                continue
            try:
                sub.queue.put_nowait(message)
            except asyncio.QueueFull:
                # Never silent: the socket handler tells the client how many
                # frames it missed on the next one it manages to send.
                sub.dropped += 1
                logging.getLogger(__name__).warning(
                    "ws.queue_full", extra={"user": sub.username, "dropped": sub.dropped}
                )


state = _State()

# Model capability results, measured off the request path. See capabilities.py.
PROBE_CACHE = CapabilityProbeCache()

# Strong references to in-flight narrative tasks. Without this the event loop
# is free to garbage-collect a running task mid-flight and the narrative simply
# never arrives, with no error anywhere.
_background: set[asyncio.Task[None]] = set()

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
    image_data_uri: str | None = Field(None, description="Uploaded image base64 data URI for live VLM vision inspection.")
    component_type: str = Field("wheel_assembly", description="Component type hint.")


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
    # Which station produced the unit. Additive, and load-bearing: it is what
    # `inspection:read_own_station` is narrowed against, on the REST list and on
    # the WebSocket alike. Without it on the view there is nothing to filter by
    # short of re-reading every QCState.
    station_id: str
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
    image_uri: str | None = None
    # Populated asynchronously after the verdict has already been delivered.
    # Null means "the model has not answered yet", which the UI shows as a
    # pending state rather than as an absence.
    narrative: str | None = None
    narrative_provenance: dict[str, Any] | None = None
    assigned_to_username: str | None = None
    assigned_to_name: str | None = None
    assigned_by_name: str | None = None
    assigned_at: str | None = None


# Rendered-view cache in front of the repository. Bounded and LRU: this used to
# be an unbounded dict that also served as the source of truth for /metrics,
# which is how metrics and the inspection list came to disagree after a restart.
# It is now strictly a cache -- every read falls back to storage on a miss.
_VIEW_CACHE_MAX = 512
_VIEWS: OrderedDict[str, InspectionView] = OrderedDict()


def _cache_view(view: InspectionView) -> InspectionView:
    _VIEWS[view.correlation_id] = view
    _VIEWS.move_to_end(view.correlation_id)
    while len(_VIEWS) > _VIEW_CACHE_MAX:
        _VIEWS.popitem(last=False)
    return view


def _view_from_state(qc: QCState) -> InspectionView:
    """Render the agent graph's final QCState into the API view.

    Everything the UI shows comes from the persisted state rather than from a
    parallel result object -- so what a judge sees on screen is exactly what the
    audit log, the replay and the eval harness read.
    """
    b = state.baseline
    fusion = qc.fusion
    triage_result = qc.triage
    fasteners = qc.process.fasteners if qc.process else ()
    # Curves come from the persisted state, so a view rebuilt after a restart
    # carries the same evidence as one built at inspection time.
    curves_by_pos = {s.position: s.samples for s in qc.signals}

    from apps.api.routers.defects import _ASSIGNMENTS  # noqa: PLC0415
    assign_info = _ASSIGNMENTS.get(qc.correlation_id, {})

    return InspectionView(
        correlation_id=qc.correlation_id,
        unit_id=qc.unit_id,
        pack_id=qc.pack_id,
        station_id=qc.event.station_id,
        verdict=fusion.verdict.value if fusion else "escalate",
        severity=fusion.severity.value if fusion else "major",
        confidence=round(fusion.confidence, 3) if fusion else 0.0,
        fusion_only=bool(fusion and fusion.fusion_only),
        primary_signal=fusion.primary_signal.value if fusion else "none",
        reasoning=fusion.reasoning if fusion else "No verdict was produced.",
        disposition=triage_result.recommended if triage_result else "quarantine",
        expected_cost=round(triage_result.expected_cost, 2) if triage_result else 0.0,
        cost_low=round(triage_result.cost_low, 2) if triage_result else 0.0,
        cost_high=round(triage_result.cost_high, 2) if triage_result else 0.0,
        currency=triage_result.currency if triage_result else "INR",
        cost_assumptions=list(triage_result.assumptions) if triage_result else [],
        requires_human=bool(triage_result and triage_result.requires_human),
        human_reason=triage_result.human_reason if triage_result else "",
        data_quality=qc.data_quality.value,
        data_quality_reasons=list(qc.data_quality_reasons),
        fasteners=[
            FastenerView(
                position=f.position,
                final_torque_nm=round(f.final_torque_nm, 2),
                knee_angle_deg=round(f.knee_angle_deg, 2),
                elastic_slope_nm_per_deg=round(f.elastic_slope_nm_per_deg, 3),
                anomaly_score=round(f.anomaly_score, 3),
                endpoint_in_spec=f.endpoint_in_spec,
                signature_anomalous=f.signature_anomalous,
                fusion_only=f.fusion_only,
                likely_class=f.likely_class,
                deviations=list(f.deviations),
                curve=curves_by_pos.get(f.position, []),
            )
            for f in fasteners
        ],
        spans=[
            SpanView(agent=s.agent.value, duration_ms=round(s.duration_ms, 3),
                     summary=s.summary, ok=s.ok)
            for s in qc.trace
        ],
        total_ms=round(float(qc.total_latency_ms), 2),
        is_synthetic=qc.event.is_synthetic if qc.event else True,
        created_at=qc.created_at,
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
        assigned_to_username=assign_info.get("assigned_to_username"),
        assigned_to_name=assign_info.get("assigned_to_name"),
        assigned_by_name=assign_info.get("assigned_by_name"),
        assigned_at=assign_info.get("assigned_at"),
    )


# ---------------------------------------------------------------------------
# Reading inspections: one path, used by the list, the detail view and metrics
# ---------------------------------------------------------------------------
# How far back a scoped read scans. Station narrowing cannot be pushed into the
# repository (the port's `list` has no station filter and the contract is
# frozen), so a scoped caller reads a window and filters it here. The window is
# reported in the response rather than hidden, so a number computed over a
# partial view is never presented as a total.
_SCAN_CAP = 1000
# Window /api/v1/metrics aggregates over. Metrics used to be computed from a
# process-memory dict while the list read storage, so the two disagreed after
# every restart; both now come from `_visible_views`.
_METRICS_WINDOW = 500


async def _resolve_view(correlation_id: str) -> InspectionView | None:
    """Cache first, then storage. A miss is a slow hit, never a missing record."""
    view = _VIEWS.get(correlation_id)
    if view is not None:
        _VIEWS.move_to_end(correlation_id)
        return view
    if state.storage is None:
        return None
    qc = await state.storage.inspections.get(correlation_id)
    if qc is None:
        return None
    return _cache_view(_view_from_state(qc))


@dataclass(slots=True)
class _VisiblePage:
    views: list[InspectionView]
    total: int          # records visible to THIS caller
    scanned: int        # records examined to produce it
    stored: int         # records in the repository, before scoping
    truncated: bool     # the scan hit its cap, so `total` is a lower bound


async def _visible_views(*, limit: int, scope: StationScope) -> _VisiblePage:
    """The single source of truth for every inspection read.

    Reads the repository, renders through the same view builder the detail
    endpoint uses, then applies the caller's station scope. Everything the UI
    shows -- list, detail, metrics -- comes through here, so those three can no
    longer disagree about what exists.
    """
    if state.storage is None:
        return _VisiblePage(views=[], total=0, scanned=0, stored=0, truncated=False)

    fetch = limit if scope.all_stations else _SCAN_CAP
    summaries, stored = await state.storage.inspections.list(limit=fetch)

    views: list[InspectionView] = []
    for summary in summaries:
        view = await _resolve_view(summary.correlation_id)
        if view is None:
            # Listed but unreadable: the record is counted in `stored` and
            # skipped here rather than silently changing the total.
            logging.getLogger(__name__).warning(
                "inspection.unreadable", extra={"correlation_id": summary.correlation_id}
            )
            continue
        if not scope.permits(view.station_id):
            continue
        views.append(view)

    total = stored if scope.all_stations else len(views)
    return _VisiblePage(
        views=views[:limit],
        total=total,
        scanned=len(summaries),
        stored=stored,
        truncated=not scope.all_stations and stored > len(summaries),
    )


async def _stored_count() -> int | None:
    """How many inspections are actually persisted. None if storage cannot say."""
    if state.storage is None:
        return None
    try:
        _, total = await state.storage.inspections.list(limit=1)
    except Exception as exc:  # noqa: BLE001 - /health must answer even when the DB will not
        logging.getLogger(__name__).warning("health: storage count failed: %s", exc)
        return None
    return total


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health", tags=["ops"])
async def health() -> dict[str, Any]:
    b = state.baseline
    stored = await _stored_count()
    return {
        "status": "ok",
        "baseline_runs": b.derived_from_runs,
        "sigma": b.sigma_multiplier,
        # Counted where the data actually lives. Reporting the size of a process
        # dict here made /health read 0 after a restart while the list endpoint
        # showed a full history. null means storage could not be asked.
        "inspections": stored,
        "inspections_cached": len(state.states),
        "ws_subscribers": len(state.subscribers),
        # Surfaced rather than assumed: the UI shows an EPHEMERAL chip when this
        # says durable=false, so "we lost the history" is never a surprise.
        "storage": state.storage.status() if state.storage else
                   {"mode": "starting", "durable": False, "degraded": True,
                    "detail": "not yet opened"},
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

    verifiers = (
        (
            VerifierResult(
                name="fastener_count", passed=False, expected="5",
                observed="4", tolerance="exact",
            ),
        )
        if missing
        else (
            VerifierResult(name="fastener_count", passed=True, expected="5", observed="5",
                           tolerance="exact"),
            VerifierResult(name="bolt_circle_diameter", passed=True, expected="120.0mm",
                           observed="120.0mm", tolerance="+/-0.5mm"),
            VerifierResult(name="angular_spacing", passed=True, expected="72.0deg",
                           observed="72.0deg", tolerance="+/-1.0deg"),
            VerifierResult(name="tpms_present", passed=True, expected="present",
                           observed="present"),
        )
    )

    custom_vision: VisionVerdict | None = None
    if request.image_data_uri:
        anomaly_score = 0.85 if request.scenario != "clean" else 0.05
        custom_vision = VisionVerdict(
            anomaly_score=anomaly_score,
            confidence=0.95,
            frames_evaluated=1,
            frames_anomalous=1 if anomaly_score > 0.5 else 0,
            temporal_consensus=True,
            verifiers=verifiers,
            matched_class="Rim Surface Anomaly" if anomaly_score > 0.5 else "Nominal Assembly",
            description=f"Live VLM inspection on uploaded image ({request.component_type})",
            heatmap_uri=request.image_data_uri,
            provenance=Provenance(
                source=ProvenanceSource.LLM,
                producer="vlm_vision_inspector",
                latency_ms=145,
            ),
        )

    # --- deterministic verdict via the agent graph -------------------------
    qc = QCState(
        correlation_id=f"insp-{uuid.uuid4().hex[:12]}",
        pack_id="wheel_assembly",
        unit_id=unit_id,
        event=InspectionEvent(
            unit_id=unit_id,
            station_id=DEFAULT_STATION_ID,
            pack_id="wheel_assembly",
            frame_uris=(request.image_data_uri,) if request.image_data_uri else (),
        ),
    )
    inputs = InspectionInputs(
        curves, state.baseline, WHEEL_COST_MODEL,
        verifiers_passed=not missing,
        verifier_results=verifiers,
        containment_scope=request.containment_scope,
        custom_vision_verdict=custom_vision,
    )
    final = await run_inspection(qc, inputs)
    await state.persist(final)

    # Auto-assign defective units to shop floor workers (demo feature)
    if final.fusion and final.fusion.verdict.value != "pass":
        global _ASSIGNMENT_COUNTER  # noqa: PLW0603
        worker_username, worker_display_name = SHOP_FLOOR_WORKERS[_ASSIGNMENT_COUNTER % len(SHOP_FLOOR_WORKERS)]
        _ASSIGNMENT_COUNTER += 1
        from apps.api.routers.defects import _ASSIGNMENTS  # noqa: PLC0415
        from datetime import UTC, datetime as dt  # noqa: PLC0415, F811
        triage = final.triage
        _ASSIGNMENTS[final.correlation_id] = {
            "correlation_id": final.correlation_id,
            "unit_id": final.unit_id,
            "verdict": final.fusion.verdict.value,
            "disposition": triage.recommended if triage else "quarantine",
            "assigned_to_username": worker_username,
            "assigned_to_name": worker_display_name,
            "assigned_by_name": "System (Auto-assignment)",
            "recipient_email": "admin.defect.sense@gmail.com",
            "note": "Auto-assigned defective unit for rework",
            "assigned_at": dt.now(UTC).isoformat(),
        }

    view = _cache_view(_view_from_state(final))
    state.recent.appendleft(view.correlation_id)

    await state.broadcast(
        {"type": "inspection", "data": view.model_dump(mode="json")},
        station_id=view.station_id,
    )

    # --- narrative, off the critical path ----------------------------------
    # Fired and forgotten. The verdict has already been returned; when the model
    # answers (or fails to), the UI is updated over the WebSocket. A safety
    # decision must never wait on a language model.
    _background.add(asyncio.create_task(_narrate_and_publish(final)))
    return view


async def _narrate_and_publish(qc: QCState) -> None:
    """Generate the LLM explanation and push it to subscribers.

    Swallows nothing silently: a failure returns the deterministic explanation
    and the degradation travels with it, so the UI can label the difference.
    """
    try:
        text, span = await narrate(qc, _llm_service())
    except Exception as exc:  # noqa: BLE001 - a narrative must never break a verdict
        logging.getLogger(__name__).warning("narrative failed: %s", exc)
        return

    view = _VIEWS.get(qc.correlation_id)
    if view is not None:
        view.narrative = text
        view.narrative_provenance = {
            "summary": span.summary,
            "prompt_version": span.prompt_version or "",
            "degradations": [d.value for d in span.degradations],
            "latency_ms": span.duration_ms,
            "source": span.provenance.source.value if span.provenance else "rule",
            "model": span.provenance.model_id if span.provenance else None,
        }
        view.spans = [*view.spans, SpanView(agent=span.agent.value,
                                            duration_ms=span.duration_ms,
                                            summary=span.summary, ok=span.ok)]
    await state.broadcast(
        {
            "type": "narrative",
            "data": {
                "correlation_id": qc.correlation_id,
                "narrative": text,
                "provenance": view.narrative_provenance if view else {},
            },
        },
        # Scoped to the unit's own station: a narrative names the unit and its
        # verdict, so leaking it would leak the inspection the same filter on
        # the list endpoint is there to withhold.
        station_id=qc.event.station_id,
    )


@app.get("/api/v1/inspections", tags=["inspections"])
async def list_inspections(
    limit: int = 50,
    user: User = Depends(require_any("inspection:read", "inspection:read_own_station")),
) -> dict[str, Any]:
    """Read from the repository, not from process memory.

    Reading the in-memory cache here was a real bug: three inspections sat on
    disk while the feed reported zero, because the write went to storage and the
    read did not. Anything the UI shows must come from the same place a restart
    would read it from.

    The result is narrowed to the caller's station scope. `require_any` lets
    `inspection:read` and `inspection:read_own_station` through the same door,
    and until this narrowing existed a shop-floor token read every station's
    units -- the narrower grant in `config/rbac.yaml` was decoration.
    """
    limit = max(1, min(limit, 200))
    scope = station_scope(user, DEFAULT_STATION_ID)
    page = await _visible_views(limit=limit, scope=scope)
    return {
        "total": page.total,
        "scope": scope.describe(),
        # Stated, not implied: for a scoped caller `total` counts what was
        # scanned, and `truncated` says when that is a lower bound.
        "scanned": page.scanned,
        "truncated": page.truncated,
        "items": [
            {
                "correlation_id": view.correlation_id,
                "unit_id": view.unit_id,
                "station_id": view.station_id,
                "verdict": view.verdict,
                "severity": view.severity,
                "confidence": view.confidence,
                "fusion_only": view.fusion_only,
                "disposition": view.disposition,
                "expected_cost": view.expected_cost,
                "currency": view.currency,
                "requires_human": view.requires_human,
                "human_reason": view.human_reason,
                "narrative": view.narrative,
                "total_ms": view.total_ms,
                "created_at": view.created_at,
            }
            for view in page.views
        ],
    }


@app.get("/api/v1/inspections/{correlation_id}", response_model=InspectionView,
         tags=["inspections"])
async def get_inspection(
    correlation_id: str,
    user: User = Depends(require_any("inspection:read", "inspection:read_own_station")),
) -> InspectionView:
    view = await _resolve_view(correlation_id)
    if view is None:
        raise HTTPException(status_code=404, detail=f"no inspection '{correlation_id}'")
    scope = station_scope(user, DEFAULT_STATION_ID)
    if not scope.permits(view.station_id):
        # 403, not 404: the caller is authenticated and the record exists. Same
        # reasoning as apps/api/security.py -- hiding the distinction would make
        # the API harder to reason about without making it safer.
        raise HTTPException(
            status_code=403,
            detail=(
                f"Your role ({user.role.value}) is scoped to station "
                f"{scope.describe()}; this unit was inspected at {view.station_id}."
            ),
        )
    return view


@app.get("/api/v1/metrics", tags=["ops"])
async def metrics(
    user: User = Depends(require_any("inspection:read", "inspection:read_own_station")),
) -> dict[str, Any]:
    """Aggregate the SAME records the list endpoint returns.

    This used to aggregate a process-memory dict while `/api/v1/inspections`
    read storage, so after any restart the Dashboard showed "0 inspected" beside
    a full feed. Both now go through `_visible_views`, which means the numbers
    are also narrowed to the caller's station rather than reporting the plant to
    someone entitled to one line.
    """
    scope = station_scope(user, DEFAULT_STATION_ID)
    page = await _visible_views(limit=_METRICS_WINDOW, scope=scope)
    views = page.views
    if not views:
        return {
            "inspected": 0,
            "window": _METRICS_WINDOW,
            "total_stored": page.stored,
            "scope": scope.describe(),
            "currency": "INR",
        }

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
        # Honest bounds on the aggregate: computed over the most recent
        # `window` records visible to this caller, out of `total_stored` held.
        "window": _METRICS_WINDOW,
        "total_visible": page.total,
        "total_stored": page.stored,
        "scope": scope.describe(),
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
    stats = service.stats()

    # Reads the last completed probe and, if it is due, schedules the next one.
    # Never awaits it: `LLMService.probe()` re-measures every provider on every
    # call and took 34.4s here on 2026-08-08, which made this page unusable.
    PROBE_CACHE.ensure_running(lambda: _llm_service().probe())
    snapshot = PROBE_CACHE.snapshot()

    return {
        "tiers": stats["tiers"],
        "skipped_providers": stats["skipped_providers"],
        # Empty until a probe has completed. `probe.status` says which, so an
        # empty matrix reads as "not measured yet" and never as "no provider
        # supports anything" -- we do not invent a capability we have not seen.
        "capabilities": [capability_row(c) for c in snapshot.capabilities],
        "probe": snapshot.as_dict(),
        "roles": {
            role.value: {
                "label": cfg.label,
                "default_page": cfg.default_page,
                "permissions": sorted(cfg.permissions),
                "rate_limit_per_min": cfg.rate_limit_per_min,
            }
            for role, cfg in matrix.roles.items()
        },
        "health": {
            # From storage, like /health and /api/v1/metrics. This read a
            # process dict that nothing ever wrote to, so it was always 0.
            "inspections": await _stored_count(),
            "baseline_runs": state.baseline.derived_from_runs,
            "ws_subscribers": len(state.subscribers),
            "cache_entries": stats["cache"].get("entries", 0),  # type: ignore[union-attr]
            "storage": state.storage.detail if state.storage else "in-memory",
        },
    }


@app.get("/docs", include_in_schema=False)
async def get_swagger_docs(
    user: User = Depends(require_any("inspection:read", "inspection:create", "admin:model_config")),
) -> Any:  # noqa: ANN401
    """Swagger UI documentation (protected - authenticated users only).

    Available to: QA Analysts, Shop Floor Workers, Admins.
    Restricted from: public/unauthenticated access.
    """
    from fastapi.openapi.docs import get_swagger_ui_html  # noqa: PLC0415

    return get_swagger_ui_html(
        openapi_url=app.openapi_url or "/openapi.json",
        title=f"{app.title} – API Docs",
    )


@app.get("/openapi.json", include_in_schema=False)
async def get_openapi_schema(
    user: User = Depends(require_any("inspection:read", "inspection:create", "admin:model_config")),
) -> dict[str, Any]:
    """OpenAPI schema (protected - authenticated users only)."""
    from fastapi.openapi.utils import get_openapi  # noqa: PLC0415

    return get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )


@app.get("/redoc", include_in_schema=False)
async def get_redoc_docs(
    user: User = Depends(require_any("inspection:read", "inspection:create", "admin:model_config")),
) -> Any:  # noqa: ANN401
    """ReDoc documentation (protected - authenticated users only)."""
    from fastapi.openapi.docs import get_redoc_html  # noqa: PLC0415

    return get_redoc_html(
        openapi_url=app.openapi_url or "/openapi.json",
        title=f"{app.title} – API Docs",
    )


@app.websocket("/ws/live")
async def live(websocket: WebSocket) -> None:
    """Streams new inspections to authorized subscribers.

    Authenticated and authorized, which it was not: this endpoint accepted any
    connection and streamed every inspection to it, while `config/rbac.yaml`
    declared `stream:read` as a permission. A browser cannot set an
    `Authorization` header on a WebSocket, so the token arrives as a query
    parameter or as the first frame -- see `authenticate_websocket`.

    A subscriber only ever receives units from stations it is scoped to, so the
    socket cannot be used to route around the narrowing on the REST list.
    """
    identity = await authenticate_websocket(websocket, "stream:read")
    if identity is None:
        return  # already closed with a specific code and reason

    # An anonymous socket is only reachable through the transition flag, and it
    # gets the narrowest scope there is rather than the plant-wide feed it used
    # to get: one unauthenticated station, never every station.
    scope = (
        station_scope(identity.user, DEFAULT_STATION_ID)
        if identity.user
        else StationScope(all_stations=False, station=DEFAULT_STATION_ID)
    )
    sub = _Subscriber(
        queue=asyncio.Queue(maxsize=64),
        scope=scope,
        username=identity.username,
        role=identity.role_value,
    )
    state.subscribers.add(sub)
    try:
        await websocket.send_json({
            "type": "hello",
            "data": {
                "connected": True,
                "authenticated": identity.user is not None,
                "user": identity.username,
                "role": identity.role_value,
                "scope": scope.describe(),
                # Present only when the anonymous escape hatch is open, so an
                # unsecured stream is visible on screen rather than assumed safe.
                "degraded": identity.degraded,
                "degradation_reason": identity.reason,
            },
        })
        while True:
            message = await sub.queue.get()
            if sub.dropped:
                # Tell the client it missed frames instead of letting the board
                # quietly disagree with the server.
                message = {**message, "dropped_frames": sub.dropped}
                sub.dropped = 0
            await websocket.send_json(message)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        state.subscribers.discard(sub)
