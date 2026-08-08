"""Defect assignment and rework management routes.

Enables QA Analysts and Quality Managers to assign defect units to specific
shop-floor workers. Triggers an email notification (from admin.defect.sense@gmail.com
to prakhar181999@gmail.com) and updates the line state in real time.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from apps.api.security import require
from forge.infrastructure.auth import User, user_store
from forge.infrastructure.email import send_defect_assignment_email

router = APIRouter(prefix="/api/v1/defects", tags=["defects"])
_log = logging.getLogger(__name__)

# Persistent in-memory map for assignments: correlation_id -> assignment details
_ASSIGNMENTS: dict[str, dict[str, Any]] = {}


class AssignDefectRequest(BaseModel):
    correlation_id: str = Field(min_length=1)
    assigned_to_username: str = Field(examples=["ravi", "aarav"])
    recipient_email: str = Field("prakhar181999@gmail.com")
    note: str = ""


class AssignmentResponse(BaseModel):
    correlation_id: str
    unit_id: str
    verdict: str
    disposition: str
    assigned_to_username: str
    assigned_to_name: str
    assigned_by_name: str
    recipient_email: str
    email_sent: bool
    note: str
    assigned_at: str


from apps.api.security import require_any

@router.post("/assign", response_model=AssignmentResponse)
async def assign_defect(
    request: AssignDefectRequest,
    user: User = Depends(require_any("inspection:create", "inspection:acknowledge", "inspection:read_own_station")),
) -> AssignmentResponse:
    """QA Analyst or Shop Floor Worker assigns a defect unit to a worker, sending Email & Slack alerts."""
    from apps.api import main as api_main  # noqa: PLC0415
    from datetime import UTC, datetime  # noqa: PLC0415

    # 1. Verify assigned user exists in user store
    target_user = user_store().get(request.assigned_to_username.strip().lower())
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Shop floor worker '{request.assigned_to_username}' not found.",
        )

    # 2. Lookup inspection record
    qc = api_main.state.states.get(request.correlation_id)
    if not qc and api_main.state.storage:
        qc = await api_main.state.storage.inspections.get(request.correlation_id)

    if not qc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Inspection record '{request.correlation_id}' not found.",
        )

    disposition = qc.triage.recommended.value if qc.triage else "REWORK"
    verdict = qc.fusion.verdict.value if qc.fusion else "defect"
    primary_signal = qc.fusion.primary_signal.value if qc.fusion else "measured"

    now_iso = datetime.now(UTC).isoformat()
    assignment_record = {
        "correlation_id": qc.correlation_id,
        "unit_id": qc.unit_id,
        "verdict": verdict,
        "disposition": disposition,
        "assigned_to_username": target_user.username,
        "assigned_to_name": target_user.display_name,
        "assigned_by_name": user.display_name,
        "recipient_email": request.recipient_email,
        "note": request.note,
        "assigned_at": now_iso,
    }

    _ASSIGNMENTS[qc.correlation_id] = assignment_record

    # Invalidate view cache so GET /api/v1/inspections shows updated custody immediately
    api_main._VIEWS.pop(qc.correlation_id, None)

    # Persist assignment record into MongoDB / document store
    try:
        if api_main.state.storage:
            docs_store = api_main.state.storage.documents
            if hasattr(docs_store, "_db") and docs_store._db is not None:
                await docs_store._db["assignments"].replace_one(
                    {"correlation_id": qc.correlation_id},
                    assignment_record,
                    upsert=True,
                )
            await docs_store.put("assignments", qc.correlation_id, assignment_record)
    except Exception as exc:  # noqa: BLE001
        _log.warning("defect.assignment_persist_failed", extra={"error": str(exc)})

    # 3. Trigger email notification asynchronously
    email_sent = await send_defect_assignment_email(
        unit_id=qc.unit_id,
        verdict=verdict,
        disposition=disposition,
        primary_signal=primary_signal,
        assigned_to_display=f"{target_user.display_name} ({target_user.username})",
        assigned_by_display=f"{user.display_name} ({user.role.value})",
        recipient_email=request.recipient_email,
    )

    # 4. Trigger Slack notification asynchronously
    from forge.infrastructure.slack import send_slack_defect_assignment  # noqa: PLC0415
    slack_sent = await send_slack_defect_assignment(
        unit_id=qc.unit_id,
        verdict=verdict,
        disposition=disposition,
        primary_signal=primary_signal,
        assigned_to_display=f"{target_user.display_name} ({target_user.username})",
        assigned_by_display=f"{user.display_name} ({user.role.value})",
    )

    # 5. Broadcast live update via WebSocket
    await api_main.state.broadcast(
        {
            "type": "assignment",
            "data": {**assignment_record, "email_sent": email_sent, "slack_sent": slack_sent},
        },
        station_id=qc.event.station_id,
    )

    _log.info(
        "defect.assigned",
        extra={
            "correlation_id": qc.correlation_id,
            "unit_id": qc.unit_id,
            "assigned_to": target_user.username,
            "assigned_by": user.username,
            "email_sent": email_sent,
        },
    )

    return AssignmentResponse(
        correlation_id=qc.correlation_id,
        unit_id=qc.unit_id,
        verdict=verdict,
        disposition=disposition,
        assigned_to_username=target_user.username,
        assigned_to_name=target_user.display_name,
        assigned_by_name=user.display_name,
        recipient_email=request.recipient_email,
        email_sent=email_sent,
        note=request.note,
        assigned_at=now_iso,
    )


@router.get("/assignments", response_model=list[AssignmentResponse])
async def list_assignments() -> list[AssignmentResponse]:
    """List all current defect assignments."""
    return [
        AssignmentResponse(
            correlation_id=v["correlation_id"],
            unit_id=v["unit_id"],
            verdict=v["verdict"],
            disposition=v["disposition"],
            assigned_to_username=v["assigned_to_username"],
            assigned_to_name=v["assigned_to_name"],
            assigned_by_name=v["assigned_by_name"],
            recipient_email=v["recipient_email"],
            email_sent=True,
            note=v["note"],
            assigned_at=v["assigned_at"],
        )
        for v in _ASSIGNMENTS.values()
    ]
