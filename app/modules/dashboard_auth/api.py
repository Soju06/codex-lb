from __future__ import annotations

import hashlib
import logging
from time import time

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse

from app.core.audit.service import AuditActor, AuditService, AuditTarget
from app.core.auth.dashboard_access import (
    ADMIN_GRANTS,
    GUEST_GRANTS,
    DashboardPrincipal,
    DashboardRole,
    Permission,
    PresetRoleSlug,
    permission_strings,
)
from app.core.auth.dashboard_mode import (
    DashboardAuthMode,
    get_dashboard_request_auth,
    password_management_enabled,
)
from app.core.auth.dashboard_session_ttl import (
    resolve_admin_dashboard_session_ttl_seconds,
    resolve_dashboard_session_ttl_seconds,
)
from app.core.auth.dashboard_users_cache import get_dashboard_users_cache
from app.core.auth.dependencies import (
    ensure_dashboard_permission,
    recorded_step_up,
    require_dashboard_permission,
    set_dashboard_error_format,
    validate_dashboard_session,
)
from app.core.auth.external_identity import ExternalResolution, resolve_trusted_header_request
from app.core.auth.step_up import STEP_UP_COOKIE, STEP_UP_UNAVAILABLE_MESSAGE, step_up_expires_at
from app.core.bootstrap import (
    ensure_auto_bootstrap_token,
    get_bootstrap_validation_status,
    has_active_bootstrap_token,
    log_bootstrap_token,
)
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.exceptions import (
    DashboardAuthError,
    DashboardBadRequestError,
    DashboardConflictError,
    DashboardNotFoundError,
    DashboardPermissionError,
    DashboardRateLimitError,
    DashboardValidationError,
)
from app.core.rate_limiter.db_rate_limiter import DatabaseRateLimiter
from app.core.request_locality import is_local_request
from app.db.models import DashboardUser
from app.dependencies import (
    DashboardAuthContext,
    DashboardUsersContext,
    get_dashboard_auth_context,
    get_dashboard_users_context,
)
from app.modules.dashboard_auth.schemas import (
    DashboardAuthSessionResponse,
    DashboardMeResponse,
    GuestLoginRequest,
    GuestPasswordSetRequest,
    InviteAcceptRequest,
    InviteDescriptionResponse,
    PasswordChangeRequest,
    PasswordLoginRequest,
    PasswordRemoveRequest,
    PasswordSetupRequest,
    StepUpRequest,
    StepUpResponse,
    TotpSetupConfirmRequest,
    TotpSetupStartResponse,
    TotpVerifyRequest,
)
from app.modules.dashboard_auth.service import (
    DASHBOARD_SESSION_COOKIE,
    DashboardSessionState,
    GuestAccessDisabledError,
    InvalidCredentialsError,
    OtherUsersExistError,
    PasswordAlreadyConfiguredError,
    PasswordNotConfiguredError,
    PasswordSessionRequiredError,
    ResolvedUserSession,
    SessionDescription,
    StepUpUnavailableError,
    TotpAlreadyConfiguredError,
    TotpEnrollmentRequiredError,
    TotpInvalidCodeError,
    TotpInvalidSetupError,
    TotpNotConfiguredError,
    TotpVerificationRequiredError,
    UsernameRequiredError,
    assignable_role_ids,
    get_dashboard_session_store,
    get_guest_password_rate_limiter,
    get_invite_accept_rate_limiter,
    get_invite_accept_token_rate_limiter,
    get_invite_lookup_rate_limiter,
    get_login_failed_audit_rate_limiter,
    get_password_address_rate_limiter,
    get_password_rate_limiter,
    get_step_up_cookie_store,
    get_totp_rate_limiter,
    hash_password,
    log_login_failed,
    session_clock,
    session_user,
    step_up_state,
)
from app.modules.dashboard_roles.service import resolve_role_grants
from app.modules.dashboard_users.api import mapped_user_errors
from app.modules.dashboard_users.break_glass import (
    LastBreakGlassProtectedError,
    local_login_admits,
)
from app.modules.dashboard_users.credentials import CredentialRequiredError
from app.modules.dashboard_users.schemas import ProfileUpdateRequest
from app.modules.dashboard_users.service import InviteNotFoundError, UsernameLockedError, invite_token_hash

router = APIRouter(
    prefix="/api/dashboard-auth",
    tags=["dashboard"],
    dependencies=[Depends(set_dashboard_error_format)],
)

logger = logging.getLogger(__name__)


def _client_host(request: Request) -> str | None:
    return request.client.host if request.client else None


def _session_client_key(request: Request, *, prefix: str) -> str:
    return f"{prefix}:{request.client.host if request.client else 'unknown'}"


def _password_login_address_key(request: Request) -> str:
    """The coarse per-address failed-login budget (PLAN §4.4: ``password_login:{ip}``).

    This is the endpoint's actual ceiling. The per-account bucket below is
    deliberately local — a limit for one username at one address bars nothing
    else — which on its own would let a single address mint an unlimited number
    of buckets by inventing usernames, each of them costing a blocking
    password-hash comparison. The two are added, never swapped.
    """

    return _session_client_key(request, prefix="password_login")


def _password_login_key(request: Request, username: str | None) -> str:
    """The failed-login budget: one bucket per (username, address) pair.

    Keyed on the *normalized* username so case variants share a budget, and
    hashed so the rate-limit table never holds a username in the clear. The
    pair is what makes a limit local: eight failures for one account from one
    address bar neither that account elsewhere nor another account from the
    same address.
    """

    digest = hashlib.sha256((username or "").encode("utf-8")).hexdigest()
    return f"{_password_login_address_key(request)}:{digest}"


def _session_ttl_seconds(request: Request, user: DashboardUser, configured_ttl_seconds: int) -> int:
    if user.role.slug == PresetRoleSlug.ADMIN.value:
        return resolve_admin_dashboard_session_ttl_seconds(request, configured_ttl_seconds)
    return resolve_dashboard_session_ttl_seconds(request, configured_ttl_seconds)


async def _create_user_session(
    request: Request,
    user: DashboardUser,
    *,
    totp_verified: bool,
    auth_method: str,
    max_ttl_seconds: int | None = None,
    step_up_verified_at: int | None = None,
) -> tuple[str, int]:
    settings = await get_settings_cache().get()
    ttl_seconds = _session_ttl_seconds(request, user, settings.dashboard_session_ttl_seconds)
    if max_ttl_seconds is not None:
        ttl_seconds = max(1, min(ttl_seconds, max_ttl_seconds))
    session_id = get_dashboard_session_store().create_user_session(
        user.id,
        user.session_generation,
        password_verified=True,
        totp_verified=totp_verified,
        ttl_seconds=ttl_seconds,
        auth_method=auth_method,
        step_up_verified_at=step_up_verified_at,
        break_glass=user.is_break_glass,
    )
    return session_id, ttl_seconds


async def _create_guest_session(request: Request, *, guest_session_generation: int) -> tuple[str, int]:
    settings = await get_settings_cache().get()
    ttl_seconds = resolve_dashboard_session_ttl_seconds(request, settings.dashboard_session_ttl_seconds)
    session_id = get_dashboard_session_store().create_guest_session(
        ttl_seconds=ttl_seconds,
        guest_session_generation=guest_session_generation,
    )
    return session_id, ttl_seconds


#: Fields that describe a signed-in account; stripped whenever the response is
#: served to a caller that is not authenticated as one.
_UNAUTHENTICATED_ACCOUNT_FIELDS: dict[str, object] = {
    "user": None,
    "access_summary": None,
    "assignable_role_ids": [],
}


async def _decorate_session_response(
    description: SessionDescription,
    *,
    request: Request,
    context: DashboardAuthContext,
    force_authenticated: bool = False,
) -> DashboardAuthSessionResponse:
    response, resolved = description.response, description.resolved
    request_auth = get_dashboard_request_auth(request)
    auth_mode = get_settings().dashboard_auth_mode
    has_pwd = resolved is not None and resolved.state.password_verified
    totp_pending = (
        has_pwd and resolved is not None and response.totp_required_on_login and not resolved.state.totp_verified
    )
    fully_authorized = has_pwd and not totp_pending and response.password_required
    # Behind a reverse proxy the cookie only counts as a fallback while the
    # local login policy admits the account -- the same call the session gate
    # makes, so ``password_session_active`` never advertises a fallback the
    # gate would refuse.
    fallback_admitted = resolved is None or local_login_admits(
        resolved.user, (await get_settings_cache().get()).local_login_policy
    )
    fallback_authorized = fully_authorized and fallback_admitted

    if request_auth is None:
        update: dict[str, object] = {
            "auth_mode": auth_mode,
            "password_management_enabled": password_management_enabled(auth_mode),
            "password_session_active": (
                fallback_authorized if auth_mode == DashboardAuthMode.TRUSTED_HEADER else fully_authorized
            ),
        }
        # Without the header only a password session gets in. When no account
        # holds a password (proxy-created accounts do not count) there is no
        # form to show: the client renders the reverse-proxy notice.
        if auth_mode == DashboardAuthMode.TRUSTED_HEADER and not fallback_authorized and not totp_pending:
            local_password_users = (await get_dashboard_users_cache().local_auth_state()).active_local_password_users
            if local_password_users == 0 or not response.password_required:
                update["authenticated"] = False
                update["password_required"] = False
                update.update(_UNAUTHENTICATED_ACCOUNT_FIELDS)
        return response.model_copy(update=update)

    if request_auth.mode == DashboardAuthMode.TRUSTED_HEADER:
        resolution = await resolve_trusted_header_request(request, request_auth)
        if resolution is None:
            # Header present but the provider is inactive: the dependency answers
            # proxy_auth_required, so the session must not describe an admin.
            return response.model_copy(
                update={
                    "authenticated": False,
                    "password_required": False,
                    "auth_mode": DashboardAuthMode.TRUSTED_HEADER,
                    "password_session_active": fallback_authorized,
                    **_UNAUTHENTICATED_ACCOUNT_FIELDS,
                }
            )
        if resolution.user is None and has_pwd and fallback_admitted:
            # Refused identity but a password cookie rides along: describe that
            # session (the break-glass admin stays reachable behind the proxy).
            return response.model_copy(
                update={
                    "auth_mode": DashboardAuthMode.TRUSTED_HEADER,
                    "password_management_enabled": True,
                    "password_session_active": fallback_authorized,
                }
            )
        return await _trusted_header_session_response(
            response, resolution, request=request, context=context, password_session_active=fallback_authorized
        )

    # Disabled auth: the implicit admin holds every permission.
    return response.model_copy(
        update={
            "authenticated": force_authenticated or response.authenticated,
            "totp_required_on_login": totp_pending,
            "auth_mode": request_auth.mode,
            "password_management_enabled": password_management_enabled(request_auth.mode),
            "password_session_active": fully_authorized,
            "role": DashboardRole.ADMIN,
            "permissions": permission_strings(ADMIN_GRANTS),
            "totp_enrollment_required": False,
            "access_summary": await context.service.access_summary(),
            "assignable_role_ids": assignable_role_ids(),
        }
    )


async def _trusted_header_session_response(
    response: DashboardAuthSessionResponse,
    resolution: ExternalResolution,
    *,
    request: Request,
    context: DashboardAuthContext,
    password_session_active: bool,
) -> DashboardAuthSessionResponse:
    """The session as the proxy-asserted account sees it, or the "not ready" state for a refused identity.

    ``password_session_active`` reports whether a fallback password cookie also
    rode along, so the settings page keeps gating password management on it.
    The account's step-up rides in the step-up cookie (no session cookie exists).
    """

    user = resolution.user
    if user is None:
        return response.model_copy(
            update={
                "authenticated": False,
                "auth_mode": DashboardAuthMode.TRUSTED_HEADER,
                "totp_required_on_login": False,
                "totp_configured": False,
                "password_session_active": password_session_active,
                "auth_method": None,
                "totp_enrollment_required": False,
                "login": response.login.model_copy(update={"pending_identity": True}) if response.login else None,
                **_UNAUTHENTICATED_ACCOUNT_FIELDS,
            }
        )
    grants = resolve_role_grants(user.role)
    manages_users = Permission.USERS_MANAGE in grants
    return response.model_copy(
        update={
            "authenticated": True,
            # An account with an identity exists (this one), so sign-in is required
            # even when the cached auth state predates its just-in-time creation.
            "password_required": True,
            "auth_mode": DashboardAuthMode.TRUSTED_HEADER,
            "password_management_enabled": True,
            "password_session_active": password_session_active,
            "totp_required_on_login": False,
            "totp_configured": user.totp_secret_encrypted is not None,
            "role": DashboardRole.ADMIN,
            "permissions": permission_strings(grants),
            "user": session_user(user),
            "auth_method": DashboardAuthMode.TRUSTED_HEADER.value,
            "must_change_password": False,
            "totp_enrollment_required": False,
            "access_summary": await context.service.access_summary() if manages_users else None,
            "assignable_role_ids": assignable_role_ids() if manages_users else [],
            "step_up": step_up_state(user, verified_at=recorded_step_up(request, user)),
        }
    )


def _guest_overrides() -> dict[str, object]:
    return {
        "bootstrap_required": False,
        "bootstrap_token_configured": False,
        "role": DashboardRole.GUEST,
        "permissions": permission_strings(GUEST_GRANTS),
        "guest_access_enabled": True,
        "password_session_active": False,
        "user": None,
        "auth_method": None,
        "must_change_password": False,
        "totp_enrollment_required": False,
        "access_summary": None,
        "assignable_role_ids": [],
    }


def _public_guest_response(response: DashboardAuthSessionResponse) -> DashboardAuthSessionResponse:
    return response.model_copy(update={**_guest_overrides(), "authenticated": True, "guest_password_required": False})


def _guest_login_required_response(response: DashboardAuthSessionResponse) -> DashboardAuthSessionResponse:
    return response.model_copy(update={**_guest_overrides(), "authenticated": False, "guest_password_required": True})


async def _require_password_session(request: Request, context: DashboardAuthContext) -> ResolvedUserSession:
    try:
        return await context.service.require_password_session(request.cookies.get(DASHBOARD_SESSION_COOKIE))
    except PasswordSessionRequiredError as exc:
        raise DashboardAuthError("Authentication is required") from exc


async def _resolve_account_session(request: Request, context: DashboardAuthContext) -> ResolvedUserSession:
    """The signed-in account for the TOTP routes: the trusted-header account when the header is present,
    else the password session — the same precedence as the session dependency, so the account that
    enrols is the account the dashboard acts as.

    A trusted-header account has no session cookie; it gets a stand-in state
    (``password_verified=False``, ``auth_method=trusted_header``) so the same
    routes can enrol its TOTP secret — the only step-up method such an account
    can have without a local password.
    """

    header_account = await _header_account_session(request)
    if header_account is not None:
        return header_account
    try:
        return await context.service.require_password_session(request.cookies.get(DASHBOARD_SESSION_COOKIE))
    except PasswordSessionRequiredError as exc:
        raise DashboardAuthError(str(exc)) from exc


async def _header_account_session(request: Request) -> ResolvedUserSession | None:
    request_auth = get_dashboard_request_auth(request)
    if request_auth is None or request_auth.mode != DashboardAuthMode.TRUSTED_HEADER:
        return None
    resolution = await resolve_trusted_header_request(request, request_auth)
    if resolution is None or resolution.user is None:
        return None
    now = session_clock()
    state = DashboardSessionState(
        expires_at=now,
        issued_at=now,
        kind="user",
        user_id=resolution.user.id,
        session_generation=resolution.user.session_generation,
        auth_method=DashboardAuthMode.TRUSTED_HEADER.value,
    )
    return ResolvedUserSession(user=resolution.user, state=state)


def _is_header_account(resolved: ResolvedUserSession) -> bool:
    return not resolved.state.password_verified


async def _require_management_session(
    request: Request, context: DashboardAuthContext, *, allow_unenrolled: bool = False
) -> ResolvedUserSession:
    """A password session that also passed TOTP when the install requires it.

    Password/TOTP management is refused outright in the auth-bypass modes. An
    account that still has to enrol a TOTP secret is refused with 403 unless the
    route is one of the self-service routes (``allow_unenrolled``).
    """

    _ensure_password_management_enabled(request)
    try:
        return await context.service.require_management_session(
            request.cookies.get(DASHBOARD_SESSION_COOKIE), allow_unenrolled=allow_unenrolled
        )
    except PasswordSessionRequiredError as exc:
        raise DashboardAuthError("Authentication is required") from exc
    except TotpVerificationRequiredError as exc:
        raise DashboardAuthError(str(exc), code="totp_required") from exc
    except TotpEnrollmentRequiredError as exc:
        raise _enrollment_required(exc) from exc


def _enrollment_required(exc: TotpEnrollmentRequiredError) -> DashboardPermissionError:
    return DashboardPermissionError(str(exc), code="totp_enrollment_required")


def _ensure_password_management_enabled(request: Request) -> None:
    request_auth = get_dashboard_request_auth(request)
    if request_auth is not None and not password_management_enabled(request_auth.mode):
        raise DashboardBadRequestError(
            "Password and TOTP management is disabled while dashboard auth is bypassed",
            code="password_management_disabled",
        )


async def _invalidate_auth_caches() -> None:
    await get_settings_cache().invalidate()
    await get_dashboard_users_cache().invalidate()


# bcrypt enforces a hard 72-byte limit on the input password; anything longer
# raises ``ValueError`` from ``bcrypt.hashpw`` and surfaces as a 500 to the
# client. Validate the encoded length here so the API returns a clear 400.
_MAX_PASSWORD_BYTES = 72


def _validate_password_length(password: str) -> None:
    if len(password) < 8:
        raise DashboardValidationError("Password must be at least 8 characters")
    if len(password.encode("utf-8")) > _MAX_PASSWORD_BYTES:
        raise DashboardValidationError(
            f"Password must be at most {_MAX_PASSWORD_BYTES} bytes when encoded as UTF-8. "
            "Note that multi-byte characters (e.g. emoji, non-ASCII letters) count for more than one byte.",
            code="password_too_long",
        )


def _rate_limit_error(exc: DashboardRateLimitError, *, code: str) -> DashboardRateLimitError:
    return DashboardRateLimitError(
        f"Too many attempts. Try again in {exc.retry_after} seconds.",
        retry_after=exc.retry_after,
        code=code,
    )


@router.get("/session", response_model=DashboardAuthSessionResponse)
async def get_dashboard_auth_session(
    request: Request,
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> DashboardAuthSessionResponse:
    session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
    description = await context.service.describe_session(session_id)
    decorated = await _decorate_session_response(
        description, request=request, context=context, force_authenticated=True
    )
    if decorated.auth_mode != DashboardAuthMode.STANDARD:
        return decorated
    if decorated.password_required or is_local_request(request):
        return decorated
    current_settings = await context.repository.get_settings()
    if current_settings.guest_access_enabled:
        if current_settings.guest_password_hash is None:
            return _public_guest_response(decorated)
        if decorated.authenticated and decorated.role == DashboardRole.GUEST:
            session_state = get_dashboard_session_store().get(session_id)
            if (
                session_state is not None
                and session_state.is_guest
                and session_state.guest_session_generation == current_settings.guest_session_generation
            ):
                return decorated
        return _guest_login_required_response(decorated)
    bootstrap_token_configured = await has_active_bootstrap_token()
    return decorated.model_copy(
        update={
            "authenticated": False,
            "bootstrap_required": True,
            "bootstrap_token_configured": bootstrap_token_configured,
            **_UNAUTHENTICATED_ACCOUNT_FIELDS,
        }
    )


@router.post("/password/setup", response_model=DashboardAuthSessionResponse)
async def setup_password(
    request: Request,
    payload: PasswordSetupRequest = Body(...),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> DashboardAuthSessionResponse | JSONResponse:
    settings = get_settings()
    auth_state = await context.repository.get_local_auth_state()
    if settings.dashboard_auth_mode == DashboardAuthMode.DISABLED:
        raise DashboardBadRequestError(
            "Password management is disabled while dashboard auth is bypassed",
            code="password_management_disabled",
        )
    if settings.dashboard_auth_mode == DashboardAuthMode.TRUSTED_HEADER:
        # Behind the proxy the break-glass admin is created by an account that
        # manages users (a header-resolved account or a password session), never
        # by a bare request: without the header this is proxy_auth_required, with
        # a refused identity identity_not_provisioned, as for every other route.
        principal = await validate_dashboard_session(request)
        ensure_dashboard_permission(principal, Permission.USERS_MANAGE)
    elif not auth_state.requires_auth and not is_local_request(request):
        submitted_bootstrap_token = (payload.bootstrap_token or "").strip()
        validation_status = await get_bootstrap_validation_status(submitted_bootstrap_token)
        if validation_status == "unavailable":
            raise DashboardAuthError(
                "Remote bootstrap is disabled until CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN is configured.",
                code="bootstrap_unavailable",
            )
        if validation_status == "password_already_configured":
            raise DashboardConflictError("Password is already configured", code="password_already_configured")
        if validation_status != "valid":
            raise DashboardAuthError("Invalid dashboard bootstrap token.", code="invalid_bootstrap_token")
    password = payload.password.strip()
    _validate_password_length(password)
    try:
        user = await context.service.setup_password(password)
    except PasswordAlreadyConfiguredError as exc:
        raise DashboardConflictError(str(exc), code="password_already_configured") from exc

    await _invalidate_auth_caches()
    return await _issue_user_session_response(request, context, user, totp_verified=False, auth_method="password")


async def _issue_user_session_response(
    request: Request,
    context: DashboardAuthContext,
    user: DashboardUser,
    *,
    totp_verified: bool,
    auth_method: str,
) -> JSONResponse:
    # Every caller just verified a password. That is a complete step-up only
    # for an account without a TOTP secret; otherwise ``/totp/verify`` mints it.
    session_id, session_ttl_seconds = await _create_user_session(
        request,
        user,
        totp_verified=totp_verified,
        auth_method=auth_method,
        step_up_verified_at=session_clock() if user.totp_secret_encrypted is None else None,
    )
    response = await _decorate_session_response(
        await context.service.describe_session(session_id), request=request, context=context
    )
    json_response = JSONResponse(status_code=200, content=response.model_dump(by_alias=True))
    _set_session_cookie(json_response, session_id, request, max_age_seconds=session_ttl_seconds)
    return json_response


@router.post("/guest/login", response_model=DashboardAuthSessionResponse)
async def login_guest(
    request: Request,
    payload: GuestLoginRequest | None = Body(default=None),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> DashboardAuthSessionResponse | JSONResponse:
    settings = await get_settings_cache().get()
    if not settings.guest_access_enabled:
        raise DashboardBadRequestError("Guest access is disabled", code="guest_access_disabled")
    if (
        get_settings().dashboard_auth_mode == DashboardAuthMode.TRUSTED_HEADER
        and get_dashboard_request_auth(request) is None
    ):
        raise DashboardAuthError("Reverse proxy authentication is required", code="proxy_auth_required")

    limiter = get_guest_password_rate_limiter()
    rate_key = _session_client_key(request, prefix="guest_login")
    if settings.guest_password_hash is not None:
        try:
            await limiter.check_and_increment(rate_key, context.session)
        except DashboardRateLimitError as exc:
            raise _rate_limit_error(exc, code="guest_password_rate_limited") from exc

    try:
        verification = await context.service.verify_guest_password(
            None if payload is None else payload.password,
            actor_ip=_client_host(request),
        )
    except GuestAccessDisabledError as exc:
        raise DashboardBadRequestError(str(exc), code="guest_access_disabled") from exc
    except InvalidCredentialsError as exc:
        raise DashboardAuthError(str(exc), code="invalid_credentials") from exc

    await limiter.clear_for_key(rate_key, context.session)

    # Stamp the generation of the very settings row the credential was checked
    # against (one database read, not the 5 s cache and not a second read): a
    # guest password enabled between check and mint must invalidate this cookie.
    session_id, session_ttl_seconds = await _create_guest_session(
        request, guest_session_generation=verification.guest_session_generation
    )
    response = await _decorate_session_response(
        await context.service.describe_session(session_id), request=request, context=context
    )
    json_response = JSONResponse(status_code=200, content=response.model_dump(by_alias=True))
    _set_session_cookie(json_response, session_id, request, max_age_seconds=session_ttl_seconds)
    return json_response


@router.post("/password/login", response_model=DashboardAuthSessionResponse)
async def login_password(
    request: Request,
    payload: PasswordLoginRequest = Body(...),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> DashboardAuthSessionResponse | JSONResponse:
    if get_settings().dashboard_auth_mode == DashboardAuthMode.DISABLED:
        raise DashboardBadRequestError(
            "Password login is disabled while dashboard auth is bypassed",
            code="password_management_disabled",
        )

    # Target resolution happens before any limiter so a missing username on a
    # multi-user install (422) and an unconfigured install (400) never spend budget.
    try:
        target = await context.service.resolve_login_target(payload.username)
    except PasswordNotConfiguredError as exc:
        raise DashboardBadRequestError(str(exc), code="password_not_configured") from exc
    except UsernameRequiredError as exc:
        await _audit_username_required(request, context)
        raise DashboardValidationError(str(exc), code="username_required") from exc

    # Two buckets, both spent by every attempt: the coarse per-address ceiling
    # that bounds the endpoint, and the per-(address, username) budget that
    # keeps one account's failures from barring another. No account is exempt
    # from either. PLAN §4.4 exempted the emergency account from a limit it
    # assumed was keyed on the username alone, which an attacker could exhaust
    # from anywhere; both keys carry the client address, so an attacker
    # hammering that username from their own address cannot touch the operator
    # signing in from a different one. T13/T15 ("no remotely triggerable
    # account lockout") therefore hold without an exemption -- and the
    # exemption itself was an oracle, because whether the ninth failure for a
    # username answers 429 or 401 named the emergency account to an
    # unauthenticated caller.
    address_limiter, address_key = get_password_address_rate_limiter(), _password_login_address_key(request)
    limiter = get_password_rate_limiter()
    rate_key = _password_login_key(request, target.username)
    try:
        await address_limiter.check_and_increment(address_key, context.session)
        await limiter.check_and_increment(rate_key, context.session)
    except DashboardRateLimitError as exc:
        raise _rate_limit_error(exc, code="password_rate_limited") from exc

    try:
        user = await context.service.verify_user_password(target, payload.password, actor_ip=_client_host(request))
    except InvalidCredentialsError as exc:
        raise DashboardAuthError(str(exc), code="invalid_credentials") from exc

    # Only the per-account bucket is cleared. The coarse address bucket is the
    # endpoint's only ceiling on *how many* usernames one address may try, and
    # clearing it on success would hand that ceiling to anyone holding a single
    # valid account: seven guesses at someone else's username, one sign-in of
    # their own, seven more, for as long as they like. It is left to expire on
    # its own window instead, which the person who just signed in never notices
    # (60/60 s against one sign-in).
    await limiter.clear_for_key(rate_key, context.session)
    # Only last_login_at changed; nothing on the auth path reads it, so the users
    # cache stays warm.

    return await _issue_user_session_response(request, context, user, totp_verified=False, auth_method="password")


async def _audit_username_required(request: Request, context: DashboardAuthContext) -> None:
    """Audit a ``username_required`` refusal without letting it become an unbounded row source.

    The refusal itself never spends password budget, so a client that is
    already at the password limit gets no row, and a dedicated per-client
    budget (same 8/60 s shape, own counter) bounds the rows a client can add
    without ever touching the password limiter's counter.

    The budget it reads is the per-address one, which is the bucket ordinary
    logins from this client actually increment; a per-username key would be a
    counter nothing ever advances, so the guard would let every refusal
    through.
    """

    try:
        await get_password_address_rate_limiter().check(_password_login_address_key(request), context.session)
        await get_login_failed_audit_rate_limiter().check_and_increment(
            _session_client_key(request, prefix="login_failed_audit"), context.session
        )
    except DashboardRateLimitError:
        return
    log_login_failed(_client_host(request), "password", "username_required")


@router.post("/password/change")
async def change_password(
    request: Request,
    payload: PasswordChangeRequest = Body(...),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> JSONResponse:
    resolved = await _require_management_session(request, context, allow_unenrolled=True)

    new_password = payload.new_password.strip()
    _validate_password_length(new_password)

    try:
        await context.service.change_password(
            resolved.user,
            payload.current_password,
            new_password,
            actor_ip=_client_host(request),
            auth_method=resolved.state.auth_method,
        )
    except PasswordNotConfiguredError as exc:
        raise DashboardBadRequestError(str(exc), code="password_not_configured") from exc
    except InvalidCredentialsError as exc:
        raise DashboardAuthError(str(exc), code="invalid_credentials") from exc

    await _invalidate_auth_caches()
    # Every other device is logged out by the generation bump; this caller stays
    # signed in through a fresh cookie that keeps the old session's remaining life.
    user = await context.repository.get_user_by_id(resolved.user.id)
    if user is None:
        raise DashboardAuthError("Authentication is required")
    remaining = max(1, resolved.state.expires_at - int(time()))
    session_id, session_ttl_seconds = await _create_user_session(
        request,
        user,
        totp_verified=resolved.state.totp_verified,
        auth_method=resolved.state.auth_method or "password",
        max_ttl_seconds=remaining,
        step_up_verified_at=resolved.state.step_up_verified_at,
    )
    response = JSONResponse(status_code=200, content={"status": "ok"})
    _set_session_cookie(response, session_id, request, max_age_seconds=session_ttl_seconds)
    return response


@router.post("/guest/password")
async def set_guest_password(
    payload: GuestPasswordSetRequest = Body(...),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
    _principal: DashboardPrincipal = Depends(require_dashboard_permission(Permission.SECURITY_WRITE)),
) -> JSONResponse:
    password = payload.password.strip()
    _validate_password_length(password)
    await context.service.set_guest_password(password)
    await get_settings_cache().invalidate()
    return JSONResponse(status_code=200, content={"status": "ok"})


@router.delete("/guest/password")
async def remove_guest_password(
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
    _principal: DashboardPrincipal = Depends(require_dashboard_permission(Permission.SECURITY_WRITE)),
) -> JSONResponse:
    await context.service.clear_guest_password()
    await get_settings_cache().invalidate()
    return JSONResponse(status_code=200, content={"status": "ok"})


@router.post("/guest/logout-all")
async def revoke_guest_sessions(
    request: Request,
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
    principal: DashboardPrincipal = Depends(require_dashboard_permission(Permission.SECURITY_WRITE)),
) -> JSONResponse:
    """Invalidate every outstanding guest session without touching guest settings."""

    await context.service.revoke_guest_sessions()
    await get_settings_cache().invalidate()
    AuditService.log_async(
        "guest_sessions_revoked",
        actor_ip=_client_host(request),
        actor=AuditActor.from_principal(principal),
        target=AuditTarget("settings", "guest_access"),
    )
    return JSONResponse(status_code=200, content={"status": "ok"})


@router.delete("/password")
async def remove_password(
    request: Request,
    payload: PasswordRemoveRequest = Body(...),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> JSONResponse:
    resolved = await _require_management_session(request, context)

    try:
        await context.service.remove_password(
            resolved.user,
            payload.password,
            actor_ip=_client_host(request),
            auth_method=resolved.state.auth_method,
        )
    except PasswordNotConfiguredError as exc:
        raise DashboardBadRequestError(str(exc), code="password_not_configured") from exc
    except InvalidCredentialsError as exc:
        raise DashboardAuthError(str(exc), code="invalid_credentials") from exc
    except OtherUsersExistError as exc:
        raise DashboardConflictError(str(exc), code="other_users_exist") from exc
    except CredentialRequiredError as exc:
        raise DashboardConflictError(str(exc), code="credential_required") from exc
    except LastBreakGlassProtectedError as exc:
        raise DashboardConflictError(str(exc), code="last_break_glass_protected") from exc

    await _invalidate_auth_caches()
    bootstrap_token = await ensure_auto_bootstrap_token()
    if bootstrap_token:
        log_bootstrap_token(logger, bootstrap_token, reason="password-removed")
    response = JSONResponse(status_code=200, content={"status": "ok"})
    response.delete_cookie(key=DASHBOARD_SESSION_COOKIE, path="/")
    return response


@router.post("/logout-all")
async def logout_everywhere(
    request: Request,
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> JSONResponse:
    """Revoke every session of the signed-in account, including this one."""

    resolved = await _require_password_session(request, context)
    await context.service.revoke_user_sessions(
        resolved.user, actor_ip=_client_host(request), auth_method=resolved.state.auth_method
    )
    await get_dashboard_users_cache().invalidate()
    response = JSONResponse(status_code=200, content={"status": "ok"})
    response.delete_cookie(key=DASHBOARD_SESSION_COOKIE, path="/")
    return response


@router.get("/me", response_model=DashboardMeResponse)
async def get_me(
    request: Request,
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> DashboardMeResponse:
    """The signed-in account. Guests and implicit admins have no account and get 401."""

    try:
        return await context.service.me(request.cookies.get(DASHBOARD_SESSION_COOKIE))
    except PasswordSessionRequiredError as exc:
        raise DashboardAuthError("A user account session is required", code="user_account_required") from exc
    except TotpVerificationRequiredError as exc:
        raise DashboardAuthError(str(exc), code="totp_required") from exc
    except TotpEnrollmentRequiredError as exc:
        raise _enrollment_required(exc) from exc


@router.patch("/me", response_model=DashboardMeResponse)
async def update_me(
    request: Request,
    payload: ProfileUpdateRequest = Body(...),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
    users: DashboardUsersContext = Depends(get_dashboard_users_context),
) -> DashboardMeResponse:
    """Self-service profile edit (display name, e-mail) for the signed-in account."""

    resolved = await _require_management_session(request, context, allow_unenrolled=True)
    with mapped_user_errors():
        await users.service.update_profile(resolved.user, payload, actor_ip=_client_host(request))
    return await context.service.me(request.cookies.get(DASHBOARD_SESSION_COOKIE))


@router.get("/invite/{token}", response_model=InviteDescriptionResponse)
async def describe_invite(
    token: str,
    request: Request,
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
    users: DashboardUsersContext = Depends(get_dashboard_users_context),
) -> InviteDescriptionResponse:
    """Unauthenticated: what the acceptance screen shows. Every invalid token is the same 404."""

    limiter = get_invite_lookup_rate_limiter()
    try:
        await limiter.check_and_increment(_session_client_key(request, prefix="invite_lookup"), context.session)
    except DashboardRateLimitError as exc:
        raise _rate_limit_error(exc, code="invite_rate_limited") from exc
    try:
        description = await users.service.describe_invite(token)
    except InviteNotFoundError as exc:
        raise DashboardNotFoundError(str(exc), code="invite_not_found") from exc
    return InviteDescriptionResponse(
        role_name=description.role_name,
        inviter_display_name=description.inviter_display_name,
        suggested_username=description.suggested_username,
        username_locked=description.username_locked,
        expires_at=description.expires_at,
    )


@router.post("/invite/accept", response_model=DashboardAuthSessionResponse)
async def accept_invite(
    request: Request,
    payload: InviteAcceptRequest = Body(...),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
    users: DashboardUsersContext = Depends(get_dashboard_users_context),
) -> DashboardAuthSessionResponse | JSONResponse:
    """Unauthenticated: set the invited account's password, activate it, and sign it in."""

    if get_settings().dashboard_auth_mode == DashboardAuthMode.DISABLED:
        raise DashboardBadRequestError(
            "Invites cannot be accepted while dashboard auth is bypassed", code="password_management_disabled"
        )
    if await context.service.resolve_user_session(request.cookies.get(DASHBOARD_SESSION_COOKIE)) is not None:
        raise DashboardConflictError("Sign out before accepting an invite", code="already_signed_in")
    password = payload.password.strip()
    _validate_password_length(password)

    try:
        await get_invite_accept_rate_limiter().check_and_increment(
            _session_client_key(request, prefix="invite_accept"), context.session
        )
        await get_invite_accept_token_rate_limiter().check_and_increment(
            f"invite_accept_token:{invite_token_hash(payload.token).hex()}", context.session
        )
    except DashboardRateLimitError as exc:
        raise _rate_limit_error(exc, code="invite_rate_limited") from exc

    try:
        with mapped_user_errors():
            user = await users.service.accept_invite(
                payload.token,
                username=payload.username,
                password_hash=hash_password(password),
                display_name=payload.display_name,
                actor_ip=_client_host(request),
            )
    except InviteNotFoundError as exc:
        raise DashboardNotFoundError(str(exc), code="invite_not_found") from exc
    except UsernameLockedError as exc:
        raise DashboardValidationError(str(exc), code="username_locked") from exc

    await _invalidate_auth_caches()
    return await _issue_user_session_response(request, context, user, totp_verified=False, auth_method="password")


@router.post("/totp/setup/start", response_model=TotpSetupStartResponse)
async def start_totp_setup(
    request: Request,
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> TotpSetupStartResponse:
    _ensure_password_management_enabled(request)
    resolved = await _resolve_account_session(request, context)
    session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
    try:
        return await context.service.start_totp_setup(session_id=session_id, resolved=resolved)
    except PasswordSessionRequiredError as exc:
        raise DashboardAuthError(str(exc)) from exc
    except TotpAlreadyConfiguredError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_totp_setup") from exc


@router.post("/totp/setup/confirm")
async def confirm_totp_setup(
    request: Request,
    payload: TotpSetupConfirmRequest = Body(...),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> JSONResponse:
    _ensure_password_management_enabled(request)
    resolved = await _resolve_account_session(request, context)

    limiter = get_totp_rate_limiter()
    rate_key = _session_client_key(request, prefix="totp_setup_confirm")
    try:
        await limiter.check_and_increment(rate_key, context.session)
    except DashboardRateLimitError as exc:
        raise _rate_limit_error(exc, code="totp_rate_limited") from exc

    try:
        session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
        await context.service.confirm_totp_setup(
            session_id=session_id,
            secret=payload.secret,
            code=payload.code,
            actor_ip=_client_host(request),
            resolved=resolved,
        )
    except PasswordSessionRequiredError as exc:
        raise DashboardAuthError(str(exc)) from exc
    except TotpInvalidCodeError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_totp_code") from exc
    except TotpInvalidSetupError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_totp_setup") from exc
    except TotpAlreadyConfiguredError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_totp_setup") from exc

    await limiter.clear_for_key(rate_key, context.session)
    await _invalidate_auth_caches()
    # The enrolment itself does not mint a TOTP-verified session: whenever the
    # new secret makes the second factor mandatory the account presents it
    # once through ``/totp/verify``, which is the shape the enrolment gate
    # already has for the global toggle.
    return JSONResponse(status_code=200, content={"status": "ok"})


@router.post("/totp/verify", response_model=DashboardAuthSessionResponse)
async def verify_totp(
    request: Request,
    payload: TotpVerifyRequest = Body(...),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> DashboardAuthSessionResponse | JSONResponse:
    _ensure_password_management_enabled(request)
    limiter = get_totp_rate_limiter()
    rate_key = _session_client_key(request, prefix="totp_verify")
    current_session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
    resolved = await _resolve_account_session(request, context)
    try:
        await limiter.check_and_increment(rate_key, context.session)
    except DashboardRateLimitError as exc:
        raise _rate_limit_error(exc, code="totp_rate_limited") from exc
    if _is_header_account(resolved):
        # No session cookie to upgrade: a valid code is a step-up for the header account.
        return await _step_up_header_account(request, context, resolved.user, limiter, rate_key, code=payload.code)
    try:
        configured_ttl_seconds = (await get_settings_cache().get()).dashboard_session_ttl_seconds
        session_ttl_seconds = _session_ttl_seconds(request, resolved.user, configured_ttl_seconds)
        session_id, applied_ttl_seconds = await context.service.verify_totp(
            session_id=current_session_id,
            code=payload.code,
            ttl_seconds=session_ttl_seconds,
            actor_ip=_client_host(request),
        )
    except PasswordSessionRequiredError as exc:
        raise DashboardAuthError(str(exc)) from exc
    except TotpInvalidCodeError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_totp_code") from exc
    except TotpNotConfiguredError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_totp_code") from exc

    await limiter.clear_for_key(rate_key, context.session)
    await get_dashboard_users_cache().invalidate()
    response = await _decorate_session_response(
        await context.service.describe_session(session_id), request=request, context=context
    )
    json_response = JSONResponse(status_code=200, content=response.model_dump(by_alias=True))
    _set_session_cookie(json_response, session_id, request, max_age_seconds=applied_ttl_seconds)
    return json_response


@router.post("/totp/disable")
async def disable_totp(
    request: Request,
    payload: TotpVerifyRequest = Body(...),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> JSONResponse:
    _ensure_password_management_enabled(request)
    limiter = get_totp_rate_limiter()
    rate_key = _session_client_key(request, prefix="totp_disable")
    session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
    # A header account has no TOTP-verified cookie; its valid code is the verification.
    header_account = await _header_account_session(request)
    if header_account is None:
        try:
            await context.service.ensure_totp_verified_session(session_id)
        except PasswordSessionRequiredError as exc:
            raise DashboardAuthError(str(exc)) from exc
        except TotpEnrollmentRequiredError as exc:
            raise _enrollment_required(exc) from exc
    try:
        await limiter.check_and_increment(rate_key, context.session)
    except DashboardRateLimitError as exc:
        raise _rate_limit_error(exc, code="totp_rate_limited") from exc
    try:
        user = await context.service.disable_totp(
            session_id=session_id,
            code=payload.code,
            actor_ip=_client_host(request),
            resolved=header_account,
        )
    except PasswordSessionRequiredError as exc:
        raise DashboardAuthError(str(exc)) from exc
    except TotpInvalidCodeError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_totp_code") from exc
    except TotpNotConfiguredError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_totp_code") from exc
    except LastBreakGlassProtectedError as exc:
        raise DashboardConflictError(str(exc), code="last_break_glass_protected") from exc

    await limiter.clear_for_key(rate_key, context.session)
    await _invalidate_auth_caches()
    # Disabling is allowed even when TOTP was the account's only step-up
    # method; the client is told so it can point at enrolling again.
    return JSONResponse(status_code=200, content={"status": "ok", "stepUpAvailable": user.password_hash is not None})


@router.post("/step-up", response_model=StepUpResponse)
async def step_up(
    request: Request,
    payload: StepUpRequest = Body(...),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> JSONResponse:
    """Re-verify the signed-in account for sensitive changes (PLAN §5 H5).

    Password accounts present their password (and their TOTP code when they
    have a secret); provider-only accounts present their TOTP code. Success
    stamps ``su`` into a re-issued session cookie, or — for a trusted-header
    account, which has no session cookie — into the short-lived step-up cookie.
    """

    principal = await validate_dashboard_session(request)
    if principal.user_id is None:
        raise DashboardAuthError("A user account session is required", code="user_account_required")
    user = await context.repository.get_user_by_id(principal.user_id)
    if user is None:
        raise DashboardAuthError("Authentication is required")
    limiter = get_password_rate_limiter()
    rate_key = _session_client_key(request, prefix="step_up")
    try:
        await limiter.check_and_increment(rate_key, context.session)
    except DashboardRateLimitError as exc:
        raise _rate_limit_error(exc, code="step_up_rate_limited") from exc
    try:
        await context.service.verify_step_up(
            user,
            password=payload.password,
            code=payload.code,
            actor_ip=_client_host(request),
            auth_method=principal.auth_method,
        )
    except StepUpUnavailableError as exc:
        raise DashboardPermissionError(STEP_UP_UNAVAILABLE_MESSAGE, code="step_up_unavailable") from exc
    except InvalidCredentialsError as exc:
        raise DashboardAuthError(str(exc), code="invalid_credentials") from exc
    await limiter.clear_for_key(rate_key, context.session)
    await get_dashboard_users_cache().invalidate()

    verified_at = session_clock()
    response = _step_up_response(verified_at)
    state = get_dashboard_session_store().get(request.cookies.get(DASHBOARD_SESSION_COOKIE))
    if state is not None and state.is_user and state.user_id == user.id and state.password_verified:
        # Keep the session exactly as it was (method, TOTP step, remaining life); only ``su`` changes.
        session_id, session_ttl_seconds = await _create_user_session(
            request,
            user,
            totp_verified=state.totp_verified,
            auth_method=state.auth_method or "password",
            max_ttl_seconds=max(1, state.expires_at - verified_at),
            step_up_verified_at=verified_at,
        )
        _set_session_cookie(response, session_id, request, max_age_seconds=session_ttl_seconds)
    else:
        _set_step_up_cookie(response, user, request, verified_at=verified_at)
    return response


async def _step_up_header_account(
    request: Request,
    context: DashboardAuthContext,
    user: DashboardUser,
    limiter: DatabaseRateLimiter,
    rate_key: str,
    *,
    code: str,
) -> JSONResponse:
    """``/totp/verify`` for a trusted-header account: the code proves the account and mints the step-up cookie."""

    try:
        await context.service.verify_step_up(
            user,
            password=None,
            code=code,
            actor_ip=_client_host(request),
            auth_method=DashboardAuthMode.TRUSTED_HEADER.value,
        )
    except (InvalidCredentialsError, StepUpUnavailableError) as exc:
        raise DashboardBadRequestError("Invalid TOTP code", code="invalid_totp_code") from exc
    await limiter.clear_for_key(rate_key, context.session)
    verified_at = session_clock()
    response = await _decorate_session_response(
        await context.service.describe_session(None), request=request, context=context
    )
    json_response = JSONResponse(status_code=200, content=response.model_dump(by_alias=True))
    _set_step_up_cookie(json_response, user, request, verified_at=verified_at)
    return json_response


def _step_up_response(verified_at: int) -> JSONResponse:
    body = StepUpResponse(verified_at=verified_at, expires_at=step_up_expires_at(verified_at))
    return JSONResponse(status_code=200, content=body.model_dump(by_alias=True))


def _set_step_up_cookie(response: JSONResponse, user: DashboardUser, request: Request, *, verified_at: int) -> None:
    response.set_cookie(
        key=STEP_UP_COOKIE,
        value=get_step_up_cookie_store().create(
            user.id, session_generation=user.session_generation, verified_at=verified_at
        ),
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=step_up_expires_at(verified_at) - verified_at,
        path="/",
    )


@router.post("/logout")
async def logout_dashboard(
    request: Request,
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> JSONResponse:
    session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
    context.service.logout(session_id)
    response = JSONResponse(status_code=200, content={"status": "ok"})
    response.delete_cookie(key=DASHBOARD_SESSION_COOKIE, path="/")
    return response


def _set_session_cookie(response: JSONResponse, session_id: str, request: Request, *, max_age_seconds: int) -> None:
    response.set_cookie(
        key=DASHBOARD_SESSION_COOKIE,
        value=session_id,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=max_age_seconds,
        path="/",
    )
