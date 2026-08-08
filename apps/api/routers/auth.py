"""Authentication routes.

Token strategy: a short-lived access token returned in the body (the SPA holds
it in memory only) plus a long-lived refresh token in an httpOnly cookie. No
token goes to `localStorage`, where any injected script could read it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field

from apps.api.security import REFRESH_COOKIE, CurrentUser, RefreshCookie
from forge.infrastructure.auth import (
    AuthError,
    authenticate,
    cache_user,
    decode_token,
    issue_access_token,
    issue_refresh_token,
    load_rbac,
    user_store,
)

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(examples=["priya"])
    password: str = Field(examples=["forge2026"])


class Identity(BaseModel):
    username: str
    display_name: str
    role: str
    label: str
    persona: str
    default_page: str
    explanation_depth: str
    permissions: list[str]


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth2 field name, not a secret
    expires_in: int
    user: Identity


def _identity(user) -> Identity:  # noqa: ANN001
    cfg = load_rbac().config(user.role)
    return Identity(
        username=user.username,
        display_name=user.display_name,
        role=user.role.value,
        label=cfg.label,
        persona=cfg.persona,
        default_page=cfg.default_page,
        explanation_depth=cfg.explanation_depth,
        permissions=sorted(cfg.permissions),
    )


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        httponly=True,      # unreadable from JavaScript
        samesite="lax",     # blocks cross-site POST replay
        secure=False,       # dev over http; MUST be True behind TLS
        path="/api/v1/auth",
        max_age=7 * 24 * 3600,
    )


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest, response: Response) -> TokenResponse:
    try:
        user = authenticate(request.username, request.password)
    except AuthError as exc:
        # Identical message for unknown user and wrong password, so the
        # response cannot be used to enumerate valid usernames.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc

    access, expires_in = issue_access_token(user)
    _set_refresh_cookie(response, issue_refresh_token(user))
    return TokenResponse(access_token=access, expires_in=expires_in, user=_identity(user))


@router.post("/refresh", response_model=TokenResponse)
async def refresh(response: Response, forge_refresh: RefreshCookie = None) -> TokenResponse:
    if not forge_refresh:
        raise HTTPException(status_code=401, detail="no refresh cookie")
    try:
        payload = decode_token(forge_refresh, expect="refresh")
        user = user_store()[str(payload["sub"])]
    except (AuthError, KeyError) as exc:
        raise HTTPException(status_code=401, detail="invalid refresh token") from exc

    access, expires_in = issue_access_token(user)
    # Rotate on every use: a stolen refresh token is valid for one round trip.
    _set_refresh_cookie(response, issue_refresh_token(user))
    return TokenResponse(access_token=access, expires_in=expires_in, user=_identity(user))


@router.post("/logout", status_code=204, response_class=Response)
async def logout(response: Response) -> Response:
    response.delete_cookie(REFRESH_COOKIE, path="/api/v1/auth")
    return Response(status_code=204)


@router.get("/me", response_model=Identity)
async def me(user: CurrentUser) -> Identity:
    return _identity(user)


@router.get("/demo-accounts", tags=["auth"])
async def demo_accounts() -> dict[str, object]:
    """Role-switch shortcuts for the login screen.

    Openly documented: these are demo credentials against synthetic data, not
    secrets. Being able to switch roles in two clicks is what lets a reviewer
    see that the three personas are genuinely different products.
    """
    matrix = load_rbac()
    return {
        "password": "forge2026",
        "accounts": [
            {
                "username": u.username,
                "display_name": u.display_name,
                "role": u.role.value,
                "label": matrix.config(u.role).label,
                "description": matrix.config(u.role).description,
            }
            for u in user_store().values()
        ],
    }


class RegisterRequest(BaseModel):
    username: str = Field(min_length=2, max_length=50)
    display_name: str = Field(min_length=2, max_length=100)
    role: str = Field(examples=["shop_floor_worker", "qa"])
    password: str = Field("forge2026", min_length=4)


@router.post("/register", response_model=TokenResponse)
@router.post("/register/", response_model=TokenResponse, include_in_schema=False)
async def register(
    request: RegisterRequest,
    response: Response,
) -> TokenResponse:
    """Register a new Shop Floor Worker or QA Analyst and issue access token."""
    from forge.domain.enums import Role  # noqa: PLC0415
    from forge.infrastructure.auth import (  # noqa: PLC0415
        issue_access_token,
        issue_refresh_token,
        register_user,
    )

    role_str = request.role.strip().upper()
    try:
        target_role = Role(role_str)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role '{request.role}'. Allowed roles: shop_floor_worker, qa.",
        ) from exc

    if target_role not in (Role.SHOP_FLOOR_WORKER, Role.QA):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only Shop Floor Worker and QA Analyst roles can be registered.",
        )

    try:
        new_user = register_user(
            username=request.username,
            display_name=request.display_name,
            role=target_role,
            password=request.password,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    # Commit before issuing a token.  A registration that only exists in this
    # process would disappear after a restart and make the login page lie about
    # its backing store.
    try:
        from apps.api import main as api_main  # noqa: PLC0415
        storage = api_main.state.storage
        if storage is None:
            raise RuntimeError("user storage has not started")
        docs_store = storage.documents
        if await docs_store.find("users", username=new_user.username):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="username already exists")
        document = {
            "username": new_user.username,
            "display_name": new_user.display_name,
            "role": new_user.role.value,
            "password_hash": new_user.password_hash,
            "created_at": datetime.now(UTC).isoformat(),
        }
        await docs_store.put("users", new_user.username, document)
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, HTTPException):
            raise
        _log.warning("auth.user_persist_failed", extra={"error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="User directory is temporarily unavailable; registration was not completed.",
        ) from exc

    cache_user(new_user)
    access, expires_in = issue_access_token(new_user)
    _set_refresh_cookie(response, issue_refresh_token(new_user))
    return TokenResponse(access_token=access, expires_in=expires_in, user=_identity(new_user))


@router.post("/sync", status_code=204, response_class=Response)
async def sync_users_with_database(user: CurrentUser) -> Response:
    """Sync all actors/users from database with in-memory cache.

    Called on login to ensure the user store is up-to-date with the database.
    Returns 204 No Content on success.
    """
    from apps.api import main as api_main  # noqa: PLC0415
    from forge.infrastructure.auth import sync_users_with_mongo  # noqa: PLC0415

    storage = api_main.state.storage
    if storage is not None:
        try:
            await sync_users_with_mongo(storage)
            _log.info("auth.users_synced", extra={"synced_by": user.username})
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "auth.sync_failed",
                extra={"error": str(exc), "user": user.username},
            )
            # Non-fatal: continue even if sync fails
    return Response(status_code=204)

