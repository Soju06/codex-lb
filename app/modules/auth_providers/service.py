"""Provider settings: read the rows, edit the resolver knobs, audit the change.

A provider row can be turned on and off here (whether it then *serves* also
depends on ``CODEX_LB_DASHBOARD_AUTH_MODE``); turning on a method that offers
no local password fallback needs a qualifying break-glass account, for the
same reason tightening ``local_login_policy`` does, and turning on the OIDC
row additionally needs the acting admin's own browser to have completed a test
sign-in against this exact configuration in the last ten minutes (a connected
identity provider that does not work is a lockout, so it is proven before it
is trusted). Role handouts go through the same assignability and delegation
rules as inviting a person: an operator cannot make the proxy hand out admin.

Two levers here are bounded harder than the role fields, because they reach
further than any role does: writing the identity provider's connection
document, and turning e-mail linking on. Both let whoever holds them decide
*who an inbound identity is* rather than what that identity may do, so both
require the caller to already hold every grant any account could hold
(:func:`_assert_can_repoint`). Holding ``security:write`` is permission to
administer sign-in, not permission to become somebody else.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Final

from app.core.audit.service import AuditActor, AuditDetails, AuditService, AuditSeverity, AuditTarget
from app.core.auth.dashboard_access import ADMIN_GRANTS, DashboardPrincipal, assert_can_delegate
from app.core.auth.providers.registry import get_auth_provider_registry
from app.core.utils.time import to_utc_naive, utcnow
from app.db.models import AuthProviderKind, DashboardAuthProvider
from app.modules.auth_providers.config import (
    ConfigNotSupportedError,
    InvalidProviderConfigError,
    OidcProviderConfig,
    build_oidc_config,
    load_oidc_config,
    seal_oidc_config,
)
from app.modules.auth_providers.repository import AuthProvidersRepository
from app.modules.auth_providers.schemas import AuthProviderUpdateRequest, OidcConfigRequest
from app.modules.dashboard_roles.repository import DashboardRolesRepository
from app.modules.dashboard_roles.service import resolve_assignable_role, resolve_role_grants
from app.modules.dashboard_users.break_glass import BreakGlassRequiresTotpError


class ProviderNotFoundError(LookupError):
    pass


class OidcTestLoginRequiredError(Exception):
    """The acting admin has no fresh test sign-in against this configuration."""


_ROLE_FIELDS = ("unknown_identity_role_id", "no_match_role_id")
_FLAG_FIELDS = ("label", "enabled", "link_by_email", "skip_role_sync", "idp_mfa_enforced")
#: Levers on this endpoint whose reach is *every* account. Delegation-bounded
#: by every grant rather than by the grants of one role -- see
#: :func:`_assert_can_repoint`, which is called wherever they are written.
ADMIN_BOUND_FIELDS: Final[frozenset[str]] = frozenset({"config", "link_by_email"})
#: Sign-in methods that cannot fall back to a local password of their own.
_PASSWORDLESS_KINDS = frozenset({AuthProviderKind.TRUSTED_HEADER.value, AuthProviderKind.OIDC.value})
#: How long a pre-flight test sign-in arms the enable (PLAN §4.6). Short on
#: purpose: the proof is that *this* admin's browser reached *this* identity
#: provider just now, not that someone once could.
TEST_LOGIN_MAX_AGE: Final[timedelta] = timedelta(minutes=10)


def _assert_can_repoint(principal: DashboardPrincipal) -> None:
    """The delegation rule for the levers in :data:`ADMIN_BOUND_FIELDS`.

    The role fields on this endpoint hand out one named role, so delegation
    compares the caller against *that role's* grants. These two hand out no
    role and reach further than any role does. An identity provider asserts
    **who someone is**: whoever repoints the connection can point it at an
    identity provider they control, mint a token whose ``sub`` is an existing
    admin's subject, and be resolved into that admin's account. Turning e-mail
    linking on widens the same move from "accounts that already have an
    identity here" to "any active account whose address you know".

    So the only caller who cannot escalate by writing either is one who already
    holds everything any account could hold. ``security:write`` is what lets an
    operator *administer* sign-in; it is not what lets them decide who the
    identity provider may claim to be. ``InsufficientDelegationError`` is what
    the API already turns into ``403 insufficient_delegation``.
    """

    assert_can_delegate(principal.grants, ADMIN_GRANTS)


def _oidc_config_from(payload: OidcConfigRequest) -> OidcProviderConfig:
    """The request body through the one validator, so the API and the flow agree.

    ``exclude_none`` is what makes the optional fields mean "use the default":
    an absent discovery URL is derived from the issuer and an absent claim name
    falls back to its specification default, both inside
    :func:`build_oidc_config` rather than here.
    """

    return build_oidc_config(payload.model_dump(exclude_none=True))


class AuthProvidersService:
    def __init__(self, repository: AuthProvidersRepository, roles: DashboardRolesRepository) -> None:
        self._repo = repository
        self._roles = roles

    async def list_providers(self) -> list[DashboardAuthProvider]:
        return list(await self._repo.list_providers())

    async def update_provider(
        self,
        principal: DashboardPrincipal,
        provider_id: str,
        payload: AuthProviderUpdateRequest,
        *,
        actor_ip: str | None,
    ) -> DashboardAuthProvider:
        # Same reason as the settings tightening gate: the break-glass count
        # below and the provider write are one atomic step, so the accounts lock
        # is taken before this service reads anything and held until ``commit``.
        # Only a request that could turn a provider on takes it.
        if payload.enabled is True:
            await self._repo.acquire_account_write_intent()
        provider = await self._repo.get_provider(provider_id)
        if provider is None:
            raise ProviderNotFoundError("Provider not found")
        fields = payload.model_fields_set
        changes: dict[str, str | bool | None] = {}
        for name in _ROLE_FIELDS:
            if name not in fields:
                continue
            role_id: str | None = getattr(payload, name)
            if role_id is not None:
                role = await resolve_assignable_role(self._roles, role_id)
                assert_can_delegate(principal.grants, resolve_role_grants(role))
            if getattr(provider, name) != role_id:
                setattr(provider, name, role_id)
                changes[name] = role_id
        # Before the flags, so a body that repoints the identity provider and
        # enables it in one request is refused: the connection write clears the
        # proof that the enable then looks for.
        if "config" in fields:
            _assert_can_repoint(principal)
            if self._write_config(provider, payload.config):
                changes["config"] = True
        enabling = False
        for name in _FLAG_FIELDS:
            if name not in fields:
                continue
            value = getattr(payload, name)
            if value is not None and getattr(provider, name) != value:
                if name == "enabled" and value is True:
                    # Authorization before the two lockout gates, so a caller
                    # that may not arm this row is told that and not "enrol a
                    # second factor first".
                    await self._assert_may_hand_out(provider, principal)
                    await self._assert_break_glass_ready(provider)
                    self._assert_test_login_proof(provider, principal)
                    enabling = True
                if name in ADMIN_BOUND_FIELDS and value is True:
                    # Only the widening direction: turning e-mail linking back
                    # off narrows what the provider may do, and the recovery
                    # direction is never gated.
                    _assert_can_repoint(principal)
                setattr(provider, name, value)
                changes[name] = value
        if enabling:
            # Spent by the enable it armed, so an operator who later turns the
            # provider off cannot turn it back on without proving it again.
            provider.test_login_user_id = None
            provider.test_login_verified_at = None
        if not changes:
            return provider
        provider = await self._repo.commit(provider)
        await get_auth_provider_registry().invalidate()
        details: AuditDetails = {"kind": provider.kind, "provider_key": provider.provider_key, **changes}
        AuditService.log_async(
            "provider_updated",
            actor_ip=actor_ip,
            details=details,
            actor=AuditActor.from_principal(principal),
            target=AuditTarget("auth_provider", provider.id),
        )
        if "enabled" in changes:
            AuditService.log_async(
                "provider_enabled" if provider.enabled else "provider_disabled",
                actor_ip=actor_ip,
                details={"kind": provider.kind, "provider_key": provider.provider_key},
                actor=AuditActor.from_principal(principal),
                target=AuditTarget("auth_provider", provider.id),
                severity=AuditSeverity.WARNING,
            )
        return provider

    def _write_config(self, provider: DashboardAuthProvider, payload: OidcConfigRequest | None) -> bool:
        """Seal a new connection document onto the row; ``True`` when it changed.

        Only the OIDC row has one. The comparison is between the *decrypted*
        documents, not the blobs: the encryption is randomised, so two seals of
        the same settings never match and an unchanged re-save would otherwise
        look like a repoint and throw away a valid test login.
        """

        if provider.kind != AuthProviderKind.OIDC.value:
            raise ConfigNotSupportedError(
                f"The '{provider.kind}' sign-in method has no editable configuration",
                param="config",
            )
        if payload is None:
            # ``config: null`` is not "forget the connection": a provider is
            # retired by disabling it, and a row that is enabled with nothing
            # to connect to would refuse every sign-in silently.
            raise InvalidProviderConfigError("config: is required", param="config")
        config = _oidc_config_from(payload)
        if load_oidc_config(provider) == config:
            return False
        provider.config_encrypted = seal_oidc_config(config)
        # The proof said "this admin's browser reached *that* identity
        # provider"; it says nothing about this one.
        provider.test_login_user_id = None
        provider.test_login_verified_at = None
        return True

    async def _assert_may_hand_out(self, provider: DashboardAuthProvider, principal: DashboardPrincipal) -> None:
        """Turning a row on arms the roles it ALREADY hands out, so they are delegated too.

        The role fields are bounded when they are *written*, which leaves the
        row a caller never wrote: the seeded reverse-proxy row hands unknown
        identities the admin preset, and a rule written by an admin can hand
        out anything. Enabling such a row is the same act as writing its role,
        performed without naming it — the exact case the role-mappings surface
        refuses with :meth:`_assert_may_hand_out` there. ``get_role`` rather
        than ``resolve_assignable_role``: a role that later stopped being
        assignable must still refuse the caller who may not delegate it.
        """

        for name in _ROLE_FIELDS:
            role_id = getattr(provider, name)
            if role_id is None:
                continue
            role = await self._roles.get_role(role_id)
            if role is not None:
                assert_can_delegate(principal.grants, resolve_role_grants(role))

    def _assert_test_login_proof(self, provider: DashboardAuthProvider, principal: DashboardPrincipal) -> None:
        """A redirect-style provider is proven before it is trusted (PLAN §4.6 pre-flight).

        Runs under the same accounts write intent as the break-glass count, and
        before the same commit: the proof cannot be stamped by a concurrent
        callback between the check and the write. Freshness is computed from
        the stored instant on every read, so a stored deadline can never
        outlive a change to the window.
        """

        if provider.kind != AuthProviderKind.OIDC.value:
            return
        verified_at = provider.test_login_verified_at
        if (
            verified_at is not None
            and provider.test_login_user_id is not None
            and provider.test_login_user_id == principal.user_id
            and utcnow() - to_utc_naive(verified_at) <= TEST_LOGIN_MAX_AGE
        ):
            return
        raise OidcTestLoginRequiredError("Run a test sign-in with this configuration before turning single sign-on on")

    async def _assert_break_glass_ready(self, provider: DashboardAuthProvider) -> None:
        """An install must keep one way in that does not depend on the identity provider.

        Runs under the accounts write intent taken by :meth:`update_provider`,
        which is held until ``commit``: the count and the enable are one step.
        """

        if provider.kind not in _PASSWORDLESS_KINDS:
            return
        if await self._repo.count_qualifying_break_glass() > 0:
            return
        designated = await self._repo.list_break_glass_designations()
        username = designated[0].username if designated else None
        raise BreakGlassRequiresTotpError(
            (
                f"Turn on two-factor for '{username}' before enabling this sign-in method"
                if username is not None
                else "Designate an admin account with two-factor as the emergency account "
                "before enabling this sign-in method"
            ),
            username=username,
        )
