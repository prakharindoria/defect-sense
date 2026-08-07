"""FastAPI auth dependencies.

Routes declare the permission they need; `config/rbac.yaml` decides who holds
it. A route never names a role, so adding or re-scoping a role is a config
change and no endpoint has to be re-audited.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request, status

from forge.infrastructure.auth import (
    AuthError,
    PermissionDeniedError,
    RbacMatrix,
    User,
    load_rbac,
    user_from_token,
)

REFRESH_COOKIE = "forge_refresh"


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
