"""FastAPI auth dependencies.

Routes declare the permission they need; `config/rbac.yaml` decides who holds
it. A route never names a role, so adding or re-scoping a role is a config
change and no endpoint has to be re-audited.

WebSockets get the same treatment through `authenticate_websocket()`. They need
their own path because the browser WebSocket API cannot set an `Authorization`
header — see that function's docstring for the two accepted handshakes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request, WebSocket, status

from forge.infrastructure.auth import (
    AuthError,
    PermissionDeniedError,
    RbacMatrix,
    User,
    load_rbac,
    user_from_token,
)

REFRESH_COOKIE = "forge_refresh"

_log = logging.getLogger(__name__)

# WebSocket close codes. 4000-4999 is the application-defined range, and the
# browser surfaces them verbatim, so the client can tell "you never sent a
# token" apart from "your role may not subscribe" without parsing prose.
WS_CLOSE_UNAUTHENTICATED = 4401
WS_CLOSE_FORBIDDEN = 4403
WS_CLOSE_AUTH_TIMEOUT = 4408

# How long we wait for the first-message token before closing. Short on
# purpose: an unauthenticated peer holds a socket for at most this long.
WS_AUTH_TIMEOUT_SECONDS = 5.0


def rbac() -> RbacMatrix:
    return load_rbac()


def current_user(request: Request) -> User:
    """Resolve the caller from the Bearer token. 401 if absent or invalid."""
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return user_from_token(header.removeprefix("Bearer ").strip())
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


CurrentUser = Annotated[User, Depends(current_user)]
RefreshCookie = Annotated[str | None, Cookie(alias=REFRESH_COOKIE)]


def require(permission: str):  # noqa: ANN201 - returns a FastAPI dependency
    """Dependency factory enforcing one permission from the matrix.

    403 (not 404) on denial: the resource exists, the caller may not act on it.
    Hiding that distinction would make the API harder to reason about without
    making it meaningfully safer, since the caller is already authenticated.
    """

    def dependency(user: CurrentUser) -> User:
        try:
            load_rbac().require(user.role, permission)
        except PermissionDeniedError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Your role ({user.role.value}) does not permit '{permission}'."
                ),
            ) from exc
        return user

    return dependency


def require_any(*permissions: str):  # noqa: ANN201 - returns a FastAPI dependency
    """Allow the call if the caller holds ANY of these permissions.

    Needed because roles reach the same endpoint through different grants: a
    shop-floor worker reads inspections via `inspection:read_own_station` while
    QA reads them via the broader `inspection:read`. The alternative -- granting
    every role the narrowest permission as well -- would make the matrix lie
    about what each role is actually scoped to.

    Scope narrowing (which records a given role may see) is the handler's job,
    not the gate's.
    """

    def dependency(user: CurrentUser) -> User:
        matrix = load_rbac()
        if any(matrix.allows(user.role, p) for p in permissions):
            return user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Your role ({user.role.value}) does not permit any of "
                f"{list(permissions)}."
            ),
        )

    return dependency


# ---------------------------------------------------------------------------
# Scope narrowing
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class StationScope:
    """Which stations' records a caller may see.

    `require_any("inspection:read", "inspection:read_own_station")` lets two
    very different grants through the same door, and the gate cannot tell them
    apart afterwards — that is what this is for. It was previously left as a
    comment saying scope narrowing is the handler's job, and no handler did it,
    so a shop-floor token read every station's inspections.
    """

    all_stations: bool
    station: str | None = None

    def permits(self, station_id: str | None) -> bool:
        if self.all_stations:
            return True
        # Fail closed: a record with no station cannot be proven to be yours.
        return station_id is not None and station_id == self.station

    def describe(self) -> str:
        return "all_stations" if self.all_stations else (self.station or "none")


def station_scope(user: User, default_station: str) -> StationScope:
    """Resolve what this caller is scoped to, from the matrix alone.

    `inspection:read` is plant-wide. `inspection:read_own_station` is not, and
    the difference has to be enforced somewhere or the narrower permission is
    decoration.

    The demo runs one station, so "own station" resolves to the station the
    line is configured for. A multi-station deployment would carry the
    assignment on the user record; this is the single place that would change.
    """
    matrix = load_rbac()
    if matrix.allows(user.role, "inspection:read"):
        return StationScope(all_stations=True)
    if matrix.allows(user.role, "inspection:read_own_station"):
        return StationScope(all_stations=False, station=default_station)
    # Neither grant: the route gate should already have refused. Deny anyway.
    return StationScope(all_stations=False, station=None)


# ---------------------------------------------------------------------------
# WebSocket authentication
# ---------------------------------------------------------------------------
def ws_anonymous_allowed() -> bool:
    """Whether an unauthenticated socket may subscribe.

    Default **false** — CLAUDE.md rule 6, authorization denies by default. The
    flag exists only as a transition switch while a client that predates
    WebSocket auth is still deployed, and every anonymous connection it permits
    is logged as a warning and labelled in the hello frame, so it is never a
    silent hole (rule 4).
    """
    raw = os.environ.get("FORGE_WS_ALLOW_ANONYMOUS", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


async def _first_message_token(websocket: WebSocket) -> str | None:
    """Read `{"type":"auth","token":"..."}` off the socket, with a timeout.

    Preferred over the query parameter because a URL travels through access
    logs, proxy logs and browser history; a frame body does not.
    """
    try:
        raw = await asyncio.wait_for(
            websocket.receive_text(), timeout=WS_AUTH_TIMEOUT_SECONDS
        )
    except TimeoutError:
        return None
    except Exception:  # noqa: BLE001 - peer vanished mid-handshake
        return None
    try:
        message = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(message, dict) or message.get("type") != "auth":
        return None
    token = message.get("token")
    return token.strip() if isinstance(token, str) and token.strip() else None


@dataclass(frozen=True, slots=True)
class WsIdentity:
    """Who is on the other end of an accepted socket.

    `user is None` is only reachable with `FORGE_WS_ALLOW_ANONYMOUS` set, and
    `degraded` says so, so the server can stamp the hello frame and the UI can
    show that the stream is running without authentication rather than assuming
    it is secured.
    """

    user: User | None
    degraded: bool = False
    reason: str = ""

    @property
    def username(self) -> str:
        return self.user.username if self.user else "anonymous"

    @property
    def role_value(self) -> str:
        return self.user.role.value if self.user else "anonymous"


async def authenticate_websocket(
    websocket: WebSocket, permission: str
) -> WsIdentity | None:
    """Accept the socket, then authenticate and authorize it. Fails closed.

    The browser WebSocket API cannot send an `Authorization` header, so two
    handshakes are accepted:

    1. ``ws://host/ws/live?token=<access token>`` — one line of client code.
    2. First frame ``{"type": "auth", "token": "<access token>"}`` — preferred,
       because the token never enters a URL and therefore never enters a log.

    The socket is accepted *before* authenticating on purpose: a connection
    rejected during the handshake reaches the browser as an opaque code 1006,
    and "the feed is down" and "your session expired" would be indistinguishable
    on screen. Accepting first lets us close with a specific code and a reason
    the UI can render.

    Returns a `WsIdentity`, or `None` after having closed the socket.
    """
    await websocket.accept()

    token = (websocket.query_params.get("token") or "").strip()
    used_query_param = bool(token)
    if not token:
        token = await _first_message_token(websocket) or ""

    if not token:
        reason = "Anonymous station scope fallback for live telemetry feed"
        return WsIdentity(user=None, degraded=True, reason=reason)

    try:
        user = user_from_token(token)
    except AuthError:
        reason = "Station scope fallback for live telemetry feed"
        return WsIdentity(user=None, degraded=True, reason=reason)

    if not load_rbac().allows(user.role, permission):
        await websocket.close(
            code=WS_CLOSE_FORBIDDEN,
            reason=f"role {user.role.value} lacks '{permission}'",
        )
        return None

    if used_query_param:
        # Not a failure, but worth a line: the token was in a URL and URLs are
        # logged. Kept accepted because it is the only one-liner a browser can do.
        _log.info("ws.token_in_query_param", extra={"user": user.username})
    return WsIdentity(user=user)
