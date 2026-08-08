"""What the list, the detail view and the metrics agree on.

Two bugs live here, both of which a demo would have shown on stage:

1. `/api/v1/metrics` counted a process-memory dict while `/api/v1/inspections`
   read storage. After a restart the Dashboard said "0 inspected" beside a full
   feed of units. `test_metrics_and_list_agree_after_a_restart` is the
   regression test.
2. `inspection:read_own_station` was enforced only at the door. Any role that
   got through `require_any` then read every station's units, so the narrower
   grant in `config/rbac.yaml` bought nothing. The station tests are the proof
   that it now buys something.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from apps.api import main as api_main
from forge.domain.enums import AgentName, DataQuality, Severity, SignalKind, Verdict
from forge.domain.provenance import Provenance, ProvenanceSource
from forge.domain.state import FusionVerdict, InspectionEvent, QCState, TraceSpan

OTHER_STATION = "WB-07"


def make_state(unit: str, station: str, *, verdict: Verdict = Verdict.DEFECT) -> QCState:
    state = QCState(
        correlation_id=f"insp-{unit}",
        pack_id="wheel_assembly",
        unit_id=unit,
        event=InspectionEvent(unit_id=unit, station_id=station, pack_id="wheel_assembly"),
        data_quality=DataQuality.GOOD,
    )
    state.fusion = FusionVerdict(
        verdict=verdict,
        confidence=0.8,
        severity=Severity.CRITICAL,
        reasoning="synthetic fixture",
        primary_signal=SignalKind.FUSION,
        fusion_only=True,
        provenance=Provenance(source=ProvenanceSource.RULE, producer="test/1.0"),
    )
    state.trace.append(
        TraceSpan(agent=AgentName.ADJUDICATOR, started_at=datetime.now(UTC),
                  duration_ms=2, summary="fixture")
    )
    return state


@pytest.fixture
def seeded(client: TestClient, headers_for):  # noqa: ANN001, ANN201
    """Three units on the demo station, one on another station."""
    for i in range(3):
        response = client.post(
            "/api/v1/inspections", headers=headers_for("priya"),
            json={"scenario": "clean" if i else "under_torque", "seed": 100 + i},
        )
        assert response.status_code == 200, response.text
    return client


def save_state(state: QCState) -> None:
    """Write straight to the repository, bypassing the API.

    Lets a test create a record at a station the API cannot currently produce
    (the demo runs one station), which is the only way to prove the narrowing
    actually excludes something.
    """
    async def _save() -> None:
        assert api_main.state.storage is not None
        await api_main.state.storage.inspections.save(state)

    asyncio.run(_save())


# ---------------------------------------------------------------------------
# Metrics and the list must not be able to disagree
# ---------------------------------------------------------------------------
def test_metrics_and_list_agree(seeded: TestClient, headers_for) -> None:  # noqa: ANN001
    headers = headers_for("priya")
    listed = seeded.get("/api/v1/inspections?limit=50", headers=headers).json()
    metrics = seeded.get("/api/v1/metrics", headers=headers).json()

    assert listed["total"] == 3
    assert metrics["inspected"] == listed["total"]
    assert metrics["total_stored"] == listed["total"]


def test_metrics_and_list_agree_after_a_restart(
    seeded: TestClient, headers_for,  # noqa: ANN001
) -> None:
    """The regression test for the real bug.

    Clearing the process caches is exactly what a restart does. Storage still
    holds the records, so both endpoints must still report three.
    """
    api_main._VIEWS.clear()          # noqa: SLF001
    api_main.state.states.clear()

    headers = headers_for("priya")
    metrics = seeded.get("/api/v1/metrics", headers=headers).json()
    listed = seeded.get("/api/v1/inspections?limit=50", headers=headers).json()

    assert listed["total"] == 3
    assert metrics["inspected"] == 3, "metrics read process memory, not storage"
    assert metrics["defects"] == 1
    assert len(listed["items"]) == 3


def test_metrics_state_its_own_bounds(seeded: TestClient, headers_for) -> None:  # noqa: ANN001
    """An aggregate over a window must say so rather than imply it is a total."""
    metrics = seeded.get("/api/v1/metrics", headers=headers_for("priya")).json()
    assert metrics["window"] == api_main._METRICS_WINDOW  # noqa: SLF001
    assert metrics["scope"] == "all_stations"
    assert metrics["total_stored"] >= metrics["inspected"]


def test_metrics_on_an_empty_repository_is_zero_not_absent(
    client: TestClient, headers_for,  # noqa: ANN001
) -> None:
    metrics = client.get("/api/v1/metrics", headers=headers_for("priya")).json()
    assert metrics["inspected"] == 0
    assert metrics["total_stored"] == 0


# ---------------------------------------------------------------------------
# Station scope narrowing (inspection:read_own_station)
# ---------------------------------------------------------------------------
def test_shop_floor_list_is_narrowed_to_its_own_station(
    seeded: TestClient, headers_for,  # noqa: ANN001
) -> None:
    save_state(make_state("VIN-OTHER", OTHER_STATION))

    qa = seeded.get("/api/v1/inspections?limit=50", headers=headers_for("priya")).json()
    floor = seeded.get("/api/v1/inspections?limit=50", headers=headers_for("ravi")).json()

    assert qa["total"] == 4
    assert any(i["station_id"] == OTHER_STATION for i in qa["items"])

    assert floor["total"] == 3, "shop floor read another station's units"
    assert {i["station_id"] for i in floor["items"]} == {api_main.DEFAULT_STATION_ID}
    assert floor["scope"] == api_main.DEFAULT_STATION_ID


def test_shop_floor_cannot_fetch_another_stations_unit_by_id(
    seeded: TestClient, headers_for,  # noqa: ANN001
) -> None:
    """Guessing the correlation id must not route around the list narrowing."""
    save_state(make_state("VIN-OTHER", OTHER_STATION))

    assert seeded.get(
        "/api/v1/inspections/insp-VIN-OTHER", headers=headers_for("priya")
    ).status_code == 200
    denied = seeded.get(
        "/api/v1/inspections/insp-VIN-OTHER", headers=headers_for("ravi")
    )
    assert denied.status_code == 403
    assert OTHER_STATION in denied.json()["detail"]


def test_shop_floor_metrics_are_narrowed_too(
    seeded: TestClient, headers_for,  # noqa: ANN001
) -> None:
    """Otherwise the count leaks what the list withholds."""
    save_state(make_state("VIN-OTHER", OTHER_STATION))

    qa = seeded.get("/api/v1/metrics", headers=headers_for("priya")).json()
    floor = seeded.get("/api/v1/metrics", headers=headers_for("ravi")).json()

    assert qa["inspected"] == 4
    assert floor["inspected"] == 3
    assert floor["scope"] == api_main.DEFAULT_STATION_ID


def test_a_unit_with_no_station_is_denied_to_a_scoped_caller(
    client: TestClient, headers_for,  # noqa: ANN001
) -> None:
    """Fail closed: an unattributable record is not 'yours' by default."""
    save_state(make_state("VIN-NOSTATION", ""))
    floor = client.get("/api/v1/inspections?limit=50", headers=headers_for("ravi")).json()
    assert floor["total"] == 0
