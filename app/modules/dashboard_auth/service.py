from __future__ import annotations

import base64
import json
import secrets
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from time import time
from typing import Literal, Protocol, cast

import bcrypt
import segno

from app.core.audit.service import AuditActor, AuditAuthMethod, AuditService, AuditSeverity, AuditTarget
from app.core.auth.dashboard_access import (
    ADMIN_GRANTS,
    ASSIGNABLE_PRESET_ROLES,
    GUEST_GRANTS,
    PRESET_ROLE_IDS,
    DashboardRole,
    Grants,
    Permission,
    PresetRoleSlug,
    permission_strings,
    totp_policy_applies,
)
from app.core.auth.dashboard_mode import DashboardAuthMode
from app.core.auth.dashboard_users_cache import get_dashboard_users_cache
from app.core.auth.providers.registry import ActiveProvider, get_auth_provider_registry
from app.core.auth.step_up import StepUpMethod, is_step_up_fresh, step_up_expires_at, step_up_methods
from app.core.auth.totp import build_otpauth_uri, generate_totp_secret, verify_totp_code
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.rate_limiter.db_rate_limiter import DatabaseRateLimiter
from app.db.models import DashboardUser, DashboardUserStatus
from app.modules.dashboard_auth.schemas import (
    DashboardAccessSummary,
    DashboardAuthSessionResponse,
    DashboardLoginHint,
    DashboardLoginProvider,
    DashboardMeResponse,
    DashboardSessionUser,
    DashboardStepUpState,
    DashboardUserRoleSummary,
    LocalLoginPolicyValue,
    LoginProviderKind,
    TotpSetupStartResponse,
)
from app.modules.dashboard_roles.service import resolve_role_grants
from app.modules.dashboard_users.break_glass import (
    assert_break_glass_remains,
    break_glass_second_factor_required,
    local_login_admits,
)
from app.modules.dashboard_users.credentials import assert_credential_remains
from app.modules.dashboard_users.repository import (
    DashboardUserCounts,
    LocalAuthState,
    is_valid_username,
    normalize_username,
)

DASHBOARD_SESSION_COOKIE = "codex_lb_dashboard_session"
#: Cookie payload format. Version 1 carried ``pw``/``tv``/``role``/``gv`` and no
#: user id; it is rejected outright so a stale cookie can never resolve to a
#: user it was not issued for.
SESSION_PAYLOAD_VERSION = 2
_TOTP_ISSUER = "codex-lb"
AUTH_METHOD_PASSWORD = "password"


class DashboardAuthSettingsProtocol(Protocol):
    guest_access_enabled: bool
    guest_password_hash: str | None
    guest_session_generation: int
    totp_required_on_login: bool
    totp_required_for_admin_role: bool
    local_login_policy: str


class DashboardAuthRepositoryProtocol(Protocol):
    async def get_settings(self) -> DashboardAuthSettingsProtocol: ...

    async def get_local_auth_state(self) -> LocalAuthState: ...

    async def get_user_by_id(self, user_id: str) -> DashboardUser | None: ...

    async def get_user_by_username(self, normalized_username: str) -> DashboardUser | None: ...

    async def list_active_local_password_users(self) -> Sequence[DashboardUser]: ...

    async def count_user_identities(self, user_id: str) -> int: ...

    async def count_live_invites(self) -> int: ...

    async def acquire_write_intent(self) -> None: ...

    async def get_user_counts(self) -> DashboardUserCounts: ...

    async def count_qualifying_break_glass(self, *, exclude_user_id: str | None = None) -> int: ...

    async def count_custom_roles(self) -> int: ...

    async def count_role_mappings(self) -> int: ...

    async def create_first_admin(self, password_hash: str) -> DashboardUser | None: ...

    async def rotate_user_password(self, user_id: str, password_hash: str) -> DashboardUser: ...

    async def set_user_totp_secret(
        self,
        user_id: str,
        secret_encrypted: bytes | None,
        *,
        bump_generation: bool = False,
        preserve_policy: bool = False,
    ) -> DashboardUser: ...

    async def try_advance_user_totp_step(self, user_id: str, step: int) -> bool: ...

    async def bump_session_generation(self, user_id: str) -> int: ...

    async def clear_user_credentials(self, user_id: str) -> DashboardUser: ...

    async def touch_last_login(self, user_id: str) -> None: ...

    async def set_guest_password_hash(self, password_hash: str) -> DashboardAuthSettingsProtocol: ...

    async def clear_guest_password_hash(self) -> DashboardAuthSettingsProtocol: ...

    async def bump_guest_session_generation(self) -> DashboardAuthSettingsProtocol: ...


class TotpAlreadyConfiguredError(ValueError):
    pass


class TotpNotConfiguredError(ValueError):
    pass


class TotpInvalidCodeError(ValueError):
    pass


class TotpInvalidSetupError(ValueError):
    pass


class TotpVerificationRequiredError(ValueError):
    pass


class TotpEnrollmentRequiredError(ValueError):
    pass


class PasswordAlreadyConfiguredError(ValueError):
    pass


class PasswordNotConfiguredError(ValueError):
    pass


class InvalidCredentialsError(ValueError):
    pass


class UsernameRequiredError(ValueError):
    pass


class OtherUsersExistError(ValueError):
    pass


class GuestAccessDisabledError(ValueError):
    pass


class PasswordSessionRequiredError(ValueError):
    pass


class StepUpUnavailableError(ValueError):
    """The account holds neither a password nor a TOTP secret, so nothing can be re-verified."""


SessionKind = Literal["user", "guest"]


@dataclass(slots=True, frozen=True)
class DashboardSessionState:
    """Decoded session cookie (payload version 2).

    ``kind == "user"`` carries the account id and the ``session_generation`` the
    cookie was minted under; the request path re-reads the user row and rejects
    the cookie when the account is gone, disabled, or has revoked its sessions.
    ``kind == "guest"`` carries only the guest generation. ``step_up_verified_at``
    (``su``) is when the account last re-verified a credential for a sensitive
    change; absent until it does. ``break_glass`` (``bg``) marks an emergency
    session so the dashboard can say so; it is optional and its absence means
    false, which is why the payload version does not change for it.
    """

    expires_at: int
    issued_at: int
    kind: SessionKind
    user_id: str | None = None
    session_generation: int | None = None
    password_verified: bool = False
    totp_verified: bool = False
    auth_method: str | None = None
    guest_session_generation: int | None = None
    step_up_verified_at: int | None = None
    break_glass: bool = False

    @property
    def is_user(self) -> bool:
        return self.kind == "user"

    @property
    def is_guest(self) -> bool:
        return self.kind == "guest"

    @property
    def role(self) -> DashboardRole:
        """Coarse wire role: every user session is ``admin`` on the wire this release."""

        return DashboardRole.GUEST if self.kind == "guest" else DashboardRole.ADMIN


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


class DashboardSessionStore:
    def __init__(self) -> None:
        self._encryptor: TokenEncryptor | None = None

    def _get_encryptor(self) -> TokenEncryptor:
        if self._encryptor is None:
            self._encryptor = TokenEncryptor()
        return self._encryptor

    def _seal(self, data: dict[str, object]) -> str:
        payload = json.dumps(data, separators=(",", ":"))
        return self._get_encryptor().encrypt(payload).decode("ascii")

    def create_user_session(
        self,
        user_id: str,
        session_generation: int,
        *,
        password_verified: bool,
        totp_verified: bool,
        ttl_seconds: int,
        auth_method: str = AUTH_METHOD_PASSWORD,
        step_up_verified_at: int | None = None,
        break_glass: bool = False,
    ) -> str:
        now = int(time())
        payload: dict[str, object] = {
            "v": SESSION_PAYLOAD_VERSION,
            "exp": now + ttl_seconds,
            "iat": now,
            "uid": user_id,
            "sg": session_generation,
            "pv": password_verified,
            "tp": totp_verified,
            "am": auth_method,
        }
        if step_up_verified_at is not None:
            payload["su"] = step_up_verified_at
        if break_glass:
            payload["bg"] = True
        return self._seal(payload)

    def create_guest_session(self, *, ttl_seconds: int, guest_session_generation: int) -> str:
        now = int(time())
        return self._seal(
            {
                "v": SESSION_PAYLOAD_VERSION,
                "exp": now + ttl_seconds,
                "iat": now,
                "guest": True,
                "gg": guest_session_generation,
            }
        )

    def get(self, session_id: str | None) -> DashboardSessionState | None:
        if not session_id:
            return None
        token = session_id.strip()
        if not token:
            return None
        try:
            raw = self._get_encryptor().decrypt(token.encode("ascii"))
        except Exception:
            return None
        try:
            data = json.loads(raw)
        except Exception:
            return None
        if not isinstance(data, dict) or data.get("v") != SESSION_PAYLOAD_VERSION:
            return None
        exp = _as_int(data.get("exp"))
        iat = _as_int(data.get("iat"))
        if exp is None or iat is None or exp < int(time()):
            return None
        if data.get("guest") is True:
            gg = _as_int(data.get("gg"))
            if gg is None:
                return None
            return DashboardSessionState(expires_at=exp, issued_at=iat, kind="guest", guest_session_generation=gg)
        uid = data.get("uid")
        sg = _as_int(data.get("sg"))
        pv = data.get("pv")
        tp = data.get("tp")
        am = data.get("am")
        if not isinstance(uid, str) or not uid or sg is None:
            return None
        if not isinstance(pv, bool) or not isinstance(tp, bool) or not isinstance(am, str):
            return None
        return DashboardSessionState(
            expires_at=exp,
            issued_at=iat,
            kind="user",
            user_id=uid,
            session_generation=sg,
            password_verified=pv,
            totp_verified=tp,
            auth_method=am,
            step_up_verified_at=_as_int(data.get("su")),
            break_glass=data.get("bg") is True,
        )

    def delete(self, session_id: str | None) -> None:
        # Stateless: deletion is handled by clearing the cookie client-side.
        return


def session_clock() -> int:
    """The clock sessions and step-ups are minted and checked against (one clock, one truth)."""

    return int(time())


class StepUpCookieStore:
    """The ``codex_lb_step_up`` cookie: a step-up for principals without a session cookie.

    Trusted-header accounts prove who they are on every request through the
    proxy header and carry no session cookie, so their re-verification rides
    in this separate short-lived cookie (``{v, uid, sg, su, exp}``). It is
    only honoured for the account it was minted for, and only while that
    account's ``session_generation`` is unchanged: resetting its TOTP or
    revoking its sessions must void the proof, not leave it usable for the
    rest of the window.
    """

    PAYLOAD_VERSION = 1

    def __init__(self) -> None:
        self._encryptor: TokenEncryptor | None = None

    def _get_encryptor(self) -> TokenEncryptor:
        if self._encryptor is None:
            self._encryptor = TokenEncryptor()
        return self._encryptor

    def create(self, user_id: str, *, session_generation: int, verified_at: int) -> str:
        payload = {
            "v": self.PAYLOAD_VERSION,
            "uid": user_id,
            "sg": session_generation,
            "su": verified_at,
            "exp": step_up_expires_at(verified_at),
        }
        return self._get_encryptor().encrypt(json.dumps(payload, separators=(",", ":"))).decode("ascii")

    def get(self, token: str | None, *, user_id: str, session_generation: int) -> int | None:
        """The verification time the cookie records for this account, or ``None``.

        A generation bump (TOTP reset, sessions revoked) voids the proof.
        """

        if not token or not token.strip():
            return None
        try:
            data = json.loads(self._get_encryptor().decrypt(token.strip().encode("ascii")))
        except Exception:
            return None
        if not isinstance(data, dict) or data.get("v") != self.PAYLOAD_VERSION or data.get("uid") != user_id:
            return None
        if _as_int(data.get("sg")) != session_generation:
            return None
        verified_at, exp = _as_int(data.get("su")), _as_int(data.get("exp"))
        if verified_at is None or exp is None or exp < session_clock():
            return None
        return verified_at


@dataclass(slots=True, frozen=True)
class ResolvedUserSession:
    """A decoded user cookie whose account is still active and whose generation still matches."""

    user: DashboardUser
    state: DashboardSessionState


@dataclass(slots=True, frozen=True)
class SessionDescription:
    """The session response together with the account it was resolved for (reused by the API layer)."""

    response: DashboardAuthSessionResponse
    resolved: ResolvedUserSession | None


AuthStateProvider = Callable[[], Awaitable[LocalAuthState]]
ActiveProvidersProvider = Callable[[], Awaitable[list[ActiveProvider]]]


@dataclass(slots=True, frozen=True)
class GuestVerification:
    """Outcome of a guest credential check plus the generation of the very row it was checked against.

    The cookie must be stamped with this generation: re-reading settings after
    the check could pick up a bump (guest password just enabled) and mint a
    cookie that outlives the credential it never satisfied.
    """

    password_verified: bool
    guest_session_generation: int


@dataclass(slots=True, frozen=True)
class LoginTarget:
    """Who a password login attempt is for, before the password is checked.

    ``username`` is the normalized submitted (or resolved) username and is
    ``None`` only when no username could be resolved; ``user`` is ``None``
    when no active account with that username exists. Callers must not leak
    the difference to the client.
    """

    username: str | None
    user: DashboardUser | None
    #: Why no account was resolved (audit ``login_failed.reason``); ``None`` when ``user`` is set.
    refusal_reason: str | None = None


def _user_is_active(user: DashboardUser | None) -> bool:
    return user is not None and user.status == DashboardUserStatus.ACTIVE.value


def _user_actor(user: DashboardUser, auth_method: str) -> AuditActor:
    return AuditActor(user_id=user.id, username=user.username, role_slug=user.role.slug, auth_method=auth_method)


def _user_target(user: DashboardUser) -> AuditTarget:
    return AuditTarget("user", user.id)


_GUEST_ACTOR = AuditActor(
    user_id=None,
    username=None,
    role_slug=PresetRoleSlug.GUEST.value,
    auth_method=AuditAuthMethod.GUEST.value,
)


def log_login_failed(actor_ip: str | None, method: str, reason: str, *, username: str | None = None) -> None:
    """Audit a refused sign-in: no actor (nobody authenticated), warning severity, a fixed reason."""

    AuditService.log_async(
        "login_failed",
        actor_ip=actor_ip,
        details={"method": method, "username": username, "reason": reason},
        severity=AuditSeverity.WARNING,
    )


def role_summary(user: DashboardUser) -> DashboardUserRoleSummary:
    return DashboardUserRoleSummary(id=user.role.id, slug=user.role.slug, name=user.role.name, kind=user.role.kind)


def session_user(user: DashboardUser) -> DashboardSessionUser:
    return DashboardSessionUser(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=role_summary(user),
    )


class DashboardAuthService:
    def __init__(
        self,
        repository: DashboardAuthRepositoryProtocol,
        session_store: DashboardSessionStore,
        *,
        auth_state_provider: AuthStateProvider | None = None,
        active_providers_provider: ActiveProvidersProvider | None = None,
    ) -> None:
        self._repository = repository
        self._session_store = session_store
        self._encryptor = TokenEncryptor()
        # The session response is served on every page load; it reads the derived
        # auth state and the active providers through the process caches. Login
        # and setup decisions keep reading the repository directly.
        self._auth_state = auth_state_provider or _cached_local_auth_state
        self._active_providers = active_providers_provider or _cached_active_providers

    # --- session resolution ---

    async def resolve_user_session(self, session_id: str | None) -> ResolvedUserSession | None:
        state = self._session_store.get(session_id)
        return await self._resolve_state(state)

    async def _resolve_state(self, state: DashboardSessionState | None) -> ResolvedUserSession | None:
        if state is None or not state.is_user or state.user_id is None:
            return None
        user = await self._repository.get_user_by_id(state.user_id)
        if not _user_is_active(user) or user is None:
            return None
        if user.session_generation != state.session_generation:
            return None
        return ResolvedUserSession(user=user, state=state)

    async def require_password_session(self, session_id: str | None) -> ResolvedUserSession:
        resolved = await self.resolve_user_session(session_id)
        if resolved is None or not resolved.state.password_verified:
            raise PasswordSessionRequiredError("Password-authenticated session is required")
        return resolved

    async def require_management_session(
        self, session_id: str | None, *, allow_unenrolled: bool = False
    ) -> ResolvedUserSession:
        """A password session that has also passed TOTP when the install requires it.

        With the TOTP policy on, an account that has no secret yet is refused
        (``TotpEnrollmentRequiredError``) unless ``allow_unenrolled`` marks the
        caller as one of the self-service routes an unenrolled account may use.
        """

        resolved = await self.require_password_session(session_id)
        if await self._totp_required_for(resolved.user):
            if resolved.user.totp_secret_encrypted is None:
                if not allow_unenrolled:
                    raise TotpEnrollmentRequiredError("TOTP enrollment is required before dashboard access")
            elif not resolved.state.totp_verified:
                raise TotpVerificationRequiredError("TOTP verification is required for dashboard access")
        return resolved

    async def _totp_required_for(self, user: DashboardUser) -> bool:
        """The TOTP policy as it binds ``user``.

        The global toggle, the admin-role toggle for admin-level roles, and --
        whatever either says -- an emergency account that holds a secret.
        """

        settings = await self._repository.get_settings()
        return totp_policy_applies(
            required_on_login=settings.totp_required_on_login,
            required_for_admin_role=settings.totp_required_for_admin_role,
            grants=resolve_role_grants(user.role),
        ) or break_glass_second_factor_required(user)

    async def _require_totp_verified_session(self, session_id: str | None) -> ResolvedUserSession:
        resolved = await self.require_password_session(session_id)
        if resolved.user.totp_secret_encrypted is None and await self._totp_required_for(resolved.user):
            raise TotpEnrollmentRequiredError("TOTP enrollment is required before dashboard access")
        if not resolved.state.totp_verified:
            raise PasswordSessionRequiredError("TOTP-verified session is required")
        return resolved

    async def ensure_active_password_session(self, session_id: str | None) -> None:
        await self.require_password_session(session_id)

    async def ensure_totp_verified_session(self, session_id: str | None) -> None:
        await self._require_totp_verified_session(session_id)

    # --- session response ---

    async def get_session_state(self, session_id: str | None) -> DashboardAuthSessionResponse:
        return (await self.describe_session(session_id)).response

    async def describe_session(self, session_id: str | None) -> SessionDescription:
        settings = await self._repository.get_settings()
        auth_state = await self._auth_state()
        password_required = auth_state.requires_auth
        guest_access_enabled = settings.guest_access_enabled
        guest_password_required = guest_access_enabled and settings.guest_password_hash is not None
        state = self._session_store.get(session_id) if password_required or guest_access_enabled else None
        resolved = await self._resolve_state(state)
        public_guest_authenticated = bool(
            guest_access_enabled
            and not guest_password_required
            and password_required
            and get_settings().dashboard_auth_mode == DashboardAuthMode.STANDARD
        )

        user: DashboardUser | None = None
        grants: Grants
        step_up: DashboardStepUpState | None = None
        totp_configured = False
        totp_pending = False
        totp_enrollment_required = False
        auth_method: str | None = None
        if (
            state is not None
            and state.is_guest
            and guest_access_enabled
            and state.guest_session_generation == settings.guest_session_generation
        ):
            authenticated = True
            role = DashboardRole.GUEST
            grants = GUEST_GRANTS
        elif resolved is not None and resolved.state.password_verified:
            user = resolved.user
            grants = resolve_role_grants(user.role)
            totp_policy = totp_policy_applies(
                required_on_login=settings.totp_required_on_login,
                required_for_admin_role=settings.totp_required_for_admin_role,
                grants=grants,
            ) or break_glass_second_factor_required(user)
            totp_configured = user.totp_secret_encrypted is not None
            totp_pending = totp_policy and totp_configured and not resolved.state.totp_verified
            totp_enrollment_required = totp_policy and not totp_configured
            authenticated = not totp_pending
            role = DashboardRole.ADMIN
            auth_method = resolved.state.auth_method
            step_up = step_up_state(user, verified_at=resolved.state.step_up_verified_at)
        elif not password_required:
            authenticated = True
            role = DashboardRole.ADMIN
            grants = ADMIN_GRANTS
        elif public_guest_authenticated:
            authenticated = True
            role = DashboardRole.GUEST
            grants = GUEST_GRANTS
        else:
            authenticated = False
            role = DashboardRole.ADMIN
            grants = ADMIN_GRANTS

        # Team facts and assignable roles are for signed-in accounts holding
        # users:manage only; the implicit admin has no account and gets none.
        manages_users = bool(
            authenticated and user is not None and not totp_enrollment_required and Permission.USERS_MANAGE in grants
        )
        response = DashboardAuthSessionResponse(
            authenticated=authenticated,
            password_required=password_required,
            local_password_configured=auth_state.active_local_password_users > 0,
            totp_required_on_login=totp_pending,
            totp_configured=totp_configured,
            role=role,
            permissions=permission_strings(grants),
            guest_access_enabled=guest_access_enabled,
            guest_password_required=guest_password_required,
            user=session_user(user) if user is not None else None,
            auth_method=auth_method,
            must_change_password=bool(user is not None and user.must_change_password),
            totp_enrollment_required=totp_enrollment_required,
            login=await self.login_hint(auth_state, settings.local_login_policy),
            access_summary=await self.access_summary() if manages_users else None,
            assignable_role_ids=assignable_role_ids() if manages_users else [],
            step_up=step_up,
            break_glass_session=bool(resolved is not None and resolved.state.break_glass and user is not None),
        )
        return SessionDescription(response=response, resolved=resolved)

    async def login_hint(self, auth_state: LocalAuthState, policy: str) -> DashboardLoginHint:
        """The login screen's facts, served to unauthenticated clients too — never a username."""

        return DashboardLoginHint(
            username_field="hidden" if auth_state.active_local_password_users == 1 else "shown",
            providers=[
                DashboardLoginProvider(
                    # The registry only activates kinds with an implementation, which are the wire kinds.
                    kind=cast(LoginProviderKind, item.row.kind),
                    provider_key=item.row.provider_key,
                    label=item.row.label,
                    login_url=None,
                )
                for item in await self._active_providers()
            ],
            local_login=cast(LocalLoginPolicyValue, policy),
        )

    async def access_summary(self) -> DashboardAccessSummary:
        counts = await self._repository.get_user_counts()
        return DashboardAccessSummary(
            users_total=counts.total,
            users_active=counts.active,
            users_invited=counts.invited,
            users_disabled=counts.disabled,
            pending_invites=counts.pending_invites,
            non_admin_users=counts.non_admin,
            custom_roles=await self._repository.count_custom_roles(),
            providers_enabled=[item.row.kind for item in await self._active_providers()],
            role_mappings=await self._repository.count_role_mappings(),
            scim_tokens=0,
            audit_sinks=0,
            local_login_policy=cast(LocalLoginPolicyValue, (await self._repository.get_settings()).local_login_policy),
        )

    async def me(self, session_id: str | None) -> DashboardMeResponse:
        resolved = await self.require_management_session(session_id, allow_unenrolled=True)
        user = resolved.user
        return DashboardMeResponse(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            email=user.email,
            role=role_summary(user),
            auth_method=resolved.state.auth_method,
            totp_configured=user.totp_secret_encrypted is not None,
            must_change_password=user.must_change_password,
        )

    # --- password ---

    async def setup_password(self, password: str) -> DashboardUser:
        user = await self._repository.create_first_admin(hash_password(password))
        if user is None:
            raise PasswordAlreadyConfiguredError("Password is already configured")
        return user

    async def resolve_login_target(self, username: str | None) -> LoginTarget:
        """Pick the account a login attempt is for.

        A single-user install accepts a login without a username. With more
        than one active local password user the username is mandatory
        (``UsernameRequiredError``); that refusal must not spend any rate-limit
        budget, and the route decides whether to audit it. Why a username did
        not resolve is kept on the target for the audit row only; the client
        never learns it -- including when ``local_login_policy`` is what
        refused the account.
        """

        auth_state = await self._repository.get_local_auth_state()
        if auth_state.active_local_password_users == 0:
            raise PasswordNotConfiguredError("Password is not configured")
        policy = (await self._repository.get_settings()).local_login_policy
        if username is None:
            if auth_state.sole_local_password_user_id is None:
                raise UsernameRequiredError("Username is required")
            user = await self._repository.get_user_by_id(auth_state.sole_local_password_user_id)
            if user is not None and not local_login_admits(user, policy):
                return LoginTarget(username=user.username, user=None, refusal_reason="login_policy")
            return LoginTarget(username=user.username if user is not None else None, user=user)
        normalized = normalize_username(username)
        if not is_valid_username(normalized):
            return LoginTarget(username=normalized, user=None, refusal_reason="invalid_username")
        user = await self._repository.get_user_by_username(normalized)
        if user is None:
            return LoginTarget(username=normalized, user=None, refusal_reason="unknown_identity")
        if not _user_is_active(user):
            return LoginTarget(username=normalized, user=None, refusal_reason="disabled_user")
        # A policy refusal drops the account exactly like an unknown username
        # does: ``verify_user_password`` still runs its one hash comparison, so
        # the answer, its body and its timing are the same.
        if not local_login_admits(user, policy):
            return LoginTarget(username=normalized, user=None, refusal_reason="login_policy")
        return LoginTarget(username=normalized, user=user)

    async def verify_user_password(
        self,
        target: LoginTarget,
        password: str,
        *,
        actor_ip: str | None = None,
    ) -> DashboardUser:
        """Check ``password`` for ``target``; unknown accounts take the same path and time.

        Exactly one bcrypt verification runs whether or not the account exists
        or holds a password (a memoised dummy hash stands in), so response
        timing cannot reveal which usernames are real. The audit record only
        keeps well-formed usernames.
        """

        user = target.user
        stored_hash = user.password_hash if user is not None else None
        matched = _check_password(password, stored_hash if stored_hash is not None else _dummy_password_hash())
        if user is None or stored_hash is None or not matched:
            username_is_valid = target.username is not None and is_valid_username(target.username)
            log_login_failed(
                actor_ip,
                AUTH_METHOD_PASSWORD,
                target.refusal_reason or "bad_password",
                username=target.username if username_is_valid else None,
            )
            raise InvalidCredentialsError("Invalid credentials")
        await self._repository.touch_last_login(user.id)
        if user.totp_secret_encrypted is None or not await self._totp_required_for(user):
            AuditService.log_async(
                "login_success",
                actor_ip=actor_ip,
                details={"method": AUTH_METHOD_PASSWORD, "username": user.username},
                actor=_user_actor(user, AUTH_METHOD_PASSWORD),
                target=_user_target(user),
            )
        return user

    async def verify_password(self, password: str, *, actor_ip: str | None = None) -> DashboardUser:
        """Single-user convenience used by tests and callers without a username."""

        return await self.verify_user_password(await self.resolve_login_target(None), password, actor_ip=actor_ip)

    async def verify_guest_password(self, password: str | None, *, actor_ip: str | None = None) -> GuestVerification:
        """Check the guest credential against one settings row read from the database.

        The returned generation belongs to that same row; callers stamp it into
        the cookie instead of reading settings again.
        """

        settings = await self._repository.get_settings()
        if not settings.guest_access_enabled:
            raise GuestAccessDisabledError("Guest access is disabled")
        generation = settings.guest_session_generation
        current = settings.guest_password_hash
        if current is None:
            AuditService.log_async("login_success", actor_ip=actor_ip, details={"method": "guest"}, actor=_GUEST_ACTOR)
            return GuestVerification(password_verified=False, guest_session_generation=generation)
        if password is None or not _check_password(password, current):
            log_login_failed(actor_ip, AuditAuthMethod.GUEST.value, "bad_password")
            raise InvalidCredentialsError("Invalid credentials")
        AuditService.log_async("login_success", actor_ip=actor_ip, details={"method": "guest"}, actor=_GUEST_ACTOR)
        return GuestVerification(password_verified=True, guest_session_generation=generation)

    async def change_password(
        self,
        user: DashboardUser,
        current_password: str,
        new_password: str,
        *,
        actor_ip: str | None = None,
        auth_method: str | None = None,
    ) -> int:
        """Rotate the password and revoke every other session; returns the new generation.

        ``auth_method`` is how the calling session authenticated (audit actor).
        """

        if user.password_hash is None:
            raise PasswordNotConfiguredError("Password is not configured")
        if not _check_password(current_password, user.password_hash):
            raise InvalidCredentialsError("Invalid credentials")
        rotated = await self._repository.rotate_user_password(user.id, hash_password(new_password))
        AuditService.log_async(
            "password_changed",
            actor_ip=actor_ip,
            details={"username": user.username},
            actor=_user_actor(user, auth_method or AUTH_METHOD_PASSWORD),
            target=_user_target(user),
        )
        return rotated.session_generation

    async def remove_password(
        self,
        user: DashboardUser,
        password: str,
        *,
        actor_ip: str | None = None,
        auth_method: str | None = None,
    ) -> None:
        """Solo-install only: drop the user's credentials so the install is passwordless again.

        "Solo" is every account the install holds, in any status -- not just
        the active ones. A disabled account is still an account: it keeps its
        role, its owned keys and (if it ever had one) its password hash, and
        only an account with ``users:manage`` can bring it back. Removing the
        last *active* password while one exists would hand the install to the
        implicit local admin, which holds no account and therefore cannot
        manage users at all, stranding the disabled row with no way to enable,
        delete or sign in as it -- while the install-wide TOTP requirements
        this write also clears were only ever justified by "this account *is*
        the install". Deleting the other accounts first is the way through.
        """

        if user.password_hash is None:
            raise PasswordNotConfiguredError("Password is not configured")
        if not _check_password(password, user.password_hash):
            raise InvalidCredentialsError("Invalid credentials")
        # Serialised with account mutations and invite acceptance: a pending
        # invite would otherwise turn a passwordless install into one where
        # authentication is mandatory again but no admin holds a password.
        await self._repository.acquire_write_intent()
        counts = await self._repository.get_user_counts()
        solo_install = counts.total == 1 and counts.active == 1
        identities = await self._repository.count_user_identities(user.id)
        if not solo_install or identities or await self._repository.count_live_invites():
            raise OtherUsersExistError(
                "Other accounts exist or invites are pending; delete the other accounts "
                "or revoke pending invites instead"
            )
        assert_credential_remains(password_hash=None, identity_count=identities, solo_install=solo_install)
        # Dropping the password is one more way to lose the last qualifying
        # emergency account: a restricted policy would survive the removal, and
        # the re-bootstrapped ``admin`` is designated but never qualifying, so
        # the next sign-in would meet a door nothing can open. Same guard, same
        # refusal, still under the write intent taken above.
        await self._assert_break_glass_remains(user, has_password=False, has_totp=False)
        await self._repository.clear_user_credentials(user.id)
        AuditService.log_async(
            "password_removed",
            actor_ip=actor_ip,
            details={"username": user.username},
            actor=_user_actor(user, auth_method or AUTH_METHOD_PASSWORD),
            target=_user_target(user),
        )

    async def revoke_user_sessions(
        self,
        user: DashboardUser,
        *,
        actor_ip: str | None = None,
        auth_method: str | None = None,
    ) -> int:
        generation = await self._repository.bump_session_generation(user.id)
        AuditService.log_async(
            "user_sessions_revoked",
            actor_ip=actor_ip,
            details={"username": user.username, "scope": "self"},
            actor=_user_actor(user, auth_method or AUTH_METHOD_PASSWORD),
            target=_user_target(user),
        )
        return generation

    async def set_guest_password(self, password: str) -> None:
        await self._repository.set_guest_password_hash(hash_password(password))

    async def clear_guest_password(self) -> None:
        await self._repository.clear_guest_password_hash()

    async def revoke_guest_sessions(self) -> None:
        """Invalidate every outstanding guest session cookie."""

        await self._repository.bump_guest_session_generation()

    # --- TOTP (per user) ---

    async def _account_for_totp(
        self, session_id: str | None, resolved: ResolvedUserSession | None
    ) -> ResolvedUserSession:
        """The account a TOTP route acts on: the password session, or the account the route already resolved.

        Routes pass ``resolved`` for a trusted-header account, which has no
        session cookie but may still enrol TOTP (it is its only step-up method).
        """

        return resolved if resolved is not None else await self.require_password_session(session_id)

    async def start_totp_setup(
        self, *, session_id: str | None, resolved: ResolvedUserSession | None = None
    ) -> TotpSetupStartResponse:
        resolved = await self._account_for_totp(session_id, resolved)
        if resolved.user.totp_secret_encrypted is not None:
            raise TotpAlreadyConfiguredError("TOTP is already configured. Disable it before setting a new secret")
        secret = generate_totp_secret()
        otpauth_uri = build_otpauth_uri(secret, issuer=_TOTP_ISSUER, account_name=resolved.user.username)
        return TotpSetupStartResponse(
            secret=secret,
            otpauth_uri=otpauth_uri,
            qr_svg_data_uri=_qr_svg_data_uri(otpauth_uri),
        )

    async def confirm_totp_setup(
        self,
        *,
        session_id: str | None,
        secret: str,
        code: str,
        actor_ip: str | None = None,
        resolved: ResolvedUserSession | None = None,
    ) -> None:
        resolved = await self._account_for_totp(session_id, resolved)
        if resolved.user.totp_secret_encrypted is not None:
            raise TotpAlreadyConfiguredError("TOTP is already configured. Disable it before setting a new secret")
        try:
            verification = verify_totp_code(secret, code, window=1)
        except ValueError as exc:
            raise TotpInvalidSetupError("Invalid TOTP setup payload") from exc
        if not verification.is_valid:
            raise TotpInvalidCodeError("Invalid TOTP code")
        await self._repository.set_user_totp_secret(resolved.user.id, self._encryptor.encrypt(secret))
        AuditService.log_async(
            "totp_enabled",
            actor_ip=actor_ip,
            details={"username": resolved.user.username},
            actor=_user_actor(resolved.user, resolved.state.auth_method or AUTH_METHOD_PASSWORD),
            target=_user_target(resolved.user),
        )

    async def verify_totp(
        self,
        *,
        session_id: str | None,
        code: str,
        ttl_seconds: int,
        actor_ip: str | None = None,
    ) -> tuple[str, int]:
        resolved = await self.require_password_session(session_id)
        user, existing_state = resolved.user, resolved.state
        secret_encrypted = user.totp_secret_encrypted
        if secret_encrypted is None:
            raise TotpNotConfiguredError("TOTP is not configured")
        secret = self._encryptor.decrypt(secret_encrypted)
        verification = verify_totp_code(
            secret,
            code,
            window=1,
            last_verified_step=user.totp_last_verified_step,
        )
        # Snapshot the attribution before the counter write: a refused replay
        # rolls the session back, which expires ``user`` and would turn a later
        # attribute read into a lazy load outside the async context.
        username = user.username
        actor = _user_actor(user, AuditAuthMethod.TOTP.value)
        target = _user_target(user)
        if not verification.is_valid or verification.matched_step is None:
            log_login_failed(actor_ip, AuditAuthMethod.TOTP.value, "bad_totp", username=username)
            raise TotpInvalidCodeError("Invalid TOTP code")
        updated = await self._repository.try_advance_user_totp_step(user.id, verification.matched_step)
        if not updated:
            log_login_failed(actor_ip, AuditAuthMethod.TOTP.value, "bad_totp", username=username)
            raise TotpInvalidCodeError("Invalid TOTP code")
        AuditService.log_async(
            "login_success",
            actor_ip=actor_ip,
            details={"method": "totp", "username": username},
            actor=actor,
            target=target,
        )
        if user.is_break_glass:
            # The emergency door was used. This is the first (and, with the CLI,
            # one of two) critical-severity events the product writes: an
            # operator reading the audit log must never have to infer it.
            AuditService.log_async(
                "break_glass_login",
                actor_ip=actor_ip,
                details={"method": "totp", "username": username},
                actor=actor,
                target=target,
                severity=AuditSeverity.CRITICAL,
            )
        # Honor the existing password-session expiry so that a TTL change
        # mid-flow (between password login and TOTP submission) cannot extend
        # an already-issued session, while still applying the TTL cap resolved
        # for the current TOTP request. The state captured by
        # require_password_session is reused so a second store.get() race
        # cannot turn a near-expiry password session into a full-length one.
        now = int(time())
        inherited_ttl = max(1, existing_state.expires_at - now)
        applied_ttl = min(inherited_ttl, ttl_seconds)
        # Completing the second factor of a sign-in that just happened is a
        # step-up: both credentials were presented within the window. Verifying
        # against an old session is not — the password behind it was proven
        # long ago (and whoever holds the cookie could have enrolled the secret
        # themselves), so that path keeps whatever step-up the session already
        # carried and leaves /step-up to ask for the password.
        completing_fresh_login = is_step_up_fresh(existing_state.issued_at, now=now)
        step_up_verified_at = now if completing_fresh_login else existing_state.step_up_verified_at
        new_session_id = self._session_store.create_user_session(
            user.id,
            user.session_generation,
            password_verified=True,
            totp_verified=True,
            ttl_seconds=applied_ttl,
            auth_method=existing_state.auth_method or AUTH_METHOD_PASSWORD,
            step_up_verified_at=step_up_verified_at,
            break_glass=user.is_break_glass,
        )
        return new_session_id, applied_ttl

    async def _consume_totp_code(self, user: DashboardUser, code: str) -> None:
        """Check ``code`` against the account's secret and advance its replay counter, or raise."""

        secret_encrypted = user.totp_secret_encrypted
        if secret_encrypted is None:
            raise TotpNotConfiguredError("TOTP is not configured")
        secret = self._encryptor.decrypt(secret_encrypted)
        verification = verify_totp_code(secret, code, window=1, last_verified_step=user.totp_last_verified_step)
        if not verification.is_valid or verification.matched_step is None:
            raise TotpInvalidCodeError("Invalid TOTP code")
        if not await self._repository.try_advance_user_totp_step(user.id, verification.matched_step):
            raise TotpInvalidCodeError("Invalid TOTP code")

    async def disable_totp(
        self,
        *,
        session_id: str | None,
        code: str,
        actor_ip: str | None = None,
        resolved: ResolvedUserSession | None = None,
    ) -> DashboardUser:
        """Drop the account's secret after a valid code; returns the account.

        A header account never carries a TOTP-verified cookie; its valid
        ``code`` is the verification (a password session still needs ``tp``).
        """

        resolved = resolved if resolved is not None else await self._require_totp_verified_session(session_id)
        user = resolved.user
        # The code is consumed *first* because advancing the replay counter
        # commits, and a commit between the guard's count and the secret write
        # would drop the write-intent lock the guard took -- two concurrent
        # disables would then both pass the count and clear both secrets. The
        # cost is that a refused removal still spends the code the caller
        # typed; the guard is what must not be racy.
        await self._consume_totp_code(user, code)
        await self._assert_break_glass_remains(user, has_totp=False)
        await self._repository.set_user_totp_secret(user.id, None)
        AuditService.log_async(
            "totp_disabled",
            actor_ip=actor_ip,
            details={"username": user.username},
            actor=_user_actor(user, resolved.state.auth_method or AUTH_METHOD_PASSWORD),
            target=_user_target(user),
        )
        return user

    async def _assert_break_glass_remains(
        self, user: DashboardUser, *, has_totp: bool | None = None, has_password: bool | None = None
    ) -> None:
        """The shared break-glass guard, reached from the self-service side.

        Same function, same refusal as the management paths: an account that
        is the install's only qualifying emergency account cannot remove its
        own second factor (or its own password) while local sign-in is
        restricted.

        The caller must not commit between this call and its write: the write
        intent taken here is what keeps two simultaneous removals from both
        passing the count, and a commit releases it.
        """

        # Serialised with the management paths *before the policy is read*: the
        # secret write has no conditional form, so the lock is what keeps two
        # simultaneous removals from both passing the count -- and a policy read
        # that happens outside it can be a tightening older than the guard, which
        # would let this removal take the last qualifying account away.
        await self._repository.acquire_write_intent()
        settings = await self._repository.get_settings()
        await assert_break_glass_remains(
            user,
            policy=settings.local_login_policy,
            count_other_qualifying=lambda: self._repository.count_qualifying_break_glass(exclude_user_id=user.id),
            has_totp=has_totp,
            has_password=has_password,
        )

    # --- step-up (per user) ---

    async def verify_step_up(
        self,
        user: DashboardUser,
        *,
        password: str | None,
        code: str | None,
        actor_ip: str | None = None,
        auth_method: str | None = None,
    ) -> list[StepUpMethod]:
        """Re-verify ``user`` with every factor it holds; returns the methods that were checked.

        Password accounts present the password (and a TOTP code when the account
        has a secret); provider-only accounts present a TOTP code. Every refusal
        is the same ``InvalidCredentialsError``. An account with no factor at all
        raises ``StepUpUnavailableError`` instead — the client is told to enrol.
        """

        methods = step_up_methods(user)
        if not methods:
            raise StepUpUnavailableError("The account holds no credential to re-verify")
        username = user.username
        actor, target = _user_actor(user, auth_method or AUTH_METHOD_PASSWORD), _user_target(user)
        if "password" in methods and (
            user.password_hash is None or not _check_password(password or "", user.password_hash)
        ):
            log_login_failed(actor_ip, "step_up", "bad_password", username=username)
            raise InvalidCredentialsError("Invalid credentials")
        if "totp" in methods:
            try:
                await self._consume_totp_code(user, code or "")
            except (TotpInvalidCodeError, TotpNotConfiguredError) as exc:
                log_login_failed(actor_ip, "step_up", "bad_totp", username=username)
                raise InvalidCredentialsError("Invalid credentials") from exc
        AuditService.log_async(
            "step_up_verified",
            actor_ip=actor_ip,
            details={"username": username, "methods": list(methods)},
            actor=actor,
            target=target,
        )
        return methods

    def logout(self, session_id: str | None) -> None:
        self._session_store.delete(session_id)


def step_up_state(user: DashboardUser, *, verified_at: int | None) -> DashboardStepUpState:
    """The session response's ``step_up`` block: a still-fresh verification and the account's methods."""

    fresh = verified_at if is_step_up_fresh(verified_at, now=session_clock()) else None
    return DashboardStepUpState(
        verified_at=fresh,
        expires_at=step_up_expires_at(fresh) if fresh is not None else None,
        methods=step_up_methods(user),
    )


def assignable_role_ids() -> list[str]:
    return [PRESET_ROLE_IDS[slug] for slug in sorted(ASSIGNABLE_PRESET_ROLES, key=lambda slug: slug.value)]


async def _cached_local_auth_state() -> LocalAuthState:
    return await get_dashboard_users_cache().local_auth_state()


async def _cached_active_providers() -> list[ActiveProvider]:
    return await get_auth_provider_registry().get_active_providers(get_settings().dashboard_auth_mode)


_dashboard_session_store = DashboardSessionStore()
_step_up_cookie_store = StepUpCookieStore()
_totp_rate_limiter = DatabaseRateLimiter(max_attempts=8, window_seconds=60, type="totp")
_password_rate_limiter = DatabaseRateLimiter(max_attempts=8, window_seconds=60, type="password")
#: The ceiling on failed password logins from one address, whatever usernames
#: they name. The per-(address, username) bucket above keeps one account's
#: failures from barring another, which also means one address can mint a fresh
#: bucket per username it invents; without a coarse address bucket the endpoint
#: has no ceiling at all and every attempt costs a blocking password-hash
#: comparison on the event loop that serves the proxy.
#:
#: 60/60 s is seven and a half times the per-account allowance: a NATed office
#: whose people are all mistyping at once never reaches it (one sign-in form
#: submission is one request), while a sprayer behind one address is held to a
#: bounded number of hash comparisons per minute. It is deliberately *not* the
#: per-account limit, so the 8/60 s semantics and the tests that pin them are
#: untouched.
_password_address_rate_limiter = DatabaseRateLimiter(max_attempts=60, window_seconds=60, type="password_address")
_guest_password_rate_limiter = DatabaseRateLimiter(max_attempts=8, window_seconds=60, type="guest_password")
#: Bounds the anonymous ``login_failed`` rows a client can append from refusals
#: that by design spend no password budget (``username_required``).
_login_failed_audit_rate_limiter = DatabaseRateLimiter(max_attempts=8, window_seconds=60, type="login_failed_audit")
_invite_lookup_rate_limiter = DatabaseRateLimiter(max_attempts=30, window_seconds=60, type="invite_lookup")
_invite_accept_rate_limiter = DatabaseRateLimiter(max_attempts=8, window_seconds=60, type="invite_accept")
_invite_accept_token_rate_limiter = DatabaseRateLimiter(max_attempts=5, window_seconds=60, type="invite_accept_token")


def get_dashboard_session_store() -> DashboardSessionStore:
    return _dashboard_session_store


def get_step_up_cookie_store() -> StepUpCookieStore:
    return _step_up_cookie_store


def get_totp_rate_limiter() -> DatabaseRateLimiter:
    return _totp_rate_limiter


def get_password_rate_limiter() -> DatabaseRateLimiter:
    return _password_rate_limiter


def get_password_address_rate_limiter() -> DatabaseRateLimiter:
    return _password_address_rate_limiter


def get_guest_password_rate_limiter() -> DatabaseRateLimiter:
    return _guest_password_rate_limiter


def get_login_failed_audit_rate_limiter() -> DatabaseRateLimiter:
    return _login_failed_audit_rate_limiter


def get_invite_lookup_rate_limiter() -> DatabaseRateLimiter:
    return _invite_lookup_rate_limiter


def get_invite_accept_rate_limiter() -> DatabaseRateLimiter:
    return _invite_accept_rate_limiter


def get_invite_accept_token_rate_limiter() -> DatabaseRateLimiter:
    return _invite_accept_token_rate_limiter


def _qr_svg_data_uri(payload: str) -> str:
    qr = segno.make(payload)
    buffer = BytesIO()
    qr.save(buffer, kind="svg", xmldecl=False, scale=6, border=2)
    raw = buffer.getvalue()
    return f"data:image/svg+xml;base64,{base64.b64encode(raw).decode('ascii')}"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


@lru_cache(maxsize=1)
def _dummy_password_hash() -> str:
    """A throwaway hash verified against when no real one exists (built on first use, not at import)."""

    return hash_password(secrets.token_urlsafe(32))


def _check_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False
