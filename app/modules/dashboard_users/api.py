"""Account management API (``users:manage``).

Every mutation additionally requires the caller to *be* an account
(``principal.user_id``): the implicit local admin, the disabled-auth principal
and a trusted-header principal without an account row get
``409 admin_account_required`` instead of creating people nobody can attribute.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

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
from app.db.models import DashboardUser
from app.dependencies import DashboardUsersContext, get_dashboard_users_context
from app.modules.dashboard_auth.service import role_summary
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
    CompatUserLockedError,
    EmailTakenError,
    InvalidEmailError,
    InvalidUsernameError,
    InviteNotPendingError,
    InvitePendingError,
    IssuedInvite,
    LastAdminProtectedError,
    RoleNotAssignableError,
    SelfModificationForbiddenError,
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
    CompatUserLockedError: (DashboardConflictError, "compat_user_locked"),
    InviteNotPendingError: (DashboardConflictError, "invite_not_pending"),
    InvitePendingError: (DashboardConflictError, "invite_pending"),
    UserNotActiveError: (DashboardConflictError, "user_not_active"),
    CredentialRequiredError: (DashboardConflictError, "credential_required"),
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


def user_response(user: DashboardUser, *, pending_invite_expires_at: datetime | None = None) -> DashboardUserResponse:
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
        pending_invite=(
            None if pending_invite_expires_at is None else PendingInviteSummary(expires_at=pending_invite_expires_at)
        ),
    )


def _invite_response(invite: IssuedInvite) -> IssuedInviteResponse:
    return IssuedInviteResponse(token=invite.token, expires_at=invite.expires_at)


@router.get("", response_model=list[DashboardUserResponse])
async def list_users(
    context: DashboardUsersContext = Depends(get_dashboard_users_context),
) -> list[DashboardUserResponse]:
    return [
        user_response(listing.user, pending_invite_expires_at=listing.pending_invite_expires_at)
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
        user, invite = await context.service.create_user(principal, payload, actor_ip=_client_host(request))
    return DashboardUserCreateResponse(
        user=user_response(user, pending_invite_expires_at=invite.expires_at), invite=_invite_response(invite)
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
            expires_at=as_utc(invite.expires_at),
            created_by_user_id=invite.created_by_user_id,
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
    return user_response(listing.user, pending_invite_expires_at=listing.pending_invite_expires_at)


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
