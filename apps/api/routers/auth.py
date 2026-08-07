"""Authentication routes.

Token strategy: a short-lived access token returned in the body (the SPA holds
it in memory only) plus a long-lived refresh token in an httpOnly cookie. No
token goes to `localStorage`, where any injected script could read it.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field

from apps.api.security import REFRESH_COOKIE, CurrentUser, RefreshCookie
from forge.infrastructure.auth import (
    AuthError,
    authenticate,
    decode_token,
    issue_access_token,
    issue_refresh_token,
    load_rbac,
    user_store,
)

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
