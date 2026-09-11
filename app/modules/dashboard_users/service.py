"""Account management: invite, edit, disable (with the owned-key cascade), delete, invite lifecycle.

Every mutation is attributed to the calling principal (``AuditActor``), bumps
the ``dashboard_users`` cache namespace so peers drop their copy, and applies
the invariants in one place: no self role/status change, at least one active
admin preset, delegation subset checks, the compat ``admin`` lock, and the
credential-required rule.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError

import app.modules.dashboard_users.repository as users_repository
from app.core.audit.service import AuditActor, AuditDetails, AuditService, AuditSeverity, AuditTarget
from app.core.auth.api_key_cache import get_api_key_cache
from app.core.auth.dashboard_access import (
    PRESET_ROLE_IDS,
    DashboardPrincipal,
    PresetRoleSlug,
    assert_can_act_on,
    assert_can_delegate,
)
from app.core.auth.dashboard_users_cache import get_dashboard_users_cache
from app.core.auth.providers.registry import get_auth_provider_registry
from app.core.cache.invalidation import NAMESPACE_API_KEY, get_cache_invalidation_poller
from app.core.config.settings import get_settings
from app.core.utils.time import utcnow
from app.db.models import (
    COMPAT_ADMIN_USERNAME,
    AuthProviderKind,
    DashboardRoleRecord,
    DashboardUser,
    DashboardUserInvite,
    DashboardUserRoleSource,
    DashboardUserStatus,
)
from app.modules.dashboard_auth.repository import DashboardAuthRepository
from app.modules.dashboard_roles.repository import DashboardRolesRepository
from app.modules.dashboard_roles.service import resolve_assignable_role, resolve_role_grants
from app.modules.dashboard_users.break_glass import (
    BreakGlassRoleRequiredError,
    LastBreakGlassProtectedError,
    assert_break_glass_remains,
    is_admin_preset,
)
from app.modules.dashboard_users.credentials import assert_credential_remains
from app.modules.dashboard_users.repository import (
    DashboardUsersRepository,
    is_valid_email,
    is_valid_username,
    normalize_email,
    normalize_username,
)
from app.modules.dashboard_users.schemas import (
    DashboardUserCreateRequest,
    DashboardUserUpdateRequest,
    ExpectedIdentityRequest,
    ProfileUpdateRequest,
)

INVITE_TTL = timedelta(hours=24)
_AUTH_METHOD_PASSWORD = "password"


class AdminAccountRequiredError(ValueError):
    pass


class UserNotFoundError(LookupError):
    pass


class UsernameTakenError(ValueError):
    pass


class EmailTakenError(ValueError):
    pass


class InvalidUsernameError(ValueError):
    pass


class InvalidEmailError(ValueError):
    pass


class SelfModificationForbiddenError(ValueError):
    pass


class LastAdminProtectedError(ValueError):
    pass


class CompatUserLockedError(ValueError):
    pass


class InviteNotPendingError(ValueError):
    pass


class InvitePendingError(ValueError):
    pass


class InviteNotFoundError(LookupError):
    pass


class UsernameLockedError(ValueError):
    pass


class UserNotActiveError(ValueError):
    pass


class RoleManagedExternallyError(ValueError):
    pass


class ForceWithoutRoleChangeError(ValueError):
    pass


class SsoNotAvailableError(ValueError):
    pass


class IdentityTakenError(ValueError):
    pass


class SsoOnlyInviteError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class IssuedInvite:
    token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class CreatedAccount:
    """A pre-created account and its invite; the token is handed out only when ``sso_only`` is false."""

    user: DashboardUser
    invite: IssuedInvite
    sso_only: bool


@dataclass(frozen=True, slots=True)
class InviteDescription:
    role_name: str
    inviter_display_name: str | None
    suggested_username: str
    username_locked: bool
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class UserListing:
    user: DashboardUser
    pending_invite: DashboardUserInvite | None


def _now() -> datetime:
    # Looked up through the module so one patched clock drives the service and the counts alike.
    return users_repository.utc_now()


def as_utc(value: datetime) -> datetime:
    """Rows come back naive on SQLite; they were written as UTC."""

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def invite_token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def _clean(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None


def _is_active(user: DashboardUser) -> bool:
    return user.status == DashboardUserStatus.ACTIVE.value


def _is_admin_preset(user: DashboardUser) -> bool:
    return is_admin_preset(user.role_id)


def _user_actor(user: DashboardUser) -> AuditActor:
    return AuditActor(
        user_id=user.id, username=user.username, role_slug=user.role.slug, auth_method=_AUTH_METHOD_PASSWORD
    )


class DashboardUsersService:
    def __init__(
        self,
        repository: DashboardUsersRepository,
        roles: DashboardRolesRepository,
        auth_repository: DashboardAuthRepository,
    ) -> None:
        self._repo = repository
        self._roles = roles
        self._auth = auth_repository

    # --- reads ---

    async def list_users(self) -> list[UserListing]:
        await self._purge_expired()
        now = _now()
        invites = {invite.user_id: invite for invite in await self._repo.list_live_invites(now)}
        return [UserListing(user=user, pending_invite=invites.get(user.id)) for user in await self._repo.list_users()]

    async def list_pending_invites(self) -> Sequence[DashboardUserInvite]:
        await self._purge_expired()
        return await self._repo.list_live_invites(_now())

    # --- account lifecycle ---

    async def create_user(
        self, principal: DashboardPrincipal, payload: DashboardUserCreateRequest, *, actor_ip: str | None
    ) -> CreatedAccount:
        """Pre-create an account. With a link (the default) the person sets a password;
        SSO-only accounts get no link and are activated by their first provider sign-in
        matching ``expected_identity`` exactly."""

        caller_id = self._require_account(principal)
        await self._purge_expired()
        username = self._new_username(payload.username)
        email = self._normalized_email(payload.email)
        role = await self._assignable_role(payload.role_id)
        assert_can_delegate(principal.grants, resolve_role_grants(role))
        expected = await self._expected_identity(payload)
        if await self._repo.get_by_username(username) is not None:
            raise UsernameTakenError("Username is already taken")
        if email is not None and await self._repo.get_by_email(email) is not None:
            raise EmailTakenError("E-mail is already in use")
        user = DashboardUser(
            id=str(uuid.uuid4()),
            username=username,
            display_name=_clean(payload.display_name),
            email=email,
            role_id=role.id,
            role_source=DashboardUserRoleSource.MANUAL.value,
            status=DashboardUserStatus.INVITED.value,
            created_by_user_id=caller_id,
        )
        issued, invite = self._new_invite(
            user.id,
            created_by_user_id=caller_id,
            username_locked=payload.username_locked,
            expires_at=_now() + INVITE_TTL,
        )
        invite.sso_only = payload.sso_only
        if expected is not None:
            invite.expected_provider = expected.provider
            invite.expected_provider_key = expected.provider_key
            invite.expected_subject = expected.subject
        role_slug = role.slug
        self._repo.add(user, invite)
        try:
            user = await self._repo.commit_user(user.id)
        except IntegrityError as exc:
            conflict = await self._repo.conflicting_field(username, email)
            if conflict == "email":
                raise EmailTakenError("E-mail is already in use") from exc
            if conflict is None and expected is not None:
                # The partial unique index on the open expected identity fired: a concurrent create won.
                raise IdentityTakenError("Another pending account already waits for that identity") from exc
            raise UsernameTakenError("Username is already taken") from exc
        await self._invalidate_users()
        self._audit("user_created", principal, user.id, actor_ip, {"username": user.username, "role": role_slug})
        invited: AuditDetails = {"sso_only": payload.sso_only}
        if not payload.sso_only:
            invited = {**invited, "expires_at": issued.expires_at.isoformat()}
        if expected is not None:
            invited = {**invited, "expected_provider": expected.provider, "expected_subject": expected.subject}
        self._audit("user_invited", principal, user.id, actor_ip, invited)
        return CreatedAccount(user=user, invite=issued, sso_only=payload.sso_only)

    @staticmethod
    def _new_username(raw: str) -> str:
        """Normalise and validate a username chosen for a person; ``admin`` stays the break-glass account's."""

        username = normalize_username(raw)
        if not is_valid_username(username):
            raise InvalidUsernameError("Username must be 1-64 characters of a-z, 0-9, '.', '_' or '-'")
        if username == COMPAT_ADMIN_USERNAME:
            raise InvalidUsernameError("'admin' is reserved for the local break-glass account")
        return username

    async def _expected_identity(self, payload: DashboardUserCreateRequest) -> ExpectedIdentityRequest | None:
        """Validate the SSO fields: they need an active non-password provider, and the
        identity must not be linked yet. Trusted-header subjects are case-folded like the
        resolver does, so the first sign-in matches regardless of the proxy's spelling."""

        expected = payload.expected_identity
        if not payload.sso_only and expected is None:
            return None
        if expected is None:
            raise InvalidUsernameError("An SSO-only account needs the identity it will sign in with")
        mode = get_settings().dashboard_auth_mode
        active = {
            (item.row.kind, item.row.provider_key)
            for item in await get_auth_provider_registry().get_active_providers(mode)
            if item.row.kind != AuthProviderKind.PASSWORD.value
        }
        if (expected.provider, expected.provider_key) not in active:
            raise SsoNotAvailableError("No sign-in provider other than the password is active")
        subject = expected.subject.strip()
        if expected.provider == AuthProviderKind.TRUSTED_HEADER.value:
            subject = subject.casefold()
        if not subject:
            raise InvalidUsernameError("The expected identity must not be empty")
        if await self._repo.get_identity(expected.provider, expected.provider_key, subject) is not None:
            raise IdentityTakenError("That identity already belongs to an account")
        if await self._repo.find_invite_expecting_identity(expected.provider, expected.provider_key, subject, _now()):
            raise IdentityTakenError("Another pending account already waits for that identity")
        return ExpectedIdentityRequest(provider=expected.provider, provider_key=expected.provider_key, subject=subject)

    async def update_user(
        self, principal: DashboardPrincipal, user_id: str, payload: DashboardUserUpdateRequest, *, actor_ip: str | None
    ) -> UserListing:
        """Rules, in this order: compat lock, self, invite pending, externally
        managed role, delegation (new role), act-on (current role), last admin,
        last qualifying break-glass, credential required."""

        caller_id = self._require_account(principal)
        await self._purge_expired()
        await self._repo.acquire_write_intent()
        user = await self._get(user_id)
        is_self = user.id == caller_id
        fields = payload.model_fields_set
        role_changes = payload.role_id is not None and payload.role_id != user.role_id
        new_status = payload.status if payload.status is not None and payload.status != user.status else None
        designation = (
            payload.is_break_glass
            if payload.is_break_glass is not None and payload.is_break_glass != user.is_break_glass
            else None
        )
        if (role_changes or new_status is not None) and user.username == COMPAT_ADMIN_USERNAME:
            raise CompatUserLockedError("The migrated 'admin' account keeps its role and status in this release")
        if (role_changes or new_status is not None) and is_self:
            raise SelfModificationForbiddenError("You cannot change your own role or status")
        if new_status is not None and user.status == DashboardUserStatus.INVITED.value:
            raise InvitePendingError("The account has not accepted its invite yet; revoke the invite instead")
        if payload.force and not role_changes:
            raise ForceWithoutRoleChangeError("force only applies to a role change")
        # A role a sign-in provider manages is only edited on purpose: without
        # ``force`` the next sign-in would silently move it back.
        overridden_source = (
            user.role_source if role_changes and user.role_source != DashboardUserRoleSource.MANUAL.value else None
        )
        if overridden_source is not None and not payload.force:
            raise RoleManagedExternallyError(
                "That account's role is managed by its sign-in method; repeat with force to take it over"
            )
        designation_takes_over = designation is True and user.role_source != DashboardUserRoleSource.MANUAL.value
        if overridden_source is None and designation_takes_over:
            # Designating an emergency account takes its role over by hand;
            # it must not be a role the next sign-in can move.
            overridden_source = user.role_source
        new_role: DashboardRoleRecord | None = None
        if role_changes:
            assert payload.role_id is not None
            new_role = await self._assignable_role(payload.role_id)
            assert_can_delegate(principal.grants, resolve_role_grants(new_role))
        if not is_self:
            assert_can_act_on(principal.grants, resolve_role_grants(user.role))
        role_id_after = new_role.id if new_role is not None else user.role_id
        status_after = new_status or user.status
        # The designation says "this account is the way in when the identity
        # provider is not": it is meaningless on any other role, and it must
        # never be a role a provider can re-evaluate away. Asking for it on a
        # non-admin is a mistake; a role change that moves a designated
        # account off admin simply drops it -- and then meets the guard below.
        if designation is True and not is_admin_preset(role_id_after):
            raise BreakGlassRoleRequiredError("Only an admin account can be an emergency (break-glass) account")
        designation_after = (user.is_break_glass if designation is None else designation) and is_admin_preset(
            role_id_after
        )
        leaves_admin = (
            _is_active(user)
            and _is_admin_preset(user)
            and not (role_id_after == PRESET_ROLE_IDS[PresetRoleSlug.ADMIN] and status_after == "active")
        )
        if leaves_admin:
            await self._assert_other_active_admin(user.id)
        # One guard, whatever the field: demoting, disabling and clearing the
        # designation are the same question to the install.
        loses_break_glass = await self.assert_break_glass_remains(
            user, role_id=role_id_after, status=status_after, is_break_glass=designation_after
        )
        if new_status == DashboardUserStatus.ACTIVE.value:
            assert_credential_remains(
                password_hash=user.password_hash,
                identity_count=await self._repo.count_identities(user.id),
                solo_install=False,
            )

        old_role_slug = user.role.slug
        new_role_slug = new_role.slug if new_role is not None else None
        key_hashes: list[str] = []
        try:
            profile_changed = await self._apply_profile(user, payload, fields)
            if new_status == DashboardUserStatus.DISABLED.value:
                key_hashes = await self._repo.deactivate_owned_keys(user.id)
            if overridden_source is not None or designation_after:
                # Taken over by hand (or designated): no later re-evaluation
                # moves this role again -- which is what makes the mapping
                # exemption a fact rather than a check.
                user.role_source = DashboardUserRoleSource.MANUAL.value
            if leaves_admin or loses_break_glass:
                # The invariants are part of the write: the row only changes
                # while another active admin (and, once local sign-in is
                # restricted, another qualifying emergency account) exists at
                # the moment of the UPDATE.
                if not await self._repo.update_role_status_guarded(
                    user.id,
                    role_id=role_id_after,
                    status=status_after,
                    is_break_glass=designation_after,
                    require_other_admin=leaves_admin,
                    require_other_break_glass=loses_break_glass,
                ):
                    await self._repo.rollback()
                    if leaves_admin and await self._repo.count_active_admins(exclude_user_id=user.id) == 0:
                        raise LastAdminProtectedError("At least one active admin account must remain")
                    raise LastBreakGlassProtectedError(
                        "This is the only emergency account that can still sign in while local sign-in is "
                        "restricted; designate another admin with two-factor first"
                    )
            else:
                user.role_id = role_id_after
                user.status = status_after
                user.is_break_glass = designation_after
            bump = new_role is not None or new_status == DashboardUserStatus.DISABLED.value
            user = await self._repo.commit_user(user.id, bump_generation=bump)
        except IntegrityError as exc:
            await self._repo.rollback()
            raise EmailTakenError("E-mail is already in use") from exc
        await self._invalidate_users()
        await self._invalidate_api_keys(key_hashes)

        if profile_changed or designation is not None:
            self._audit(
                "user_updated",
                principal,
                user.id,
                actor_ip,
                {"username": user.username, "is_break_glass": user.is_break_glass}
                if designation is not None
                else {"username": user.username},
            )
        if new_role is not None:
            self._audit(
                "user_role_changed",
                principal,
                user.id,
                actor_ip,
                {"username": user.username, "from": old_role_slug, "to": new_role_slug},
            )
        if overridden_source is not None:
            self._audit(
                "role_source_overridden",
                principal,
                user.id,
                actor_ip,
                {
                    "username": user.username,
                    "from_source": overridden_source,
                    "to_source": DashboardUserRoleSource.MANUAL.value,
                    "provider": await self._repo.primary_identity_provider(user.id),
                },
            )
        if new_status == DashboardUserStatus.DISABLED.value:
            self._audit("user_disabled", principal, user.id, actor_ip, {"username": user.username})
            self._audit("user_keys_deactivated", principal, user.id, actor_ip, {"count": len(key_hashes)})
        elif new_status == DashboardUserStatus.ACTIVE.value:
            self._audit("user_enabled", principal, user.id, actor_ip, {"username": user.username})
        return UserListing(user=user, pending_invite=await self._repo.live_invite_for_user(user.id, _now()))

    async def update_profile(
        self, user: DashboardUser, payload: ProfileUpdateRequest, *, actor_ip: str | None
    ) -> DashboardUser:
        """Self-service edit: display name and e-mail only, audited as the account itself."""

        user = await self._get(user.id)
        changed = await self._apply_profile(user, payload, payload.model_fields_set)
        try:
            user = await self._repo.commit_user(user.id)
        except IntegrityError as exc:
            raise EmailTakenError("E-mail is already in use") from exc
        if changed:
            await self._invalidate_users()
            AuditService.log_async(
                "user_updated",
                actor_ip=actor_ip,
                details={"username": user.username, "self": True},
                actor=_user_actor(user),
                target=AuditTarget("user", user.id),
            )
        return user

    async def delete_user(self, principal: DashboardPrincipal, user_id: str, *, actor_ip: str | None) -> None:
        caller_id = self._require_account(principal)
        await self._purge_expired()
        await self._repo.acquire_write_intent()
        user = await self._get(user_id)
        if user.id == caller_id:
            raise SelfModificationForbiddenError("You cannot delete your own account")
        if user.username == COMPAT_ADMIN_USERNAME:
            raise CompatUserLockedError("The migrated 'admin' account cannot be deleted in this release")
        counts_as_admin = _is_active(user) and _is_admin_preset(user)
        if counts_as_admin:
            await self._assert_other_active_admin(user.id)
        counts_as_break_glass = await self.assert_break_glass_remains(user, is_break_glass=False)
        assert_can_act_on(principal.grants, resolve_role_grants(user.role))
        details: AuditDetails = {"username": user.username, "role": user.role.slug}
        key_hashes = await self._repo.delete_user(
            user, require_other_admin=counts_as_admin, require_other_break_glass=counts_as_break_glass
        )
        if key_hashes is None:
            if counts_as_admin and await self._repo.count_active_admins(exclude_user_id=user_id) == 0:
                raise LastAdminProtectedError("At least one active admin account must remain")
            raise LastBreakGlassProtectedError(
                "This is the only emergency account that can still sign in while local sign-in is restricted; "
                "designate another admin with two-factor first"
            )
        await self._invalidate_users()
        await self._invalidate_api_keys(key_hashes)
        self._audit("user_deleted", principal, user_id, actor_ip, details)
        if key_hashes:
            self._audit("user_keys_deactivated", principal, user_id, actor_ip, {"count": len(key_hashes)})

    # --- the break-glass invariant (one guard, every mutation) ---

    async def assert_break_glass_remains(
        self,
        user: DashboardUser,
        *,
        role_id: str | None = None,
        status: str | None = None,
        is_break_glass: bool | None = None,
        has_totp: bool | None = None,
        has_password: bool | None = None,
    ) -> bool:
        """Refuse a change that would leave no qualifying emergency account (PLAN §4.2).

        The single entry point for every call site: the role and status
        branches of the account PATCH, deletion, clearing the designation,
        self-service ``/totp/disable`` (through the auth service, which calls
        the same free function), the administrative TOTP reset, and
        :meth:`deactivate_user`. Each keyword is the value the field would
        carry after the write. Returns whether the caller's conditional write
        must re-apply the count (see the free function).
        """

        settings = await self._auth.get_settings()
        return await assert_break_glass_remains(
            user,
            policy=settings.local_login_policy,
            count_other_qualifying=lambda: self._repo.count_qualifying_break_glass(exclude_user_id=user.id),
            role_id=role_id,
            status=status,
            is_break_glass=is_break_glass,
            has_totp=has_totp,
            has_password=has_password,
        )

    async def deactivate_user(
        self,
        user_id: str,
        *,
        actor: AuditActor,
        actor_ip: str | None,
        source: str,
    ) -> bool:
        """Disable an account through one back-channel: guard, status, generation, key cascade, audit.

        This is the shared path for deactivations that do not come from the
        account PATCH — the SCIM ``active=false`` endpoint of Phase 3b and the
        identity resolver — so none of them can bypass the break-glass guard.
        A refusal audits ``scim_deprovision_refused`` next to raising, because
        the caller is a machine whose 409 nobody reads. Returns ``False`` when
        the account was already inactive (nothing to do, nothing audited).
        """

        await self._repo.acquire_write_intent()
        user = await self._get(user_id)
        if not _is_active(user):
            return False
        try:
            guarded = await self.assert_break_glass_remains(user, status=DashboardUserStatus.DISABLED.value)
        except LastBreakGlassProtectedError:
            AuditService.log_async(
                "scim_deprovision_refused",
                actor_ip=actor_ip,
                details={"username": user.username, "source": source, "reason": "last_break_glass_protected"},
                actor=actor,
                target=AuditTarget("user", user.id),
                severity=AuditSeverity.WARNING,
            )
            raise
        if _is_admin_preset(user):
            await self._assert_other_active_admin(user.id)
        key_hashes = await self._repo.deactivate_owned_keys(user.id)
        if not await self._repo.update_role_status_guarded(
            user.id,
            role_id=user.role_id,
            status=DashboardUserStatus.DISABLED.value,
            require_other_admin=_is_admin_preset(user),
            require_other_break_glass=guarded,
        ):
            await self._repo.rollback()
            raise LastAdminProtectedError("At least one active admin account must remain")
        username = user.username
        await self._repo.commit_user(user.id, bump_generation=True)
        await self._invalidate_users()
        await self._invalidate_api_keys(key_hashes)
        AuditService.log_async(
            "user_disabled",
            actor_ip=actor_ip,
            details={"username": username, "source": source},
            actor=actor,
            target=AuditTarget("user", user_id),
        )
        AuditService.log_async(
            "user_keys_deactivated",
            actor_ip=actor_ip,
            details={"count": len(key_hashes), "source": source},
            actor=actor,
            target=AuditTarget("user", user_id),
        )
        return True

    # --- invites (management side) ---

    async def resend_invite(self, principal: DashboardPrincipal, user_id: str, *, actor_ip: str | None) -> IssuedInvite:
        caller_id = self._require_account(principal)
        await self._purge_expired()
        user = await self._get(user_id)
        if user.status != DashboardUserStatus.INVITED.value:
            raise InviteNotPendingError("The account has no pending invite")
        current = await self._repo.get_invite_for_user(user.id)
        if current is not None and current.sso_only:
            raise SsoOnlyInviteError("This account signs in through a provider; there is no link to resend")
        assert_can_act_on(principal.grants, resolve_role_grants(user.role))
        issued, fresh = self._new_invite(
            user.id, created_by_user_id=caller_id, username_locked=False, expires_at=_now() + INVITE_TTL
        )
        # Conditional on the account still being invited: an acceptance that
        # committed since the read must not have its consumed invite re-armed.
        rotated = await self._repo.rotate_invite(user.id, token_hash=fresh.token_hash, expires_at=fresh.expires_at)
        if not rotated:
            await self._repo.rollback()
            raise InviteNotPendingError("The account has no pending invite")
        user = await self._repo.commit_user(user.id)
        await self._invalidate_users()
        self._audit("invite_resent", principal, user.id, actor_ip, {"username": user.username})
        return issued

    async def revoke_invite(self, principal: DashboardPrincipal, user_id: str, *, actor_ip: str | None) -> None:
        self._require_account(principal)
        await self._purge_expired()
        user = await self._get(user_id)
        assert_can_act_on(principal.grants, resolve_role_grants(user.role))
        username = user.username
        if user.status == DashboardUserStatus.INVITED.value:
            # An invited account has no credential, no keys and no sessions: the
            # row goes with the link -- unless an acceptance committed meanwhile.
            if await self._repo.delete_user(user, only_while_invited=True) is None:
                raise InviteNotPendingError("The account has no pending invite")
        else:
            invite = await self._repo.get_invite_for_user(user.id)
            if invite is None or invite.consumed_at is not None or invite.revoked_at is not None:
                raise InviteNotPendingError("The account has no pending invite")
            invite.revoked_at = _now()
            await self._repo.commit_user(user.id)
        await self._invalidate_users()
        self._audit("invite_revoked", principal, user_id, actor_ip, {"username": username})

    # --- account actions ---

    async def reset_totp(self, principal: DashboardPrincipal, user_id: str, *, actor_ip: str | None) -> None:
        caller_id = self._require_account(principal)
        await self._purge_expired()
        # Serialised with the other account mutations *before the first read*,
        # exactly as the account PATCH does, and held until the secret write
        # commits: the write has no conditional form, so the lock is the only
        # thing stopping two admins from clearing the last two second factors
        # after both passed the count. Acquiring before the first read also
        # keeps ``BEGIN IMMEDIATE`` on its primary path rather than the
        # in-transaction fallback.
        await self._repo.acquire_write_intent()
        user = await self._get(user_id)
        if user.id == caller_id:
            raise SelfModificationForbiddenError("Disable your own TOTP through /totp/disable")
        if user.username == COMPAT_ADMIN_USERNAME and (await self._auth.get_settings()).totp_required_on_login:
            # A previous-release replica reads the legacy columns: without a
            # secret and with the policy on it would refuse this account forever.
            raise CompatUserLockedError("Turn off 'require TOTP on login' before resetting the 'admin' account's TOTP")
        assert_can_act_on(principal.grants, resolve_role_grants(user.role))
        await self.assert_break_glass_remains(user, has_totp=False)
        await self._auth.set_user_totp_secret(user.id, None, bump_generation=True, preserve_policy=True)
        await self._invalidate_users()
        self._audit("user_totp_reset", principal, user.id, actor_ip, {"username": user.username})

    async def revoke_sessions(self, principal: DashboardPrincipal, user_id: str, *, actor_ip: str | None) -> None:
        self._require_account(principal)
        await self._purge_expired()
        user = await self._get(user_id)
        assert_can_act_on(principal.grants, resolve_role_grants(user.role))
        await self._auth.bump_session_generation(user.id)
        await self._invalidate_users()
        self._audit(
            "user_sessions_revoked", principal, user.id, actor_ip, {"username": user.username, "scope": "admin"}
        )

    async def reactivate_keys(self, principal: DashboardPrincipal, user_id: str, *, actor_ip: str | None) -> int:
        self._require_account(principal)
        await self._purge_expired()
        # Serialised with disable (same write-intent lock); the UPDATE itself is
        # also conditional on the owner still being active.
        await self._repo.acquire_write_intent()
        user = await self._get(user_id)
        if not _is_active(user):
            raise UserNotActiveError("Keys can only be restored for an active account")
        assert_can_act_on(principal.grants, resolve_role_grants(user.role))
        key_hashes = await self._repo.reactivate_owner_disabled_keys(user.id)
        if key_hashes is None:
            raise UserNotActiveError("Keys can only be restored for an active account")
        await self._invalidate_api_keys(key_hashes)
        self._audit("user_keys_reactivated", principal, user.id, actor_ip, {"count": len(key_hashes)})
        return len(key_hashes)

    # --- invites (public side) ---

    async def describe_invite(self, token: str) -> InviteDescription:
        invite = await self._pending_invite(token)
        inviter = await self._repo.get_by_id(invite.created_by_user_id)
        return InviteDescription(
            role_name=invite.user.role.name,
            inviter_display_name=(inviter.display_name or inviter.username) if inviter is not None else None,
            suggested_username=invite.user.username,
            username_locked=invite.username_locked,
            expires_at=as_utc(invite.expires_at),
        )

    async def accept_invite(
        self,
        token: str,
        *,
        username: str | None,
        password_hash: str,
        display_name: str | None,
        actor_ip: str | None,
    ) -> DashboardUser:
        """Activate the invited account; the invite is consumed compare-and-set so a link works once."""

        invite = await self._pending_invite(token)
        user = invite.user
        try:
            if username is not None and normalize_username(username) != user.username:
                if invite.username_locked:
                    raise UsernameLockedError("The username of this invite cannot be changed")
                normalized = self._new_username(username)
                if await self._repo.get_by_username(normalized) is not None:
                    raise UsernameTakenError("Username is already taken")
                user.username = normalized
            if display_name is not None:
                user.display_name = _clean(display_name)
            user.password_hash = password_hash
            user.status = DashboardUserStatus.ACTIVE.value
            user.last_login_at = utcnow()
            if not await self._repo.consume_invite(invite.id, token_hash=invite_token_hash(token), now=_now()):
                await self._repo.rollback()
                raise InviteNotFoundError("This invite is no longer valid")
            user = await self._repo.commit_user(user.id, bump_generation=True)
        except IntegrityError as exc:
            # The autoflush inside consume_invite (or the commit) hit the unique
            # username: a concurrent create or accept won the name.
            await self._repo.rollback()
            raise UsernameTakenError("Username is already taken") from exc
        await self._invalidate_users()
        AuditService.log_async(
            "invite_accepted",
            actor_ip=actor_ip,
            details={"username": user.username, "role": user.role.slug},
            actor=_user_actor(user),
            target=AuditTarget("user", user.id),
        )
        return user

    # --- helpers ---

    async def _pending_invite(self, token: str) -> DashboardUserInvite:
        """Expired, consumed, revoked and unknown tokens are indistinguishable to the caller."""

        invite = await self._repo.get_invite_by_token_hash(invite_token_hash(token))
        if (
            invite is None
            or invite.sso_only  # no link exists for an SSO-only account, whatever the token claims
            or invite.consumed_at is not None
            or invite.revoked_at is not None
            or as_utc(invite.expires_at) <= _now()
            or invite.user.status != DashboardUserStatus.INVITED.value
        ):
            raise InviteNotFoundError("This invite is no longer valid")
        return invite

    @staticmethod
    def _new_invite(
        user_id: str, *, created_by_user_id: str, username_locked: bool, expires_at: datetime
    ) -> tuple[IssuedInvite, DashboardUserInvite]:
        """A fresh token: the plaintext goes to the caller once, only its hash is stored."""

        token = secrets.token_urlsafe(32)
        invite = DashboardUserInvite(
            id=str(uuid.uuid4()),
            user_id=user_id,
            token_hash=invite_token_hash(token),
            expires_at=expires_at,
            created_by_user_id=created_by_user_id,
            username_locked=username_locked,
        )
        return IssuedInvite(token=token, expires_at=expires_at), invite

    async def _purge_expired(self) -> None:
        if await self._repo.purge_expired_invited_users(_now()):
            await self._invalidate_users()

    async def _get(self, user_id: str) -> DashboardUser:
        user = await self._repo.get_by_id(user_id)
        if user is None:
            raise UserNotFoundError("Account not found")
        return user

    async def _assignable_role(self, role_id: str) -> DashboardRoleRecord:
        return await resolve_assignable_role(self._roles, role_id)

    async def _assert_other_active_admin(self, user_id: str) -> None:
        if await self._repo.count_active_admins(exclude_user_id=user_id) == 0:
            raise LastAdminProtectedError("At least one active admin account must remain")

    async def _apply_profile(
        self, user: DashboardUser, payload: DashboardUserUpdateRequest | ProfileUpdateRequest, fields: set[str]
    ) -> bool:
        changed = False
        if "display_name" in fields and _clean(payload.display_name) != user.display_name:
            user.display_name = _clean(payload.display_name)
            changed = True
        if "email" in fields:
            email = self._normalized_email(payload.email)
            if email != user.email:
                if email is not None:
                    other = await self._repo.get_by_email(email)
                    if other is not None and other.id != user.id:
                        raise EmailTakenError("E-mail is already in use")
                user.email = email
                changed = True
        return changed

    @staticmethod
    def _normalized_email(value: str | None) -> str | None:
        email = normalize_email(value)
        if email is not None and not is_valid_email(email):
            raise InvalidEmailError("E-mail address is not valid")
        return email

    @staticmethod
    def _require_account(principal: DashboardPrincipal) -> str:
        if principal.user_id is None:
            raise AdminAccountRequiredError("Set a dashboard password and sign in before managing accounts")
        return principal.user_id

    @staticmethod
    def _audit(
        action: str, principal: DashboardPrincipal, user_id: str, actor_ip: str | None, details: AuditDetails
    ) -> None:
        AuditService.log_async(
            action,
            actor_ip=actor_ip,
            details=details,
            actor=AuditActor.from_principal(principal),
            target=AuditTarget("user", user_id),
        )

    @staticmethod
    async def _invalidate_users() -> None:
        await get_dashboard_users_cache().invalidate()

    @staticmethod
    async def _invalidate_api_keys(key_hashes: Sequence[str]) -> None:
        if not key_hashes:
            return
        cache = get_api_key_cache()
        for key_hash in key_hashes:
            await cache.invalidate(key_hash)
        poller = get_cache_invalidation_poller()
        if poller is not None:
            await poller.bump(NAMESPACE_API_KEY)
