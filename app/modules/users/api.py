from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query

from app.core.auth.dependencies import (
    DashboardPrincipal,
    get_dashboard_principal,
    require_admin_principal,
    set_dashboard_error_format,
    validate_dashboard_session,
)
from app.core.exceptions import (
    DashboardBadRequestError,
    DashboardConflictError,
    DashboardForbiddenError,
    DashboardNotFoundError,
)
from app.dependencies import UsersContext, get_users_context
from app.modules.users.schemas import (
    DashboardUserCreateRequest,
    DashboardUserDeleteResponse,
    DashboardUserResponse,
    DashboardUsersResponse,
    DashboardUserUpdateRequest,
)
from app.modules.users.service import (
    DashboardUserConflictError,
    DashboardUserCreateData,
    DashboardUserNotFoundError,
    DashboardUserOperationError,
    DashboardUserUpdateData,
)

router = APIRouter(
    prefix="/api/users",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


def _to_response(user) -> DashboardUserResponse:
    return DashboardUserResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


@router.get("", response_model=DashboardUsersResponse)
async def list_users(
    search: str | None = Query(default=None),
    role: str | None = Query(default=None, pattern="^(admin|user)$"),
    _principal: DashboardPrincipal = Depends(require_admin_principal),
    context: UsersContext = Depends(get_users_context),
) -> DashboardUsersResponse:
    rows = await context.service.list_users(search=search, role=role)
    return DashboardUsersResponse(users=[_to_response(row) for row in rows])


@router.post("", response_model=DashboardUserResponse)
async def create_user(
    payload: DashboardUserCreateRequest = Body(...),
    _principal: DashboardPrincipal = Depends(require_admin_principal),
    context: UsersContext = Depends(get_users_context),
) -> DashboardUserResponse:
    try:
        row = await context.service.create_user(
            DashboardUserCreateData(
                username=payload.username,
                password=payload.password,
                role=payload.role,
            )
        )
    except DashboardUserConflictError as exc:
        raise DashboardConflictError(str(exc), code="user_conflict") from exc
    except DashboardUserOperationError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_user_payload") from exc
    return _to_response(row)


@router.patch("/{user_id}", response_model=DashboardUserResponse)
async def update_user(
    user_id: str,
    payload: DashboardUserUpdateRequest = Body(...),
    principal: DashboardPrincipal = Depends(require_admin_principal),
    context: UsersContext = Depends(get_users_context),
) -> DashboardUserResponse:
    fields = payload.model_fields_set
    try:
        row = await context.service.update_user(
            user_id,
            DashboardUserUpdateData(
                username=payload.username,
                username_set="username" in fields,
                password=payload.password,
                password_set="password" in fields,
                role=payload.role,
                role_set="role" in fields,
                is_active=payload.is_active,
                is_active_set="is_active" in fields,
            ),
            actor_user_id=principal.user_id,
        )
    except DashboardUserNotFoundError as exc:
        raise DashboardNotFoundError(str(exc), code="user_not_found") from exc
    except DashboardUserConflictError as exc:
        raise DashboardConflictError(str(exc), code="user_conflict") from exc
    except DashboardUserOperationError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_user_payload") from exc
    return _to_response(row)


@router.delete("/{user_id}", response_model=DashboardUserDeleteResponse)
async def delete_user(
    user_id: str,
    principal: DashboardPrincipal = Depends(get_dashboard_principal),
    context: UsersContext = Depends(get_users_context),
) -> DashboardUserDeleteResponse:
    if not principal.is_admin:
        raise DashboardForbiddenError("Admin role is required")
    try:
        await context.service.delete_user(user_id, actor_user_id=principal.user_id)
    except DashboardUserNotFoundError as exc:
        raise DashboardNotFoundError(str(exc), code="user_not_found") from exc
    except DashboardUserOperationError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_user_payload") from exc
    return DashboardUserDeleteResponse(status="deleted")
