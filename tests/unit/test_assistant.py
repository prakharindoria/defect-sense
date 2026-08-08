"""The assistant's role-scoping and fallback logic.

The chat endpoint itself just wires these to `_llm_service()` (see
`apps/api/routers/assistant.py`), so what actually needs a test is the part
that decides *what a role is allowed to see*: a shop-floor user must be scoped
to their own station, QA/Admin get the fuller recent feed, and an unavailable
model must fall back to a factual (not fabricated) reply. All offline -- no
FastAPI app, no LLM provider.
"""

from __future__ import annotations

from types import SimpleNamespace

from apps.api.routers.assistant import _fallback_reply, _gather_context
from forge.domain.enums import Role
from forge.domain.state import InspectionEvent, QCState
from forge.infrastructure.auth import User
from forge.infrastructure.persistence.memory import InMemoryInspectionRepository

STATION_A = "WA-01"
STATION_B = "WA-02"


def _qc(unit: str, station: str, correlation_id: str | None = None) -> QCState:
    return QCState(
        correlation_id=correlation_id or f"insp-{unit}",
        pack_id="wheel_assembly",
        unit_id=unit,
        event=InspectionEvent(unit_id=unit, station_id=station, pack_id="wheel_assembly"),
    )


def _api_main(states: dict[str, QCState], storage: object | None = None) -> SimpleNamespace:
    """A stand-in for `apps.api.main` carrying only what `_gather_context` reads."""
    return SimpleNamespace(
        state=SimpleNamespace(states=states, storage=storage),
        DEFAULT_STATION_ID=STATION_A,
    )


def _user(role: Role) -> User:
    return User(username="test", display_name="Test User", role=role, password_hash="x")


async def test_shop_floor_is_scoped_to_own_station() -> None:
    own = _qc("VIN-OWN-1", STATION_A)
    other = _qc("VIN-OTHER-1", STATION_B)
    api_main = _api_main({own.correlation_id: own, other.correlation_id: other})

    context, grounded_on = await _gather_context(api_main, _user(Role.SHOP_FLOOR_WORKER), None)

    assert grounded_on == [own.correlation_id]
    assert context[0]["unit_id"] == "VIN-OWN-1"


async def test_qa_sees_every_station() -> None:
    own = _qc("VIN-OWN-1", STATION_A)
    other = _qc("VIN-OTHER-1", STATION_B)
    api_main = _api_main({own.correlation_id: own, other.correlation_id: other})

    _, grounded_on = await _gather_context(api_main, _user(Role.QA), None)

    assert set(grounded_on) == {own.correlation_id, other.correlation_id}


async def test_shop_floor_cannot_smuggle_in_a_foreign_correlation_id() -> None:
    """Explicitly asking about a unit outside your station must not surface it.

    Otherwise `correlation_id` in the request body would be a way around the
    station scoping that filters the general feed.
    """
    own = _qc("VIN-OWN-1", STATION_A)
    other = _qc("VIN-OTHER-1", STATION_B, correlation_id="insp-foreign")
    api_main = _api_main({own.correlation_id: own, other.correlation_id: other})

    context, grounded_on = await _gather_context(
        api_main, _user(Role.SHOP_FLOOR_WORKER), correlation_id="insp-foreign"
    )

    assert "insp-foreign" not in grounded_on
    assert all(c["unit_id"] != "VIN-OTHER-1" for c in context)


async def test_qa_requested_correlation_id_is_pulled_into_context() -> None:
    own = _qc("VIN-OWN-1", STATION_A)
    target = _qc("VIN-TARGET-1", STATION_B, correlation_id="insp-target")
    api_main = _api_main({own.correlation_id: own, target.correlation_id: target})

    context, grounded_on = await _gather_context(
        api_main, _user(Role.QA), correlation_id="insp-target"
    )

    assert "insp-target" in grounded_on
    assert any(c["unit_id"] == "VIN-TARGET-1" for c in context)


async def test_backfills_from_storage_when_memory_cache_is_empty() -> None:
    """A restart empties `_State.states` but not durable storage.

    Without the backfill, the assistant would tell an operator "no records"
    for a unit the Station/Dashboard pages are still showing from disk --
    reproduces the exact mismatch seen manually: Station listed VIN-SYN-00003
    from a prior process while a fresh `state.states` was empty.
    """
    persisted = _qc("VIN-PERSISTED-1", STATION_A)
    storage = SimpleNamespace(inspections=InMemoryInspectionRepository())
    await storage.inspections.save(persisted)
    api_main = _api_main({}, storage=storage)

    context, grounded_on = await _gather_context(api_main, _user(Role.QA), None)

    assert grounded_on == [persisted.correlation_id]
    assert context[0]["unit_id"] == "VIN-PERSISTED-1"


async def test_backfill_still_honours_station_scoping() -> None:
    persisted_other = _qc("VIN-OTHER-1", STATION_B)
    storage = SimpleNamespace(inspections=InMemoryInspectionRepository())
    await storage.inspections.save(persisted_other)
    api_main = _api_main({}, storage=storage)

    context, grounded_on = await _gather_context(
        api_main, _user(Role.SHOP_FLOOR_WORKER), None
    )

    assert grounded_on == []
    assert context == []


def test_fallback_reply_never_fabricates_when_context_is_empty() -> None:
    reply = _fallback_reply([])
    assert "recent inspections" in reply.lower()


def test_fallback_reply_cites_only_given_context() -> None:
    context = [{
        "correlation_id": "insp-1", "unit_id": "VIN-1", "verdict": "defect",
        "disposition": "quarantine",
    }]
    reply = _fallback_reply(context)
    assert "VIN-1" in reply
    assert "defect" in reply
    assert "quarantine" in reply
