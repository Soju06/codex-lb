"""Request-time resolution of a provider-asserted identity to an account.

Shared by the session dependency (which turns the outcome into a principal or
a 401) and the session endpoint (which turns it into the session response, so
a refused identity can render the "account not ready" screen). The outcome is
memoised on the request so both read the same answer.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from app.core.auth.dashboard_mode import DashboardAuthMode, DashboardRequestAuth
from app.core.auth.dashboard_users_cache import get_dashboard_users_cache
from app.core.auth.providers import TrustedHeaderProvider
from app.core.auth.providers.registry import get_auth_provider_registry
from app.db.models import AuthProviderKind, DashboardAuthProvider, DashboardUser, DashboardUserStatus
from app.modules.dashboard_users.identity_resolver import (
    DenialCode,
    Denied,
    Resolution,
    get_identity_resolution_cache,
)


@dataclass(frozen=True, slots=True)
class ExternalResolution:
    """Who the provider-asserted identity is on this install.

    ``user`` is the active account (``denial`` is ``None``); otherwise
    ``denial`` names why the request is refused: ``identity_not_provisioned``
    (no account, none created) or ``account_disabled``.
    """

    provider: DashboardAuthProvider
    user: DashboardUser | None
    denial: DenialCode | None


async def resolve_trusted_header_request(
    request: Request, request_auth: DashboardRequestAuth
) -> ExternalResolution | None:
    """Resolve the trusted-header identity of ``request``; ``None`` when the provider is not active.

    Cached on ``request.state`` so the dependency and the session endpoint agree.
    """

    cached = getattr(request.state, "external_resolution", None)
    if isinstance(cached, ExternalResolution):
        return cached
    if request_auth.mode != DashboardAuthMode.TRUSTED_HEADER or not request_auth.actor:
        return None
    active = await get_auth_provider_registry().get_active(
        AuthProviderKind.TRUSTED_HEADER, TrustedHeaderProvider.provider_key, request_auth.mode
    )
    if active is None:
        return None
    identity = TrustedHeaderProvider().identity_from_subject(request_auth.actor, groups=request_auth.groups)
    if identity is None:
        return None
    actor_ip = request.client.host if request.client else None
    result = await get_identity_resolution_cache().resolve(identity, active.row, actor_ip=actor_ip)
    resolution = await _from_result(active.row, result)
    request.state.external_resolution = resolution
    return resolution


async def _from_result(provider: DashboardAuthProvider, result: Resolution) -> ExternalResolution:
    if isinstance(result, Denied):
        return ExternalResolution(provider=provider, user=None, denial=result.code)
    user = await get_dashboard_users_cache().get_user(result.user_id)
    if user is None:
        return ExternalResolution(provider=provider, user=None, denial="identity_not_provisioned")
    if user.status != DashboardUserStatus.ACTIVE.value:
        return ExternalResolution(provider=provider, user=None, denial="account_disabled")
    return ExternalResolution(provider=provider, user=user, denial=None)
