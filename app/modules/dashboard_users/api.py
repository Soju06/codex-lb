"""Account management API (``users:manage``).

Every mutation additionally requires the caller to *be* an account
(``principal.user_id``): the implicit local admin, the disabled-auth principal
and a trusted-header principal without an account row get
``409 admin_account_required`` instead of creating people nobody can attribute.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import APIRouter, Body, Depends, Request, Response

from app.core.auth.dashboard_access import DashboardPrincipal, InsufficientDelegationError, Permission
from app.core.auth.dependencies import require_dashboard_permission, set_dashboard_error_format
from app.core.exceptions import (
    AppError,
    DashboardConflictError,
    DashboardNotFoundError,
    DashboardPermissionError,
    DashboardValidationError,
)
from app.db.models import DashboardUser, DashboardUserInvite
from app.dependencies import DashboardUsersContext, get_dashboard_users_context
from app.modules.dashboard_auth.service import role_summary
from app.modules.dashboard_roles.service import RoleNotAssignableError
from app.modules.dashboard_users.break_glass import BreakGlassRoleRequiredError, LastBreakGlassProtectedError
from app.modules.dashboard_users.credentials import CredentialRequiredError
from app.modules.dashboard_users.schemas import (
    DashboardUserCreateRequest,
    DashboardUserCreateResponse,
    DashboardUserResponse,
    DashboardUserUpdateRequest,
    IssuedInviteResponse,
    PendingInviteResponse,
    PendingInviteSummary,
    ReactivateKeysResponse,
)
from app.modules.dashboard_users.service import (
    AdminAccountRequiredError,
    EmailTakenError,
    ForceWithoutRoleChangeError,
    IdentityTakenError,
    InvalidEmailError,
    InvalidUsernameError,
    InviteNotPendingError,
    InvitePendingError,
    IssuedInvite,
    LastAdminProtectedError,
    RoleManagedExternallyError,
    SelfModificationForbiddenError,
    SsoNotAvailableError,
    SsoOnlyInviteError,
    UsernameTakenError,
    UserNotActiveError,
    UserNotFoundError,
    as_utc,
)

router = APIRouter(
    prefix="/api/dashboard-users",
    tags=["dashboard"],
    dependencies=[Depends(require_dashboard_permission(Permission.USERS_MANAGE)), Depends(set_dashboard_error_format)],
)

_ERROR_MAP: dict[type[Exception], tuple[type[AppError], str]] = {
    AdminAccountRequiredError: (DashboardConflictError, "admin_account_required"),
    UserNotFoundError: (DashboardNotFoundError, "user_not_found"),
    UsernameTakenError: (DashboardConflictError, "username_taken"),
    EmailTakenError: (DashboardConflictError, "email_taken"),
    InvalidUsernameError: (DashboardValidationError, "validation_error"),
    InvalidEmailError: (DashboardValidationError, "validation_error"),
    RoleNotAssignableError: (DashboardValidationError, "role_not_assignable"),
    InsufficientDelegationError: (DashboardPermissionError, "insufficient_delegation"),
    SelfModificationForbiddenError: (DashboardConflictError, "self_modification_forbidden"),
    LastAdminProtectedError: (DashboardConflictError, "last_admin_protected"),
    LastBreakGlassProtectedError: (DashboardConflictError, "last_break_glass_protected"),
    BreakGlassRoleRequiredError: (DashboardValidationError, "validation_error"),
    InviteNotPendingError: (DashboardConflictError, "invite_not_pending"),
    InvitePendingError: (DashboardConflictError, "invite_pending"),
    UserNotActiveError: (DashboardConflictError, "user_not_active"),
    RoleManagedExternallyError: (DashboardConflictError, "role_managed_externally"),
    ForceWithoutRoleChangeError: (DashboardValidationError, "validation_error"),
    CredentialRequiredError: (DashboardConflictError, "credential_required"),
    SsoNotAvailableError: (DashboardConflictError, "sso_not_available"),
    IdentityTakenError: (DashboardConflictError, "identity_taken"),
    SsoOnlyInviteError: (DashboardConflictError, "sso_only_invite"),
}


@contextmanager
def mapped_user_errors() -> Iterator[None]:
    """Translate service-level refusals into the dashboard error envelope."""

    try:
        yield
    except tuple(_ERROR_MAP) as exc:
        error_type, code = _ERROR_MAP[type(exc)]
        raise error_type(str(exc), code=code) from exc


async def require_admin_account(
    principal: DashboardPrincipal = Depends(require_dashboard_permission(Permission.USERS_MANAGE)),
) -> DashboardPrincipal:
    if principal.user_id is None:
        raise DashboardConflictError(
            "Set a dashboard password and sign in before managing accounts", code="admin_account_required"
        )
    return principal


def _client_host(request: Request) -> str | None:
    return request.client.host if request.client else None


def pending_invite_summary(invite: DashboardUserInvite | None) -> PendingInviteSummary | None:
    """An SSO-only account waits for its first sign-in and has no expiry to show."""

    if invite is None:
        return None
    return PendingInviteSummary(
        expires_at=None if invite.sso_only else as_utc(invite.expires_at), sso_only=invite.sso_only
    )


def user_response(user: DashboardUser, *, pending_invite: PendingInviteSummary | None = None) -> DashboardUserResponse:
    return DashboardUserResponse(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        role=role_summary(user),
        role_source=user.role_source,
        status=user.status,
        is_break_glass=user.is_break_glass,
        totp_configured=user.totp_secret_encrypted is not None,
        has_password=user.password_hash is not None,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        pending_invite=pending_invite,
    )


def _invite_response(invite: IssuedInvite) -> IssuedInviteResponse:
    return IssuedInviteResponse(token=invite.token, expires_at=invite.expires_at)


@router.get("", response_model=list[DashboardUserResponse])
async def list_users(
    context: DashboardUsersContext = Depends(get_dashboard_users_context),
) -> list[DashboardUserResponse]:
    return [
        user_response(listing.user, pending_invite=pending_invite_summary(listing.pending_invite))
        for listing in await context.service.list_users()
    ]


@router.post("", response_model=DashboardUserCreateResponse, status_code=201)
async def create_user(
    request: Request,
    payload: DashboardUserCreateRequest = Body(...),
    principal: DashboardPrincipal = Depends(require_admin_account),
    context: DashboardUsersContext = Depends(get_dashboard_users_context),
) -> DashboardUserCreateResponse:
    with mapped_user_errors():
        created = await context.service.create_user(principal, payload, actor_ip=_client_host(request))
    # An SSO-only account has no link to hand over: the plaintext token never leaves the server.
    summary = PendingInviteSummary(
        expires_at=None if created.sso_only else created.invite.expires_at, sso_only=created.sso_only
    )
    return DashboardUserCreateResponse(
        user=user_response(created.user, pending_invite=summary),
        invite=None if created.sso_only else _invite_response(created.invite),
    )


@router.get("/invites", response_model=list[PendingInviteResponse])
async def list_pending_invites(
    context: DashboardUsersContext = Depends(get_dashboard_users_context),
) -> list[PendingInviteResponse]:
    return [
        PendingInviteResponse(
            user_id=invite.user_id,
            username=invite.user.username,
            role_id=invite.user.role_id,
            expires_at=None if invite.sso_only else as_utc(invite.expires_at),
            created_by_user_id=invite.created_by_user_id,
            sso_only=invite.sso_only,
        )
        for invite in await context.service.list_pending_invites()
    ]


@router.patch("/{user_id}", response_model=DashboardUserResponse)
async def update_user(
    user_id: str,
    request: Request,
    payload: DashboardUserUpdateRequest = Body(...),
    principal: DashboardPrincipal = Depends(require_admin_account),
    context: DashboardUsersContext = Depends(get_dashboard_users_context),
) -> DashboardUserResponse:
    with mapped_user_errors():
        listing = await context.service.update_user(principal, user_id, payload, actor_ip=_client_host(request))
    return user_response(listing.user, pending_invite=pending_invite_summary(listing.pending_invite))


@router.delete("/{user_id}", status_code=204)
async def delete_user(
    user_id: str,
    request: Request,
    principal: DashboardPrincipal = Depends(require_admin_account),
    context: DashboardUsersContext = Depends(get_dashboard_users_context),
) -> Response:
    with mapped_user_errors():
        await context.service.delete_user(principal, user_id, actor_ip=_client_host(request))
    return Response(status_code=204)


@router.post("/{user_id}/invite", response_model=IssuedInviteResponse)
async def resend_invite(
    user_id: str,
    request: Request,
    principal: DashboardPrincipal = Depends(require_admin_account),
    context: DashboardUsersContext = Depends(get_dashboard_users_context),
) -> IssuedInviteResponse:
    with mapped_user_errors():
        invite = await context.service.resend_invite(principal, user_id, actor_ip=_client_host(request))
    return _invite_response(invite)


@router.delete("/{user_id}/invite", status_code=204)
async def revoke_invite(
    user_id: str,
    request: Request,
    principal: DashboardPrincipal = Depends(require_admin_account),
    context: DashboardUsersContext = Depends(get_dashboard_users_context),
) -> Response:
    with mapped_user_errors():
        await context.service.revoke_invite(principal, user_id, actor_ip=_client_host(request))
    return Response(status_code=204)


@router.post("/{user_id}/reset-totp")
async def reset_totp(
    user_id: str,
    request: Request,
    principal: DashboardPrincipal = Depends(require_admin_account),
    context: DashboardUsersContext = Depends(get_dashboard_users_context),
) -> dict[str, str]:
    with mapped_user_errors():
        await context.service.reset_totp(principal, user_id, actor_ip=_client_host(request))
    return {"status": "ok"}


@router.post("/{user_id}/revoke-sessions")
async def revoke_sessions(
    user_id: str,
    request: Request,
    principal: DashboardPrincipal = Depends(require_admin_account),
    context: DashboardUsersContext = Depends(get_dashboard_users_context),
) -> dict[str, str]:
    with mapped_user_errors():
        await context.service.revoke_sessions(principal, user_id, actor_ip=_client_host(request))
    return {"status": "ok"}


@router.post("/{user_id}/reactivate-keys", response_model=ReactivateKeysResponse)
async def reactivate_keys(
    user_id: str,
    request: Request,
    principal: DashboardPrincipal = Depends(require_admin_account),
    context: DashboardUsersContext = Depends(get_dashboard_users_context),
) -> ReactivateKeysResponse:
    with mapped_user_errors():
        count = await context.service.reactivate_keys(principal, user_id, actor_ip=_client_host(request))
    return ReactivateKeysResponse(reactivated=count)
