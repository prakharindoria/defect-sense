"""Per-role request rate limiting.

`config/rbac.yaml` has declared `rate_limit_per_min` for every role since v1 and
nothing read it, so the number on the Admin page was a claim rather than a
control. This middleware makes it real: the limit comes from the same matrix
that grants permissions, so raising a role's ceiling is a config change and no
endpoint has to be touched.

Design notes worth defending:

- **Keyed on the authenticated subject, not the IP.** Three operators behind one
  plant NAT must not share a bucket. Unauthenticated traffic has no subject, so
  the login and refresh routes fall back to an IP bucket — that path is a
  credential-stuffing surface and needs *some* ceiling.
- **The role comes from the user store, never from the token claim.** A token is
  signed, but taking the role from its payload would make the limit part of the
  attacker's input. `user_from_token` resolves the subject and reads the role
  from the store; this only asks it for the answer.
- **Rejection does not consume budget.** A denied request is not recorded, so a
  client that backs off recovers exactly one window later instead of being held
  down by its own retries.
- **429 is RFC 7807.** `application/problem+json` with `Retry-After`, plus the
  `X-RateLimit-*` headers a client needs to self-throttle before being told to.

Sliding window over a deque of timestamps: exact within the window, unlike a
fixed bucket which lets 2x the limit through across a boundary. The state is
per-process, which is honest for a single-worker deployment and is stated in
`docs/RUNBOOK.md` terms: two API workers would each enforce their own half.
"""

from __future__ import annotations

import logging
import math
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from forge.infrastructure.auth import AuthError, load_rbac, user_from_token

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.requests import Request
    from starlette.responses import Response

_log = logging.getLogger(__name__)

WINDOW_SECONDS = 60.0

# Ops and discovery endpoints. A liveness probe that trips the rate limiter
# reports the app as down, which is the opposite of what it is for.
EXEMPT_PATHS = frozenset({
    "/health", "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect",
})

# Unauthenticated routes that still need a ceiling, because they are where
# credentials are guessed.
ANON_LIMITED_PREFIXES = ("/api/v1/auth/login", "/api/v1/auth/refresh")

PROBLEM_TYPE = "https://forge.local/problems/rate-limit-exceeded"


def _env_flag(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _anon_limit() -> int:
    try:
        return max(1, int(os.environ.get("FORGE_ANON_RATE_LIMIT_PER_MIN", "60")))
    except ValueError:
        return 60


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int          # seconds until the window has room again
    reset_in: int             # seconds until the window is empty
    subject: str
    role: str


class SlidingWindowLimiter:
    """Fixed-length sliding window, one deque of timestamps per subject."""

    def __init__(self, *, window_seconds: float = WINDOW_SECONDS) -> None:
        self._window = window_seconds
        self._hits: defaultdict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, limit: int, *, role: str = "") -> Decision:
        now = time.monotonic()
        hits = self._hits[key]
        cutoff = now - self._window
        while hits and hits[0] <= cutoff:
            hits.popleft()

        if len(hits) >= limit:
            oldest = hits[0]
            retry_after = max(1, math.ceil(self._window - (now - oldest)))
            return Decision(
                allowed=False, limit=limit, remaining=0, retry_after=retry_after,
                reset_in=max(1, math.ceil(self._window - (now - hits[0]))),
                subject=key, role=role,
            )

        hits.append(now)
        reset_in = max(1, math.ceil(self._window - (now - hits[0])))
        return Decision(
            allowed=True, limit=limit, remaining=limit - len(hits), retry_after=0,
            reset_in=reset_in, subject=key, role=role,
        )

    def prune(self) -> None:
        """Drop subjects with no traffic in the window, so the map cannot grow."""
        cutoff = time.monotonic() - self._window
        for key in [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]:
            del self._hits[key]

    def reset(self) -> None:
        self._hits.clear()

    @property
    def tracked_subjects(self) -> int:
        return len(self._hits)


# One limiter per process. Exposed so tests (and an admin endpoint, later) can
# inspect or reset it without reaching into the middleware instance.
LIMITER = SlidingWindowLimiter()

_PRUNE_EVERY = 500


class RoleRateLimitMiddleware(BaseHTTPMiddleware):
    """Enforces `roles.<ROLE>.rate_limit_per_min` from `config/rbac.yaml`."""

    def __init__(self, app, *, limiter: SlidingWindowLimiter | None = None) -> None:  # noqa: ANN001
        super().__init__(app)
        self._limiter = limiter or LIMITER
        self._seen = 0

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not _env_flag("FORGE_RATE_LIMIT_ENABLED", default=True):
            return await call_next(request)

        decision = self._decide(request)
        if decision is None:
            return await call_next(request)

        self._seen += 1
        if self._seen % _PRUNE_EVERY == 0:
            self._limiter.prune()

        if not decision.allowed:
            _log.warning(
                "rate_limit.exceeded",
                extra={
                    "subject": decision.subject, "role": decision.role,
                    "limit_per_min": decision.limit, "path": request.url.path,
                },
            )
            return _problem(decision, request.url.path)

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(decision.limit)
        response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
        response.headers["X-RateLimit-Reset"] = str(decision.reset_in)
        return response

    def _decide(self, request: Request) -> Decision | None:
        """Resolve the bucket for this request, or None if it is not limited."""
        path = request.url.path
        if path in EXEMPT_PATHS:
            return None

        header = request.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            try:
                user = user_from_token(header.removeprefix("Bearer ").strip())
            except AuthError:
                # An invalid token is not an identity. Let the route's own
                # dependency return the 401; bucketing it by IP here would let
                # one bad client's retries lock out a whole NAT.
                return None
            try:
                limit = load_rbac().config(user.role).rate_limit_per_min
            except Exception:  # noqa: BLE001 - an unmapped role must not 500 the request
                limit = _anon_limit()
            return self._limiter.check(
                f"user:{user.username}", limit, role=user.role.value
            )

        if path.startswith(ANON_LIMITED_PREFIXES):
            client = request.client.host if request.client else "unknown"
            return self._limiter.check(f"ip:{client}", _anon_limit(), role="anonymous")

        return None


def _problem(decision: Decision, path: str) -> JSONResponse:
    """RFC 7807 problem response. Never leaks who else is in the bucket."""
    who = (
        f"Role {decision.role}" if decision.role != "anonymous"
        else "Unauthenticated traffic from this address"
    )
    return JSONResponse(
        status_code=429,
        media_type="application/problem+json",
        headers={
            "Retry-After": str(decision.retry_after),
            "X-RateLimit-Limit": str(decision.limit),
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": str(decision.reset_in),
        },
        content={
            "type": PROBLEM_TYPE,
            "title": "Rate limit exceeded",
            "status": 429,
            "detail": (
                f"{who} is limited to {decision.limit} requests per minute "
                f"(config/rbac.yaml). Retry in {decision.retry_after}s."
            ),
            "instance": path,
            "limit_per_min": decision.limit,
            "role": decision.role,
            "retry_after_seconds": decision.retry_after,
        },
    )
