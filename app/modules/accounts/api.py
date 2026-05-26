from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.core.audit.service import AuditService
from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.core.clients.account_proxy_probe import ProxyProbeError
from app.core.errors import dashboard_error
from app.core.exceptions import DashboardBadRequestError, DashboardConflictError, DashboardNotFoundError
from app.dependencies import AccountsContext, get_accounts_context
from app.modules.accounts.repository import AccountIdentityConflictError
from app.modules.accounts.schemas import (
    AccountAliasRequest,
    AccountAliasResponse,
    AccountDeleteResponse,
    AccountExportResponse,
    AccountImportResponse,
    AccountLimitWarmupUpdateRequest,
    AccountLimitWarmupUpdateResponse,
    AccountOpenCodeAuthExportResponse,
    AccountPauseResponse,
    AccountProxyClearResponse,
    AccountProxyInput,
    AccountProxySummary,
    AccountReactivateResponse,
    AccountsResponse,
    AccountTrendsResponse,
)
from app.modules.accounts.service import (
    AccountCredentialsUnrecoverableError,
    AccountNotFoundError,
    InvalidAuthJsonError,
    ProxyPasswordUnrecoverableError,
)

router = APIRouter(
    prefix="/api/accounts",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


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
) -> AccountTrendsResponse:
    result = await context.service.get_account_trends(account_id)
    if not result:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    return result


@router.post("/{account_id}/export", response_model=AccountExportResponse)
async def export_account(
    request: Request,
    response: Response,
    account_id: str,
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountExportResponse:
    result = await context.service.export_account(account_id)
    if not result:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    AuditService.log_async(
        "account_exported",
        actor_ip=request.client.host if request.client else None,
        details={"account_id": result.account_id},
    )
    return result


@router.post("/{account_id}/export/opencode-auth", response_model=AccountOpenCodeAuthExportResponse)
async def export_account_opencode_auth(
    request: Request,
    response: Response,
    account_id: str,
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountOpenCodeAuthExportResponse:
    result = await context.service.export_opencode_auth(account_id)
    if not result:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    AuditService.log_async(
        "account_auth_exported",
        actor_ip=request.client.host if request.client else None,
        details={"account_id": account_id},
    )
    return result


@router.post("/import", response_model=AccountImportResponse)
async def import_account(
    request: Request,
    auth_json: UploadFile = File(...),
    proxy_host: str | None = Form(default=None, alias="proxyHost"),
    proxy_port: int | None = Form(default=None, alias="proxyPort"),
    proxy_username: str | None = Form(default=None, alias="proxyUsername"),
    proxy_password: str | None = Form(default=None, alias="proxyPassword"),
    proxy_remote_dns: bool = Form(default=True, alias="proxyRemoteDns"),
    proxy_label: str | None = Form(default=None, alias="proxyLabel"),
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountImportResponse | JSONResponse:
    raw = await auth_json.read()
    try:
        proxy_payload = _proxy_payload_from_import_form(
            host=proxy_host,
            port=proxy_port,
            username=proxy_username,
            password=proxy_password,
            remote_dns=proxy_remote_dns,
            label=proxy_label,
        )
        response = await context.service.import_account(raw, proxy_payload=proxy_payload)
        AuditService.log_async(
            "account_created",
            actor_ip=request.client.host if request.client else None,
            details={
                "account_id": response.account_id,
                "proxy_configured": proxy_payload is not None,
            },
        )
        if proxy_payload is not None:
            AuditService.log_async(
                "account_proxy_set",
                actor_ip=request.client.host if request.client else None,
                details={
                    "account_id": response.account_id,
                    "host": proxy_payload.host,
                    "port": proxy_payload.port,
                    "label": proxy_payload.label,
                    "remote_dns": proxy_payload.remote_dns,
                    "has_password": proxy_payload.password is not None,
                },
            )
        return response
    except ValidationError:
        return JSONResponse(
            status_code=422,
            content=dashboard_error("validation_error", "Invalid proxy configuration"),
        )
    except InvalidAuthJsonError as exc:
        raise DashboardBadRequestError("Invalid auth.json payload", code="invalid_auth_json") from exc
    except AccountIdentityConflictError as exc:
        raise DashboardConflictError(str(exc), code="duplicate_identity_conflict") from exc
    except ProxyProbeError as exc:
        envelope = dashboard_error("proxy_probe_failed", _proxy_probe_error_message(exc))
        envelope["error"]["reason"] = exc.reason.value  # type: ignore[typeddict-unknown-key]
        return JSONResponse(status_code=422, content=envelope)


@router.post("/{account_id}/reactivate", response_model=AccountReactivateResponse)
async def reactivate_account(
    account_id: str,
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountReactivateResponse:
    success = await context.service.reactivate_account(account_id)
    if not success:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    return AccountReactivateResponse(status="reactivated")


@router.post("/{account_id}/pause", response_model=AccountPauseResponse)
async def pause_account(
    account_id: str,
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountPauseResponse:
    success = await context.service.pause_account(account_id)
    if not success:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    return AccountPauseResponse(status="paused")


@router.put("/{account_id}/alias", response_model=AccountAliasResponse)
async def set_account_alias(
    account_id: str,
    payload: AccountAliasRequest,
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountAliasResponse:
    success = await context.service.set_account_alias(account_id, payload.alias)
    if not success:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    normalized = payload.alias.strip() if isinstance(payload.alias, str) else None
    if normalized == "":
        normalized = None
    return AccountAliasResponse(account_id=account_id, alias=normalized)


@router.put("/{account_id}/limit-warmup", response_model=AccountLimitWarmupUpdateResponse)
async def update_account_limit_warmup(
    account_id: str,
    payload: AccountLimitWarmupUpdateRequest,
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountLimitWarmupUpdateResponse:
    success = await context.service.set_limit_warmup_enabled(account_id, payload.enabled)
    if not success:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    return AccountLimitWarmupUpdateResponse(
        status="enabled" if payload.enabled else "disabled",
        enabled=payload.enabled,
    )


@router.delete("/{account_id}", response_model=AccountDeleteResponse)
async def delete_account(
    request: Request,
    account_id: str,
    delete_history: bool = False,
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountDeleteResponse:
    success = await context.service.delete_account(account_id, delete_history=delete_history)
    if not success:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    AuditService.log_async(
        "account_deleted",
        actor_ip=request.client.host if request.client else None,
        details={"account_id": account_id, "delete_history": delete_history},
    )
    return AccountDeleteResponse(status="deleted")


@router.post("/{account_id}/proxy", response_model=AccountProxySummary)
async def set_account_proxy(
    request: Request,
    account_id: str,
    payload: AccountProxyInput,
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountProxySummary | JSONResponse:
    try:
        summary = await context.service.set_account_proxy(account_id, payload)
    except AccountNotFoundError as exc:
        raise DashboardNotFoundError("Account not found", code="account_not_found") from exc
    except ProxyPasswordUnrecoverableError:
        # Fernet key has been rotated since the password was stored.
        # Surface a typed envelope so the dashboard can prompt the
        # operator to re-enter the password instead of erroring opaquely.
        envelope = dashboard_error(
            "proxy_password_unrecoverable",
            "Stored proxy password could not be decrypted. Please re-enter the password.",
        )
        return JSONResponse(status_code=422, content=envelope)
    except AccountCredentialsUnrecoverableError:
        # Same Fernet-rotation scenario as the password decrypt above
        # but for the OAuth refresh token. The recovery path is
        # "re-import the account from auth.json", not "re-enter the
        # password" — surface a distinct error code so the dashboard
        # can prompt for the right action. Without this branch the
        # InvalidToken would propagate as an unhandled 500.
        envelope = dashboard_error(
            "account_credentials_unrecoverable",
            "Account credentials could not be decrypted (encryption key changed?). "
            "Please re-import the account from auth.json.",
        )
        return JSONResponse(status_code=422, content=envelope)
    except ProxyProbeError as exc:
        # Surface a typed `reason` alongside the standard dashboard error
        # envelope so the dashboard can render a precise message
        # (`proxy_connect`, `proxy_auth`, `tls`, `upstream_status`,
        # `timeout`) without parsing free-form text. Do not echo
        # low-level transport details: some libraries include proxy URLs
        # or credentials in exception text.
        envelope = dashboard_error("proxy_probe_failed", _proxy_probe_error_message(exc))
        envelope["error"]["reason"] = exc.reason.value  # type: ignore[typeddict-unknown-key]
        return JSONResponse(status_code=422, content=envelope)

    AuditService.log_async(
        "account_proxy_set",
        actor_ip=request.client.host if request.client else None,
        details={
            "account_id": account_id,
            "host": payload.host,
            "port": payload.port,
            "label": payload.label,
            "remote_dns": payload.remote_dns,
            "has_password": summary.has_password,
        },
    )
    return summary


def _proxy_payload_from_import_form(
    *,
    host: str | None,
    port: int | None,
    username: str | None,
    password: str | None,
    remote_dns: bool,
    label: str | None,
) -> AccountProxyInput | None:
    if all(value is None for value in (host, port, username, password, label)) and remote_dns is True:
        return None
    return AccountProxyInput.model_validate(
        {
            "host": host,
            "port": port,
            "username": username,
            "password": password,
            "remote_dns": remote_dns,
            "label": label,
        }
    )


def _proxy_probe_error_message(exc: ProxyProbeError) -> str:
    return f"Proxy validation failed: {exc.reason.value}"


@router.delete("/{account_id}/proxy", response_model=AccountProxyClearResponse)
async def clear_account_proxy(
    request: Request,
    account_id: str,
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountProxyClearResponse:
    cleared = await context.service.clear_account_proxy(account_id)
    if not cleared:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    AuditService.log_async(
        "account_proxy_cleared",
        actor_ip=request.client.host if request.client else None,
        details={"account_id": account_id},
    )
    return AccountProxyClearResponse(status="cleared")
