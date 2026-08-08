"""The DefectSense chat/voice assistant.

Read-only by design: it explains and narrates using inspection records already
on the state, exactly the way the Adjudicator's narrative does (see
`forge.agents.graph.narrate`). It never submits an inspection, changes a
disposition, overrides a verdict, or halts the line -- those stay
button-driven actions with their own permission checks, and the system prompt
(`agents/prompts/assistant_chat.md`) tells the model the same thing so it does
not claim a capability it does not have.

Reuses the process-wide `_llm_service()` singleton from `apps.api.main` rather
than constructing a second LLM client. The import is deferred to inside the
request handler (not module level) so this module can be imported by
`apps.api.main` while it is still being defined -- the same pattern
`_llm_service()` itself uses for its adapter import.
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from apps.api.security import require_any
from forge.agents.prompts import load as load_prompt
from forge.application.ports.llm import GenerationRequest, LLMError, Message
from forge.domain.enums import DegradationKind, ModelTier
from forge.domain.enums import ProvenanceSource as _ProvenanceSource
from forge.infrastructure.auth import User, load_rbac

if TYPE_CHECKING:
    from forge.domain.state import QCState

router = APIRouter(prefix="/api/v1/assistant", tags=["assistant"])
_log = logging.getLogger(__name__)

# How many recent records to ground the assistant on. Small on purpose: this
# is a chat prompt on the FAST tier, not the Adjudicator's evidence block --
# more context here means slower, costlier answers for a feature that has to
# feel conversational.
_CONTEXT_WINDOW = 8


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    correlation_id: str | None = Field(
        default=None, description="Inspection the user is currently looking at, if any."
    )


class ChatProvenance(BaseModel):
    source: str
    producer: str
    tier: str | None = None
    model_id: str | None = None
    latency_ms: int | None = None
    degradations: list[str] = []
    prompt_version: str | None = None


class ChatResponse(BaseModel):
    reply: str
    provenance: ChatProvenance
    # Correlation IDs actually shown to the model as context, so the UI (and a
    # curious user) can see exactly what the answer was grounded on rather
    # than trusting the prose.
    grounded_on: list[str]


def _summarise(qc: QCState) -> dict[str, Any]:
    """Project a QCState into the small JSON shape handed to the prompt.

    Deliberately narrower than the API's InspectionView: the assistant needs
    enough to answer "what happened to this unit", not the full evidence
    payload the Workbench renders.
    """
    fusion = qc.fusion
    triage = qc.triage
    return {
        "correlation_id": qc.correlation_id,
        "unit_id": qc.unit_id,
        "station_id": qc.event.station_id,
        "verdict": fusion.verdict.value if fusion else "pending",
        "severity": fusion.severity.value if fusion else None,
        "confidence": round(fusion.confidence, 3) if fusion else None,
        "fusion_only": bool(fusion and fusion.fusion_only),
        "reasoning": fusion.reasoning if fusion else None,
        "disposition": triage.recommended if triage else None,
        "requires_human": bool(triage and triage.requires_human),
        "created_at": qc.created_at.isoformat(),
    }


async def _gather_context(
    api_main: Any, user: User, correlation_id: str | None
) -> tuple[list[dict[str, Any]], list[str]]:
    """Scope recent inspections to what this role may see.

    Mirrors the `inspection:read` vs `inspection:read_own_station` split
    already enforced at the route level: QA/Admin get the fuller recent feed,
    a shop-floor user is scoped to their own station's units.

    Reads `_State.states` first (the same in-process cache the Agent Console
    and the narrative background task read) and backfills from durable
    storage for anything missing there. The in-process cache is empty right
    after a restart -- `--reload` during a 36-hour build, a crash, a redeploy
    -- while file/Mongo storage is not; without the backfill the assistant
    would tell an operator "no records" for units the Station and Dashboard
    pages are still showing from disk, which is a worse look than a slower
    answer (mirrors the same cache-miss fallback `get_inspection` uses).
    """
    full_feed = load_rbac().allows(user.role, "inspection:read")
    own_station = getattr(api_main, "DEFAULT_STATION_ID", None)

    by_id: dict[str, QCState] = dict(api_main.state.states)
    storage = api_main.state.storage
    if storage is not None:
        summaries, _ = await storage.inspections.list(limit=_CONTEXT_WINDOW * 3)
        for summary in summaries:
            if summary.correlation_id in by_id:
                continue
            qc = await storage.inspections.get(summary.correlation_id)
            if qc is not None:
                by_id[qc.correlation_id] = qc
        if correlation_id and correlation_id not in by_id:
            qc = await storage.inspections.get(correlation_id)
            if qc is not None:
                by_id[qc.correlation_id] = qc

    all_states: list[QCState] = list(by_id.values())
    if not full_feed:
        all_states = [s for s in all_states if s.event.station_id == own_station]
    all_states.sort(key=lambda s: s.created_at, reverse=True)

    recent = all_states[:_CONTEXT_WINDOW]

    # If the caller is looking at a specific unit, make sure it is in context
    # even if it has scrolled out of the recent window -- but never smuggle in
    # a unit the caller is not scoped to see.
    if correlation_id and not any(s.correlation_id == correlation_id for s in recent):
        target = by_id.get(correlation_id)
        if target is not None and (full_feed or target.event.station_id == own_station):
            recent = [target, *recent[: _CONTEXT_WINDOW - 1]]

    return [_summarise(s) for s in recent], [s.correlation_id for s in recent]


def _fallback_reply(context: list[dict[str, Any]]) -> str:
    """Deterministic stand-in when every provider in the chain is exhausted.

    Never fabricated prose (CLAUDE.md rule 3) -- just a factual readout of the
    same context block the model would have received.
    """
    if not context:
        return (
            "I can't reach the reasoning model right now, and I don't have any "
            "recent inspections in view to summarise."
        )
    latest = context[0]
    return (
        "I can't reach the reasoning model right now. From the record directly: "
        f"unit {latest['unit_id']} was verdict '{latest['verdict']}'"
        + (f", disposition '{latest['disposition']}'" if latest["disposition"] else "")
        + "."
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    user: User = Depends(require_any("inspection:read", "inspection:read_own_station")),
) -> ChatResponse:
    # Deferred import: `apps.api.main` imports this module to register the
    # router, so importing it back at module load time would be circular.
    # By request time the module is fully initialised.
    from apps.api import main as api_main  # noqa: PLC0415

    started = time.perf_counter()
    context, grounded_on = await _gather_context(api_main, user, request.correlation_id)

    prompt = load_prompt("assistant_chat")
    evidence = {
        "caller_role": user.role.value,
        "recent_inspections": context,
    }
    llm_request = GenerationRequest(
        tier=ModelTier.FAST,
        messages=(
            Message("system", prompt.render(evidence=json.dumps(evidence, indent=2))),
            Message("user", request.message),
        ),
        pack_id="wheel_assembly",
        correlation_id=request.correlation_id or "",
    )

    llm = api_main._llm_service()  # noqa: SLF001 - the one sanctioned reuse point
    try:
        completion = await llm.generate(llm_request)
    except LLMError as exc:
        _log.warning("assistant.chat unavailable: %s", exc)
        return ChatResponse(
            reply=_fallback_reply(context),
            provenance=ChatProvenance(
                source=_ProvenanceSource.RULE.value,
                producer="assistant-chat-fallback",
                degradations=[DegradationKind.LLM_UNAVAILABLE.value],
            ),
            grounded_on=grounded_on,
        )

    from_model = DegradationKind.RULE_ENGINE_FALLBACK not in completion.degradations
    _log.info(
        "assistant.chat",
        extra={
            "correlation_id": request.correlation_id,
            "role": user.role.value,
            "from_model": from_model,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )
    return ChatResponse(
        reply=completion.text.strip(),
        provenance=ChatProvenance(
            source=(_ProvenanceSource.LLM if from_model else _ProvenanceSource.RULE).value,
            producer="assistant-chat",
            tier=completion.tier_used.value,
            model_id=completion.model_used,
            latency_ms=completion.latency_ms,
            degradations=[d.value for d in completion.degradations],
            prompt_version=prompt.version,
        ),
        grounded_on=grounded_on,
    )
