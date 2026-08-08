"""Persistence.

Every behavioural test here is **parameterised over both adapters**. That is
what makes swapping in-memory for Mongo trustworthy: they are not "similar", they
are held to one identical contract. Mongo tests skip automatically when
`MONGO_URL` is unset so the suite stays offline by default.

The property that matters most is `test_saved_state_is_not_a_live_reference` —
if a caller can mutate a record after it was "persisted", the audit trail is a
lie regardless of which database is underneath.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from forge.application.ports.platform import AuditEntry
from forge.domain.enums import AgentName, DataQuality, Severity, SignalKind, Verdict
from forge.domain.provenance import Provenance, ProvenanceSource
from forge.domain.state import (
    FusionVerdict,
    InspectionEvent,
    QCState,
    TraceSpan,
)
from forge.infrastructure.persistence import Storage, open_storage
from forge.infrastructure.persistence.memory import (
    InMemoryAuditLog,
    InMemoryDocumentStore,
    InMemoryInspectionRepository,
)

MONGO_URL = os.environ.get("MONGO_URL", "").strip()
mongo_only = pytest.mark.skipif(not MONGO_URL, reason="MONGO_URL not set")


def make_state(
    unit: str = "VIN-1", *, verdict: Verdict = Verdict.DEFECT, fusion_only: bool = True
) -> QCState:
    state = QCState(
        correlation_id=f"insp-{unit}",
        pack_id="wheel_assembly",
        unit_id=unit,
        event=InspectionEvent(unit_id=unit, station_id="WA-01", pack_id="wheel_assembly"),
        data_quality=DataQuality.GOOD,
    )
    state.fusion = FusionVerdict(
        verdict=verdict,
        confidence=0.81,
        severity=Severity.CRITICAL,
        reasoning="Endpoint in spec at 118.0 Nm; elastic slope 2.31 against a 3.20 baseline.",
        primary_signal=SignalKind.FUSION,
        fusion_only=fusion_only,
        escalation_reason="ambiguous" if verdict is Verdict.ESCALATE else None,
        provenance=Provenance(source=ProvenanceSource.RULE, producer="adjudicator/1.0"),
    )
    state.trace.append(
        TraceSpan(
            agent=AgentName.ADJUDICATOR,
            started_at=datetime.now(UTC),
            duration_ms=2,
            summary="DEFECT conf 0.81 (FUSION-ONLY)",
        )
    )
    return state


# ---------------------------------------------------------------------------
# Both adapters, one contract
# ---------------------------------------------------------------------------
@pytest.fixture(params=["memory", pytest.param("mongo", marks=mongo_only)])
async def storage(request: pytest.FixtureRequest):  # noqa: ANN201
    if request.param == "memory":
        yield Storage(
            inspections=InMemoryInspectionRepository(),
            audit=InMemoryAuditLog(),
            documents=InMemoryDocumentStore(),
            mode="ephemeral", detail="test", durable=False,
        )
        return

    store = await open_storage(url=MONGO_URL, database="forge_test")
    assert store.mode == "mongo", "MONGO_URL is set but unreachable"
    yield store
    # Leave the test database clean for the next run.
    for name in ("inspections", "audit_log", "components"):
        await store.documents._db[name].delete_many({})  # noqa: SLF001


class TestInspectionRepository:
    async def test_round_trips_a_full_state(self, storage: Storage) -> None:
        original = make_state("VIN-1")
        await storage.inspections.save(original)
        loaded = await storage.inspections.get("insp-VIN-1")

        assert loaded is not None
        assert loaded.correlation_id == original.correlation_id
        assert loaded.fusion is not None
        assert loaded.fusion.verdict is Verdict.DEFECT
        assert loaded.fusion.fusion_only
        # The trace is the audit record; losing it loses the whole point.
        assert len(loaded.trace) == 1
        assert loaded.trace[0].agent is AgentName.ADJUDICATOR

    async def test_missing_id_returns_none(self, storage: Storage) -> None:
        assert await storage.inspections.get("nope") is None

    async def test_save_is_idempotent(self, storage: Storage) -> None:
        """The Action agent retries on failure. A retry must not duplicate."""
        state = make_state("VIN-2")
        await storage.inspections.save(state)
        await storage.inspections.save(state)
        _, total = await storage.inspections.list()
        assert total == 1

    async def test_saved_state_is_not_a_live_reference(self, storage: Storage) -> None:
        """Mutating the object after saving must not change what was stored.

        Without a copy-on-write the caller keeps a handle into the store and can
        silently rewrite history, which makes the audit trail worthless.
        """
        state = make_state("VIN-3")
        await storage.inspections.save(state)

        state.status = "tampered"
        state.trace.append(
            TraceSpan(agent=AgentName.ACTION, started_at=datetime.now(UTC),
                      duration_ms=1, summary="injected after save")
        )

        loaded = await storage.inspections.get("insp-VIN-3")
        assert loaded is not None
        assert loaded.status != "tampered"
        assert len(loaded.trace) == 1

    async def test_lists_newest_first(self, storage: Storage) -> None:
        for i in range(3):
            await storage.inspections.save(make_state(f"VIN-L{i}"))
        page, total = await storage.inspections.list()
        assert total == 3
        assert page[0].unit_id == "VIN-L2"

    async def test_filters(self, storage: Storage) -> None:
        await storage.inspections.save(make_state("VIN-P", verdict=Verdict.PASS,
                                                  fusion_only=False))
        await storage.inspections.save(make_state("VIN-D", verdict=Verdict.DEFECT,
                                                  fusion_only=True))

        defects, n = await storage.inspections.list(verdict="defect")
        assert n == 1 and defects[0].unit_id == "VIN-D"

        fusion, n = await storage.inspections.list(fusion_only=True)
        assert n == 1 and fusion[0].unit_id == "VIN-D"

        none, n = await storage.inspections.list(pack_id="does_not_exist")
        assert n == 0 and none == ()

    async def test_pagination(self, storage: Storage) -> None:
        for i in range(5):
            await storage.inspections.save(make_state(f"VIN-{i:02d}"))
        page, total = await storage.inspections.list(limit=2, offset=2)
        assert total == 5
        assert len(page) == 2

    async def test_summary_carries_what_the_feed_needs(self, storage: Storage) -> None:
        await storage.inspections.save(make_state("VIN-S"))
        page, _ = await storage.inspections.list()
        summary = page[0]
        assert summary.verdict == "defect"
        assert summary.severity == "critical"
        assert summary.fusion_only is True
        assert summary.latency_ms >= 0


class TestAuditLog:
    async def test_records_and_queries(self, storage: Storage) -> None:
        await storage.audit.record(
            AuditEntry(actor="priya", role="QA", action="inspection:override_verdict",
                       resource="insp-1", correlation_id="insp-1", at=datetime.now(UTC),
                       before={"verdict": "defect"}, after={"verdict": "pass"}, ip="10.0.0.1")
        )
        entries = await storage.audit.query()
        assert len(entries) == 1
        assert entries[0].actor == "priya"
        # before/after must survive: an override without them is not auditable.
        assert entries[0].before == {"verdict": "defect"}
        assert entries[0].after == {"verdict": "pass"}

    async def test_filters_by_actor_and_action(self, storage: Storage) -> None:
        now = datetime.now(UTC)
        for actor, action in (("priya", "a"), ("sam", "b"), ("priya", "b")):
            await storage.audit.record(
                AuditEntry(actor=actor, role="QA", action=action, resource="r",
                           correlation_id="c", at=now)
            )
        assert len(await storage.audit.query(actor="priya")) == 2
        assert len(await storage.audit.query(action="b")) == 2


class TestDocumentStore:
    async def test_put_get_find_delete(self, storage: Storage) -> None:
        await storage.documents.put(
            "components", "wheel_assembly",
            {"component_id": "wheel_assembly", "kind": "annulus", "inspection_points": 5},
        )
        found = await storage.documents.get("components", "wheel_assembly")
        assert found is not None
        assert found["inspection_points"] == 5

        assert len(await storage.documents.find("components", kind="annulus")) == 1
        assert await storage.documents.find("components", kind="board") == []
        assert await storage.documents.delete("components", "wheel_assembly") is True
        assert await storage.documents.get("components", "wheel_assembly") is None

    async def test_stored_document_is_not_a_live_reference(self, storage: Storage) -> None:
        doc = {"component_id": "x", "spec": {"limit": 1}}
        await storage.documents.put("components", "x", doc)
        doc["spec"]["limit"] = 999

        loaded = await storage.documents.get("components", "x")
        assert loaded is not None
        assert loaded["spec"]["limit"] == 1


# ---------------------------------------------------------------------------
# Degradation must be visible, never silent
# ---------------------------------------------------------------------------
class TestStorageSelection:
    async def test_unset_url_degrades_visibly(self) -> None:
        store = await open_storage(url="")
        assert store.mode == "ephemeral"
        assert store.durable is False
        assert "lost on restart" in store.detail

    async def test_unreachable_mongo_does_not_stop_boot(self) -> None:
        """A database being down must not prevent inspecting a wheel."""
        store = await open_storage(url="mongodb://127.0.0.1:1/forge", database="forge_test")
        assert store.mode == "ephemeral"
        assert store.durable is False
        assert "unreachable" in store.detail.lower() or "not set" in store.detail.lower()
        # And it still works.
        await store.inspections.save(make_state("VIN-DEGRADED"))
        assert await store.inspections.get("insp-VIN-DEGRADED") is not None

    async def test_credentials_are_redacted(self) -> None:
        """A connection string reaches logs and the Admin page. It must not carry a password."""
        store = await open_storage(
            url="mongodb://admin:sup3rsecret@127.0.0.1:1/forge", database="forge_test"
        )
        assert "sup3rsecret" not in store.detail
        assert "***" in store.detail

    @mongo_only
    async def test_real_mongo_reports_durable(self) -> None:
        store = await open_storage(url=MONGO_URL, database="forge_test")
        assert store.mode == "mongo"
        assert store.durable is True
