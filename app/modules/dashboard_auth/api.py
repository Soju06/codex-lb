from __future__ import annotations

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
from app.core.auth.dependencies import require_dashboard_permission, set_dashboard_error_format
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
    DashboardPermissionError,
    DashboardRateLimitError,
    DashboardValidationError,
)
from app.core.request_locality import is_local_request
from app.db.models import DashboardUser
from app.dependencies import DashboardAuthContext, get_dashboard_auth_context
from app.modules.dashboard_auth.schemas import (
    DashboardAuthSessionResponse,
    DashboardMeResponse,
    GuestLoginRequest,
    GuestPasswordSetRequest,
    PasswordChangeRequest,
    PasswordLoginRequest,
    PasswordRemoveRequest,
    PasswordSetupRequest,
    TotpSetupConfirmRequest,
    TotpSetupStartResponse,
    TotpVerifyRequest,
)
from app.modules.dashboard_auth.service import (
    DASHBOARD_SESSION_COOKIE,
    GuestAccessDisabledError,
    InvalidCredentialsError,
    OtherUsersExistError,
    PasswordAlreadyConfiguredError,
    PasswordNotConfiguredError,
    PasswordSessionRequiredError,
    ResolvedUserSession,
    SessionDescription,
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
    get_login_failed_audit_rate_limiter,
    get_password_rate_limiter,
    get_totp_rate_limiter,
    log_login_failed,
)

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

    if request_auth is None:
        update: dict[str, object] = {
            "auth_mode": auth_mode,
            "password_management_enabled": password_management_enabled(auth_mode),
            "password_session_active": fully_authorized,
        }
        if (
            auth_mode == DashboardAuthMode.TRUSTED_HEADER
            and not response.password_required
            and not response.totp_required_on_login
        ):
            update["authenticated"] = False
            update.update(_UNAUTHENTICATED_ACCOUNT_FIELDS)
        return response.model_copy(update=update)

    # Trusted-header / disabled auth: the implicit admin holds every permission.
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
    request_auth = get_dashboard_request_auth(request)
    auth_state = await context.repository.get_local_auth_state()
    if settings.dashboard_auth_mode == DashboardAuthMode.DISABLED:
        raise DashboardBadRequestError(
            "Password management is disabled while dashboard auth is bypassed",
            code="password_management_disabled",
        )
    if (
        settings.dashboard_auth_mode == DashboardAuthMode.TRUSTED_HEADER
        and request_auth is None
        and not auth_state.requires_auth
    ):
        raise DashboardAuthError("Reverse proxy authentication is required", code="proxy_auth_required")
    if (
        not auth_state.requires_auth
        and settings.dashboard_auth_mode != DashboardAuthMode.TRUSTED_HEADER
        and not is_local_request(request)
    ):
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
    session_id, session_ttl_seconds = await _create_user_session(
        request, user, totp_verified=totp_verified, auth_method=auth_method
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

    limiter = get_password_rate_limiter()
    rate_key = _session_client_key(request, prefix="password_login")
    try:
        await limiter.check_and_increment(rate_key, context.session)
    except DashboardRateLimitError as exc:
        raise _rate_limit_error(exc, code="password_rate_limited") from exc

    try:
        user = await context.service.verify_user_password(target, payload.password, actor_ip=_client_host(request))
    except InvalidCredentialsError as exc:
        raise DashboardAuthError(str(exc), code="invalid_credentials") from exc

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
    """

    try:
        await get_password_rate_limiter().check(_session_client_key(request, prefix="password_login"), context.session)
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


@router.post("/totp/setup/start", response_model=TotpSetupStartResponse)
async def start_totp_setup(
    request: Request,
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> TotpSetupStartResponse:
    _ensure_password_management_enabled(request)
    await _require_password_session(request, context)
    session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
    try:
        return await context.service.start_totp_setup(session_id=session_id)
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
    await _require_password_session(request, context)

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
    try:
        resolved = await context.service.require_password_session(current_session_id)
    except PasswordSessionRequiredError as exc:
        raise DashboardAuthError(str(exc)) from exc
    try:
        await limiter.check_and_increment(rate_key, context.session)
    except DashboardRateLimitError as exc:
        raise _rate_limit_error(exc, code="totp_rate_limited") from exc
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
        await context.service.disable_totp(
            session_id=session_id,
            code=payload.code,
            actor_ip=_client_host(request),
        )
    except PasswordSessionRequiredError as exc:
        raise DashboardAuthError(str(exc)) from exc
    except TotpInvalidCodeError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_totp_code") from exc
    except TotpNotConfiguredError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_totp_code") from exc

    await limiter.clear_for_key(rate_key, context.session)
    await _invalidate_auth_caches()
    return JSONResponse(status_code=200, content={"status": "ok"})


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
