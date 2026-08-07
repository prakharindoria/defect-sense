"""Ports for live third-party data.

Each of these is load-bearing, not decoration. The bar for adding one is a
one-line answer to *"why is this API here?"*:

  WeatherPort   ambient humidity drives thread surface condition, which is the
                physical mechanism behind the fusion case (ADR-0002)
  RecallPort    the failure mode we detect, pulled live from a US government
                database, and the source of recall_exposure_per_unit in triage
  FxPort        cost impact rendered in both plant and reporting currency
  CalendarPort  holiday and shift patterns, which is the bias-report slice

All four share one contract about honesty: a value that could not be fetched
fresh is still returned when a cached or recorded one exists, but it is
**marked stale with its age**. Silently substituting a stale value for a live
one is the failure this design exists to prevent (CLAUDE.md rule 4).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime  # noqa: TC003 - dataclass field types; see llm.py


class ExternalDataError(Exception):
    """No fresh value, and no cached or recorded fallback either."""


@dataclass(frozen=True, slots=True)
class Freshness:
    """How much to trust this value's currency.

    `age_seconds` is None only when the value was fetched live just now.
    """

    is_live: bool
    fetched_at: datetime
    age_seconds: int | None = None
    source: str = ""            # "live" | "cache" | "recorded_fixture"
    note: str = ""              # shown in the UI when not live

    @property
    def is_stale(self) -> bool:
        return not self.is_live


@dataclass(frozen=True, slots=True)
class AmbientConditions:
    temperature_c: float
    relative_humidity_pct: float
    latitude: float
    longitude: float
    observed_at: datetime
    freshness: Freshness


@dataclass(frozen=True, slots=True)
class RecallRecord:
    """One safety recall campaign."""

    campaign_number: str        # e.g. "24V237000"
    manufacturer: str
    component: str              # e.g. "WHEELS:LUGS/NUTS/BOLTS/STUDS"
    summary: str
    consequence: str
    remedy: str
    report_received_date: str
    freshness: Freshness


@dataclass(frozen=True, slots=True)
class FxRate:
    base: str
    quote: str
    rate: float
    as_of: datetime
    freshness: Freshness


@dataclass(frozen=True, slots=True)
class Holiday:
    on: date
    name: str
    country_code: str


@dataclass(frozen=True, slots=True)
class HolidayCalendar:
    country_code: str
    year: int
    holidays: tuple[Holiday, ...]
    freshness: Freshness
    # True when the provider has no data for this market at all. Distinct from
    # "fetch failed": Nager.Date returns HTTP 204 for India, which is a real
    # coverage gap we state in the UI rather than rendering as an empty year.
    unsupported_market: bool = False


class WeatherPort(ABC):
    @abstractmethod
    async def current(self, latitude: float, longitude: float) -> AmbientConditions: ...


class RecallPort(ABC):
    @abstractmethod
    async def by_vehicle(self, make: str, model: str, year: int) -> tuple[RecallRecord, ...]: ...

    @abstractmethod
    async def by_campaign(self, campaign_number: str) -> RecallRecord | None: ...


class FxPort(ABC):
    @abstractmethod
    async def rate(self, base: str, quote: str) -> FxRate: ...


class CalendarPort(ABC):
    @abstractmethod
    async def holidays(self, country_code: str, year: int) -> HolidayCalendar: ...

