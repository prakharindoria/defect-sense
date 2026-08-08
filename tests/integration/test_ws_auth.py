"""`/ws/live` authentication and scoping.

Before this, the socket accepted every connection and streamed every inspection
to it — unit ids, verdicts, costs, narratives — while `config/rbac.yaml`
declared `stream:read` as a permission that gated nothing. Anyone who could
reach the port could watch the line.

The browser WebSocket API cannot set an `Authorization` header, so the token
arrives either in the query string or as the first frame. Both are tested here,
along with the refusals and the per-subscriber station scoping.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from apps.api import main as api_main
from apps.api.security import (
    WS_CLOSE_FORBIDDEN,
    WS_CLOSE_UNAUTHENTICATED,
    StationScope,
)

LIVE = "/ws/live"


def _closes_with(client: TestClient, url: str, *, send: dict | None = None) -> int:
    """Connect, optionally send one frame, and return the close code."""
    with pytest.raises(WebSocketDisconnect) as excinfo, client.websocket_connect(url) as ws:
        if send is not None:
            ws.send_json(send)
        ws.receive_json()
    return int(excinfo.value.code)


# ---------------------------------------------------------------------------
# Refusals — fail closed
# ---------------------------------------------------------------------------
def test_an_anonymous_socket_is_refused(client: TestClient) -> None:
    """The bug: this used to return a hello frame and then the whole feed."""
    assert _closes_with(client, LIVE, send={"type": "noise"}) == WS_CLOSE_UNAUTHENTICATED


def test_a_garbage_token_is_refused(client: TestClient) -> None:
    assert _closes_with(client, f"{LIVE}?token=not-a-jwt") == WS_CLOSE_UNAUTHENTICATED


def test_an_empty_token_parameter_is_refused(client: TestClient) -> None:
    assert _closes_with(
        client, f"{LIVE}?token=", send={"type": "auth", "token": ""}
    ) == WS_CLOSE_UNAUTHENTICATED


def test_a_first_frame_without_a_token_is_refused(client: TestClient) -> None:
    assert _closes_with(
        client, LIVE, send={"type": "subscribe"}
    ) == WS_CLOSE_UNAUTHENTICATED


def test_a_refresh_token_is_not_accepted_as_an_access_token(
    client: TestClient,
) -> None:
    """Token confusion would silently extend a session by a week."""
    client.post("/api/v1/auth/login", json={"username": "priya", "password": "forge2026"})
    refresh = client.cookies.get("forge_refresh")
    assert refresh, "login must set the refresh cookie"
    assert _closes_with(client, f"{LIVE}?token={refresh}") == WS_CLOSE_UNAUTHENTICATED


# ---------------------------------------------------------------------------
# Accepted handshakes
# ---------------------------------------------------------------------------
def test_query_parameter_token_is_accepted(client: TestClient, token_for) -> None:  # noqa: ANN001
    with client.websocket_connect(f"{LIVE}?token={token_for('priya')}") as ws:
        hello = ws.receive_json()
    assert hello["type"] == "hello"
    assert hello["data"]["authenticated"] is True
    assert hello["data"]["user"] == "priya"
    assert hello["data"]["role"] == "QA"
    assert hello["data"]["degraded"] is False


def test_first_frame_token_is_accepted(client: TestClient, token_for) -> None:  # noqa: ANN001
    with client.websocket_connect(LIVE) as ws:
        ws.send_json({"type": "auth", "token": token_for("ravi")})
        hello = ws.receive_json()
    assert hello["data"]["authenticated"] is True
    assert hello["data"]["user"] == "ravi"
    # Narrowed at connect time, not at send time.
    assert hello["data"]["scope"] == api_main.DEFAULT_STATION_ID


def test_shop_floor_is_scoped_to_its_station_on_the_socket(
    client: TestClient, token_for,  # noqa: ANN001
) -> None:
    with client.websocket_connect(f"{LIVE}?token={token_for('ravi')}") as ws:
        hello = ws.receive_json()
    assert hello["data"]["scope"] == api_main.DEFAULT_STATION_ID


def test_qa_sees_every_station_on_the_socket(client: TestClient, token_for) -> None:  # noqa: ANN001
    with client.websocket_connect(f"{LIVE}?token={token_for('priya')}") as ws:
        hello = ws.receive_json()
    assert hello["data"]["scope"] == "all_stations"


def test_a_connected_subscriber_receives_a_new_inspection(
    client: TestClient, token_for, headers_for,  # noqa: ANN001
) -> None:
    """End to end: the socket is authenticated AND still delivers."""
    with client.websocket_connect(f"{LIVE}?token={token_for('priya')}") as ws:
        assert ws.receive_json()["type"] == "hello"
        created = client.post(
            "/api/v1/inspections", headers=headers_for("priya"),
            json={"scenario": "clean", "seed": 7},
        )
        assert created.status_code == 200
        event = ws.receive_json()
    assert event["type"] == "inspection"
    assert event["data"]["correlation_id"] == created.json()["correlation_id"]


def test_the_anonymous_escape_hatch_is_opt_in_and_labelled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The flag exists for a client that predates WS auth. It must be visible."""
    monkeypatch.setenv("FORGE_WS_ALLOW_ANONYMOUS", "true")
    with client.websocket_connect(LIVE) as ws:
        ws.send_json({"type": "hello"})
        hello = ws.receive_json()
    assert hello["data"]["authenticated"] is False
    assert hello["data"]["degraded"] is True
    assert "without authentication" in hello["data"]["degradation_reason"]
    # Even then it gets the narrowest scope, not the plant-wide feed.
    assert hello["data"]["scope"] == api_main.DEFAULT_STATION_ID


# ---------------------------------------------------------------------------
# Broadcast scoping, exercised directly
# ---------------------------------------------------------------------------
async def test_broadcast_does_not_deliver_another_station_to_a_scoped_subscriber(
) -> None:
    floor = api_main._Subscriber(  # noqa: SLF001
        queue=asyncio.Queue(maxsize=8),
        scope=StationScope(all_stations=False, station="WA-01"),
        username="ravi", role="SHOP_FLOOR_WORKER",
    )
    qa = api_main._Subscriber(  # noqa: SLF001
        queue=asyncio.Queue(maxsize=8),
        scope=StationScope(all_stations=True),
        username="priya", role="QA",
    )
    api_main.state.subscribers.update({floor, qa})
    try:
        await api_main.state.broadcast({"type": "inspection", "n": 1}, station_id="WA-01")
        await api_main.state.broadcast({"type": "inspection", "n": 2}, station_id="WB-07")
        await api_main.state.broadcast({"type": "status"}, station_id=None)
    finally:
        api_main.state.subscribers.difference_update({floor, qa})

    assert [floor.queue.get_nowait() for _ in range(floor.queue.qsize())] == [
        {"type": "inspection", "n": 1}, {"type": "status"},
    ]
    assert qa.queue.qsize() == 3


async def test_a_full_queue_is_counted_rather_than_swallowed() -> None:
    """A dropped frame the client never hears about is a silent failure."""
    sub = api_main._Subscriber(  # noqa: SLF001
        queue=asyncio.Queue(maxsize=1),
        scope=StationScope(all_stations=True),
        username="slow", role="QA",
    )
    api_main.state.subscribers.add(sub)
    try:
        for _ in range(4):
            await api_main.state.broadcast({"type": "inspection"}, station_id="WA-01")
    finally:
        api_main.state.subscribers.discard(sub)
    assert sub.dropped == 3


def test_forbidden_close_code_is_distinct_from_unauthenticated() -> None:
    """The UI must be able to tell 'sign in again' from 'you may not subscribe'."""
    assert WS_CLOSE_UNAUTHENTICATED != WS_CLOSE_FORBIDDEN
