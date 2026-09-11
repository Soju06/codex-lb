"""Machine-checked dashboard route authorization matrix.

Every dashboard API route must carry its session gate and its unconditional
permission requirements in the FastAPI dependency tree. This test walks the live
application routes so a new endpoint that forgets a gate, or a sensitive endpoint
whose gate silently regresses, fails CI instead of relying on review to notice.

Request-conditional checks (``PUT /api/settings`` security fields,
``GET /api/request-logs`` with ``conversation_id``) call
``ensure_dashboard_permission`` inside the handler and are covered by
``test_dashboard_permission_gates.py`` instead.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from httpx import AsyncClient

import app.core.auth.dependencies as auth_dependencies
from app.core.auth.dashboard_access import STEP_UP_PERMISSIONS, Permission, Scope
from app.core.auth.dependencies import DashboardPermissionDependency, PermissionRequirement
from app.core.middleware.dashboard_csrf import CROSS_SITE_REQUEST_REJECTED_CODE

pytestmark = pytest.mark.integration

DASHBOARD_PREFIX = "/api/"

#: Routes under ``/api/`` that intentionally do not use the dashboard session:
#: the fleet API and the Codex usage-identity bridge authenticate with proxy
#: API keys, and the dashboard-auth module implements the login/bootstrap/TOTP
#: flows that *issue* sessions.
SESSION_EXEMPT_PREFIXES: tuple[str, ...] = (
    "/api/fleet/",
    "/api/codex/",
    "/api/dashboard-auth/",
)

#: Dashboard-auth mutations that are gated by a permission dependency even
#: though the rest of the module is session-exempt.
DASHBOARD_AUTH_GATED: dict[tuple[str, str], PermissionRequirement] = {
    ("POST", "/api/dashboard-auth/guest/password"): PermissionRequirement(Permission.SECURITY_WRITE),
    ("DELETE", "/api/dashboard-auth/guest/password"): PermissionRequirement(Permission.SECURITY_WRITE),
    ("POST", "/api/dashboard-auth/guest/logout-all"): PermissionRequirement(Permission.SECURITY_WRITE),
}

#: Routes whose permission requirement is part of the security contract.
EXPECTED_REQUIREMENTS: dict[tuple[str, str], PermissionRequirement] = {
    ("POST", "/api/accounts/{account_id}/export/auth"): PermissionRequirement(Permission.ACCOUNTS_EXPORT),
    ("GET", "/api/audit-logs"): PermissionRequirement(Permission.AUDIT_READ),
    ("GET", "/api/conversation-archive/records"): PermissionRequirement(Permission.CONVERSATIONS_READ),
    ("GET", "/api/conversations"): PermissionRequirement(Permission.CONVERSATIONS_READ),
    ("GET", "/api/conversations/"): PermissionRequirement(Permission.CONVERSATIONS_READ),
    ("GET", "/api/conversations/{conversation_id:path}"): PermissionRequirement(Permission.CONVERSATIONS_READ),
    ("GET", "/api/dashboard/overview"): PermissionRequirement(Permission.ACCOUNTS_READ),
    ("GET", "/api/dashboard/projections"): PermissionRequirement(Permission.ACCOUNTS_READ),
    ("GET", "/api/models"): PermissionRequirement(Permission.DASHBOARD_READ),
    ("GET", "/api/usage/summary"): PermissionRequirement(Permission.ACCOUNTS_READ),
    ("GET", "/api/usage/history"): PermissionRequirement(Permission.ACCOUNTS_READ),
    ("GET", "/api/usage/window"): PermissionRequirement(Permission.ACCOUNTS_READ),
    ("POST", "/api/firewall/ips"): PermissionRequirement(Permission.SECURITY_WRITE),
    ("DELETE", "/api/firewall/ips/{ip_address}"): PermissionRequirement(Permission.SECURITY_WRITE),
    ("POST", "/api/settings/upstream-proxy/endpoints"): PermissionRequirement(Permission.SECURITY_WRITE),
    # Guest-restricted reads (PR-0a-2): inventories and topology are not guest-safe.
    ("GET", "/api/api-keys"): PermissionRequirement(Permission.API_KEYS_READ),
    ("GET", "/api/api-keys/"): PermissionRequirement(Permission.API_KEYS_READ),
    ("GET", "/api/api-keys/{key_id}/trends"): PermissionRequirement(Permission.API_KEYS_READ),
    ("GET", "/api/api-keys/{key_id}/usage-7d"): PermissionRequirement(Permission.API_KEYS_READ),
    ("GET", "/api/settings/upstream-proxy"): PermissionRequirement(Permission.OPS_WRITE),
    ("GET", "/api/settings/runtime/connect-address"): PermissionRequirement(Permission.OPS_WRITE),
    ("GET", "/api/sticky-sessions"): PermissionRequirement(Permission.OPS_WRITE),
    # The cache isolation probe spends real account quota, so both its cost
    # preview and its run sit on the operational write gate.
    ("GET", "/api/diagnostics/cache-isolation-probe"): PermissionRequirement(Permission.OPS_WRITE),
    ("POST", "/api/diagnostics/cache-isolation-probe/run"): PermissionRequirement(Permission.OPS_WRITE),
    ("GET", "/api/oauth/status"): PermissionRequirement(Permission.ACCOUNTS_WRITE),
    # Pure account mutations (PR-2a): accounts:write suffices, the coarse alias is not required.
    ("POST", "/api/oauth/start"): PermissionRequirement(Permission.ACCOUNTS_WRITE),
    ("POST", "/api/oauth/complete"): PermissionRequirement(Permission.ACCOUNTS_WRITE),
    ("POST", "/api/oauth/manual-callback"): PermissionRequirement(Permission.ACCOUNTS_WRITE),
    ("POST", "/api/accounts/import"): PermissionRequirement(Permission.ACCOUNTS_WRITE),
    ("PATCH", "/api/accounts/{account_id}"): PermissionRequirement(Permission.ACCOUNTS_WRITE),
    ("DELETE", "/api/accounts/{account_id}"): PermissionRequirement(Permission.ACCOUNTS_WRITE),
    ("POST", "/api/accounts/{account_id}/pause"): PermissionRequirement(Permission.ACCOUNTS_WRITE),
    ("POST", "/api/accounts/{account_id}/reactivate"): PermissionRequirement(Permission.ACCOUNTS_WRITE),
    ("POST", "/api/accounts/{account_id}/probe"): PermissionRequirement(Permission.ACCOUNTS_WRITE),
    ("PUT", "/api/accounts/{account_id}/alias"): PermissionRequirement(Permission.ACCOUNTS_WRITE),
    ("PUT", "/api/accounts/{account_id}/limit-warmup"): PermissionRequirement(Permission.ACCOUNTS_WRITE),
    ("PUT", "/api/accounts/{account_id}/routing-policy"): PermissionRequirement(Permission.ACCOUNTS_WRITE),
    ("POST", "/api/accounts/{account_id}/usage-reset-credits/consume"): PermissionRequirement(
        Permission.ACCOUNTS_WRITE
    ),
    ("POST", "/api/accounts/{account_id}/rate-limit-reset-credits/consume"): PermissionRequirement(
        Permission.ACCOUNTS_WRITE
    ),
    # Account management and the roles read API (PR-1c): users:manage throughout.
    ("GET", "/api/dashboard-users"): PermissionRequirement(Permission.USERS_MANAGE),
    ("POST", "/api/dashboard-users"): PermissionRequirement(Permission.USERS_MANAGE),
    ("GET", "/api/dashboard-users/invites"): PermissionRequirement(Permission.USERS_MANAGE),
    ("PATCH", "/api/dashboard-users/{user_id}"): PermissionRequirement(Permission.USERS_MANAGE),
    ("DELETE", "/api/dashboard-users/{user_id}"): PermissionRequirement(Permission.USERS_MANAGE),
    ("POST", "/api/dashboard-users/{user_id}/invite"): PermissionRequirement(Permission.USERS_MANAGE),
    ("DELETE", "/api/dashboard-users/{user_id}/invite"): PermissionRequirement(Permission.USERS_MANAGE),
    ("POST", "/api/dashboard-users/{user_id}/reset-totp"): PermissionRequirement(Permission.USERS_MANAGE),
    ("POST", "/api/dashboard-users/{user_id}/revoke-sessions"): PermissionRequirement(Permission.USERS_MANAGE),
    ("POST", "/api/dashboard-users/{user_id}/reactivate-keys"): PermissionRequirement(Permission.USERS_MANAGE),
    ("GET", "/api/dashboard-roles"): PermissionRequirement(Permission.USERS_MANAGE),
    ("GET", "/api/dashboard-roles/permissions"): PermissionRequirement(Permission.USERS_MANAGE),
    # Sign-in provider settings (PR-2c-1): security:write throughout.
    ("GET", "/api/auth-providers"): PermissionRequirement(Permission.SECURITY_WRITE),
    ("PATCH", "/api/auth-providers/{provider_id}"): PermissionRequirement(Permission.SECURITY_WRITE),
    # Group-to-role rules (PR-2c-2): security:write throughout.
    ("GET", "/api/role-mappings"): PermissionRequirement(Permission.SECURITY_WRITE),
    ("GET", "/api/role-mappings/assignable-roles"): PermissionRequirement(Permission.SECURITY_WRITE),
    ("POST", "/api/role-mappings"): PermissionRequirement(Permission.SECURITY_WRITE),
    ("PUT", "/api/role-mappings/order"): PermissionRequirement(Permission.SECURITY_WRITE),
    ("PATCH", "/api/role-mappings/{mapping_id}"): PermissionRequirement(Permission.SECURITY_WRITE),
    ("DELETE", "/api/role-mappings/{mapping_id}"): PermissionRequirement(Permission.SECURITY_WRITE),
    **DASHBOARD_AUTH_GATED,
}

#: Mutations that additionally require a recent step-up (PLAN §5 H5): every
#: non-safe route whose permission requirement is a ``STEP_UP_PERMISSIONS``
#: member. ``PUT /api/settings`` joins them only when the body changes a
#: security field (handler-level, covered by ``test_step_up_auth.py``).
STEP_UP_GATED: frozenset[tuple[str, str]] = frozenset(
    {
        # ``POST /api/accounts/{id}/export`` and ``.../export/opencode-auth`` are the
        # retired predecessors that ``unified-auth-export`` forbids serving; only the
        # single export route below exists.
        ("POST", "/api/accounts/{account_id}/export/auth"),
        ("POST", "/api/firewall/ips"),
        ("DELETE", "/api/firewall/ips/{ip_address}"),
        ("POST", "/api/settings/upstream-proxy/endpoints"),
        ("POST", "/api/dashboard-users"),
        ("PATCH", "/api/dashboard-users/{user_id}"),
        ("DELETE", "/api/dashboard-users/{user_id}"),
        ("POST", "/api/dashboard-users/{user_id}/invite"),
        ("DELETE", "/api/dashboard-users/{user_id}/invite"),
        ("POST", "/api/dashboard-users/{user_id}/reset-totp"),
        ("POST", "/api/dashboard-users/{user_id}/revoke-sessions"),
        ("POST", "/api/dashboard-users/{user_id}/reactivate-keys"),
        ("PATCH", "/api/auth-providers/{provider_id}"),
        ("POST", "/api/role-mappings"),
        ("PUT", "/api/role-mappings/order"),
        ("PATCH", "/api/role-mappings/{mapping_id}"),
        ("DELETE", "/api/role-mappings/{mapping_id}"),
        ("POST", "/api/dashboard-auth/guest/password"),
        ("DELETE", "/api/dashboard-auth/guest/password"),
        ("POST", "/api/dashboard-auth/guest/logout-all"),
    }
)

#: Permissions that never authorize a mutation on their own — every ``*:read``
#: permission, derived structurally so a new read permission cannot slip through.
READ_ONLY_PERMISSIONS: frozenset[Permission] = frozenset(p for p in Permission if p.value.endswith(":read"))

SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})

#: Routes under ``/api/`` that authenticate with a bearer proxy API key and are
#: therefore outside the cross-site (CSRF) origin check.
CSRF_EXEMPT_PREFIXES: tuple[str, ...] = (
    "/api/fleet/",
    "/api/codex/",
)

_PATH_PARAM = re.compile(r"\{[^}]+\}")


@dataclass(frozen=True, slots=True)
class RouteAuth:
    session_validated: bool
    write_alias_gate: bool
    requirements: tuple[PermissionRequirement, ...]


def _walk(dependant: Dependant) -> Iterator[Callable[..., object]]:
    for sub in dependant.dependencies:
        if sub.call is not None:
            yield sub.call
        yield from _walk(sub)


def _route_auth(route: APIRoute) -> RouteAuth:
    session_validated = False
    write_alias_gate = False
    requirements: list[PermissionRequirement] = []
    for call in _walk(route.dependant):
        if call is auth_dependencies.validate_dashboard_session:
            session_validated = True
        elif call is auth_dependencies.require_dashboard_write_access:
            session_validated = True
            write_alias_gate = True
        elif isinstance(call, DashboardPermissionDependency):
            session_validated = True
            requirements.append(call.requirement)
    return RouteAuth(session_validated, write_alias_gate, tuple(requirements))


def _dashboard_routes(app: FastAPI) -> Iterator[tuple[str, str, APIRoute]]:
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith(DASHBOARD_PREFIX):
            continue
        for method in sorted(route.methods or ()):
            yield method, route.path, route


def _is_session_exempt(method: str, path: str) -> bool:
    if (method, path) in DASHBOARD_AUTH_GATED:
        return False
    return path.startswith(SESSION_EXEMPT_PREFIXES)


def test_every_dashboard_route_validates_the_session(app_instance: FastAPI) -> None:
    unguarded = [
        f"{method} {path}"
        for method, path, route in _dashboard_routes(app_instance)
        if not _is_session_exempt(method, path) and not _route_auth(route).session_validated
    ]
    assert unguarded == [], f"dashboard routes without a session gate: {unguarded}"


def test_every_dashboard_mutation_declares_a_write_class_gate(app_instance: FastAPI) -> None:
    read_only_mutations = []
    for method, path, route in _dashboard_routes(app_instance):
        if method in SAFE_METHODS or _is_session_exempt(method, path):
            continue
        auth = _route_auth(route)
        write_permissions = [r for r in auth.requirements if r.permission not in READ_ONLY_PERMISSIONS]
        if not auth.write_alias_gate and not write_permissions:
            read_only_mutations.append(f"{method} {path}")
    assert read_only_mutations == [], f"mutating routes without a write-class gate: {read_only_mutations}"


def test_sensitive_routes_declare_their_permission(app_instance: FastAPI) -> None:
    actual: dict[tuple[str, str], tuple[PermissionRequirement, ...]] = {}
    for method, path, route in _dashboard_routes(app_instance):
        actual[(method, path)] = _route_auth(route).requirements

    missing = [key for key in EXPECTED_REQUIREMENTS if key not in actual]
    assert missing == [], f"expected routes no longer exist: {missing}"

    mismatched = {
        f"{method} {path}": [r.permission.value for r in actual[(method, path)]]
        for (method, path), expected in EXPECTED_REQUIREMENTS.items()
        if expected not in actual[(method, path)]
    }
    assert mismatched == {}, f"routes whose permission requirement changed: {mismatched}"


def test_step_up_gated_mutations_are_exactly_the_declared_set(app_instance: FastAPI) -> None:
    """The permission dependency requires a fresh step-up for every mutation guarded by a
    ``STEP_UP_PERMISSIONS`` member; this pins which routes that is, so a new sensitive
    route (or one that drops its permission) shows up here."""

    actual = {
        (method, path)
        for method, path, route in _dashboard_routes(app_instance)
        if method not in SAFE_METHODS
        and any(r.permission in STEP_UP_PERMISSIONS for r in _route_auth(route).requirements)
    }
    assert actual == STEP_UP_GATED


def test_read_only_permission_set_matches_vocabulary() -> None:
    assert READ_ONLY_PERMISSIONS == {
        Permission.DASHBOARD_READ,
        Permission.ACCOUNTS_READ,
        Permission.API_KEYS_READ,
        Permission.CONVERSATIONS_READ,
        Permission.AUDIT_READ,
    }


def test_no_route_requires_own_scope_yet(app_instance: FastAPI) -> None:
    """``own`` scope is reserved for per-user ownership; nothing may require it before owners exist."""

    own_scoped = [
        f"{method} {path}"
        for method, path, route in _dashboard_routes(app_instance)
        for requirement in _route_auth(route).requirements
        if requirement.minimum_scope is Scope.OWN
    ]
    assert own_scoped == []


async def test_every_dashboard_mutation_rejects_cross_site_requests(
    app_instance: FastAPI, async_client: AsyncClient
) -> None:
    """The origin check runs before routing, validation, and authentication.

    Every non-safe ``/api/`` route (including the session-issuing dashboard-auth
    module and ``/logout``) must answer ``403 cross_site_request_rejected`` when
    the browser reports a cross-site initiator, regardless of body validity.
    """

    unprotected: list[str] = []
    for method, path, _route in _dashboard_routes(app_instance):
        if method in SAFE_METHODS or path.startswith(CSRF_EXEMPT_PREFIXES):
            continue
        response = await async_client.request(
            method,
            _PATH_PARAM.sub("x", path),
            json={},
            headers={"Sec-Fetch-Site": "cross-site"},
        )
        payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        code = payload.get("error", {}).get("code") if isinstance(payload, dict) else None
        if response.status_code != 403 or code != CROSS_SITE_REQUEST_REJECTED_CODE:
            unprotected.append(f"{method} {path} -> {response.status_code} {code}")
    assert unprotected == [], f"mutating routes reachable cross-site: {unprotected}"


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({"Sec-Fetch-Site": "same-origin"}, id="same-origin-fetch-metadata"),
        pytest.param({"Origin": "http://testserver"}, id="matching-origin"),
    ],
)
async def test_every_dashboard_mutation_accepts_same_origin_requests(
    app_instance: FastAPI, async_client: AsyncClient, headers: dict[str, str]
) -> None:
    """Same-origin browser requests must reach the route: whatever the route answers
    (200, 401, 403 permission, 404, 422), it must not be the cross-site rejection."""

    rejected: list[str] = []
    for method, path, _route in _dashboard_routes(app_instance):
        if method in SAFE_METHODS or path.startswith(CSRF_EXEMPT_PREFIXES):
            continue
        response = await async_client.request(method, _PATH_PARAM.sub("x", path), json={}, headers=headers)
        payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        code = payload.get("error", {}).get("code") if isinstance(payload, dict) else None
        if code == CROSS_SITE_REQUEST_REJECTED_CODE:
            rejected.append(f"{method} {path} -> {response.status_code} {code}")
    assert rejected == [], f"same-origin requests rejected as cross-site: {rejected}"
