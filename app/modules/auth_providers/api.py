"""Sign-in provider settings (``security:write``).

Editing needs a caller the audit log can name (``principal.user_id``), like
every account mutation: the implicit local admin and the disabled-auth
principal get ``409 admin_account_required``.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request

from app.core.auth.dashboard_access import DashboardPrincipal, InsufficientDelegationError, Permission
from app.core.auth.dependencies import require_dashboard_permission, set_dashboard_error_format
from app.core.auth.providers.registry import provider_active
from app.core.config.settings import get_settings
from app.core.exceptions import (
    DashboardConflictError,
    DashboardNotFoundError,
    DashboardPermissionError,
    DashboardValidationError,
)
from app.db.models import AuthProviderKind, DashboardAuthProvider
from app.dependencies import AuthProvidersContext, get_auth_providers_context
from app.modules.auth_providers.schemas import AuthProviderResponse, AuthProviderUpdateRequest
from app.modules.auth_providers.service import ProviderNotFoundError
from app.modules.dashboard_roles.service import RoleNotAssignableError
from app.modules.dashboard_users.break_glass import BreakGlassRequiresTotpError

router = APIRouter(
    prefix="/api/auth-providers",
    tags=["dashboard"],
    dependencies=[
        Depends(require_dashboard_permission(Permission.SECURITY_WRITE)),
        Depends(set_dashboard_error_format),
    ],
)


async def _require_account(
    principal: DashboardPrincipal = Depends(require_dashboard_permission(Permission.SECURITY_WRITE)),
) -> DashboardPrincipal:
    if principal.user_id is None:
        raise DashboardConflictError(
            "Set a dashboard password and sign in before changing sign-in providers", code="admin_account_required"
        )
    return principal


def _config(provider: DashboardAuthProvider) -> dict[str, str]:
    """Read-only, non-secret provider configuration the settings UI shows.

    The trusted-header provider is configured by the deployment topology, not
    by the database: both header names come from ``CODEX_LB_DASHBOARD_AUTH_PROXY_*``
    and have to match the reverse proxy. Returning them lets the settings card
    name the values it cannot edit instead of only naming the variables.
    """

    if provider.kind != AuthProviderKind.TRUSTED_HEADER.value:
        return {}
    settings = get_settings()
    return {
        "identityHeader": settings.dashboard_auth_proxy_header,
        "groupsHeader": settings.dashboard_auth_proxy_groups_header,
    }


def _response(provider: DashboardAuthProvider) -> AuthProviderResponse:
    return AuthProviderResponse(
        id=provider.id,
        kind=provider.kind,
        provider_key=provider.provider_key,
        label=provider.label,
        enabled=provider.enabled,
        active=provider_active(provider, get_settings().dashboard_auth_mode),
        unknown_identity_role_id=provider.unknown_identity_role_id,
        no_match_role_id=provider.no_match_role_id,
        link_by_email=provider.link_by_email,
        skip_role_sync=provider.skip_role_sync,
        idp_mfa_enforced=provider.idp_mfa_enforced,
        config=_config(provider),
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


@router.get("", response_model=list[AuthProviderResponse])
async def list_providers(
    context: AuthProvidersContext = Depends(get_auth_providers_context),
) -> list[AuthProviderResponse]:
    return [_response(provider) for provider in await context.service.list_providers()]


@router.patch("/{provider_id}", response_model=AuthProviderResponse)
async def update_provider(
    provider_id: str,
    request: Request,
    payload: AuthProviderUpdateRequest = Body(...),
    principal: DashboardPrincipal = Depends(_require_account),
    context: AuthProvidersContext = Depends(get_auth_providers_context),
) -> AuthProviderResponse:
    actor_ip = request.client.host if request.client else None
    try:
        provider = await context.service.update_provider(principal, provider_id, payload, actor_ip=actor_ip)
    except ProviderNotFoundError as exc:
        raise DashboardNotFoundError(str(exc), code="provider_not_found") from exc
    except RoleNotAssignableError as exc:
        raise DashboardValidationError(str(exc), code="role_not_assignable") from exc
    except InsufficientDelegationError as exc:
        raise DashboardPermissionError(str(exc), code="insufficient_delegation") from exc
    except BreakGlassRequiresTotpError as exc:
        raise DashboardConflictError(
            str(exc), code="break_glass_requires_totp", param=exc.username, details={"username": exc.username}
        ) from exc
    return _response(provider)
