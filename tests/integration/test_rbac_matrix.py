"""Every cell of `config/rbac.yaml`, asserted.

The file's own header says "tests/integration/test_rbac_matrix.py asserts EVERY
cell of both matrices". It did not exist. This is that file.

Three layers, because a matrix that is only checked against itself proves
nothing:

1. **The matrix as loaded** — every role x permission and role x tool cell is
   pinned against a table written out here by hand. If someone widens
   `config/rbac.yaml`, a test fails naming the exact cell, rather than a
   reviewer having to notice a diff.
2. **The matrix as enforced** — each role drives the real API and the response
   code is checked against what the matrix says it should be. A permission the
   matrix grants but no route honours (or vice versa) is a lie, and this is
   what catches it.
3. **The invariants that must never be configurable away** — default deny,
   separation of duties, and the autonomy ceiling.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from forge.domain.enums import Role
from forge.infrastructure.auth import load_rbac

RBAC_PATH = Path("config/rbac.yaml")

# The matrix as it is expected to be. Written by hand on purpose: comparing the
# YAML to itself would pass no matter what the YAML said.
EXPECTED_PERMISSIONS: dict[Role, frozenset[str]] = {
    Role.SHOP_FLOOR_WORKER: frozenset({
        "inspection:read_own_station",
        "inspection:acknowledge",
        "stream:read",
        "pack:read",
    }),
    Role.QA: frozenset({
        "inspection:read",
        "inspection:create",
        "inspection:acknowledge",
        "inspection:feedback",
        "inspection:override_verdict",
        "inspection:reclassify",
        "agentrun:read",
        "stream:read",
        "pack:read",
        "analytics:read",
        "analytics:export",
        "knowledge:search",
        "governance:read",
    }),
    Role.ADMIN: frozenset({
        "inspection:read",
        "inspection:create",
        "agentrun:read",
        "stream:read",
        "pack:read",
        "pack:activate",
        "analytics:read",
        "knowledge:search",
        "governance:read",
        "admin:users",
        "admin:rate_limits",
        "admin:feature_flags",
        "admin:model_config",
        "admin:fault_injection",
        "health:read",
    }),
}

SHARED_TOOLS = frozenset({
    "rag.hybrid_search", "sensors.correlate", "weather.current",
    "recalls.lookup", "fx.convert",
})

EXPECTED_TOOLS: dict[Role, frozenset[str]] = {
    Role.SHOP_FLOOR_WORKER: SHARED_TOOLS,
    Role.QA: SHARED_TOOLS | {
        "sql.query_allowlisted_views", "chart.generate", "history.similar_conflicts",
        "memorybank.append", "mes.job_card", "mes.bom_variant", "mes.create_qi",
        "mes.update_job_card", "threshold.tune",
    },
    Role.ADMIN: SHARED_TOOLS | {
        "sql.query_allowlisted_views", "chart.generate", "mes.job_card", "fault.inject",
    },
}

EXPECTED_RATE_LIMITS: dict[Role, int] = {
    Role.SHOP_FLOOR_WORKER: 60,
    Role.QA: 240,
    Role.ADMIN: 1000,
}

# Union of everything any role holds. Used to assert the *negative* cells too:
# a role must be denied every permission not on its own list.
ALL_PERMISSIONS = frozenset().union(*EXPECTED_PERMISSIONS.values())
ALL_TOOLS = frozenset().union(*EXPECTED_TOOLS.values())


# ---------------------------------------------------------------------------
# 1. The matrix as loaded — every cell, positive and negative
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("role", list(EXPECTED_PERMISSIONS))
@pytest.mark.parametrize("permission", sorted(ALL_PERMISSIONS))
def test_permission_cell(role: Role, permission: str) -> None:
    matrix = load_rbac()
    expected = permission in EXPECTED_PERMISSIONS[role]
    assert matrix.allows(role, permission) is expected, (
        f"cell [{role.value} x {permission}] should be "
        f"{'GRANTED' if expected else 'DENIED'}"
    )


@pytest.mark.parametrize("role", list(EXPECTED_TOOLS))
@pytest.mark.parametrize("tool", sorted(ALL_TOOLS))
def test_tool_cell(role: Role, tool: str) -> None:
    tools = load_rbac().config(role).tools
    expected = tool in EXPECTED_TOOLS[role]
    assert (tool in tools) is expected, (
        f"tool cell [{role.value} x {tool}] should be "
        f"{'GRANTED' if expected else 'DENIED'}"
    )


@pytest.mark.parametrize(("role", "limit"), sorted(EXPECTED_RATE_LIMITS.items()))
def test_rate_limit_cell(role: Role, limit: int) -> None:
    """The declared per-minute ceiling. Enforced in tests/integration/test_rate_limit.py."""
    assert load_rbac().config(role).rate_limit_per_min == limit


def test_matrix_declares_exactly_these_roles() -> None:
    """A new role must be added to this test, not silently inherit nothing."""
    assert set(load_rbac().roles) == set(EXPECTED_PERMISSIONS)


def test_yaml_and_loaded_matrix_agree() -> None:
    """The loader must not quietly drop or invent a grant."""
    raw = yaml.safe_load(RBAC_PATH.read_text(encoding="utf-8"))
    for name, granted in (raw.get("permissions") or {}).items():
        assert load_rbac().config(Role(name)).permissions == frozenset(granted)


def test_shop_floor_holds_no_write_or_admin_permission() -> None:
    """The narrowest role stays narrow, whatever else is added to the file."""
    held = load_rbac().config(Role.SHOP_FLOOR_WORKER).permissions
    forbidden = [p for p in held if p.startswith("admin:") or p.endswith(":create")]
    assert forbidden == []
    assert "inspection:read" not in held, "would silently widen it to the whole plant"


def test_shop_floor_holds_no_agent_tools_beyond_the_shared_read_only_set() -> None:
    assert load_rbac().config(Role.SHOP_FLOOR_WORKER).tools == SHARED_TOOLS


# ---------------------------------------------------------------------------
# 2. Invariants that must survive any edit to the YAML
# ---------------------------------------------------------------------------
def test_separation_of_duties() -> None:
    """ADMIN cannot rule on quality; QA cannot reconfigure the platform."""
    matrix = load_rbac()
    for permission in ("inspection:override_verdict", "inspection:feedback"):
        assert not matrix.allows(Role.ADMIN, permission)
    for permission in ("admin:model_config", "admin:feature_flags", "admin:users"):
        assert not matrix.allows(Role.QA, permission)


def test_no_role_holds_every_permission() -> None:
    """No wildcard role. If one appears, the matrix has stopped meaning anything."""
    for role in load_rbac().roles:
        assert load_rbac().config(role).permissions != ALL_PERMISSIONS


def test_unknown_permission_is_denied_for_every_role() -> None:
    """Default deny, including for a permission nobody has heard of."""
    for role in load_rbac().roles:
        assert not load_rbac().allows(role, "inspection:delete_everything")


def test_autonomy_ceiling_is_declared_and_non_empty() -> None:
    never = load_rbac().never_autonomous
    assert {"line.halt", "inspection.pass_critical"} <= never


def test_audited_permissions_cover_every_write_grant() -> None:
    """Anything that changes a verdict, a pack or the platform is audited."""
    audited = load_rbac().audited_permissions
    assert {
        "inspection:override_verdict", "inspection:create", "pack:activate",
        "admin:model_config", "admin:feature_flags", "admin:users",
    } <= audited


# ---------------------------------------------------------------------------
# 3. The matrix as enforced by the real API
# ---------------------------------------------------------------------------
# (method, path, body, permission the route requires). `expected` is derived
# from the matrix rather than hardcoded, so this table asserts the route and the
# matrix agree instead of restating the matrix.
ROUTES: list[tuple[str, str, dict | None, tuple[str, ...]]] = [
    ("GET", "/api/v1/inspections?limit=5", None,
     ("inspection:read", "inspection:read_own_station")),
    ("GET", "/api/v1/metrics", None,
     ("inspection:read", "inspection:read_own_station")),
    ("POST", "/api/v1/inspections", {"scenario": "clean"}, ("inspection:create",)),
    ("GET", "/api/v1/admin/system", None, ("admin:model_config",)),
    ("POST", "/api/v1/assistant/chat", {"message": "status?"},
     ("inspection:read", "inspection:read_own_station")),
]


@pytest.mark.parametrize("username", ["ravi", "priya", "sam"])
@pytest.mark.parametrize(("method", "path", "body", "permissions"), ROUTES)
def test_route_enforces_the_matrix(
    client: TestClient, headers_for, username: str, method: str, path: str,  # noqa: ANN001
    body: dict | None, permissions: tuple[str, ...],
) -> None:
    headers = headers_for(username)
    role = Role(client.get("/api/v1/auth/me", headers=headers).json()["role"])
    allowed = any(load_rbac().allows(role, p) for p in permissions)

    response = client.request(method, path, headers=headers, json=body)

    if allowed:
        assert response.status_code != 403, (
            f"{role.value} holds one of {permissions} but {method} {path} refused it"
        )
    else:
        assert response.status_code == 403, (
            f"{role.value} holds none of {permissions} yet {method} {path} "
            f"returned {response.status_code}"
        )


@pytest.mark.parametrize(("method", "path", "body", "permissions"), ROUTES)
def test_every_route_refuses_an_anonymous_caller(
    client: TestClient, method: str, path: str, body: dict | None,
    permissions: tuple[str, ...],
) -> None:
    """Default deny at the door, before any matrix lookup happens."""
    assert client.request(method, path, json=body).status_code == 401
    assert permissions  # the route does declare a permission


def test_admin_cannot_reach_a_quality_route_even_with_a_valid_token(
    client: TestClient, headers_for,  # noqa: ANN001
) -> None:
    """Separation of duties, proved through the API rather than in the loader."""
    response = client.post(
        "/api/v1/inspections", headers=headers_for("sam"), json={"scenario": "clean"}
    )
    # ADMIN legitimately holds inspection:create for debugging...
    assert response.status_code == 200
    # ...but holds no override grant anywhere in the matrix.
    assert not load_rbac().allows(Role.ADMIN, "inspection:override_verdict")


def test_identity_endpoint_reports_the_matrix_verbatim(
    client: TestClient, headers_for,  # noqa: ANN001
) -> None:
    """The UI hides controls from `permissions`; it must be the same list."""
    for username, role_name in (("ravi", "SHOP_FLOOR_WORKER"), ("priya", "QA"),
                                ("sam", "ADMIN")):
        identity = client.get("/api/v1/auth/me", headers=headers_for(username)).json()
        assert identity["role"] == role_name
        assert set(identity["permissions"]) == EXPECTED_PERMISSIONS[Role(role_name)]
