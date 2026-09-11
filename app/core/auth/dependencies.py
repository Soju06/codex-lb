from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from functools import lru_cache
from ipaddress import ip_address, ip_network
from typing import cast

from fastapi import Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.requests import HTTPConnection

from app.core.auth import generate_unique_account_id
from app.core.auth.api_key_cache import get_api_key_cache
from app.core.auth.dashboard_access import (
    STEP_UP_PERMISSIONS,
    DashboardPermission,
    DashboardPrincipal,
    Permission,
    Scope,
    admin_principal,
    guest_principal,
    scope_satisfies,
    totp_policy_applies,
    user_principal,
)
from app.core.auth.dashboard_mode import DashboardAuthMode, DashboardRequestAuth, get_dashboard_request_auth
from app.core.auth.dashboard_users_cache import DashboardUsersCache, get_dashboard_users_cache
from app.core.auth.external_identity import resolve_trusted_header_request
from app.core.auth.step_up import STEP_UP_COOKIE, STEP_UP_UNAVAILABLE_MESSAGE, is_step_up_fresh, step_up_methods
from app.core.clients.proxy import CODEX_LB_REQUIRED_CAPABILITY_HEADER
from app.core.clients.usage import UsageFetchError, fetch_usage
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.crypto import TokenEncryptor
from app.core.exceptions import DashboardAuthError, DashboardPermissionError, ProxyAuthError, ProxyUpstreamError
from app.core.request_locality import is_local_request
from app.core.socket_peer import raw_socket_peer_host
from app.core.upstream_proxy import UpstreamProxyRouteError, resolve_upstream_route
from app.core.utils.time import utcnow
from app.db.models import AccountStatus, DashboardSettings, DashboardUser, DashboardUserStatus
from app.db.session import get_background_session
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyData, ApiKeyInvalidError, ApiKeysService
from app.modules.dashboard_auth.service import (
    DASHBOARD_SESSION_COOKIE,
    DashboardSessionState,
    get_dashboard_session_store,
    get_step_up_cookie_store,
    session_clock,
)
from app.modules.dashboard_roles.service import resolve_role_grants
from app.modules.dashboard_users.break_glass import break_glass_second_factor_required, local_login_admits

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(description="API key (e.g. sk-clb-…)", auto_error=False)
#: The only routes an account that still has to enrol a TOTP secret may reach
#: (an explicit allow-list, not the module prefix: guest-password management,
#: password removal and TOTP disable live under the same prefix and stay closed).
TOTP_ENROLLMENT_ALLOWED_PATHS: frozenset[str] = frozenset(
    {
        "/api/dashboard-auth/session",
        "/api/dashboard-auth/totp/setup/start",
        "/api/dashboard-auth/totp/setup/confirm",
        "/api/dashboard-auth/totp/verify",
        "/api/dashboard-auth/logout",
        "/api/dashboard-auth/logout-all",
        "/api/dashboard-auth/me",
        "/api/dashboard-auth/password/change",
    }
)
#: ``auth_method`` of the implicit admin on a local install without a password.
AUTH_METHOD_LOCAL_BOOTSTRAP = "local_bootstrap"
_CODEX_USAGE_IDENTITY_INACTIVE_WORKSPACE_STATUSES = {
    AccountStatus.PAUSED,
    AccountStatus.DEACTIVATED,
}


# --- Error format markers ---


def set_openai_error_format(request: Request) -> None:
    request.state.error_format = "openai"


def set_dashboard_error_format(request: Request) -> None:
    request.state.error_format = "dashboard"


# --- Proxy API key auth ---


async def validate_proxy_api_key(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> ApiKeyData | None:
    """A required-capability header authenticates even when global proxy API-key auth is disabled."""

    authorization = None if credentials is None else f"Bearer {credentials.credentials}"
    if request.headers.getlist(CODEX_LB_REQUIRED_CAPABILITY_HEADER):
        return await validate_required_proxy_api_key_authorization(authorization)
    return await validate_proxy_api_key_authorization(authorization, request=request)


async def validate_proxy_api_key_authorization(
    authorization: str | None,
    *,
    request: HTTPConnection | None = None,
) -> ApiKeyData | None:
    settings = await get_settings_cache().get()
    if not settings.api_key_auth_enabled:
        if request is not None and not is_local_request(request):
            if not _is_proxy_unauthenticated_socket_peer_allowed(request):
                raise ProxyAuthError("Proxy authentication must be configured before remote access is allowed")
        return None

    token = _extract_bearer_token(authorization)
    if not token:
        raise ProxyAuthError("Missing API key in Authorization header")

    return await _validate_api_key_token(token)


async def validate_required_proxy_api_key_authorization(authorization: str | None) -> ApiKeyData:
    """Validate a proxy API key even when global proxy auth is disabled."""

    token = _extract_bearer_token(authorization)
    if not token:
        raise ProxyAuthError("Missing API key in Authorization header")
    return await _validate_api_key_token(token)


async def _validate_api_key_token(token: str) -> ApiKeyData:
    """Validate a plain API key token and return the typed key data."""

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    cache = get_api_key_cache()
    cached = cast(ApiKeyData | None, await cache.get(token_hash))
    if cached is not None:
        if cached.expires_at is not None and cached.expires_at <= utcnow():
            await cache.invalidate(token_hash)
        else:
            return cached

    version_before_read = cache.version
    async with get_background_session() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        try:
            validated = await service.validate_key(token)
            await cache.set(token_hash, validated, if_version=version_before_read)
            return validated
        except ApiKeyInvalidError as exc:
            raise ProxyAuthError(str(exc)) from exc


async def validate_required_proxy_api_key(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> ApiKeyData:
    """Require a valid proxy API key regardless of the global auth setting."""

    authorization = None if credentials is None else f"Bearer {credentials.credentials}"
    return await validate_required_proxy_api_key_authorization(authorization)


# --- Self-service usage endpoint auth (always requires valid key) ---


async def validate_usage_api_key(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> ApiKeyData:
    """Validate API key for self-service usage endpoint.

    Unlike ``validate_proxy_api_key``, this dependency always requires a valid
    Bearer API key, regardless of the global ``api_key_auth_enabled`` setting.
    Raises ProxyAuthError when the key is missing or invalid.
    """
    authorization = None if credentials is None else f"Bearer {credentials.credentials}"
    return await validate_required_proxy_api_key_authorization(authorization)


# --- Dashboard session auth ---


def _set_dashboard_principal(request: Request, principal: DashboardPrincipal) -> DashboardPrincipal:
    request.state.dashboard_principal = principal
    return principal


def _get_cached_dashboard_principal(request: Request) -> DashboardPrincipal | None:
    principal = getattr(request.state, "dashboard_principal", None)
    return principal if isinstance(principal, DashboardPrincipal) else None


async def _resolve_session_user(
    state: DashboardSessionState | None, users_cache: DashboardUsersCache
) -> DashboardUser | None:
    """The account behind a user cookie, or ``None`` when the cookie no longer binds to one.

    A missing, disabled, or invited account and a stale ``session_generation``
    all fail the same way; the caller answers ``authentication_required``.
    """

    if state is None or not state.is_user or state.user_id is None:
        return None
    user = await users_cache.get_user(state.user_id)
    if user is None or user.status != DashboardUserStatus.ACTIVE.value:
        return None
    if user.session_generation != state.session_generation:
        return None
    return user


def _user_session_principal(
    request: Request,
    user: DashboardUser,
    state: DashboardSessionState,
    *,
    settings: DashboardSettings,
) -> DashboardPrincipal:
    grants = resolve_role_grants(user.role)
    # An emergency account that holds a secret always presents it, whatever
    # the two toggles say: the account a tightened policy relies on must never
    # be reachable on a password alone.
    totp_required = totp_policy_applies(
        required_on_login=settings.totp_required_on_login,
        required_for_admin_role=settings.totp_required_for_admin_role,
        grants=grants,
    ) or break_glass_second_factor_required(user)
    totp_configured = user.totp_secret_encrypted is not None
    if totp_required and totp_configured and not state.totp_verified:
        raise DashboardAuthError("TOTP verification is required for dashboard access", code="totp_required")
    totp_enrollment_required = totp_required and not totp_configured
    # Routes guarded only by this dependency (account inventory, request logs,
    # ...) assume a reader who may see everything. Until the self-service phase
    # makes own-scoped routes declare their own requirement (this guard then
    # moves to route level), a role without dashboard:read at scope `all`
    # (e.g. member) is refused outright rather than admitted to all-scope views.
    if not scope_satisfies(grants.get(Permission.DASHBOARD_READ), Scope.ALL):
        raise DashboardPermissionError(
            f"Dashboard permission '{Permission.DASHBOARD_READ.value}' is required",
            code="permission_required",
            param=Permission.DASHBOARD_READ.value,
        )
    principal = user_principal(
        user,
        grants,
        auth_method=state.auth_method,
        totp_enrollment_required=totp_enrollment_required,
        step_up_verified_at=state.step_up_verified_at,
    )
    # An account that still has to enrol may only reach the self-service routes
    # listed above; those routes gate themselves, so every other route that
    # reaches this dependency is refused here.
    if totp_enrollment_required and request.url.path not in TOTP_ENROLLMENT_ALLOWED_PATHS:
        raise DashboardPermissionError(
            "TOTP enrollment is required before dashboard access",
            code="totp_enrollment_required",
        )
    return _set_dashboard_principal(request, principal)


async def _password_fallback_principal(request: Request) -> DashboardPrincipal | None:
    """A password-verified cookie the local login policy admits, so an emergency
    account stays reachable while the proxy asserts an identity the resolver refuses.

    ``local_login_policy`` decides which cookie counts: ``enabled`` (the
    default) admits every active account, which is what shipped before this
    change; ``admins_only`` admits the admin preset; ``break_glass_only``
    admits only a designated emergency account. One function, so the gate and
    the session response that advertises the fallback cannot disagree.
    """

    users_cache = get_dashboard_users_cache()
    state = get_dashboard_session_store().get(request.cookies.get(DASHBOARD_SESSION_COOKIE))
    session_user = await _resolve_session_user(state, users_cache)
    if state is None or session_user is None or not state.password_verified:
        return None
    settings = await get_settings_cache().get()
    if not local_login_admits(session_user, settings.local_login_policy):
        return None
    return _user_session_principal(request, session_user, state, settings=settings)


async def _trusted_header_principal(request: Request, request_auth: DashboardRequestAuth) -> DashboardPrincipal:
    """The account behind the proxy-asserted identity, or a 401 naming why there is none.

    Unknown identities become accounts through the provider's
    ``unknown_identity_role_id`` (admin by default, D10). A refused identity
    answers ``identity_not_provisioned`` and a disabled account
    ``account_disabled`` -- unless a password-verified cookie rides along, in
    which case that account is served (a resolved header account always wins
    over the cookie). The TOTP policy is not applied to header sessions yet:
    there is no cookie to carry a verified step, so applying it would lock
    every header user out (the step-up change wires it, honouring the
    provider's ``idp_mfa_enforced``).
    """

    resolution = await resolve_trusted_header_request(request, request_auth)
    if resolution is None:
        raise DashboardAuthError("Reverse proxy authentication is required", code="proxy_auth_required")
    if resolution.user is None:
        fallback = await _password_fallback_principal(request)
        if fallback is not None:
            return fallback
        if resolution.denial == "account_disabled":
            raise DashboardAuthError("This account is disabled", code="account_disabled")
        raise DashboardAuthError(
            "Your account is not ready yet; ask an administrator to add you", code="identity_not_provisioned"
        )
    grants = resolve_role_grants(resolution.user.role)
    if not scope_satisfies(grants.get(Permission.DASHBOARD_READ), Scope.ALL):
        raise DashboardPermissionError(
            f"Dashboard permission '{Permission.DASHBOARD_READ.value}' is required",
            code="permission_required",
            param=Permission.DASHBOARD_READ.value,
        )
    return user_principal(
        resolution.user, grants, auth_method=request_auth.mode.value, auth_mode=DashboardAuthMode.TRUSTED_HEADER
    )


async def validate_dashboard_session(request: Request) -> DashboardPrincipal:
    cached = _get_cached_dashboard_principal(request)
    if cached is not None:
        return cached

    request_auth = get_dashboard_request_auth(request)
    if request_auth is not None and request_auth.mode == DashboardAuthMode.TRUSTED_HEADER:
        return _set_dashboard_principal(request, await _trusted_header_principal(request, request_auth))
    if request_auth is not None:
        return _set_dashboard_principal(
            request,
            admin_principal(auth_mode=request_auth.mode, actor=request_auth.actor, auth_method=request_auth.mode.value),
        )

    settings = await get_settings_cache().get()
    users_cache = get_dashboard_users_cache()
    auth_state = await users_cache.local_auth_state()
    password_required = auth_state.requires_auth
    # The admin-role requirement binds accounts only, so on its own it never
    # turns a passwordless install into one that demands a login.
    requires_auth = password_required or settings.totp_required_on_login
    guest_access_enabled = settings.guest_access_enabled
    guest_password_required = guest_access_enabled and settings.guest_password_hash is not None
    passwordless_guest_fallback_allowed = not (
        get_dashboard_request_auth_mode() == DashboardAuthMode.TRUSTED_HEADER
        and requires_auth
        and guest_access_enabled
        and not guest_password_required
    )
    session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
    state = get_dashboard_session_store().get(session_id)
    session_user = await _resolve_session_user(state, users_cache)

    # Behind a reverse proxy a header-less request only gets in on a password
    # cookie the local login policy still admits (the same decision
    # ``_password_fallback_principal`` makes for a refused identity).
    has_password_fallback_session = (
        state is not None
        and session_user is not None
        and state.password_verified
        and local_login_admits(session_user, settings.local_login_policy)
    )
    if get_dashboard_request_auth_mode() == DashboardAuthMode.TRUSTED_HEADER and not has_password_fallback_session:
        raise DashboardAuthError("Reverse proxy authentication is required", code="proxy_auth_required")
    # A guest cookie is only ever minted under the current guest generation, so
    # a matching generation proves it passed whatever guest credential applied.
    if (
        state is not None
        and state.is_guest
        and guest_access_enabled
        and state.guest_session_generation == settings.guest_session_generation
        and (guest_password_required or passwordless_guest_fallback_allowed)
    ):
        return _set_dashboard_principal(request, guest_principal())
    if state is not None and session_user is not None and state.password_verified:
        return _user_session_principal(request, session_user, state, settings=settings)

    if not requires_auth:
        if not is_local_request(request):
            if guest_access_enabled:
                if not guest_password_required:
                    return _set_dashboard_principal(request, guest_principal())
                raise DashboardAuthError("Authentication is required")
            raise DashboardAuthError(
                "Remote bootstrap is required before dashboard access is allowed",
                code="bootstrap_required",
            )
        return _set_dashboard_principal(
            request,
            admin_principal(auth_mode=DashboardAuthMode.STANDARD, auth_method=AUTH_METHOD_LOCAL_BOOTSTRAP),
        )

    if guest_access_enabled and not guest_password_required and passwordless_guest_fallback_allowed:
        return _set_dashboard_principal(request, guest_principal())

    if auth_state.active_local_password_users == 0 and settings.totp_required_on_login:
        logger.warning(
            "dashboard_auth_migration_inconsistency no active local password user"
            " while totp_required_on_login=true metric=dashboard_auth_migration_inconsistency"
        )

    raise DashboardAuthError("Authentication is required")


async def require_dashboard_write_access(request: Request) -> DashboardPrincipal:
    principal = await validate_dashboard_session(request)
    if not principal.can(DashboardPermission.WRITE):
        raise DashboardPermissionError(
            "Read-only dashboard access cannot modify dashboard state",
            code="read_only_access",
        )
    return principal


@dataclass(frozen=True, slots=True)
class PermissionRequirement:
    """Declares the permission a route dependency enforces.

    Attached to the dependency callables produced by
    :func:`require_dashboard_permission` so route-authorization audits can read
    the requirement without invoking the dependency.
    """

    permission: Permission
    minimum_scope: Scope = Scope.ALL


def ensure_dashboard_permission(
    principal: DashboardPrincipal,
    permission: Permission,
    *,
    minimum_scope: Scope = Scope.ALL,
) -> None:
    if principal.has(permission, minimum_scope=minimum_scope):
        return
    raise DashboardPermissionError(
        f"Dashboard permission '{permission.value}' is required",
        code="permission_required",
        param=permission.value,
    )


_SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})


def recorded_step_up(request: Request, user: DashboardUser) -> int | None:
    """The most recent step-up this request carries for ``user``, wherever it rides.

    A trusted-header account has no session cookie, so its step-up rides in
    the separate step-up cookie — or in a fallback password session of the
    same account that happens to accompany the request. Both are bound to the
    account's ``session_generation``, so revoking its sessions or resetting
    its TOTP voids the proof instead of leaving it usable for the rest of the
    window. Enforcement and the session response read the same sources, so
    what the dashboard reports is what the gate accepts.
    """

    from_cookie = get_step_up_cookie_store().get(
        request.cookies.get(STEP_UP_COOKIE),
        user_id=user.id,
        session_generation=user.session_generation,
    )
    state = get_dashboard_session_store().get(request.cookies.get(DASHBOARD_SESSION_COOKIE))
    from_session = (
        state.step_up_verified_at
        if state is not None
        and state.is_user
        and state.user_id == user.id
        and state.password_verified
        and state.session_generation == user.session_generation
        else None
    )
    recorded = [value for value in (from_cookie, from_session) if value is not None]
    return max(recorded) if recorded else None


async def ensure_step_up(request: Request, principal: DashboardPrincipal, permission: Permission) -> None:
    """Require a fresh step-up (PLAN §5 H5) before a mutation guarded by ``permission``.

    Reads are never gated. Principals without an account (the implicit local
    admin, the disabled-auth principal) have no credential to re-verify and are
    exempt; every account is held to it, whatever provider signed it in. A
    stale or missing step-up answers ``403 step_up_required`` naming the
    factors the account can present; an account with no factor at all answers
    ``403 step_up_unavailable``.
    """

    if request.method in _SAFE_METHODS or principal.user_id is None:
        return
    user = await get_dashboard_users_cache().get_user(principal.user_id)
    # The principal's own claim came from a cookie this request already
    # validated against the account's generation; the other sources are
    # checked against it inside recorded_step_up.
    recorded = [principal.step_up_verified_at] if principal.step_up_verified_at is not None else []
    if user is not None:
        recorded += [value for value in (recorded_step_up(request, user),) if value is not None]
    if recorded and is_step_up_fresh(max(recorded), now=session_clock()):
        return
    methods = step_up_methods(user) if user is not None else []
    if not methods:
        raise DashboardPermissionError(STEP_UP_UNAVAILABLE_MESSAGE, code="step_up_unavailable", param=permission.value)
    raise DashboardPermissionError(
        "Confirm your identity to continue",
        code="step_up_required",
        param=permission.value,
        details={"methods": list(methods)},
    )


class DashboardPermissionDependency:
    """Route dependency: validate the dashboard session, then enforce one permission.

    Mutations guarded by a :data:`STEP_UP_PERMISSIONS` member also require a
    recent step-up. Instances are callable so FastAPI can resolve them with
    ``Depends`` while route-authorization audits read :attr:`requirement`
    without invoking them.
    """

    __slots__ = ("requirement",)

    def __init__(self, requirement: PermissionRequirement) -> None:
        self.requirement = requirement

    async def __call__(self, request: Request) -> DashboardPrincipal:
        principal = await validate_dashboard_session(request)
        ensure_dashboard_permission(
            principal,
            self.requirement.permission,
            minimum_scope=self.requirement.minimum_scope,
        )
        if self.requirement.permission in STEP_UP_PERMISSIONS:
            await ensure_step_up(request, principal, self.requirement.permission)
        return principal


@lru_cache(maxsize=None)
def _dependency_for(requirement: PermissionRequirement) -> DashboardPermissionDependency:
    return DashboardPermissionDependency(requirement)


def require_dashboard_permission(
    permission: Permission,
    *,
    minimum_scope: Scope = Scope.ALL,
) -> DashboardPermissionDependency:
    """Return the shared dependency enforcing ``permission`` at ``minimum_scope``.

    Cached per :class:`PermissionRequirement` (not per call spelling) so every
    router shares one callable per requirement, keeping dependency overrides
    and OpenAPI stable.
    """

    return _dependency_for(PermissionRequirement(permission=permission, minimum_scope=minimum_scope))


def ensure_dashboard_admin_access(principal: DashboardPrincipal) -> None:
    """Backward-compatible alias for the ``conversations:read`` requirement."""

    ensure_dashboard_permission(principal, Permission.CONVERSATIONS_READ)


async def require_dashboard_admin_access(request: Request) -> DashboardPrincipal:
    """Backward-compatible alias for ``require_dashboard_permission(CONVERSATIONS_READ)``."""

    return await require_dashboard_permission(Permission.CONVERSATIONS_READ)(request)


def get_dashboard_request_auth_mode() -> DashboardAuthMode:
    from app.core.config.settings import get_settings

    return get_settings().dashboard_auth_mode


def _is_proxy_unauthenticated_socket_peer_allowed(request: HTTPConnection) -> bool:
    socket_host = raw_socket_peer_host(request)
    if socket_host is None:
        return False

    try:
        socket_ip = ip_address(socket_host)
    except ValueError:
        return False

    configured_cidrs = get_settings().proxy_unauthenticated_client_cidrs
    return any(socket_ip in ip_network(cidr, strict=False) for cidr in configured_cidrs)


# --- Codex usage caller identity auth ---


async def validate_codex_usage_identity(request: Request) -> ApiKeyData | None:
    token = _extract_bearer_token(request.headers.get("Authorization"))
    if not token:
        raise ProxyAuthError("Missing ChatGPT token in Authorization header")

    raw_account_id = request.headers.get("chatgpt-account-id")
    account_id = raw_account_id.strip() if raw_account_id else ""
    if not account_id:
        if token.startswith("sk-clb-"):
            return await _validate_api_key_token(token)
        raise ProxyAuthError("Missing chatgpt-account-id header")

    async with get_background_session() as session:
        accounts_repo = AccountsRepository(session)
        account = await accounts_repo.get_active_by_chatgpt_account_id(account_id)
        if account is None:
            raise ProxyAuthError("Unknown or inactive chatgpt-account-id")
        local_account_id = account.id
        local_account_email = account.email
        try:
            route = await resolve_upstream_route(
                session,
                account_id=local_account_id,
                operation="usage_identity",
                scope="account",
                encryptor=TokenEncryptor(),
            )
        except UpstreamProxyRouteError as exc:
            raise ProxyUpstreamError("Unable to resolve upstream proxy route for ChatGPT credentials") from exc

    try:
        usage_payload = await fetch_usage(
            access_token=token,
            account_id=account_id,
            route=route,
            allow_direct_egress=route is None,
        )
    except UsageFetchError as exc:
        if exc.status_code == 429:
            from app.core.exceptions import ProxyRateLimitError

            raise ProxyRateLimitError(exc.message) from exc
        if exc.status_code in (401, 403):
            raise ProxyAuthError("Invalid ChatGPT token or chatgpt-account-id") from exc
        raise ProxyUpstreamError("Unable to validate ChatGPT credentials at this time") from exc
    if usage_payload is not None and (usage_payload.workspace_id or usage_payload.workspace_label):
        expected_account_id = generate_unique_account_id(
            account_id,
            local_account_email,
            usage_payload.workspace_id,
            usage_payload.workspace_label,
        )
        async with get_background_session() as session:
            accounts_repo = AccountsRepository(session)
            workspace_account = await accounts_repo.get_by_id(expected_account_id)
            if workspace_account is not None and workspace_account.chatgpt_account_id == account_id:
                if workspace_account.status in _CODEX_USAGE_IDENTITY_INACTIVE_WORKSPACE_STATUSES:
                    raise ProxyAuthError("Unknown or inactive chatgpt-account-id")
                local_account_id = workspace_account.id
                try:
                    route = await resolve_upstream_route(
                        session,
                        account_id=local_account_id,
                        operation="usage_identity",
                        scope="account",
                        encryptor=TokenEncryptor(),
                    )
                except UpstreamProxyRouteError as exc:
                    raise ProxyUpstreamError("Unable to resolve upstream proxy route for ChatGPT credentials") from exc
    request.state.codex_usage_identity_access_token = token
    request.state.codex_usage_identity_chatgpt_account_id = account_id
    request.state.codex_usage_identity_account_id = local_account_id
    request.state.codex_usage_identity_route = route
    request.state.codex_usage_identity_payload = usage_payload
    return None


async def validate_codex_provider_usage_identity(request: Request) -> ApiKeyData | None:
    """Bind provider capability intent to a proxy API-key principal before usage I/O."""

    if request.headers.getlist(CODEX_LB_REQUIRED_CAPABILITY_HEADER):
        return await validate_required_proxy_api_key_authorization(request.headers.get("authorization"))
    return await validate_codex_usage_identity(request)


def _extract_bearer_token(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    prefix = "bearer "
    value = authorization.strip()
    if not value.lower().startswith(prefix):
        return None
    token = value[len(prefix) :].strip()
    if not token:
        return None
    return token
