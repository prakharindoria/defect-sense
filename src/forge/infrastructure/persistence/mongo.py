"""MongoDB repositories.

Why Mongo rather than Postgres, in one line: `QCState` is a deeply nested
document we read and write whole, and component specifications genuinely vary
by component type. In Postgres both become JSONB blobs — a document store in a
relational costume. See ADR-0015.

Everything here implements the same ports as the in-memory adapters and passes
the same test suite, so the swap is a config change (`MONGO_URL`) rather than a
code change.

Connection is **lazy and non-fatal**. An unreachable Mongo must degrade the
product to ephemeral storage, not stop it booting — a plant that cannot inspect
a wheel because a database is down is worse than one that inspects it and
forgets.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from forge.application.ports.platform import (
    AuditEntry,
    AuditLog,
    InspectionRepository,
    InspectionSummary,
)
from forge.domain.state import QCState
from forge.infrastructure.persistence.memory import _summarise

if TYPE_CHECKING:
    from collections.abc import Sequence

_log = logging.getLogger(__name__)

# Collections. Named here rather than inline so the schema is greppable.
INSPECTIONS = "inspections"
AUDIT_LOG = "audit_log"
USERS = "users"
COMPONENTS = "components"
COMPONENT_VERSIONS = "component_versions"
DATASETS = "datasets"
DETECTORS = "detectors"
CONNECTORS = "connectors"


class MongoUnavailableError(RuntimeError):
    """Mongo was configured but could not be reached."""


async def connect(url: str, *, database: str = "forge", timeout_ms: int = 3_000):  # noqa: ANN201
    """Open a client and verify it actually answers.

    `motor` creates a client object without contacting the server, so an
    unreachable Mongo would look connected until the first real query failed
    deep inside a request. We ping here so the failure surfaces at startup where
    it can be reported honestly.
    """
    try:
        from motor.motor_asyncio import AsyncIOMotorClient  # noqa: PLC0415
    except ImportError as exc:
        raise MongoUnavailableError(
            "motor is not installed; run: python tasks.py install server"
        ) from exc

    client = AsyncIOMotorClient(url, serverSelectionTimeoutMS=timeout_ms)
    try:
        await client.admin.command("ping")
    except Exception as exc:  # noqa: BLE001 - any failure means unusable
        raise MongoUnavailableError(f"{type(exc).__name__}: {exc}") from exc
    return client[database]


async def ensure_indexes(db: Any) -> None:  # noqa: ANN401
    """Create the indexes the query patterns actually need.

    Idempotent, so it runs on every boot. Without `correlation_id` unique, a
    retried write would create a second inspection record for the same run and
    the audit trail would double-count.
    """
    await db[INSPECTIONS].create_index("correlation_id", unique=True)
    await db[INSPECTIONS].create_index([("created_at", -1)])
    await db[INSPECTIONS].create_index("pack_id")
    await db[INSPECTIONS].create_index("fusion_only")
    await db[INSPECTIONS].create_index("verdict")

    await db[AUDIT_LOG].create_index([("at", -1)])
    await db[AUDIT_LOG].create_index("correlation_id")
    await db[AUDIT_LOG].create_index("actor")

    await db[USERS].create_index("username", unique=True)
    await db[COMPONENTS].create_index("component_id", unique=True)
    await db[COMPONENT_VERSIONS].create_index([("component_id", 1), ("version", -1)])
    await db[DATASETS].create_index("dataset_id", unique=True)
    await db[DETECTORS].create_index([("component_id", 1), ("version", -1)])
    await db[DETECTORS].create_index("status")
    await db[CONNECTORS].create_index("status")


class MongoInspectionRepository(InspectionRepository):
    def __init__(self, db: Any) -> None:  # noqa: ANN401
        self._db = db

    async def save(self, state: QCState) -> None:
        document = state.model_dump(mode="json")
        # Denormalised query keys alongside the full document. Mongo can index
        # a nested path, but a verdict that lives at `fusion.verdict` becomes
        # awkward the moment `fusion` is null on a rejected run.
        document["verdict"] = state.fusion.verdict.value if state.fusion else None
        document["severity"] = state.fusion.severity.value if state.fusion else None
        document["fusion_only"] = state.fusion_only
        document["latency_ms"] = state.total_latency_ms

        await self._db[INSPECTIONS].replace_one(
            {"correlation_id": state.correlation_id}, document, upsert=True
        )

    async def get(self, correlation_id: str) -> QCState | None:
        document = await self._db[INSPECTIONS].find_one(
            {"correlation_id": correlation_id}, {"_id": 0}
        )
        if document is None:
            return None
        # Drop the denormalised keys before validating: they are query aids,
        # not part of the frozen QCState contract.
        for key in ("verdict", "severity", "fusion_only", "latency_ms"):
            document.pop(key, None)
        return QCState.model_validate(document)

    async def list(
        self,
        *,
        pack_id: str | None = None,
        verdict: str | None = None,
        fusion_only: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Sequence[InspectionSummary], int]:
        query: dict[str, Any] = {}
        if pack_id is not None:
            query["pack_id"] = pack_id
        if verdict is not None:
            query["verdict"] = verdict
        if fusion_only is not None:
            query["fusion_only"] = fusion_only

        total = await self._db[INSPECTIONS].count_documents(query)
        cursor = (
            self._db[INSPECTIONS]
            .find(query, {"_id": 0})
            .sort("created_at", -1)
            .skip(offset)
            .limit(limit)
        )
        summaries: list[InspectionSummary] = []
        async for document in cursor:
            for key in ("verdict", "severity", "fusion_only", "latency_ms"):
                document.pop(key, None)
            summaries.append(_summarise(QCState.model_validate(document)))
        return tuple(summaries), total


class MongoAuditLog(AuditLog):
    def __init__(self, db: Any) -> None:  # noqa: ANN401
        self._db = db

    async def record(self, entry: AuditEntry) -> None:
        await self._db[AUDIT_LOG].insert_one(
            {
                "actor": entry.actor,
                "role": entry.role,
                "action": entry.action,
                "resource": entry.resource,
                "correlation_id": entry.correlation_id,
                "at": entry.at,
                "before": entry.before,
                "after": entry.after,
                "ip": entry.ip,
            }
        )

    async def query(
        self, *, actor: str | None = None, action: str | None = None, limit: int = 100
    ) -> Sequence[AuditEntry]:
        query: dict[str, Any] = {}
        if actor is not None:
            query["actor"] = actor
        if action is not None:
            query["action"] = action

        cursor = self._db[AUDIT_LOG].find(query, {"_id": 0}).sort("at", -1).limit(limit)
        return [AuditEntry(**document) async for document in cursor]


class MongoDocumentStore:
    """Generic collection access for components, datasets, detectors, connectors.

    Deliberately the same surface as `InMemoryDocumentStore`, so callers cannot
    tell them apart and the in-memory version is a genuine substitute rather
    than a reduced one.
    """

    def __init__(self, db: Any) -> None:  # noqa: ANN401
        self._db = db

    async def put(self, collection: str, key: str, document: dict[str, Any]) -> None:
        await self._db[collection].replace_one({"_key": key}, {**document, "_key": key},
                                               upsert=True)

    async def get(self, collection: str, key: str) -> dict[str, Any] | None:
        found = await self._db[collection].find_one({"_key": key}, {"_id": 0, "_key": 0})
        return found

    async def all(self, collection: str) -> list[dict[str, Any]]:
        return [d async for d in self._db[collection].find({}, {"_id": 0, "_key": 0})]

    async def find(self, collection: str, **equals: Any) -> list[dict[str, Any]]:
        return [
            d async for d in self._db[collection].find(equals, {"_id": 0, "_key": 0})
        ]

    async def delete(self, collection: str, key: str) -> bool:
        result = await self._db[collection].delete_one({"_key": key})
        return result.deleted_count > 0
