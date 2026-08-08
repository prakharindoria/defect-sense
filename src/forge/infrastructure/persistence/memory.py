"""In-memory repositories.

Not a stub. This is a first-class implementation with two jobs:

1. **Tests run offline and fast** — the whole suite finishes in ~2s because
   nothing touches a database.
2. **The demo survives Mongo being unreachable.** If `MONGO_URL` is unset or the
   server is down, the product keeps working with an `EPHEMERAL` chip in the UI
   rather than failing to boot. Losing history is a degradation; refusing to
   inspect a wheel is an outage.

Both implementations pass the *same* test suite (`tests/persistence/`), which is
what makes the swap trustworthy rather than hopeful.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from forge.application.ports.platform import (
    AuditEntry,
    AuditLog,
    InspectionRepository,
    InspectionSummary,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    # Type-only. Nothing here constructs or validates a QCState -- it is stored
    # and returned via `model_copy`, so the class itself is never needed at
    # runtime and importing it eagerly only slows startup.
    from forge.domain.state import QCState


class InMemoryInspectionRepository(InspectionRepository):
    def __init__(self, *, max_records: int = 5_000) -> None:
        self._states: dict[str, QCState] = {}
        self._order: list[str] = []
        self._max = max_records

    async def save(self, state: QCState) -> None:
        # Deep-copy on write. Without it the caller keeps a live reference and
        # can mutate a "persisted" record after the fact, which would make the
        # audit trail a lie.
        if state.correlation_id not in self._states:
            self._order.append(state.correlation_id)
        self._states[state.correlation_id] = state.model_copy(deep=True)

        while len(self._order) > self._max:
            self._states.pop(self._order.pop(0), None)

    async def get(self, correlation_id: str) -> QCState | None:
        found = self._states.get(correlation_id)
        return found.model_copy(deep=True) if found else None

    async def list(
        self,
        *,
        pack_id: str | None = None,
        verdict: str | None = None,
        fusion_only: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Sequence[InspectionSummary], int]:
        matched = [
            self._states[cid]
            for cid in reversed(self._order)
            if cid in self._states and _matches(self._states[cid], pack_id, verdict, fusion_only)
        ]
        page = matched[offset : offset + limit]
        return tuple(_summarise(s) for s in page), len(matched)


class InMemoryAuditLog(AuditLog):
    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []

    async def record(self, entry: AuditEntry) -> None:
        self._entries.append(entry)

    async def query(
        self, *, actor: str | None = None, action: str | None = None, limit: int = 100
    ) -> Sequence[AuditEntry]:
        found = [
            e
            for e in reversed(self._entries)
            if (actor is None or e.actor == actor) and (action is None or e.action == action)
        ]
        return found[:limit]


class InMemoryDocumentStore:
    """Generic collection store for components, datasets, detectors, connectors.

    Mirrors the subset of the Mongo API we actually use, so the two adapters
    stay behaviourally identical without pulling a Mongo dependency into tests.
    """

    def __init__(self) -> None:
        self._collections: dict[str, dict[str, dict[str, Any]]] = {}

    def _coll(self, name: str) -> dict[str, dict[str, Any]]:
        return self._collections.setdefault(name, {})

    async def put(self, collection: str, key: str, document: dict[str, Any]) -> None:
        self._coll(collection)[key] = copy.deepcopy(document)

    async def get(self, collection: str, key: str) -> dict[str, Any] | None:
        found = self._coll(collection).get(key)
        return copy.deepcopy(found) if found else None

    async def all(self, collection: str) -> list[dict[str, Any]]:
        return [copy.deepcopy(d) for d in self._coll(collection).values()]

    async def find(self, collection: str, **equals: Any) -> list[dict[str, Any]]:
        return [
            copy.deepcopy(d)
            for d in self._coll(collection).values()
            if all(d.get(k) == v for k, v in equals.items())
        ]

    async def delete(self, collection: str, key: str) -> bool:
        return self._coll(collection).pop(key, None) is not None


# ---------------------------------------------------------------------------
# Shared projection logic — used by both adapters so a summary means the same
# thing regardless of where it came from.
# ---------------------------------------------------------------------------
def _matches(
    state: QCState, pack_id: str | None, verdict: str | None, fusion_only: bool | None
) -> bool:
    if pack_id is not None and state.pack_id != pack_id:
        return False
    if verdict is not None and (state.fusion.verdict.value if state.fusion else None) != verdict:
        return False
    return not (fusion_only is not None and state.fusion_only != fusion_only)


def _summarise(state: QCState) -> InspectionSummary:
    return InspectionSummary(
        correlation_id=state.correlation_id,
        unit_id=state.unit_id,
        pack_id=state.pack_id,
        status=state.status,
        verdict=state.fusion.verdict.value if state.fusion else None,
        severity=state.fusion.severity.value if state.fusion else None,
        fusion_only=state.fusion_only,
        created_at=state.created_at,
        latency_ms=state.total_latency_ms,
        is_synthetic=state.event.is_synthetic,
    )
