"""The public OIDC routes, mounted on the dashboard-auth router.

They live in their own module for reading, not for routing: they are added to
the very same ``/api/dashboard-auth`` router the password routes use, so they
inherit its error format, its session exemption and its place in the
cross-site origin middleware's view of the world without a new prefix or a new
exemption list.

Both routes are ``GET``. That is a security decision, not a style one: the
cross-site origin middleware runs before routing and refuses any unsafe
``/api/`` request that arrives cross-site, which is exactly what the identity
provider's callback is. A ``form_post`` callback would be answered ``403``
before the handler existed, and the "fix" -- exempting this prefix -- would
exempt session-minting routes. The query response mode makes the question
disappear.

Nothing here takes a destination from the caller. Where the browser lands is
derived from the flow's stored purpose, out of a closed set of in-app paths, so
there is no ``next``, no ``return_to`` and no way to turn this into an open
redirect.
"""

from __future__ import annotations

import hmac
import secrets
from typing import Final

from fastapi import Depends, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from app.core.audit.service import AuditActor, AuditService, AuditTarget
from app.core.audit.types import AuditAuthMethod, AuditSeverity
from app.core.auth.dashboard_access import DashboardPrincipal, Permission
from app.core.auth.dashboard_session_ttl import REMOTE_DASHBOARD_SESSION_TTL_SECONDS
from app.core.auth.dependencies import (
    ensure_dashboard_permission,
    require_dashboard_permission,
    validate_dashboard_session,
)
from app.core.auth.providers import DEFAULT_PROVIDER_KEY, ExternalIdentity
from app.core.auth.providers.oidc import (
    OIDC_CLOCK_SKEW_SECONDS,
    OidcCompletion,
    OidcError,
    get_oidc_provider,
    hash_flow_value,
)
from app.core.auth.providers.registry import get_auth_provider_registry
from app.core.auth.step_up import STEP_UP_MAX_AGE_SECONDS, step_up_methods
from app.core.clients.oauth import generate_pkce_pair
from app.core.config.settings import get_settings
from app.core.exceptions import (
    AppError,
    DashboardAuthError,
    DashboardConflictError,
    DashboardNotFoundError,
    DashboardRateLimitError,
    DashboardUpstreamError,
)
from app.core.utils.masking import mask_email
from app.core.utils.time import utcnow
from app.db.models import AuthProviderKind, DashboardAuthProvider, DashboardUser
from app.dependencies import DashboardAuthContext, get_dashboard_auth_context
from app.modules.auth_providers.config import OidcProviderConfig, load_oidc_config, oidc_config_fingerprint
from app.modules.auth_providers.repository import AuthProvidersRepository
from app.modules.dashboard_auth.api import (
    _client_host,
    _create_user_session,
    _rate_limit_error,
    _session_client_key,
    _set_session_cookie,
    record_step_up_on,
    router,
)
from app.modules.dashboard_auth.oidc_flows import (
    OIDC_FLOW_COOKIE,
    OIDC_FLOW_COOKIE_PATH,
    OIDC_FLOW_TTL_SECONDS,
    OIDC_LOGIN_START_PATH,
    OidcFlowPurpose,
    OidcFlowRecord,
    OidcFlowRepository,
    clear_pending_marker,
    get_oidc_flow_cookie_store,
    set_pending_marker,
)
from app.modules.dashboard_auth.schemas import OidcStartResponse
from app.modules.dashboard_auth.service import (
    get_login_failed_audit_rate_limiter,
    get_oidc_address_rate_limiter,
    get_oidc_state_rate_limiter,
    session_clock,
)
from app.modules.dashboard_users.identity_resolver import ResolvedAccount, get_identity_resolution_cache
from app.modules.dashboard_users.repository import DashboardUsersRepository

#: The closed set of places this flow can send a browser. Every one is a
#: relative in-app path, so no absolute URL can appear even by accident.
OIDC_DASHBOARD_PATH: Final[str] = "/dashboard"
OIDC_PENDING_PATH: Final[str] = "/auth/pending"
#: Where a completed pre-flight or re-authentication lands: the card that
#: started it, through the settings page's existing deep-link convention.
OIDC_SETTINGS_PATH: Final[str] = "/settings?org=1#oidc"
#: One failure destination for every refusal that is not "no account here", and
#: a constant marker rather than a reason: an unauthenticated caller must not be
#: able to tell a bad state from a bad nonce from a refused exchange.
OIDC_FAILURE_PATH: Final[str] = "/login?sso=failed"

#: Derived from the advertised path so the route and the login screen's
#: ``login_url`` cannot drift apart.
_LOGIN_START_ROUTE: Final[str] = OIDC_LOGIN_START_PATH.removeprefix(router.prefix)
_REDIRECT_STATUS: Final[int] = 303


def _redirect(path: str) -> RedirectResponse:
    response = RedirectResponse(path, status_code=_REDIRECT_STATUS)
    _clear_flow_cookie(response)
    # Every destination except the pending screen clears the refusal marker:
    # a completed sign-in, a completed pre-flight, a completed
    # re-authentication and every failure are all answers to "who are you"
    # that supersede the last refusal this browser was handed.
    clear_pending_marker(response)
    return response


def _pending_redirect(request: Request, row: DashboardAuthProvider, identity: ExternalIdentity) -> RedirectResponse:
    """The one destination that carries something back, and only to the browser it refused.

    The person behind this browser has just authenticated at the company
    identity provider and holds no session, so the screen would otherwise have
    nothing to say. The marker names the row (the screen resolves its label,
    which is public: it is what the sign-in button says) and the masked address
    the identity provider asserted, which the administrator's refused sign-ins
    list masks the same way -- so the two strings match by eye. Nothing else
    goes in: no subject, no groups, no claim, no address in clear.

    An identity provider that asserted no address leaves no marker. There would
    be no reference to quote and the audit row it would be matched against has
    no address either, so the screen keeps its general copy instead of naming a
    provider with nothing to look up.
    """

    email = (identity.email or "").strip()
    if not email:
        return _redirect(OIDC_PENDING_PATH)
    response = RedirectResponse(OIDC_PENDING_PATH, status_code=_REDIRECT_STATUS)
    _clear_flow_cookie(response)
    set_pending_marker(response, request, provider_id=row.id, reference=mask_email(email))
    return response


def _set_flow_cookie(response: Response, request: Request, state: str) -> None:
    response.set_cookie(
        key=OIDC_FLOW_COOKIE,
        value=get_oidc_flow_cookie_store().create(state),
        httponly=True,
        secure=request.url.scheme == "https",
        # Lax, not Strict: the callback is a top-level cross-site navigation
        # and Strict would drop the cookie on the one request that needs it.
        samesite="lax",
        max_age=OIDC_FLOW_TTL_SECONDS,
        path=OIDC_FLOW_COOKIE_PATH,
    )


def _clear_flow_cookie(response: RedirectResponse) -> None:
    """Cleared with the same attributes it was set with, however the flow ends."""

    response.delete_cookie(key=OIDC_FLOW_COOKIE, path=OIDC_FLOW_COOKIE_PATH)


async def _active_oidc_row() -> tuple[DashboardAuthProvider, OidcProviderConfig] | None:
    """The enabled row the current auth mode admits, together with a configuration that opens.

    A row that is disabled, a mode that does not admit it and a blob that does
    not decrypt are one outcome, deliberately: none of them can sign anyone in,
    and an unauthenticated caller learns nothing from the difference.
    """

    active = await get_auth_provider_registry().get_active(
        AuthProviderKind.OIDC, DEFAULT_PROVIDER_KEY, get_settings().dashboard_auth_mode
    )
    if active is None:
        return None
    config = load_oidc_config(active.row)
    return None if config is None else (active.row, config)


async def _configured_oidc_row(
    context: DashboardAuthContext, *, provider_id: str | None = None
) -> tuple[DashboardAuthProvider, OidcProviderConfig] | None:
    """The OIDC row as the **pre-flight** sees it: configured, but not necessarily enabled.

    A test sign-in exists to prove a connection before an operator turns it on,
    so it is the one path that must work against a disabled row. It still reads
    the row itself (not the registry, which only holds active providers), and
    it still refuses a row nobody has configured.
    """

    repository = AuthProvidersRepository(context.session)
    row = (
        await repository.get_provider(provider_id)
        if provider_id is not None
        else await repository.get_by_kind(AuthProviderKind.OIDC.value, DEFAULT_PROVIDER_KEY)
    )
    if row is None or row.kind != AuthProviderKind.OIDC.value:
        return None
    config = load_oidc_config(row)
    return None if config is None else (row, config)


async def _audit_failure(request: Request, context: DashboardAuthContext, *, stage: str) -> None:
    """One bounded ``login_failed`` row per refusal, carrying no value from the wire.

    ``stage`` is a fixed label from this module. The code, the state, the nonce
    and every token are deliberately absent: the audit sanitizer redacts none of
    those key names, so keeping them out is the only thing that keeps them out.
    The public route also cannot be allowed to become an unbounded row source,
    so the same budget the anonymous password refusals spend applies here.
    """

    try:
        await get_login_failed_audit_rate_limiter().check_and_increment(
            _session_client_key(request, prefix="login_failed_audit"), context.session
        )
    except DashboardRateLimitError:
        return
    AuditService.log_async(
        "login_failed",
        actor_ip=_client_host(request),
        details={
            "method": AuditAuthMethod.OIDC.value,
            "username": None,
            "reason": "oidc_login_failed",
            "stage": stage,
        },
        severity=AuditSeverity.WARNING,
    )


async def _failure(request: Request, context: DashboardAuthContext, *, stage: str) -> RedirectResponse:
    await _audit_failure(request, context, stage=stage)
    return _redirect(OIDC_FAILURE_PATH)


async def _begin_flow(
    context: DashboardAuthContext,
    *,
    row: DashboardAuthProvider,
    config: OidcProviderConfig,
    purpose: OidcFlowPurpose,
    acting_user_id: str | None = None,
    prompt_login: bool = False,
) -> tuple[str, str]:
    """Mint one round trip: the authorization URL, and the ``state`` its browser must carry.

    Every flow — sign-in, pre-flight, re-authentication — is created here, so
    there is one place where the ``state``, the ``nonce`` and the PKCE verifier
    are generated, one place where only their hashes are stored, and one place
    that takes the redirect URI from the row instead of from the request.
    """

    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    verifier, challenge = generate_pkce_pair()
    authorization_url = await get_oidc_provider().begin_login(
        provider=row,
        config=config,
        state=state,
        nonce=nonce,
        code_challenge=challenge,
        prompt_login=prompt_login,
    )
    flows = OidcFlowRepository(context.session)
    await flows.purge_expired()
    await flows.create(
        state_hash=hash_flow_value(state),
        provider_id=row.id,
        nonce_hash=hash_flow_value(nonce),
        code_verifier=verifier,
        purpose=purpose,
        acting_user_id=acting_user_id,
        # Sent from the stored configuration and repeated from the row at the
        # exchange: nothing validates or rewrites ``Host``, so a redirect URI
        # built from the request would be attacker-controllable.
        redirect_uri=config.redirect_uri,
        # Which connection document this round trip is about. The callback
        # refuses a flow whose provider has been reconfigured since, and the
        # pre-flight's proof is stamped only against this same document.
        config_fingerprint=oidc_config_fingerprint(config),
    )
    return authorization_url, state


async def _spend_start_budget(request: Request, context: DashboardAuthContext) -> None:
    try:
        await get_oidc_address_rate_limiter().check_and_increment(
            _session_client_key(request, prefix="oidc_start"), context.session
        )
    except DashboardRateLimitError as exc:
        raise _rate_limit_error(exc, code="oidc_rate_limited") from exc


def _start_response(authorization_url: str, request: Request, state: str) -> JSONResponse:
    """The authorization URL as JSON, with the flow cookie that binds it to this browser."""

    body = OidcStartResponse(authorization_url=authorization_url)
    response = JSONResponse(status_code=200, content=body.model_dump(by_alias=True))
    _set_flow_cookie(response, request, state)
    return response


@router.get(_LOGIN_START_ROUTE, include_in_schema=False)
async def start_oidc_login(
    request: Request,
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> RedirectResponse:
    """Public: begin a sign-in at the configured identity provider.

    Login CSRF is not a hole worth a token here: forcing a victim's browser to
    *begin* its own sign-in achieves nothing, and finishing a flow an attacker
    started in the victim's browser is what the flow cookie refuses.
    """

    await _spend_start_budget(request, context)
    configured = await _active_oidc_row()
    if configured is None:
        raise DashboardNotFoundError("No single sign-on provider is available", code="provider_not_found")
    row, config = configured
    try:
        authorization_url, state = await _begin_flow(context, row=row, config=config, purpose=OidcFlowPurpose.LOGIN)
    except OidcError as exc:
        return await _failure(request, context, stage=exc.stage)
    response = RedirectResponse(authorization_url, status_code=_REDIRECT_STATUS)
    _set_flow_cookie(response, request, state)
    return response


@router.post("/oidc/test-login/start", response_model=OidcStartResponse)
async def start_oidc_test_login(
    request: Request,
    principal: DashboardPrincipal = Depends(require_dashboard_permission(Permission.SECURITY_WRITE)),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> JSONResponse:
    """Prove a connection before turning it on: the admin's own browser makes the round trip.

    Deliberately runs against the row as stored, enabled or not — a pre-flight
    that needed the provider to be on already would prove nothing, since
    turning it on is what it guards. The proof it produces belongs to this
    account (``principal.user_id``) and expires in ten minutes, so a connection
    that stops working cannot be enabled from an old success.

    An identity provider this server cannot reach answers ``502`` rather than
    the sign-in flow's uniform failure: the caller is the admin who typed the
    URL, telling them it does not resolve is the entire point of a pre-flight,
    and nothing from the identity provider's own response is reflected.
    """

    if principal.user_id is None:
        raise DashboardConflictError(
            "Set a dashboard password and sign in before connecting single sign-on",
            code="admin_account_required",
        )
    await _spend_start_budget(request, context)
    configured = await _configured_oidc_row(context)
    if configured is None:
        raise DashboardNotFoundError("No single sign-on provider is configured", code="provider_not_found")
    row, config = configured
    try:
        authorization_url, state = await _begin_flow(
            context,
            row=row,
            config=config,
            purpose=OidcFlowPurpose.TEST,
            acting_user_id=principal.user_id,
        )
    except OidcError as exc:
        raise DashboardUpstreamError(
            "The identity provider could not be reached with these settings", code="oidc_provider_unreachable"
        ) from exc
    return _start_response(authorization_url, request, state)


@router.post("/oidc/step-up/start", response_model=OidcStartResponse)
async def start_oidc_step_up(
    request: Request,
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> JSONResponse:
    """Re-authenticate at the identity provider as a step-up factor (PLAN §4.6, H5).

    For the account whose only credential *is* its identity provider. It asks
    for ``prompt=login`` and ``max_age=0``, and the callback refuses anything
    the identity provider does not stamp with a fresh ``auth_time`` — otherwise
    "re-authenticate" would be satisfied by a provider session the attacker's
    stolen cookie already rides alongside.
    """

    principal = await validate_dashboard_session(request)
    if principal.user_id is None:
        raise DashboardAuthError("A user account session is required", code="user_account_required")
    await _spend_start_budget(request, context)
    configured = await _active_oidc_row()
    if configured is None:
        raise DashboardNotFoundError("No single sign-on provider is available", code="provider_not_found")
    row, config = configured
    try:
        authorization_url, state = await _begin_flow(
            context,
            row=row,
            config=config,
            purpose=OidcFlowPurpose.STEP_UP,
            acting_user_id=principal.user_id,
            prompt_login=True,
        )
    except OidcError as exc:
        raise DashboardUpstreamError(
            "The identity provider could not be reached", code="oidc_provider_unreachable"
        ) from exc
    return _start_response(authorization_url, request, state)


@router.get("/oidc/callback", include_in_schema=False)
async def complete_oidc_login(
    request: Request,
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> RedirectResponse:
    """Public: finish the round trip, or fail the same way for every reason.

    ``error`` is accepted so a refusal at the identity provider is not a 422,
    and is then ignored: nothing the identity provider says about why is
    reflected to the browser, to a log line or to an audit row.
    """

    try:
        await get_oidc_address_rate_limiter().check_and_increment(
            _session_client_key(request, prefix="oidc_callback"), context.session
        )
    except DashboardRateLimitError as exc:
        raise _rate_limit_error(exc, code="oidc_rate_limited") from exc

    cookie_state = get_oidc_flow_cookie_store().get(request.cookies.get(OIDC_FLOW_COOKIE))
    # Compared as hashes rather than as the values: constant time over a fixed
    # length, and ``compare_digest`` refuses non-ASCII text, which a caller can
    # put in a query parameter but never in one of our sealed cookies.
    state_hash = "" if state is None else hash_flow_value(state)
    if cookie_state is None or state is None or not hmac.compare_digest(hash_flow_value(cookie_state), state_hash):
        # The browser that started this flow is the only one that may finish
        # it; a state without its cookie is somebody else's (or nobody's).
        return await _failure(request, context, stage="state")

    state_key = f"oidc_callback_state:{state_hash}"
    try:
        await get_oidc_state_rate_limiter().check_and_increment(state_key, context.session)
    except DashboardRateLimitError as exc:
        raise _rate_limit_error(exc, code="oidc_rate_limited") from exc

    flows = OidcFlowRepository(context.session)
    # The point of no return, and it happens BEFORE the exchange: exactly one
    # caller in the fleet wins this row, so a replayed authorization code can
    # never reach the token endpoint twice through this system.
    record = await flows.consume(state_hash)
    if record is None:
        return await _failure(request, context, stage="flow")
    if error is not None or not code:
        return await _failure(request, context, stage="authorization")
    try:
        purpose = OidcFlowPurpose(record.purpose)
    except ValueError:  # pragma: no cover - the column is only ever written from the enum
        return await _failure(request, context, stage="purpose")

    # A pre-flight runs against the row as stored, because proving a connection
    # is what precedes enabling it. A sign-in and a re-authentication need the
    # provider to be active right now, and to still be the same row the flow
    # was started against.
    configured = (
        await _configured_oidc_row(context, provider_id=record.provider_id)
        if purpose is OidcFlowPurpose.TEST
        else await _active_oidc_row()
    )
    if (
        configured is None
        or configured[0].id != record.provider_id
        or oidc_config_fingerprint(configured[1]) != record.config_fingerprint
    ):
        # The provider was disabled, reconfigured or repointed while the person
        # was at the identity provider. A flow completes against the connection
        # document it was started against or not at all: the code was issued by
        # that identity provider, and a pre-flight proves the configuration it
        # actually reached rather than whichever one the row holds when the
        # browser comes back.
        return await _failure(request, context, stage="provider")
    row, config = configured

    try:
        completion = await get_oidc_provider().complete_login(
            provider=row,
            config=config,
            code=code,
            code_verifier=record.code_verifier,
            # Repeated from the row, not re-derived: RFC 6749 requires the
            # token request to carry the same redirect URI the authorization
            # request did, and a configuration edit mid-flow must not change it
            # underneath the exchange.
            redirect_uri=record.redirect_uri,
            nonce_hash=record.nonce_hash,
            flow_created_at=record.created_at_epoch,
        )
    except OidcError as exc:
        return await _failure(request, context, stage=exc.stage)

    if purpose is OidcFlowPurpose.LOGIN:
        finished, completed = await _complete_sign_in(request, context, row, completion)
    elif purpose is OidcFlowPurpose.TEST:
        finished, completed = await _complete_test_login(request, context, row, record, completion)
    else:
        finished, completed = await _complete_step_up(request, context, row, record, completion)
    if completed:
        # Only the per-state budget is cleared. The per-address ceiling is the
        # endpoint's only bound on how many flows one client may burn, and
        # handing it to anyone who can complete one round trip would remove it.
        await get_oidc_state_rate_limiter().clear_for_key(state_key, context.session)
    return finished


async def _complete_sign_in(
    request: Request,
    context: DashboardAuthContext,
    row: DashboardAuthProvider,
    completion: OidcCompletion,
) -> tuple[RedirectResponse, bool]:
    """The ordinary sign-in: the shared resolver decides who this is, or that it is nobody.

    The second half of the pair says whether the round trip completed, which is
    the only thing the caller needs it for: whether to hand the per-``state``
    budget back.
    """

    resolution = await get_identity_resolution_cache().resolve(completion.identity, row, actor_ip=_client_host(request))
    if not isinstance(resolution, ResolvedAccount):
        # The resolver already wrote the ``login_failed reason=unknown_identity``
        # row naming the provider, subject, e-mail and groups; this route adds
        # nothing to it. The pending screen is the one place a failure is
        # allowed to differ, because the person did authenticate.
        return _pending_redirect(request, row, completion.identity), True

    user = await DashboardUsersRepository(context.session).get_by_id(resolution.user_id)
    if user is None:  # pragma: no cover - resolved a moment ago
        return await _failure(request, context, stage="account"), False
    return await _issue_oidc_session(request, user), True


async def _acting_account(
    request: Request,
    context: DashboardAuthContext,
    record: OidcFlowRecord,
    *,
    permission: Permission | None = None,
) -> tuple[DashboardPrincipal, DashboardUser] | None:
    """The signed-in account finishing this flow, when it is the one that started it.

    A pre-flight and a re-authentication are things an account does to itself,
    so the browser coming back must still be that account's, and must still
    hold whatever the start required. Nothing else is accepted: not "some
    admin", not "whoever holds the flow cookie".

    The row is read fresh rather than from the five-second users cache, like
    the body step-up endpoint does: what follows decides on the account's
    factors and mints a cookie bound to its ``session_generation``, and neither
    may be answered from a copy taken before the request.
    """

    if record.acting_user_id is None:
        return None
    try:
        principal = await validate_dashboard_session(request)
        if permission is not None:
            ensure_dashboard_permission(principal, permission)
    except AppError:
        return None
    if principal.user_id is None or principal.user_id != record.acting_user_id:
        return None
    user = await DashboardUsersRepository(context.session).get_by_id(principal.user_id)
    return None if user is None else (principal, user)


async def _complete_test_login(
    request: Request,
    context: DashboardAuthContext,
    row: DashboardAuthProvider,
    record: OidcFlowRecord,
    completion: OidcCompletion,
) -> tuple[RedirectResponse, bool]:
    """Record that this admin's browser completed a round trip through this configuration.

    It provisions nothing, links nothing and issues no session: a pre-flight
    that created an account would be a sign-in wearing a pre-flight's name, and
    the identity it just verified may well be one this install ends up
    refusing. All it leaves behind is the stamp the enable gate reads and an
    audit row saying who proved what.

    The stamp names a configuration, not just a moment: the callback has
    already refused a flow whose provider was reconfigured since it started,
    and the write below is conditional on the row still holding that same
    sealed document, which closes the window between this callback's read and
    its write.
    """

    acting = await _acting_account(request, context, record, permission=Permission.SECURITY_WRITE)
    if acting is None:
        return await _failure(request, context, stage="acting_account"), False
    principal, user = acting
    # The sealed document as it stood when this callback read the row, taken
    # before the stamping commit expires nothing else. The write is conditional
    # on it: a configuration PATCH that lands while the code is being exchanged
    # clears the proof, and an unconditional stamp would hand it straight back
    # against an issuer nobody has tested.
    verified_config = row.config_encrypted
    stamped = await AuthProvidersRepository(context.session).record_test_login(
        row.id, user_id=user.id, verified_at=utcnow(), verified_config=verified_config
    )
    if not stamped:
        return await _failure(request, context, stage="provider"), False
    AuditService.log_async(
        "oidc_test_login_succeeded",
        actor_ip=_client_host(request),
        details={
            "kind": row.kind,
            "provider_key": row.provider_key,
            "subject": completion.identity.subject,
        },
        # The acting admin as they are signed in right now -- usually a local
        # password session. The round trip proved the *connection*, not a new
        # way for this person to be here.
        actor=AuditActor.from_principal(principal),
        target=AuditTarget("auth_provider", row.id),
        severity=AuditSeverity.WARNING,
    )
    return _redirect(OIDC_SETTINGS_PATH), True


async def _complete_step_up(
    request: Request,
    context: DashboardAuthContext,
    row: DashboardAuthProvider,
    record: OidcFlowRecord,
    completion: OidcCompletion,
) -> tuple[RedirectResponse, bool]:
    """A fresh authentication at the identity provider, recorded as this account's step-up.

    Three things have to hold, and each closes a different hole: the browser
    belongs to the account that started the flow, the identity provider says
    the person authenticated just now (``auth_time``, which is why the request
    carried ``prompt=login`` and ``max_age=0``), and the subject it vouched for
    is already linked to that very account. Without the last one, signing in at
    the identity provider as anybody would step up for whoever's stolen cookie
    started the flow.
    """

    acting = await _acting_account(request, context, record)
    if acting is None:
        return await _failure(request, context, stage="acting_account"), False
    principal, user = acting
    if step_up_methods(user):
        # The identity provider is an alternative to *nothing*. An account that
        # holds a password or a TOTP secret presents that, here as everywhere
        # else: otherwise a stolen cookie riding alongside a live provider
        # session would step up without presenting anything new. This is the
        # authority; the start route mints no proof and so needs no copy of it.
        return await _failure(request, context, stage="factor"), False
    if not _is_fresh_authentication(completion.auth_time):
        return await _failure(request, context, stage="auth_time"), False
    identity = await DashboardUsersRepository(context.session).get_identity(
        completion.identity.provider, completion.identity.provider_key, completion.identity.subject
    )
    if identity is None or identity.user_id != user.id:
        return await _failure(request, context, stage="identity"), False

    verified_at = session_clock()
    response = _redirect(OIDC_SETTINGS_PATH)
    # The same two paths the body endpoint mints through -- the session
    # cookie's own ``su`` claim, or the generation-bound step-up cookie -- so
    # this flow adds no third way to hold a step-up.
    await record_step_up_on(response, request, user, verified_at=verified_at)
    AuditService.log_async(
        "step_up_verified",
        actor_ip=_client_host(request),
        details={"username": user.username, "methods": ["oidc"], "provider_key": row.provider_key},
        actor=AuditActor.from_principal(principal),
        target=AuditTarget("user", user.id),
    )
    return response, True


def _is_fresh_authentication(auth_time: int | None) -> bool:
    """Whether the identity provider says the person authenticated inside the step-up window.

    A missing ``auth_time`` is a refusal, not a pass: ``max_age=0`` was asked
    for, and an identity provider that answers without saying when the person
    authenticated has not told us what we asked. The tolerance is the same
    clock skew the ID token is verified with -- stated, not implied.
    """

    if auth_time is None:
        return False
    now = session_clock()
    if auth_time > now + OIDC_CLOCK_SKEW_SECONDS:
        return False
    return now - auth_time <= STEP_UP_MAX_AGE_SECONDS + OIDC_CLOCK_SKEW_SECONDS


def _audit_actor(user: DashboardUser) -> AuditActor:
    return AuditActor(
        user_id=user.id,
        username=user.username,
        role_slug=user.role.slug,
        auth_method=AuditAuthMethod.OIDC.value,
    )


async def _issue_oidc_session(request: Request, user: DashboardUser) -> RedirectResponse:
    """The same version-2 account session every other sign-in issues, capped at twelve hours.

    ``totp_verified`` is false because nothing here verified a local second
    factor: an account that holds a TOTP secret under a policy that requires it
    still has to present it. No step-up is stamped either -- signing in through
    the identity provider is not consent to change security settings, and the
    dedicated step-up flow is what mints that proof.
    """

    session_id, ttl_seconds = await _create_user_session(
        request,
        user,
        totp_verified=False,
        auth_method=AuditAuthMethod.OIDC.value,
        # The existing "this request is not obviously local" ceiling, reused
        # rather than duplicated: a single sign-on session that outlives the
        # identity provider's own session is a deprovisioning hole. A shorter
        # configured dashboard lifetime still wins.
        max_ttl_seconds=REMOTE_DASHBOARD_SESSION_TTL_SECONDS,
    )
    AuditService.log_async(
        "login_success",
        actor_ip=_client_host(request),
        details={"method": AuditAuthMethod.OIDC.value, "username": user.username},
        actor=_audit_actor(user),
        target=AuditTarget("user", user.id),
    )
    response = _redirect(OIDC_DASHBOARD_PATH)
    _set_session_cookie(response, session_id, request, max_age_seconds=ttl_seconds)
    return response
