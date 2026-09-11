"""Provider settings: read the rows, edit the resolver knobs, audit the change.

A provider row can be turned on and off here (whether it then *serves* also
depends on ``CODEX_LB_DASHBOARD_AUTH_MODE``); turning on a method that offers
no local password fallback needs a qualifying break-glass account, for the
same reason tightening ``local_login_policy`` does. Role handouts go through
the same assignability and delegation rules as inviting a person: an operator
cannot make the proxy hand out admin.
"""

from __future__ import annotations

from app.core.audit.service import AuditActor, AuditDetails, AuditService, AuditSeverity, AuditTarget
from app.core.auth.dashboard_access import DashboardPrincipal, assert_can_delegate
from app.core.auth.providers.registry import get_auth_provider_registry
from app.db.models import AuthProviderKind, DashboardAuthProvider
from app.modules.auth_providers.repository import AuthProvidersRepository
from app.modules.auth_providers.schemas import AuthProviderUpdateRequest
from app.modules.dashboard_roles.repository import DashboardRolesRepository
from app.modules.dashboard_roles.service import resolve_assignable_role, resolve_role_grants
from app.modules.dashboard_users.break_glass import BreakGlassRequiresTotpError


class ProviderNotFoundError(LookupError):
    pass


_ROLE_FIELDS = ("unknown_identity_role_id", "no_match_role_id")
_FLAG_FIELDS = ("label", "enabled", "link_by_email", "skip_role_sync", "idp_mfa_enforced")
#: Sign-in methods that cannot fall back to a local password of their own.
_PASSWORDLESS_KINDS = frozenset({AuthProviderKind.TRUSTED_HEADER.value, AuthProviderKind.OIDC.value})


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
        for name in _FLAG_FIELDS:
            if name not in fields:
                continue
            value = getattr(payload, name)
            if value is not None and getattr(provider, name) != value:
                if name == "enabled" and value is True:
                    await self._assert_break_glass_ready(provider)
                setattr(provider, name, value)
                changes[name] = value
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
