"""Ports: the boundary between FORGE and every external system.

Each port has at least two implementations, real and fake. The fakes are what
make the test suite fast and the demo reliable, and they are why a judge asking
to see a provider swapped gets a live switch rather than a slide.

v1 implements: LLMPort, InspectionRepository, AuditLog, EventBusPort,
NotifierPort, ClockPort, UseCasePackRepository, ROIExtractorPort,
GeometricVerifierPort, WeatherPort, RecallPort, FxPort, CalendarPort.

v2 implements the rest. They are declared here now so that adding them changes
no existing code -- which is the claim `tests/architecture/test_layering.py`
protects.
"""

from forge.application.ports.external import (
    AmbientConditions,
    CalendarPort,
    ExternalDataError,
    Freshness,
    FxPort,
    FxRate,
    Holiday,
    HolidayCalendar,
    RecallPort,
    RecallRecord,
    WeatherPort,
)
from forge.application.ports.llm import (
    Completion,
    GenerationRequest,
    LLMError,
    LLMPort,
    LLMRateLimitedError,
    LLMSchemaError,
    LLMTimeoutError,
    LLMUnavailableError,
    Message,
    ModelCapabilities,
)
from forge.application.ports.mes import (
    InspectionReading,
    JobCardContext,
    MESError,
    MESPort,
    MESUnavailableError,
    QualityInspectionRef,
    QualityInspectionWrite,
)
from forge.application.ports.platform import (
    AuditEntry,
    AuditLog,
    ClockPort,
    DefectClass,
    Event,
    EventBusPort,
    InspectionRepository,
    InspectionSummary,
    Notification,
    NotifierPort,
    PackManifest,
    UseCasePack,
    UseCasePackRepository,
    VerifierSpec,
)
from forge.application.ports.retrieval import (
    Document,
    KeywordSearchPort,
    RerankerPort,
    ScoredDocument,
    VectorStorePort,
)
from forge.application.ports.vision import (
    AnomalyMap,
    AnomalyScorerPort,
    ExemplarMatch,
    Frame,
    GeometricVerifierPort,
    RegionOfInterest,
    ROIExtractorPort,
)

__all__ = [
    "AmbientConditions",
    "AnomalyMap",
    "AnomalyScorerPort",
    "AuditEntry",
    "AuditLog",
    "CalendarPort",
    "ClockPort",
    "Completion",
    "DefectClass",
    "Document",
    "Event",
    "EventBusPort",
    "ExemplarMatch",
    "ExternalDataError",
    "Frame",
    "Freshness",
    "FxPort",
    "FxRate",
    "GenerationRequest",
    "GeometricVerifierPort",
    "Holiday",
    "HolidayCalendar",
    "InspectionReading",
    "InspectionRepository",
    "InspectionSummary",
    "JobCardContext",
    "KeywordSearchPort",
    "LLMError",
    "LLMPort",
    "LLMRateLimitedError",
    "LLMSchemaError",
    "LLMTimeoutError",
    "LLMUnavailableError",
    "MESError",
    "MESPort",
    "MESUnavailableError",
    "Message",
    "ModelCapabilities",
    "Notification",
    "NotifierPort",
    "PackManifest",
    "QualityInspectionRef",
    "QualityInspectionWrite",
    "ROIExtractorPort",
    "RecallPort",
    "RecallRecord",
    "RegionOfInterest",
    "RerankerPort",
    "ScoredDocument",
    "UseCasePack",
    "UseCasePackRepository",
    "VectorStorePort",
    "VerifierSpec",
    "WeatherPort",
]
