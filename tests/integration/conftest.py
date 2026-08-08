"""Shared fixtures for the API integration tests.

Two deliberate choices:

- **The app is driven in-process with `TestClient`, not over the network.** The
  suite must pass on a machine with nothing running, and `make demo` already
  covers the "is the server up" question.
- **The lifespan is NOT run.** `TestClient(app)` without a `with` block skips
  startup, so the tests never touch `.forge/store/` — the storage a developer's
  running instance is using. Storage is injected explicitly instead, which also
  means every test starts from an empty repository.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from apps.api import main as api_main
from apps.api.ratelimit import LIMITER
from forge.application.ports.llm import Completion, GenerationRequest, ModelCapabilities
from forge.infrastructure.llm.fake import FakeAdapter
from forge.infrastructure.persistence import Storage
from forge.infrastructure.persistence.memory import (
    InMemoryAuditLog,
    InMemoryDocumentStore,
    InMemoryInspectionRepository,
)

DEMO_PASSWORD = "forge2026"  # noqa: S105 - documented demo credential, synthetic data only
ACCOUNTS = {"ravi": "SHOP_FLOOR_WORKER", "priya": "QA", "sam": "ADMIN"}

FAKE_CAPABILITIES = (
    ModelCapabilities(
        provider="fake", model="fake-fast-v1", reachable=True,
        supports_json_mode=True, supports_streaming=True, measured_p50_latency_ms=5,
    ),
)


class StubLLM:
    """Offline stand-in for `LLMService`, with the same surface the API uses.

    The suite must never open a socket: `/api/v1/admin/system` schedules a
    capability probe and `/api/v1/assistant/chat` generates, and both would
    otherwise hit the real provider chain. `FakeAdapter` does the generating so
    the response is still schema-valid and deterministic rather than a mock
    returning a fixed string.
    """

    def __init__(self) -> None:
        self._fake = FakeAdapter()
        self.probe_calls = 0

    async def probe(self) -> tuple[ModelCapabilities, ...]:
        self.probe_calls += 1
        return FAKE_CAPABILITIES

    async def generate(self, request: GenerationRequest) -> Completion:
        return await self._fake.generate(request)

    def stats(self) -> dict[str, Any]:
        return {
            "demo_mode": True,
            "cache": {"entries": 0, "hits": 0, "misses": 0, "hit_rate": 0.0},
            "breakers": [],
            "skipped_providers": [],
            "tiers": {},
        }


@pytest.fixture
def storage() -> Storage:
    return Storage(
        inspections=InMemoryInspectionRepository(),
        audit=InMemoryAuditLog(),
        documents=InMemoryDocumentStore(),
        mode="ephemeral",
        detail="test fixture",
        durable=False,
        degraded=True,
    )


@pytest.fixture
def llm() -> StubLLM:
    return StubLLM()


@pytest.fixture
def client(  # noqa: ANN201
    storage: Storage, llm: StubLLM, monkeypatch: pytest.MonkeyPatch
):
    """A client on a clean app: empty storage, empty caches, empty rate limiter."""
    monkeypatch.setenv("JWT_SECRET", "test-secret-for-integration-tests")
    monkeypatch.delenv("FORGE_WS_ALLOW_ANONYMOUS", raising=False)
    monkeypatch.setattr(api_main, "_llm_service", lambda: llm)
    # A fresh probe cache per test: the module-level one would otherwise carry a
    # background task and a measurement across test boundaries.
    monkeypatch.setattr(api_main, "PROBE_CACHE", api_main.CapabilityProbeCache())

    api_main.state.storage = storage
    api_main.state.states.clear()
    api_main.state.subscribers.clear()
    api_main._VIEWS.clear()  # noqa: SLF001 - a cache the tests must not inherit
    LIMITER.reset()

    # Constructed WITHOUT `with`: entering the context manager runs the lifespan,
    # which would open the real storage and overwrite the fixture above.
    test_client = TestClient(api_main.app)
    yield test_client
    test_client.close()
    api_main.state.storage = None
    LIMITER.reset()


@pytest.fixture
def token_for(client: TestClient):  # noqa: ANN201
    """Log a demo account in and return its access token."""

    def _token(username: str) -> str:
        response = client.post(
            "/api/v1/auth/login", json={"username": username, "password": DEMO_PASSWORD}
        )
        assert response.status_code == 200, response.text
        return str(response.json()["access_token"])

    return _token


@pytest.fixture
def headers_for(token_for):  # noqa: ANN001, ANN201
    """Authorization headers for a demo account."""

    def _headers(username: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token_for(username)}"}

    return _headers
