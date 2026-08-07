"""Three-state circuit breaker.

Guards every outbound dependency: LLM providers, the MES, Slack, and each live
public API. One implementation, used everywhere, so breaker behaviour is
uniform and there is a single place to reason about it.

    CLOSED    ──failures >= threshold──>  OPEN
    OPEN      ──open_duration elapsed──>  HALF_OPEN
    HALF_OPEN ──successes >= threshold──> CLOSED
              ──any failure────────────>  OPEN   (immediately, no second chance)

Half-open admits a strictly limited number of trial calls. Letting the full load
through the moment the timer expires is how a recovering dependency gets knocked
straight back over.

The breaker exists to be *demonstrated*, not merely present: `/integrations`
shows live state per dependency and the fault-injection panel forces one open on
stage. Which is why `state()` and `snapshot()` are part of the interface rather
than debug helpers.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from forge.domain.enums import BreakerState


class CircuitOpenError(Exception):
    """The breaker is open; the call was not attempted.

    Distinct from a dependency failure on purpose. "We did not call it" and "we
    called it and it failed" need different handling and different UI copy.
    """

    def __init__(self, name: str, retry_after_seconds: float) -> None:
        super().__init__(
            f"circuit '{name}' is open; retry in {retry_after_seconds:.1f}s"
        )
        self.name = name
        self.retry_after_seconds = retry_after_seconds


@dataclass(slots=True)
class BreakerSnapshot:
    """What `/integrations` and `/health` render."""

    name: str
    state: BreakerState
    consecutive_failures: int
    consecutive_successes: int
    total_calls: int
    total_failures: int
    opened_at: float | None
    retry_after_seconds: float
    last_error: str = ""

    @property
    def failure_rate(self) -> float:
        return self.total_failures / self.total_calls if self.total_calls else 0.0


@dataclass(slots=True)
class CircuitBreaker:
    name: str
    failure_threshold: int = 5
    success_threshold: int = 2
    open_duration_seconds: float = 30.0
    half_open_max_calls: int = 1

    _state: BreakerState = BreakerState.CLOSED
    _consecutive_failures: int = 0
    _consecutive_successes: int = 0
    _half_open_in_flight: int = 0
    _opened_at: float | None = None
    _total_calls: int = 0
    _total_failures: int = 0
    _last_error: str = ""
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Injectable so tests do not sleep and DEMO_MODE can pin time.
    _now: object = time.monotonic

    def _clock(self) -> float:
        return float(self._now())  # type: ignore[operator]

    def state(self) -> BreakerState:
        """Current state, accounting for an elapsed open window."""
        if (
            self._state is BreakerState.OPEN
            and self._opened_at is not None
            and self._clock() - self._opened_at >= self.open_duration_seconds
        ):
            return BreakerState.HALF_OPEN
        return self._state

    @property
    def retry_after_seconds(self) -> float:
        if self._state is not BreakerState.OPEN or self._opened_at is None:
            return 0.0
        return max(self.open_duration_seconds - (self._clock() - self._opened_at), 0.0)

    async def before_call(self) -> None:
        """Raise CircuitOpenError if the call must not be attempted."""
        async with self._lock:
            current = self.state()

            if current is BreakerState.OPEN:
                raise CircuitOpenError(self.name, self.retry_after_seconds)

            if current is BreakerState.HALF_OPEN:
                if self._state is not BreakerState.HALF_OPEN:
                    # First transition into half-open: reset the trial counters.
                    self._state = BreakerState.HALF_OPEN
                    self._half_open_in_flight = 0
                    self._consecutive_successes = 0
                if self._half_open_in_flight >= self.half_open_max_calls:
                    raise CircuitOpenError(self.name, self.retry_after_seconds)
                self._half_open_in_flight += 1

            self._total_calls += 1

    async def on_success(self) -> None:
        async with self._lock:
            self._consecutive_failures = 0
            self._last_error = ""
            if self._state is BreakerState.HALF_OPEN:
                self._half_open_in_flight = max(self._half_open_in_flight - 1, 0)
                self._consecutive_successes += 1
                if self._consecutive_successes >= self.success_threshold:
                    self._state = BreakerState.CLOSED
                    self._opened_at = None
                    self._consecutive_successes = 0

    async def on_failure(self, error: str = "") -> None:
        async with self._lock:
            self._total_failures += 1
            self._last_error = error[:200]
            self._consecutive_successes = 0

            if self._state is BreakerState.HALF_OPEN:
                # A probe failed. Back to open immediately -- a dependency that
                # fails its trial does not get a second trial in the same window.
                self._half_open_in_flight = 0
                self._state = BreakerState.OPEN
                self._opened_at = self._clock()
                return

            self._consecutive_failures += 1
            if self._consecutive_failures >= self.failure_threshold:
                self._state = BreakerState.OPEN
                self._opened_at = self._clock()

    async def force_open(self) -> None:
        """Fault injection. Guarded by admin:fault_injection."""
        async with self._lock:
            self._state = BreakerState.OPEN
            self._opened_at = self._clock()
            self._last_error = "forced open via fault injection"

    async def reset(self) -> None:
        async with self._lock:
            self._state = BreakerState.CLOSED
            self._consecutive_failures = 0
            self._consecutive_successes = 0
            self._half_open_in_flight = 0
            self._opened_at = None
            self._last_error = ""

    def snapshot(self) -> BreakerSnapshot:
        return BreakerSnapshot(
            name=self.name,
            state=self.state(),
            consecutive_failures=self._consecutive_failures,
            consecutive_successes=self._consecutive_successes,
            total_calls=self._total_calls,
            total_failures=self._total_failures,
            opened_at=self._opened_at,
            retry_after_seconds=self.retry_after_seconds,
            last_error=self._last_error,
        )


class BreakerRegistry:
    """All breakers in the process, so /health can render them together."""

    def __init__(self) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}

    def get(
        self,
        name: str,
        *,
        failure_threshold: int = 5,
        success_threshold: int = 2,
        open_duration_seconds: float = 30.0,
        half_open_max_calls: int = 1,
    ) -> CircuitBreaker:
        if name not in self._breakers:
            self._breakers[name] = CircuitBreaker(
                name=name,
                failure_threshold=failure_threshold,
                success_threshold=success_threshold,
                open_duration_seconds=open_duration_seconds,
                half_open_max_calls=half_open_max_calls,
            )
        return self._breakers[name]

    def snapshots(self) -> tuple[BreakerSnapshot, ...]:
        return tuple(b.snapshot() for b in self._breakers.values())


REGISTRY = BreakerRegistry()
