from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, UploadFile

from app.core.auth.dependencies import (
    DashboardPrincipal,
    get_dashboard_principal,
    set_dashboard_error_format,
    validate_dashboard_session,
)
from app.core.exceptions import DashboardBadRequestError, DashboardConflictError, DashboardNotFoundError
from app.dependencies import AccountsContext, get_accounts_context
from app.modules.accounts.credential_parser import InvalidCredentialFormatError
from app.modules.accounts.repository import AccountIdentityConflictError
from app.modules.accounts.schemas import (
    AccountDeleteResponse,
    AccountImportResponse,
    AccountPauseResponse,
    AccountReactivateResponse,
    AccountsResponse,
    AccountTrendsResponse,
    CredentialsImportRequest,
    CredentialsImportResponse,
)
from app.modules.accounts.service import InvalidAuthJsonError

router = APIRouter(
    prefix="/api/accounts",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


@router.get("", response_model=AccountsResponse)
async def list_accounts(
    owner_user_id: str | None = Query(default=None, alias="ownerUserId"),
    principal: DashboardPrincipal = Depends(get_dashboard_principal),
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountsResponse:
    scoped_owner_id = owner_user_id if principal.is_admin else principal.user_id
    accounts = await context.service.list_accounts(owner_user_id=scoped_owner_id)
    return AccountsResponse(accounts=accounts)


@router.get("/{account_id}/trends", response_model=AccountTrendsResponse)
async def get_account_trends(
    account_id: str,
    principal: DashboardPrincipal = Depends(get_dashboard_principal),
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountTrendsResponse:
    scoped_owner_id = None if principal.is_admin else principal.user_id
    result = await context.service.get_account_trends(account_id, owner_user_id=scoped_owner_id)
    if not result:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    return result


@router.post("/import", response_model=AccountImportResponse)
async def import_account(
    auth_json: UploadFile = File(...),
    principal: DashboardPrincipal = Depends(get_dashboard_principal),
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountImportResponse:
    raw = await auth_json.read()
    try:
        return await context.service.import_account(raw, owner_user_id=principal.user_id)
    except InvalidAuthJsonError as exc:
        raise DashboardBadRequestError("Invalid auth.json payload", code="invalid_auth_json") from exc
    except AccountIdentityConflictError as exc:
        raise DashboardConflictError(str(exc), code="duplicate_identity_conflict") from exc


@router.post("/import-credentials", response_model=CredentialsImportResponse)
async def import_credentials(
    request: CredentialsImportRequest,
    principal: DashboardPrincipal = Depends(get_dashboard_principal),
    context: AccountsContext = Depends(get_accounts_context),
) -> CredentialsImportResponse:
    try:
        return await context.service.import_credentials(request.credentials_text, owner_user_id=principal.user_id)
    except InvalidCredentialFormatError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_credentials_format") from exc


@router.post("/{account_id}/reactivate", response_model=AccountReactivateResponse)
async def reactivate_account(
    account_id: str,
    principal: DashboardPrincipal = Depends(get_dashboard_principal),
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountReactivateResponse:
    scoped_owner_id = None if principal.is_admin else principal.user_id
    success = await context.service.reactivate_account(account_id, owner_user_id=scoped_owner_id)
    if not success:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    return AccountReactivateResponse(status="reactivated")


@router.post("/{account_id}/pause", response_model=AccountPauseResponse)
async def pause_account(
    account_id: str,
    principal: DashboardPrincipal = Depends(get_dashboard_principal),
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountPauseResponse:
    scoped_owner_id = None if principal.is_admin else principal.user_id
    success = await context.service.pause_account(account_id, owner_user_id=scoped_owner_id)
    if not success:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    return AccountPauseResponse(status="paused")


@router.delete("/{account_id}", response_model=AccountDeleteResponse)
async def delete_account(
    account_id: str,
    principal: DashboardPrincipal = Depends(get_dashboard_principal),
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountDeleteResponse:
    scoped_owner_id = None if principal.is_admin else principal.user_id
    success = await context.service.delete_account(account_id, owner_user_id=scoped_owner_id)
    if not success:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    return AccountDeleteResponse(status="deleted")
