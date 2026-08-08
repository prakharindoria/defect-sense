"""The per-role rate limit declared in `config/rbac.yaml`, enforced.

`rate_limit_per_min` sat in the matrix and on the Admin page while nothing read
it. These tests hold the middleware to the number in the config file rather than
to a constant copied into the test, so raising a role's ceiling in YAML is the
only change needed to move the limit.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.ratelimit import LIMITER, PROBLEM_TYPE, SlidingWindowLimiter
from forge.domain.enums import Role
from forge.infrastructure.auth import load_rbac


def limit_for(role: Role) -> int:
    return load_rbac().config(role).rate_limit_per_min


# ---------------------------------------------------------------------------
# The window itself
# ---------------------------------------------------------------------------
def test_window_allows_exactly_the_limit_then_denies() -> None:
    limiter = SlidingWindowLimiter()
    decisions = [limiter.check("user:x", 3) for _ in range(4)]
    assert [d.allowed for d in decisions] == [True, True, True, False]
    assert [d.remaining for d in decisions[:3]] == [2, 1, 0]


def test_a_denied_request_does_not_consume_budget() -> None:
    """Otherwise a client's own retries hold it down past the window."""
    limiter = SlidingWindowLimiter()
    for _ in range(2):
        limiter.check("user:x", 2)
    for _ in range(5):
        assert not limiter.check("user:x", 2).allowed
    # Two hits recorded, not seven.
    assert limiter._hits["user:x"].__len__() == 2  # noqa: SLF001


def test_subjects_have_separate_buckets() -> None:
    """Three operators behind one NAT must not share a ceiling."""
    limiter = SlidingWindowLimiter()
    assert limiter.check("user:ravi", 1).allowed
    assert not limiter.check("user:ravi", 1).allowed
    assert limiter.check("user:priya", 1).allowed


def test_retry_after_is_bounded_by_the_window() -> None:
    limiter = SlidingWindowLimiter()
    limiter.check("user:x", 1)
    denied = limiter.check("user:x", 1)
    assert 1 <= denied.retry_after <= 60


def test_prune_drops_idle_subjects() -> None:
    limiter = SlidingWindowLimiter(window_seconds=0.0)
    limiter.check("user:x", 10)
    limiter.prune()
    assert limiter.tracked_subjects == 0


# ---------------------------------------------------------------------------
# Through the real API
# ---------------------------------------------------------------------------
def test_shop_floor_is_throttled_at_its_declared_limit(
    client: TestClient, headers_for,  # noqa: ANN001
) -> None:
    limit = limit_for(Role.SHOP_FLOOR_WORKER)
    headers = headers_for("ravi")          # the login itself is an anonymous call

    statuses = [
        client.get("/api/v1/metrics", headers=headers).status_code
        for _ in range(limit + 2)
    ]
    assert statuses.count(200) == limit, f"expected exactly {limit} to pass"
    assert statuses[-1] == 429


def test_the_429_is_a_problem_document_with_retry_after(
    client: TestClient, headers_for,  # noqa: ANN001
) -> None:
    headers = headers_for("ravi")
    limit = limit_for(Role.SHOP_FLOOR_WORKER)
    for _ in range(limit):
        client.get("/api/v1/metrics", headers=headers)

    response = client.get("/api/v1/metrics", headers=headers)
    assert response.status_code == 429
    assert response.headers["content-type"].startswith("application/problem+json")
    assert int(response.headers["Retry-After"]) >= 1
    assert response.headers["X-RateLimit-Limit"] == str(limit)
    assert response.headers["X-RateLimit-Remaining"] == "0"

    problem = response.json()
    assert problem["type"] == PROBLEM_TYPE
    assert problem["status"] == 429
    assert problem["limit_per_min"] == limit
    assert problem["role"] == Role.SHOP_FLOOR_WORKER.value
    assert problem["instance"] == "/api/v1/metrics"
    # The detail names the source of the number, not a magic constant.
    assert "config/rbac.yaml" in problem["detail"]


def test_one_role_being_throttled_does_not_throttle_another(
    client: TestClient, headers_for,  # noqa: ANN001
) -> None:
    floor = headers_for("ravi")
    for _ in range(limit_for(Role.SHOP_FLOOR_WORKER) + 1):
        client.get("/api/v1/metrics", headers=floor)
    assert client.get("/api/v1/metrics", headers=floor).status_code == 429

    # QA's ceiling is four times higher and its bucket is its own.
    assert client.get("/api/v1/metrics", headers=headers_for("priya")).status_code == 200


def test_successful_responses_carry_the_remaining_budget(
    client: TestClient, headers_for,  # noqa: ANN001
) -> None:
    response = client.get("/api/v1/metrics", headers=headers_for("ravi"))
    assert response.status_code == 200
    assert int(response.headers["X-RateLimit-Limit"]) == limit_for(Role.SHOP_FLOOR_WORKER)
    assert int(response.headers["X-RateLimit-Remaining"]) >= 0


def test_health_is_never_throttled(client: TestClient) -> None:
    """A liveness probe that trips the limiter reports the app as down."""
    LIMITER.reset()
    for _ in range(200):
        assert client.get("/health").status_code == 200


def test_unauthenticated_login_attempts_are_capped_per_address(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Credential stuffing has no role to bill, so it is billed to the address."""
    monkeypatch.setenv("FORGE_ANON_RATE_LIMIT_PER_MIN", "5")
    LIMITER.reset()
    codes = [
        client.post(
            "/api/v1/auth/login", json={"username": "ravi", "password": "wrong"}
        ).status_code
        for _ in range(7)
    ]
    assert codes[:5] == [401] * 5
    assert codes[-1] == 429


def test_an_invalid_token_is_not_bucketed_by_address(client: TestClient) -> None:
    """It must return 401 from the route, not 429 from a shared IP bucket."""
    LIMITER.reset()
    for _ in range(30):
        response = client.get(
            "/api/v1/metrics", headers={"Authorization": "Bearer not-a-token"}
        )
        assert response.status_code == 401


def test_the_limiter_can_be_disabled_for_a_load_test(
    client: TestClient, headers_for, monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
) -> None:
    """A flag, off by default, so a measured throughput run is still possible."""
    headers = headers_for("ravi")
    monkeypatch.setenv("FORGE_RATE_LIMIT_ENABLED", "false")
    for _ in range(limit_for(Role.SHOP_FLOOR_WORKER) + 5):
        assert client.get("/api/v1/metrics", headers=headers).status_code == 200
