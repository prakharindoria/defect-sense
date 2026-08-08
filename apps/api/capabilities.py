"""Cached model-capability probe for the Admin page.

The probe itself is genuinely expensive and that is deliberate: it opens a real
connection to every configured provider and measures what the endpoint can do
rather than trusting a config file. `config/models.yaml` gives it a 45s budget
because a cold local model has to load from disk before it can answer.

Running that on the request path made `GET /api/v1/admin/system` take 34.4s
(measured on this machine, 2026-08-08) — the Admin page was unusable and any
sane HTTP client timed out first. `LLMService.probe()` re-probes every provider
on every call, so nothing upstream absorbed it.

This module puts the probe *beside* the request instead of inside it:

- One probe runs in the background (kicked off at boot, and again when the
  cached result ages out). Never more than one at a time.
- The endpoint reads whatever has been measured so far and returns immediately.
- **It never invents a capability.** If no probe has finished, `capabilities`
  is empty and `status` is `probing` with a human-readable reason. A stale
  result is served labelled `stale`, with its age, never as a fresh measurement.

That is CLAUDE.md rule 4 (no silent failure) applied to latency: the page is
honest about not knowing yet rather than blocking until it does.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from forge.application.ports.llm import ModelCapabilities

_log = logging.getLogger(__name__)

# How long a measurement is presented as current. Longer than the probe takes,
# far shorter than a demo session, so the matrix on screen is never mysterious.
DEFAULT_TTL_SECONDS = 900


@dataclass(frozen=True, slots=True)
class ProbeSnapshot:
    """What is known about provider capabilities *right now*.

    `status` is the load-bearing field. The UI must render `probing` and
    `failed` differently from `ready`, because in those states the capability
    list is empty or old — presenting it as a live matrix would be a lie.
    """

    status: str                                   # ready|stale|probing|failed|never_run
    detail: str
    capabilities: tuple[ModelCapabilities, ...]
    measured_at: datetime | None
    age_seconds: int | None
    duration_ms: int | None
    error: str | None
    ttl_seconds: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "detail": self.detail,
            "measured_at": self.measured_at.isoformat() if self.measured_at else None,
            "age_seconds": self.age_seconds,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "ttl_seconds": self.ttl_seconds,
        }


class CapabilityProbeCache:
    """Runs the capability probe off the request path and caches the result."""

    def __init__(self, *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._caps: tuple[ModelCapabilities, ...] = ()
        self._measured_at: float | None = None
        self._measured_wall: datetime | None = None
        self._duration_ms: int | None = None
        self._error: str | None = None
        self._running = False
        self._task: asyncio.Task[None] | None = None

    # -- state -------------------------------------------------------------
    @property
    def is_running(self) -> bool:
        return self._running

    def _age(self) -> float | None:
        return None if self._measured_at is None else time.monotonic() - self._measured_at

    def snapshot(self) -> ProbeSnapshot:
        age = self._age()
        if age is None:
            status = "probing" if self._running else ("failed" if self._error else "never_run")
            detail = {
                "probing": (
                    "A capability probe is running now. No endpoint has answered yet, so "
                    "no capabilities are reported — this list is empty because it is "
                    "unmeasured, not because the providers lack features."
                ),
                "failed": "The last capability probe failed before measuring anything.",
                "never_run": (
                    "No capability probe has run in this process yet."
                ),
            }[status]
            return ProbeSnapshot(
                status=status, detail=detail, capabilities=(), measured_at=None,
                age_seconds=None, duration_ms=None, error=self._error, ttl_seconds=self._ttl,
            )

        fresh = age <= self._ttl
        if self._error and not self._running:
            status = "failed"
            detail = (
                f"The most recent probe failed ({self._error}). Showing the last "
                f"successful measurement, taken {int(age)}s ago."
            )
        elif fresh:
            status = "ready"
            detail = f"Measured {int(age)}s ago against every configured provider."
        else:
            status = "stale"
            detail = (
                f"This measurement is {int(age)}s old (older than the {self._ttl}s "
                f"freshness window)."
                + (" A refresh is running now." if self._running else " A refresh is queued.")
            )
        return ProbeSnapshot(
            status=status, detail=detail, capabilities=self._caps,
            measured_at=self._measured_wall, age_seconds=int(age),
            duration_ms=self._duration_ms, error=self._error, ttl_seconds=self._ttl,
        )

    # -- running -----------------------------------------------------------
    def needs_refresh(self) -> bool:
        age = self._age()
        return not self._running and (age is None or age > self._ttl)

    def ensure_running(
        self, probe: Callable[[], Awaitable[tuple[ModelCapabilities, ...]]]
    ) -> bool:
        """Start a probe in the background if one is due. Never blocks.

        Returns whether a probe was started, so a caller can log the decision
        instead of guessing at it.
        """
        if not self.needs_refresh():
            return False
        self._running = True
        self._task = asyncio.create_task(self._run(probe))
        return True

    async def refresh(
        self, probe: Callable[[], Awaitable[tuple[ModelCapabilities, ...]]]
    ) -> ProbeSnapshot:
        """Run a probe and wait for it. Used at boot and by tests, never by a route."""
        if self._running and self._task is not None:
            await asyncio.shield(self._task)
            return self.snapshot()
        self._running = True
        await self._run(probe)
        return self.snapshot()

    async def _run(
        self, probe: Callable[[], Awaitable[tuple[ModelCapabilities, ...]]]
    ) -> None:
        started = time.monotonic()
        try:
            caps = await probe()
        except Exception as exc:  # noqa: BLE001 - a probe failure must not kill the app
            # Recorded, surfaced on /admin, and it deliberately does NOT clear the
            # previous measurement: last-known-good labelled `failed` is worth
            # more than an empty table that says nothing at all.
            self._error = f"{type(exc).__name__}: {exc}"[:300]
            _log.warning("capability probe failed: %s", self._error)
        else:
            self._caps = tuple(caps)
            self._measured_at = time.monotonic()
            self._measured_wall = datetime.now(UTC)
            self._duration_ms = int((time.monotonic() - started) * 1000)
            self._error = None
            _log.info(
                "capability probe complete: %d providers in %dms",
                len(self._caps), self._duration_ms,
            )
        finally:
            self._running = False


def capability_row(caps: ModelCapabilities) -> dict[str, Any]:
    """Serialise one measured capability row for the Admin page."""
    return {
        "provider": caps.provider,
        "model": caps.model,
        "reachable": caps.reachable,
        "supports_vision": caps.supports_vision,
        "supports_json_mode": caps.supports_json_mode,
        "supports_streaming": caps.supports_streaming,
        "supports_function_calling": caps.supports_function_calling,
        "max_context": caps.max_context,
        "measured_p50_latency_ms": caps.measured_p50_latency_ms,
        "error": caps.error,
    }
