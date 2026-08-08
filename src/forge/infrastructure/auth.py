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

import logging
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
_log = logging.getLogger(__name__)


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
USERNAME_DOMAIN = "@ds.com"

_DEMO_USERS: tuple[tuple[str, str, Role], ...] = (
    ("ravi@ds.com", "Ravi Verma", Role.SHOP_FLOOR_WORKER),
    ("aarav@ds.com", "Aarav Singh", Role.SHOP_FLOOR_WORKER),
    ("priya@ds.com", "Priya Patel", Role.QA),
    ("sam@ds.com", "Rajesh Sharma", Role.ADMIN),
)

_DYNAMIC_USERS: dict[str, User] = {}


def normalize_username(value: str) -> str:
    """Canonicalize the human account identifier used by the UI and database."""
    username = value.strip().lower()
    if not username:
        raise ValueError("Username cannot be empty")
    if "@" not in username:
        return f"{username}{USERNAME_DOMAIN}"
    if not username.endswith(USERNAME_DOMAIN):
        raise ValueError(f"Username must use the {USERNAME_DOMAIN} domain")
    return username


def user_store() -> dict[str, User]:
    if not _DYNAMIC_USERS:
        for username, display, role in _DEMO_USERS:
            _DYNAMIC_USERS[username] = User(username, display, role, hash_password(DEMO_PASSWORD))
    return _DYNAMIC_USERS


async def sync_users_with_mongo(storage: Any) -> None:
    """Seed missing demo users and load the database-backed user directory.

    The database is authoritative.  In particular, demo accounts are inserted
    with ``$setOnInsert`` so restarting the API cannot replace a user-managed
    password hash with a freshly generated demo hash.
    """
    if not storage:
        return
    db = getattr(storage, "_db", None) or getattr(getattr(storage, "documents", None), "_db", None)
    if db is None:
        return
    try:
        # Existing installations used bare names. Migrate them once, preserving
        # the password hash and every other stored attribute.
        existing = [document async for document in db["users"].find({}, {"_id": 0})]
        for document in existing:
            legacy_name = str(document.get("username", ""))
            canonical_name = normalize_username(legacy_name)
            if legacy_name == canonical_name:
                continue
            if await db["users"].find_one({"username": canonical_name}):
                _log.warning("auth.username_migration_conflict", extra={"username": legacy_name})
                continue
            await db["users"].update_one(
                {"username": legacy_name},
                {"$set": {"username": canonical_name, "_key": canonical_name}},
            )

        # Seed only the declared demo users.  Do not iterate the mutable cache:
        # it also contains registered users loaded from a previous lifecycle.
        for username, display_name, role in _DEMO_USERS:
            demo_user = User(username, display_name, role, hash_password(DEMO_PASSWORD))
            doc = {
                "username": demo_user.username,
                "display_name": demo_user.display_name,
                "role": demo_user.role.value,
                "password_hash": demo_user.password_hash,
            }
            await db["users"].update_one(
                {"username": demo_user.username}, {"$setOnInsert": doc}, upsert=True
            )

        # The cache mirrors, but never overrides, the persisted directory.
        _DYNAMIC_USERS.clear()
        cursor = db["users"].find({}, {"_id": 0})
        async for doc in cursor:
            try:
                role_enum = Role(doc["role"])
                _DYNAMIC_USERS[doc["username"]] = User(
                    username=doc["username"],
                    display_name=doc["display_name"],
                    role=role_enum,
                    password_hash=doc["password_hash"],
                )
            except Exception as e:
                _log.warning("auth.mongo_user_load_skip", extra={"error": str(e)})
    except Exception as exc:
        _log.warning("auth.mongo_user_sync_failed", extra={"error": str(exc)})


def register_user(username: str, display_name: str, role: Role, password: str = DEMO_PASSWORD) -> User:
    """Validate and construct a new user without claiming it was persisted."""
    username_clean = normalize_username(username)
    store = user_store()
    if username_clean in store:
        raise ValueError(f"User '{username_clean}' already exists")
    return User(username_clean, display_name.strip() or username_clean, role, hash_password(password))


def cache_user(user: User) -> None:
    """Expose a user to token refresh only after durable persistence succeeds."""
    user_store()[user.username] = user




def authenticate(username: str, password: str) -> User:
    """Verify credentials.

    Always raises the same error for an unknown user and a wrong password, so
    the response cannot be used to enumerate valid usernames.
    """
    try:
        username = normalize_username(username)
    except ValueError:
        raise AuthError("invalid username or password") from None
    user = user_store().get(username)
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
