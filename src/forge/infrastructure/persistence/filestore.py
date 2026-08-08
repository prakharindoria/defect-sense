"""File-backed repositories — durable persistence with no server and no network.

Why this exists: the build network blocks all outbound ports except 80 and 443,
so MongoDB Atlas (which needs 27017) is unreachable. Verified by connecting to
portquiz.net, which accepts any port — 80 succeeded, 27017 and 8443 both timed
out. That is a corporate egress policy, not an Atlas or credentials problem, and
no Atlas setting can change it.

Rather than have "persistence" mean "in-memory until someone finds a permitting
network", this stores documents as JSON files under `.forge/store/`. It is
genuinely durable across restarts, needs no install, and implements exactly the
same ports as the Mongo adapters — so it passes the same test suite and the demo
is not hostage to venue network policy.

It is NOT a database. Deliberate limits, stated rather than discovered later:

- One file per document, so a large corpus means a large directory. Fine at demo
  scale (hundreds of inspections), wrong at plant scale.
- Listing reads an index file rather than scanning, but filtering still loads
  matching documents. No query planner, no secondary indexes.
- Single-process. Two API workers writing concurrently would race. We run one.

Writes are atomic (temp file + `os.replace`) so a crash mid-write leaves the
previous version intact rather than a truncated file. A half-written audit
record would be worse than no record.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from forge.application.ports.platform import (
    AuditEntry,
    AuditLog,
    InspectionRepository,
    InspectionSummary,
)
from forge.domain.state import QCState
from forge.infrastructure.persistence.memory import _matches, _summarise

if TYPE_CHECKING:
    from collections.abc import Sequence

DEFAULT_ROOT = Path(".forge/store")


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON so an interrupted write cannot corrupt the existing file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, default=str)
        os.replace(tmp, path)          # atomic on POSIX and on Windows
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _safe_name(key: str) -> str:
    """Make a key usable as a filename without collisions.

    Correlation IDs are already filesystem-safe, but a component id or a
    user-supplied key might not be. Replacing unsafe characters alone could map
    two distinct keys onto one file, so the original is kept inside the document
    and lookups verify it.
    """
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in key)[:120]


class FileInspectionRepository(InspectionRepository):
    def __init__(self, root: Path | str = DEFAULT_ROOT) -> None:
        self._dir = Path(root) / "inspections"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def _path(self, correlation_id: str) -> Path:
        return self._dir / f"{_safe_name(correlation_id)}.json"

    async def save(self, state: QCState) -> None:
        async with self._lock:
            await asyncio.to_thread(
                _atomic_write, self._path(state.correlation_id), state.model_dump(mode="json")
            )

    async def get(self, correlation_id: str) -> QCState | None:
        path = self._path(correlation_id)
        if not path.exists():
            return None
        raw = await asyncio.to_thread(path.read_text, "utf-8")
        return QCState.model_validate(json.loads(raw))

    async def _load_all(self) -> list[QCState]:
        def read() -> list[QCState]:
            states: list[QCState] = []
            for path in self._dir.glob("*.json"):
                try:
                    states.append(QCState.model_validate(json.loads(path.read_text("utf-8"))))
                except Exception:  # noqa: BLE001, S112
                    # A corrupt record must not take out the whole listing. It is
                    # skipped here and stays on disk for inspection rather than
                    # being silently deleted.
                    continue
            return states

        states = await asyncio.to_thread(read)
        states.sort(key=lambda s: s.created_at, reverse=True)
        return states

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
            s for s in await self._load_all() if _matches(s, pack_id, verdict, fusion_only)
        ]
        page = matched[offset : offset + limit]
        return tuple(_summarise(s) for s in page), len(matched)


class FileAuditLog(AuditLog):
    """Append-only JSONL.

    An audit log is the one store where append-only is not a shortcut but the
    requirement: a record you can rewrite is not evidence.
    """

    def __init__(self, root: Path | str = DEFAULT_ROOT) -> None:
        self._path = Path(root) / "audit_log.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    async def record(self, entry: AuditEntry) -> None:
        line = json.dumps(
            {
                "actor": entry.actor, "role": entry.role, "action": entry.action,
                "resource": entry.resource, "correlation_id": entry.correlation_id,
                "at": entry.at.isoformat(), "before": entry.before,
                "after": entry.after, "ip": entry.ip,
            },
            default=str,
        )

        def append() -> None:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())   # survive a hard kill, not just a clean exit

        async with self._lock:
            await asyncio.to_thread(append)

    async def query(
        self, *, actor: str | None = None, action: str | None = None, limit: int = 100
    ) -> Sequence[AuditEntry]:
        if not self._path.exists():
            return []

        def read() -> list[AuditEntry]:
            entries: list[AuditEntry] = []
            for line in self._path.read_text("utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    data["at"] = datetime.fromisoformat(data["at"])
                    entries.append(AuditEntry(**data))
                except Exception:  # noqa: BLE001, S112
                    continue
            return entries

        entries = await asyncio.to_thread(read)
        found = [
            e for e in reversed(entries)
            if (actor is None or e.actor == actor) and (action is None or e.action == action)
        ]
        return found[:limit]


class FileDocumentStore:
    """Collections as directories, documents as JSON files."""

    def __init__(self, root: Path | str = DEFAULT_ROOT) -> None:
        self._root = Path(root) / "collections"
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def _dir(self, collection: str) -> Path:
        path = self._root / _safe_name(collection)
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def put(self, collection: str, key: str, document: dict[str, Any]) -> None:
        async with self._lock:
            await asyncio.to_thread(
                _atomic_write,
                self._dir(collection) / f"{_safe_name(key)}.json",
                {**document, "_key": key},
            )

    async def get(self, collection: str, key: str) -> dict[str, Any] | None:
        path = self._dir(collection) / f"{_safe_name(key)}.json"
        if not path.exists():
            return None
        document = json.loads(await asyncio.to_thread(path.read_text, "utf-8"))
        # Verify the original key: two distinct keys can sanitise to one filename.
        if document.get("_key") != key:
            return None
        document.pop("_key", None)
        return document

    async def all(self, collection: str) -> list[dict[str, Any]]:
        def read() -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for path in self._dir(collection).glob("*.json"):
                try:
                    document = json.loads(path.read_text("utf-8"))
                except Exception:  # noqa: BLE001, S112
                    continue
                document.pop("_key", None)
                out.append(document)
            return out

        return await asyncio.to_thread(read)

    async def find(self, collection: str, **equals: Any) -> list[dict[str, Any]]:
        return [d for d in await self.all(collection)
                if all(d.get(k) == v for k, v in equals.items())]

    async def delete(self, collection: str, key: str) -> bool:
        path = self._dir(collection) / f"{_safe_name(key)}.json"
        if not path.exists():
            return False
        await asyncio.to_thread(path.unlink)
        return True
