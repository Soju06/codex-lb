"""Provider settings: read the rows, edit the resolver knobs, audit the change.

Enabling and disabling providers is mode-driven in this release (the trusted
header follows ``CODEX_LB_DASHBOARD_AUTH_MODE``); the API only edits how
identities map to accounts. Role handouts go through the same assignability
and delegation rules as inviting a person: an operator cannot make the proxy
hand out admin.
"""

from __future__ import annotations

from app.core.audit.service import AuditActor, AuditDetails, AuditService, AuditTarget
from app.core.auth.dashboard_access import DashboardPrincipal, assert_can_delegate
from app.core.auth.providers.registry import get_auth_provider_registry
from app.db.models import DashboardAuthProvider
from app.modules.auth_providers.repository import AuthProvidersRepository
from app.modules.auth_providers.schemas import AuthProviderUpdateRequest
from app.modules.dashboard_roles.repository import DashboardRolesRepository
from app.modules.dashboard_roles.service import resolve_assignable_role, resolve_role_grants


class ProviderNotFoundError(LookupError):
    pass


_ROLE_FIELDS = ("unknown_identity_role_id", "no_match_role_id")
_FLAG_FIELDS = ("label", "link_by_email", "skip_role_sync", "idp_mfa_enforced")


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
        return provider
