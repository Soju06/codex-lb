from __future__ import annotations

import logging
from typing import NoReturn

from fastapi import APIRouter, Depends, Request, Response
from pydantic import ValidationError

from app.core.audit.service import AuditService
from app.core.auth.dependencies import (
    require_dashboard_write_access,
    set_dashboard_error_format,
    validate_dashboard_session,
)
from app.core.auth.refresh import RefreshError
from app.core.clients.usage import UsageFetchError
from app.core.config.settings import get_settings
from app.core.exceptions import (
    DashboardBadRequestError,
    DashboardConflictError,
    DashboardNotFoundError,
    DashboardUpstreamError,
    DashboardValidationError,
)
from app.core.middleware.multipart_content_encoding import (
    mark_account_bundle_failure_audited,
    raise_for_unsupported_multipart_content_encoding,
)
from app.core.multipart import (
    ACCOUNT_IMPORT_MULTIPART_POLICY,
    MultipartPayloadTooLarge,
    MultipartPolicy,
    bounded_multipart_form,
    read_bounded_upload,
)
from app.core.multipart_fields import required_text, required_upload
from app.core.upstream_proxy import UpstreamProxyRouteError
from app.dependencies import AccountsContext, get_accounts_context, get_proxy_service_for_app
from app.modules.accounts.account_bundle import (
    AccountBundleError,
    AccountBundleTooLargeError,
    UnsupportedAccountBundleError,
)
from app.modules.accounts.repository import AccountBundleIdentityError, AccountIdentityConflictError
from app.modules.accounts.schemas import (
    AccountAliasRequest,
    AccountAliasResponse,
    AccountAuthExportResponse,
    AccountBundleCommitResponse,
    AccountBundleExportRequest,
    AccountBundlePreflightResponse,
    AccountDeleteResponse,
    AccountExportResponse,
    AccountImportResponse,
    AccountLimitWarmupUpdateRequest,
    AccountLimitWarmupUpdateResponse,
    AccountOpenCodeAuthExportResponse,
    AccountPauseResponse,
    AccountProbeRequest,
    AccountProbeResponse,
    AccountReactivateResponse,
    AccountRoutingPolicyUpdateRequest,
    AccountRoutingPolicyUpdateResponse,
    AccountsResponse,
    AccountTrendsResponse,
    AccountUpdateRequest,
    AccountUpdateResponse,
    AccountUsageResetConsumeRequest,
    AccountUsageResetConsumeResponse,
    AccountUsageResetCreditsResponse,
)
from app.modules.accounts.service import (
    AccountNotProbableError,
    AccountStateTransitionError,
    AccountUsageResetConsumeUnavailableError,
    AccountUsageResetCreditsUnavailableError,
    InvalidAuthJsonError,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/accounts",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)

_ACCOUNT_IMPORT_OPENAPI_EXTRA = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "title": "Body_import_account_api_accounts_import_post",
                    "required": ["auth_json"],
                    "properties": {
                        "auth_json": {
                            "type": "string",
                            "contentMediaType": "application/octet-stream",
                            "title": "Auth Json",
                        }
                    },
                }
            }
        },
    },
    "responses": {
        "422": {
            "description": "Validation Error",
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/HTTPValidationError"},
                }
            },
        }
    },
}
_ACCOUNT_BUNDLE_EXPORT_OPENAPI_EXTRA = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": AccountBundleExportRequest.model_json_schema(by_alias=True),
            }
        },
    },
    "responses": _ACCOUNT_IMPORT_OPENAPI_EXTRA["responses"],
}


def _set_sensitive_response_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"


def _bundle_multipart_policy(*, fields: int) -> MultipartPolicy:
    max_bytes = get_settings().account_bundle_max_bytes
    return MultipartPolicy(
        max_body_bytes=max_bytes + 64 * 1024,
        max_file_bytes=max_bytes,
        max_aggregate_file_bytes=max_bytes,
        max_files=1,
        max_fields=fields,
        max_text_part_bytes=4096,
    )


def _extend_bounded_export_body(body: bytearray, chunk: bytes, *, max_bytes: int) -> None:
    if len(body) + len(chunk) > max_bytes:
        raise MultipartPayloadTooLarge()
    body.extend(chunk)


async def _read_bundle_export_request(request: Request) -> AccountBundleExportRequest:
    max_bytes = get_settings().account_bundle_max_bytes
    raw_content_length = request.headers.get("content-length")
    if raw_content_length is not None:
        try:
            content_length = int(raw_content_length)
        except ValueError:
            content_length = None
        if content_length is not None and content_length > max_bytes:
            raise MultipartPayloadTooLarge()

    body = bytearray()
    async for chunk in request.stream():
        _extend_bounded_export_body(body, chunk, max_bytes=max_bytes)
    try:
        return AccountBundleExportRequest.model_validate_json(body)
    except ValidationError as exc:
        raise DashboardValidationError("Invalid request payload", code="validation_error") from exc


def _raise_bundle_error(exc: Exception) -> NoReturn:
    if isinstance(exc, AccountBundleTooLargeError):
        raise MultipartPayloadTooLarge(param="bundle") from exc
    if isinstance(exc, UnsupportedAccountBundleError):
        raise DashboardBadRequestError("Unsupported account bundle format", code=exc.code) from exc
    if isinstance(exc, AccountBundleError):
        raise DashboardBadRequestError("Invalid account bundle or passphrase", code=exc.code) from exc
    if isinstance(exc, AccountBundleIdentityError):
        raise DashboardBadRequestError("Account bundle contains duplicate account identities", code=exc.code) from exc
    if isinstance(exc, AccountIdentityConflictError):
        raise DashboardConflictError(
            "Account identity conflicts with multiple destination accounts",
            code="account_identity_conflict",
        ) from exc
    if isinstance(exc, InvalidAuthJsonError):
        raise DashboardBadRequestError("Account bundle request is invalid", code="invalid_account_bundle") from exc
    raise DashboardUpstreamError(
        "Account bundle operation failed",
        code="account_bundle_operation_failed",
    ) from exc


def _bundle_error_outcome(exc: Exception) -> str:
    if isinstance(exc, (AccountBundleError, AccountBundleIdentityError)):
        return exc.code
    if isinstance(exc, AccountIdentityConflictError):
        return "account_identity_conflict"
    if isinstance(exc, InvalidAuthJsonError):
        return "validation_failed"
    return "operation_failed"


@router.get("", response_model=AccountsResponse)
async def list_accounts(
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountsResponse:
    accounts = await context.service.list_accounts()
    return AccountsResponse(accounts=accounts)


@router.post("/bundle/export", openapi_extra=_ACCOUNT_BUNDLE_EXPORT_OPENAPI_EXTRA)
async def export_account_bundle(
    request: Request,
    _write_access=Depends(require_dashboard_write_access),
    context: AccountsContext = Depends(get_accounts_context),
) -> Response:
    payload = await _read_bundle_export_request(request)
    try:
        bundle, account_count = await context.service.export_account_bundle(
            payload.account_ids,
            payload.passphrase,
            max_bytes=get_settings().account_bundle_max_bytes,
        )
    except Exception as exc:
        mark_account_bundle_failure_audited(request)
        AuditService.log_async(
            "account_bundle_export_failed",
            actor_ip=request.client.host if request.client else None,
            details={"requested_count": len(payload.account_ids) if payload.account_ids is not None else None},
        )
        _raise_bundle_error(exc)
    response = Response(content=bundle, media_type="application/vnd.codex-lb.account-bundle")
    _set_sensitive_response_headers(response)
    response.headers["Content-Disposition"] = 'attachment; filename="codex-lb-accounts-v1.clb-account-bundle"'
    AuditService.log_async(
        "account_bundle_exported",
        actor_ip=request.client.host if request.client else None,
        details={"account_count": account_count},
    )
    return response


@router.post("/bundle/import/preflight", response_model=AccountBundlePreflightResponse)
async def preflight_account_bundle(
    request: Request,
    response: Response,
    _write_access=Depends(require_dashboard_write_access),
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountBundlePreflightResponse:
    raise_for_unsupported_multipart_content_encoding(request)
    policy = _bundle_multipart_policy(fields=1)
    async with bounded_multipart_form(request, policy, typed_upload_fields=("bundle",)) as form:
        if len(form.multi_items()) != 2 or {name for name, _value in form.multi_items()} != {"bundle", "passphrase"}:
            raise DashboardBadRequestError("Invalid account bundle form", code="invalid_account_bundle")
        raw = await read_bounded_upload(required_upload(form, "bundle"), policy.max_file_bytes, "bundle")
        passphrase = required_text(form, "passphrase")
    try:
        result = await context.service.preflight_account_bundle(
            raw,
            passphrase,
            max_bytes=get_settings().account_bundle_max_bytes,
        )
    except Exception as exc:
        mark_account_bundle_failure_audited(request)
        AuditService.log_async(
            "account_bundle_preflight_failed",
            actor_ip=request.client.host if request.client else None,
            details={"outcome": _bundle_error_outcome(exc)},
        )
        _raise_bundle_error(exc)
    _set_sensitive_response_headers(response)
    AuditService.log_async(
        "account_bundle_preflighted",
        actor_ip=request.client.host if request.client else None,
        details={
            "account_count": result.account_count,
            "new_count": result.new_count,
            "matching_count": result.matching_count,
        },
    )
    return result


@router.post("/bundle/import/commit", response_model=AccountBundleCommitResponse)
async def commit_account_bundle(
    request: Request,
    response: Response,
    _write_access=Depends(require_dashboard_write_access),
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountBundleCommitResponse:
    raise_for_unsupported_multipart_content_encoding(request)
    policy = _bundle_multipart_policy(fields=4)
    async with bounded_multipart_form(request, policy, typed_upload_fields=("bundle",)) as form:
        expected = {"bundle", "passphrase", "integrity_token", "conflict_mode", "confirm_replace"}
        if len(form.multi_items()) != 5 or {name for name, _value in form.multi_items()} != expected:
            raise DashboardBadRequestError("Invalid account bundle form", code="invalid_account_bundle")
        raw = await read_bounded_upload(required_upload(form, "bundle"), policy.max_file_bytes, "bundle")
        passphrase = required_text(form, "passphrase")
        integrity_token = required_text(form, "integrity_token")
        conflict_mode_raw = required_text(form, "conflict_mode")
        confirm_replace = required_text(form, "confirm_replace") == "true"
    if conflict_mode_raw not in ("skip", "replace"):
        raise DashboardBadRequestError("Invalid account bundle conflict mode", code="invalid_account_bundle")
    try:
        result = await context.service.commit_account_bundle(
            raw,
            passphrase,
            integrity_token=integrity_token,
            conflict_mode=conflict_mode_raw,
            confirm_replace=confirm_replace,
            max_bytes=get_settings().account_bundle_max_bytes,
        )
    except Exception as exc:
        mark_account_bundle_failure_audited(request)
        AuditService.log_async(
            "account_bundle_import_failed",
            actor_ip=request.client.host if request.client else None,
            details={"mode": conflict_mode_raw, "outcome": _bundle_error_outcome(exc)},
        )
        _raise_bundle_error(exc)
    _set_sensitive_response_headers(response)
    AuditService.log_async(
        "account_bundle_imported",
        actor_ip=request.client.host if request.client else None,
        details={
            "mode": conflict_mode_raw,
            "imported": result.summary.imported,
            "replaced": result.summary.replaced,
            "skipped": result.summary.skipped,
            "failed": result.summary.failed,
            "destination_account_ids": [
                item.destination_account_id for item in result.results if item.destination_account_id
            ],
        },
    )
    return result


@router.get("/{account_id}/trends", response_model=AccountTrendsResponse)
async def get_account_trends(
    account_id: str,
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountTrendsResponse:
    result = await context.service.get_account_trends(account_id)
    if not result:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    return result


@router.get("/{account_id}/usage-reset-credits", response_model=AccountUsageResetCreditsResponse)
async def get_account_usage_reset_credits(
    account_id: str,
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountUsageResetCreditsResponse:
    try:
        result = await context.service.get_usage_reset_credits(account_id)
    except AccountUsageResetCreditsUnavailableError as exc:
        raise DashboardConflictError(str(exc), code="account_usage_reset_credits_unavailable") from exc
    except UpstreamProxyRouteError as exc:
        raise DashboardUpstreamError(
            f"Unable to resolve upstream proxy route for usage reset credits: {exc.reason}",
            code="upstream_proxy_unavailable",
        ) from exc
    except UsageFetchError as exc:
        raise DashboardUpstreamError(
            f"Usage reset credits fetch failed: {exc.message}",
            code="usage_reset_credits_fetch_failed",
        ) from exc
    if not result:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    return result


@router.post("/{account_id}/usage-reset-credits/consume", response_model=AccountUsageResetConsumeResponse)
async def consume_account_usage_reset_credit(
    request: Request,
    account_id: str,
    payload: AccountUsageResetConsumeRequest | None = None,
    _write_access=Depends(require_dashboard_write_access),
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountUsageResetConsumeResponse:
    try:
        result = await context.service.consume_usage_reset_credit(
            account_id,
            redeem_request_id=payload.redeem_request_id if payload is not None else None,
        )
    except AccountUsageResetConsumeUnavailableError as exc:
        raise DashboardConflictError(str(exc), code="account_usage_reset_consume_unavailable") from exc
    except UpstreamProxyRouteError as exc:
        raise DashboardUpstreamError(
            f"Unable to resolve upstream proxy route for usage reset: {exc.reason}",
            code="upstream_proxy_unavailable",
        ) from exc
    except UsageFetchError as exc:
        raise DashboardUpstreamError(
            f"Usage reset consume failed: {exc.message}",
            code="usage_reset_consume_failed",
        ) from exc
    if result is None:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    AuditService.log_async(
        "account_usage_reset_consumed",
        actor_ip=request.client.host if request.client else None,
        details={
            "account_id": result.account_id,
            "code": result.code,
            "windows_reset": result.windows_reset,
            "usage_written": result.usage_written,
        },
    )
    return result


@router.post("/{account_id}/export", response_model=AccountExportResponse, deprecated=True)
async def export_account(
    request: Request,
    response: Response,
    account_id: str,
    _write_access=Depends(require_dashboard_write_access),
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


@router.post("/{account_id}/export/auth", response_model=AccountAuthExportResponse)
async def export_account_auth(
    request: Request,
    response: Response,
    account_id: str,
    _write_access=Depends(require_dashboard_write_access),
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountAuthExportResponse:
    result = await context.service.export_auth(account_id)
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


@router.post("/{account_id}/export/opencode-auth", response_model=AccountOpenCodeAuthExportResponse, deprecated=True)
async def export_account_opencode_auth(
    request: Request,
    response: Response,
    account_id: str,
    _write_access=Depends(require_dashboard_write_access),
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


@router.post(
    "/import",
    response_model=AccountImportResponse,
    openapi_extra=_ACCOUNT_IMPORT_OPENAPI_EXTRA,
)
async def import_account(
    request: Request,
    _write_access=Depends(require_dashboard_write_access),
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountImportResponse:
    raise_for_unsupported_multipart_content_encoding(request)
    async with bounded_multipart_form(
        request,
        ACCOUNT_IMPORT_MULTIPART_POLICY,
        typed_upload_fields=("auth_json",),
    ) as form:
        auth_json = required_upload(form, "auth_json")
        raw = await read_bounded_upload(
            auth_json,
            max_bytes=ACCOUNT_IMPORT_MULTIPART_POLICY.max_file_bytes,
            param="auth_json",
        )
    try:
        response = await context.service.import_account(raw)
        AuditService.log_async(
            "account_created",
            actor_ip=request.client.host if request.client else None,
            details={"account_id": response.account_id},
        )
        return response
    except InvalidAuthJsonError as exc:
        raise DashboardBadRequestError("Invalid auth.json payload", code="invalid_auth_json") from exc
    except AccountIdentityConflictError as exc:
        raise DashboardConflictError(str(exc), code="duplicate_identity_conflict") from exc


@router.post("/{account_id}/reactivate", response_model=AccountReactivateResponse)
async def reactivate_account(
    account_id: str,
    _write_access=Depends(require_dashboard_write_access),
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountReactivateResponse:
    try:
        success = await context.service.reactivate_account(account_id)
    except AccountStateTransitionError as exc:
        raise DashboardConflictError(str(exc), code="account_state_transition_invalid") from exc
    if not success:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    return AccountReactivateResponse(status="reactivated")


@router.patch("/{account_id}", response_model=AccountUpdateResponse)
async def update_account(
    account_id: str,
    payload: AccountUpdateRequest,
    request: Request,
    _write_access=Depends(require_dashboard_write_access),
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountUpdateResponse:
    changed_fields = [field for field, value in payload.model_dump(exclude_unset=True).items() if value is not None]
    if not changed_fields:
        raise DashboardBadRequestError("No supported account fields to update", code="empty_account_update")
    success = await context.service.update_account(
        account_id,
        security_work_authorized=payload.security_work_authorized,
    )
    if not success:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    AuditService.log_async(
        "account_updated",
        actor_ip=request.client.host if request.client else None,
        details={
            "account_id": account_id,
            "changed_fields": changed_fields,
        },
    )
    return AccountUpdateResponse(status="updated")


@router.post("/{account_id}/probe", response_model=AccountProbeResponse)
async def probe_account(
    request: Request,
    account_id: str,
    body: AccountProbeRequest | None = None,
    _write_access=Depends(require_dashboard_write_access),
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountProbeResponse:
    requested_model = body.model if body is not None else None
    try:
        result = await context.service.probe_account(account_id, model=requested_model)
    except AccountNotProbableError as exc:
        raise DashboardConflictError(str(exc), code="account_not_probable") from exc
    except RefreshError as exc:
        raise DashboardConflictError(
            f"Probe could not refresh account credentials: {exc.message}",
            code="account_probe_refresh_failed",
        ) from exc
    if result is None:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    probe_succeeded = 200 <= result.probe_status_code < 300
    if not probe_succeeded or result.usage_refresh_ready_for_probe_settlement():
        try:
            await get_proxy_service_for_app(request.app).record_account_probe_result(
                account_id=result.account_id,
                http_status=result.probe_status_code,
            )
        except Exception:
            logger.exception(
                "Force Probe advisory settlement failed account_id=%s probe_status_code=%s",
                result.account_id,
                result.probe_status_code,
            )
    else:
        logger.warning(
            "Force Probe success skipped advisory settlement before successful usage refresh fetch "
            "account_id=%s probe_status_code=%s",
            result.account_id,
            result.probe_status_code,
        )
    AuditService.log_async(
        "account_probed",
        actor_ip=request.client.host if request.client else None,
        details={
            "account_id": result.account_id,
            "probe_status_code": result.probe_status_code,
            "model": requested_model,
        },
    )
    return result


@router.post("/{account_id}/pause", response_model=AccountPauseResponse)
async def pause_account(
    account_id: str,
    _write_access=Depends(require_dashboard_write_access),
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountPauseResponse:
    try:
        success = await context.service.pause_account(account_id)
    except AccountStateTransitionError as exc:
        raise DashboardConflictError(str(exc), code="account_state_transition_invalid") from exc
    if not success:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    return AccountPauseResponse(status="paused")


@router.put("/{account_id}/alias", response_model=AccountAliasResponse)
async def set_account_alias(
    account_id: str,
    payload: AccountAliasRequest,
    _write_access=Depends(require_dashboard_write_access),
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
    _write_access=Depends(require_dashboard_write_access),
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountLimitWarmupUpdateResponse:
    success = await context.service.set_limit_warmup_enabled(account_id, payload.enabled)
    if not success:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    return AccountLimitWarmupUpdateResponse(
        status="enabled" if payload.enabled else "disabled",
        enabled=payload.enabled,
    )


@router.put("/{account_id}/routing-policy", response_model=AccountRoutingPolicyUpdateResponse)
async def update_account_routing_policy(
    account_id: str,
    payload: AccountRoutingPolicyUpdateRequest,
    _write_access=Depends(require_dashboard_write_access),
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountRoutingPolicyUpdateResponse:
    success = await context.service.set_routing_policy(account_id, payload.routing_policy)
    if not success:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    return AccountRoutingPolicyUpdateResponse(account_id=account_id, routing_policy=payload.routing_policy)


@router.delete("/{account_id}", response_model=AccountDeleteResponse)
async def delete_account(
    request: Request,
    account_id: str,
    delete_history: bool = False,
    _write_access=Depends(require_dashboard_write_access),
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
