"""Storage wiring.

One entry point, `open_storage()`, which returns working repositories whether or
not a database is reachable — and always reports which it gave you.

The degradation is deliberate and visible. If `MONGO_URL` is unset or Mongo is
down we return the in-memory adapters and set `mode="ephemeral"`, which the UI
renders as an `EPHEMERAL` chip. A plant that cannot inspect a wheel because a
database is unreachable is worse than one that inspects it and forgets; but
forgetting silently is worse than both.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from forge.infrastructure.persistence.filestore import (
    DEFAULT_ROOT,
    FileAuditLog,
    FileDocumentStore,
    FileInspectionRepository,
)
from forge.infrastructure.persistence.memory import (
    InMemoryAuditLog,
    InMemoryDocumentStore,
    InMemoryInspectionRepository,
)
from forge.infrastructure.persistence.mongo import (
    MongoAuditLog,
    MongoDocumentStore,
    MongoInspectionRepository,
    MongoUnavailableError,
    connect,
    ensure_indexes,
)

if TYPE_CHECKING:
    # Type-only: these name the ports the adapters implement, and nothing here
    # introspects the annotations at runtime.
    from forge.application.ports.platform import AuditLog, InspectionRepository

_log = logging.getLogger(__name__)


@dataclass(slots=True)
class Storage:
    inspections: InspectionRepository
    audit: AuditLog
    documents: Any
    mode: str            # "mongo" | "file" | "ephemeral"
    detail: str          # why, in words the UI can show verbatim
    durable: bool
    # True when this is NOT the storage that was asked for. `durable` alone
    # cannot say that: the file tier is genuinely durable, so a Mongo outage
    # that fell through to it would otherwise look identical to a healthy boot.
    degraded: bool = False

    def status(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "durable": self.durable,
            "degraded": self.degraded,
            "detail": self.detail,
        }


def _ephemeral(detail: str, *, degraded: bool = True) -> Storage:
    return Storage(
        inspections=InMemoryInspectionRepository(),
        audit=InMemoryAuditLog(),
        documents=InMemoryDocumentStore(),
        mode="ephemeral",
        detail=detail,
        durable=False,
        degraded=degraded,
    )


def _file_backed(root: Path, detail: str, *, degraded: bool) -> Storage:
    return Storage(
        inspections=FileInspectionRepository(root),
        audit=FileAuditLog(root),
        documents=FileDocumentStore(root),
        mode="file",
        detail=detail,
        durable=True,
        degraded=degraded,
    )


async def open_storage(
    *,
    url: str | None = None,
    database: str | None = None,
    root: Path | str | None = None,
    allow_file: bool = True,
) -> Storage:
    """Open the best storage available, never raising.

    Ladder: **Mongo → file → memory.**

    Boot must not depend on a database being up, so this returns a working
    Storage in every case and reports which one it gave you. The file tier
    exists because on this network MongoDB is genuinely unreachable — outbound
    ports other than 80/443 are blocked by corporate egress policy (verified
    against portquiz.net, which accepts any port: 80 succeeded, 27017 and 8443
    timed out). Without it, "persistence" would silently mean "in memory".
    """
    url = url if url is not None else os.environ.get("MONGO_URL", "").strip()
    database = database or os.environ.get("MONGO_DB", "forge")
    # FORGE_STORE_ROOT keeps a test run (or a second instance on a demo laptop)
    # off the store the running app is using.
    root = Path(root or os.environ.get("FORGE_STORE_ROOT") or DEFAULT_ROOT)

    if url:
        try:
            db = await connect(url, database=database)
            await ensure_indexes(db)
        except MongoUnavailableError as exc:
            reason = (
                f"MongoDB at {_redact(url)} is unreachable ({str(exc)[:120]})."
            )
            if allow_file:
                _log.warning("mongo unavailable, using file store: %s", exc)
                return _file_backed(
                    root,
                    f"{reason} Falling back to durable file storage at {root}/ — "
                    f"inspections DO survive a restart, but this is not a database.",
                    # Configured for Mongo and not running on it: degraded, even
                    # though what we fell back to is itself durable.
                    degraded=True,
                )
            _log.warning("mongo unavailable, falling back to in-memory: %s", exc)
            return _ephemeral(f"{reason} Inspections are held in memory and lost on restart.")
        else:
            _log.info("mongo connected: %s", _redact(url))
            return Storage(
                inspections=MongoInspectionRepository(db),
                audit=MongoAuditLog(db),
                documents=MongoDocumentStore(db),
                mode="mongo",
                detail=f"Connected to MongoDB at {_redact(url)}.",
                durable=True,
            )

    if allow_file:
        return _file_backed(
            root,
            f"MONGO_URL is not set. Using durable file storage at {root}/ — "
            f"inspections survive a restart.",
            # Not degraded: no database was asked for, and the file tier is the
            # declared local profile. Calling this degraded would cry wolf.
            degraded=False,
        )
    return _ephemeral(
        "MONGO_URL is not set. Inspections are held in memory and are lost on restart."
    )


def _redact(url: str) -> str:
    """Strip credentials before a connection string reaches a log or the UI."""
    if "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    _, _, host = rest.rpartition("@")
    return f"{scheme}://***@{host}" if scheme else f"***@{host}"


__all__ = [
    "FileAuditLog",
    "FileDocumentStore",
    "FileInspectionRepository",
    "InMemoryAuditLog",
    "InMemoryDocumentStore",
    "InMemoryInspectionRepository",
    "MongoAuditLog",
    "MongoDocumentStore",
    "MongoInspectionRepository",
    "Storage",
    "open_storage",
]
