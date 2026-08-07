"""MES port. Declared in v1, implemented in v2.

Modelled on ERPNext's actual doctypes -- `Work Order`, `Job Card`,
`Quality Inspection`, `BOM`, `Batch`, `Workstation` -- rather than on an
invented schema, so the simulator and a real instance satisfy the same
interface and swapping between them is an env var (ADR-0008).

The property worth building around: ERPNext treats quality as a **transactional
dependency**, not an informational add-on. A Delivery Note cannot be submitted
while a required inspection is unapproved. So writing a rejected Quality
Inspection genuinely blocks a downstream transaction -- that is a real business
consequence, not a demo animation, and `write_quality_inspection` exists to make
that consequence reachable.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime  # noqa: TC003 - dataclass field type; see llm.py


class MESError(Exception): ...


class MESUnavailableError(MESError):
    """Retryable. The circuit breaker and DLQ handle it; inspection continues."""


@dataclass(frozen=True, slots=True)
class JobCardContext:
    """Per-operation context for one unit at one station."""

    job_card: str
    work_order: str
    item_code: str
    bom_variant: str
    batch: str
    material_lot: str
    workstation: str
    operation: str
    tool_id: str
    tool_age_cycles: int
    tool_days_since_calibration: int
    operator_token: str          # already tokenised; never a raw operator ID
    shift: str
    started_at: datetime


@dataclass(frozen=True, slots=True)
class InspectionReading:
    """One parameter reading on a Quality Inspection."""

    parameter: str
    value: str
    acceptance_criteria: str
    accepted: bool


@dataclass(frozen=True, slots=True)
class QualityInspectionWrite:
    """A Quality Inspection to create against a Job Card, In Process type."""

    job_card: str
    item_code: str
    batch: str
    accepted: bool
    readings: tuple[InspectionReading, ...]
    remarks: str
    correlation_id: str
    # Makes retries safe. The Action agent retries on failure, and a duplicated
    # inspection record would corrupt the very audit trail we are producing.
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class QualityInspectionRef:
    name: str                    # ERPNext document name
    accepted: bool
    created_at: datetime
    # Whether the downstream transaction is genuinely blocked as a result. Read
    # back from the MES rather than assumed, because "we wrote a record" and
    # "the shipment is stopped" are different claims.
    blocks_downstream: bool = False


class MESPort(ABC):
    @abstractmethod
    async def job_card_context(self, unit_id: str) -> JobCardContext: ...

    @abstractmethod
    async def expected_variant(self, work_order: str) -> str:
        """BOM variant the order specifies, for build-to-order verification."""

    @abstractmethod
    async def write_quality_inspection(
        self, write: QualityInspectionWrite
    ) -> QualityInspectionRef: ...

    @abstractmethod
    async def read_quality_inspection(self, name: str) -> QualityInspectionRef | None:
        """Read back a written record. Proves the round trip, not just the write."""

    @abstractmethod
    async def health(self) -> dict[str, object]:
        """Reachability, latency and error rate for the Integration Hub."""

