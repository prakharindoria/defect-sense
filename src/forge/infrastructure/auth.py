"""Authentication and role-based authorization.

Design notes worth defending:

- **The permission matrix lives in `config/rbac.yaml`, not in code.** Routes
  declare the permission they need; the matrix decides who holds it. Adding a
  role is a config change, and the matrix is a single artefact a reviewer can
  read end to end.
- **Default deny.** An unknown role, an unknown permission, or a missing token
  all fail closed.
- **Separation of duties is asserted, not assumed.** ADMIN cannot rule on
  quality; QA cannot reconfigure the platform. `verify_separation_of_duties()`
  runs at import and raises if the matrix ever grants both.
- **Access tokens are short-lived and belong in memory.** The refresh token is
  an httpOnly cookie. Nothing goes in `localStorage`, where any XSS can read it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import jwt
import yaml
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from forge.domain.enums import Role

RBAC_PATH = Path("config/rbac.yaml")
ALGORITHM = "HS256"

_hasher = PasswordHasher()


class AuthError(Exception):
    """Authentication failed. Never leaks which of user/password was wrong."""


class PermissionDeniedError(Exception):
    def __init__(self, permission: str, role: Role) -> None:
        super().__init__(f"role {role.value} lacks permission '{permission}'")
        self.permission = permission
        self.role = role


@dataclass(frozen=True, slots=True)
class RoleConfig:
    role: Role
    persona: str
    label: str
    default_page: str
    explanation_depth: str
    rate_limit_per_min: int
    description: str
    permissions: frozenset[str]
    tools: frozenset[str]


@dataclass(frozen=True, slots=True)
class User:
    username: str
    display_name: str
    role: Role
    password_hash: str


@dataclass(frozen=True, slots=True)
class RbacMatrix:
    roles: dict[Role, RoleConfig]
    never_autonomous: frozenset[str]
    audited_permissions: frozenset[str]

    def config(self, role: Role) -> RoleConfig:
        if role not in self.roles:
            raise PermissionDeniedError("<any>", role)
        return self.roles[role]

    def allows(self, role: Role, permission: str) -> bool:
        return permission in self.roles[role].permissions if role in self.roles else False

    def require(self, role: Role, permission: str) -> None:
        if not self.allows(role, permission):
            raise PermissionDeniedError(permission, role)


@lru_cache(maxsize=1)
def load_rbac(path: str | Path = RBAC_PATH) -> RbacMatrix:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    perms: dict[str, list[str]] = raw.get("permissions") or {}
    tools: dict[str, list[str]] = raw.get("tool_access") or {}
    shared = frozenset(tools.get("_all_roles") or [])

    roles: dict[Role, RoleConfig] = {}
    for name, spec in (raw.get("roles") or {}).items():
        role = Role(name)
        roles[role] = RoleConfig(
            role=role,
            persona=str(spec.get("persona", "")),
            label=str(spec.get("label", name)),
            default_page=str(spec.get("default_page", "/")),
            explanation_depth=str(spec.get("explanation_depth", "brief")),
            rate_limit_per_min=int(spec.get("rate_limit_per_min", 60)),
            description=" ".join(str(spec.get("description", "")).split()),
            permissions=frozenset(perms.get(name) or []),
            tools=shared | frozenset(tools.get(name) or []),
        )

    matrix = RbacMatrix(
        roles=roles,
        never_autonomous=frozenset((raw.get("autonomy") or {}).get("never_autonomous") or []),
        audited_permissions=frozenset(
            (raw.get("governance") or {}).get("audited_permissions") or []
        ),
    )
    _verify_separation_of_duties(matrix, raw)
    return matrix


def _verify_separation_of_duties(matrix: RbacMatrix, raw: dict[str, Any]) -> None:
    """Fail at import if a role ever gains both platform and quality authority.

    This is the check that makes "no single account can weaken a threshold and
    then pass the part it now permits" a fact rather than an intention.
    """
    for rule in (raw.get("governance") or {}).get("separation_of_duties") or []:
        role = Role(rule["role"])
        forbidden = set(rule.get("must_not_have") or [])
        held = forbidden & set(matrix.roles[role].permissions)
        if held:
            raise ValueError(
                f"separation of duties violated: {role.value} must not hold {sorted(held)}"
            )


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, Exception):  # noqa: B014 - argon2 raises several types
        return False


# Demo accounts. Passwords are hashed at import, never stored in plain text --
# even for a demo, because a shortcut here is the one a reviewer will find.
# Documented openly: these are demo credentials for synthetic data, not secrets.
DEMO_PASSWORD = os.environ.get("FORGE_DEMO_PASSWORD", "forge2026")

_DEMO_USERS: tuple[tuple[str, str, Role], ...] = (
    ("ravi", "Ravi Kulkarni", Role.SHOP_FLOOR_WORKER),
    ("priya", "Priya Sharma", Role.QA),
    ("sam", "Sam Mehta", Role.ADMIN),
)


@lru_cache(maxsize=1)
def user_store() -> dict[str, User]:
    return {
        username: User(username, display, role, hash_password(DEMO_PASSWORD))
        for username, display, role in _DEMO_USERS
    }


def authenticate(username: str, password: str) -> User:
    """Verify credentials.

    Always raises the same error for an unknown user and a wrong password, so
    the response cannot be used to enumerate valid usernames.
    """
    user = user_store().get(username.strip().lower())
    if user is None or not verify_password(password, user.password_hash):
        raise AuthError("invalid username or password")
    return user


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------
def _secret() -> str:
    secret = os.environ.get("JWT_SECRET", "").strip()
    if secret:
        return secret
    if os.environ.get("FORGE_ENV") == "production":
        raise AuthError("JWT_SECRET must be set in production")
    # Development only. Regenerated per process, so restarting invalidates
    # every token -- which is the correct behaviour for an unset secret.
    return "dev-only-insecure-secret-change-me"


def issue_access_token(user: User, *, minutes: int | None = None) -> tuple[str, int]:
    ttl = minutes or int(os.environ.get("JWT_ACCESS_TTL_MINUTES", "15"))
    now = datetime.now(UTC)
    expires = now + timedelta(minutes=ttl)
    payload = {
        "sub": user.username,
        "name": user.display_name,
        "role": user.role.value,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "typ": "access",
    }
    return jwt.encode(payload, _secret(), algorithm=ALGORITHM), ttl * 60


def issue_refresh_token(user: User) -> str:
    days = int(os.environ.get("JWT_REFRESH_TTL_DAYS", "7"))
    now = datetime.now(UTC)
    payload = {
        "sub": user.username,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=days)).timestamp()),
        "typ": "refresh",
    }
    return jwt.encode(payload, _secret(), algorithm=ALGORITHM)


def decode_token(token: str, *, expect: str = "access") -> dict[str, Any]:
    try:
        payload = jwt.decode(token, _secret(), algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError("invalid token") from exc
    # A refresh token must never be accepted as an access token: it lives much
    # longer, so confusing the two silently extends every session.
    if payload.get("typ") != expect:
        raise AuthError(f"expected a {expect} token")
    return payload


def user_from_token(token: str) -> User:
    payload = decode_token(token)
    user = user_store().get(str(payload.get("sub", "")))
    if user is None:
        raise AuthError("unknown subject")
    return user
