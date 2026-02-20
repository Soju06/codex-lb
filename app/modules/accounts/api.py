from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import JSONResponse

from app.core.errors import dashboard_error
from app.dependencies import AccountsContext, get_accounts_context
from app.modules.accounts.repository import AccountIdentityConflictError
from app.modules.accounts.schemas import (
    AccountDeleteResponse,
    AccountImportResponse,
    AccountPauseResponse,
    AccountReactivateResponse,
    AccountsResponse,
    AccountTrendsResponse,
)
from app.modules.accounts.service import InvalidAuthJsonError

router = APIRouter(prefix="/api/accounts", tags=["dashboard"])


@router.get("", response_model=AccountsResponse)
async def list_accounts(
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountsResponse:
    accounts = await context.service.list_accounts()
    return AccountsResponse(accounts=accounts)


@router.get("/{account_id}/trends", response_model=AccountTrendsResponse)
async def get_account_trends(
    account_id: str,
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountTrendsResponse | JSONResponse:
    result = await context.service.get_account_trends(account_id)
    if not result:
        return JSONResponse(
            status_code=404,
            content=dashboard_error("account_not_found", "Account not found"),
        )
    return result


@router.post("/import", response_model=AccountImportResponse)
async def import_account(
    auth_json: UploadFile = File(...),
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountImportResponse | JSONResponse:
    raw = await auth_json.read()
    try:
        return await context.service.import_account(raw)
    except InvalidAuthJsonError:
        return JSONResponse(
            status_code=400,
            content=dashboard_error("invalid_auth_json", "Invalid auth.json payload"),
        )
    except AccountIdentityConflictError as exc:
        return JSONResponse(
            status_code=409,
            content=dashboard_error("duplicate_identity_conflict", str(exc)),
        )


@router.post("/{account_id}/reactivate", response_model=AccountReactivateResponse)
async def reactivate_account(
    account_id: str,
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountReactivateResponse | JSONResponse:
    success = await context.service.reactivate_account(account_id)
    if not success:
        return JSONResponse(
            status_code=404,
            content=dashboard_error("account_not_found", "Account not found"),
        )
    return AccountReactivateResponse(status="reactivated")


@router.post("/{account_id}/pause", response_model=AccountPauseResponse)
async def pause_account(
    account_id: str,
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountPauseResponse | JSONResponse:
    success = await context.service.pause_account(account_id)
    if not success:
        return JSONResponse(
            status_code=404,
            content=dashboard_error("account_not_found", "Account not found"),
        )
    return AccountPauseResponse(status="paused")


@router.delete("/{account_id}", response_model=AccountDeleteResponse)
async def delete_account(
    account_id: str,
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountDeleteResponse | JSONResponse:
    success = await context.service.delete_account(account_id)
    if not success:
        return JSONResponse(
            status_code=404,
            content=dashboard_error("account_not_found", "Account not found"),
        )
    return AccountDeleteResponse(status="deleted")
