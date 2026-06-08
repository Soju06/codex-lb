from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Body, Depends

from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.core.exceptions import DashboardBadRequestError, DashboardConflictError, DashboardNotFoundError
from app.db.models import AgentProviderAccount
from app.dependencies import AgentProviderAccountsContext, get_agent_provider_accounts_context
from app.modules.agent_provider_accounts.schemas import (
    AgentProviderAccountResponse,
    AgentProviderAccountsResponse,
    AgentProviderAccountUpdateRequest,
    AntigravityProviderAccountCreateRequest,
    GeminiProviderAccountCreateRequest,
)
from app.modules.agent_provider_accounts.service import (
    AgentProviderAccountDuplicateError,
    AgentProviderAccountNotFoundError,
    AgentProviderAccountUpdateData,
    AgentProviderAccountValidationError,
    AntigravityProviderAccountCreateData,
    GeminiProviderAccountCreateData,
)
from app.modules.agent_providers.schemas import AgentProviderId

router = APIRouter(
    prefix="/api/agent-providers",
    tags=["agent-provider-accounts"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


def _to_response(row: AgentProviderAccount) -> AgentProviderAccountResponse:
    return AgentProviderAccountResponse(
        account_id=row.id,
        provider_id=cast(AgentProviderId, row.provider_id),
        external_account_id=row.external_account_id,
        display_name=row.display_name,
        status=row.status,
        auth_mode=row.auth_mode,
        api_key_set=row.api_key_encrypted is not None,
        credential_fingerprint=row.credential_fingerprint,
        project_id=row.project_id,
        location=row.location,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/{provider_id}/accounts", response_model=AgentProviderAccountsResponse)
async def list_provider_accounts(
    provider_id: str,
    context: AgentProviderAccountsContext = Depends(get_agent_provider_accounts_context),
) -> AgentProviderAccountsResponse:
    try:
        rows = await context.service.list_accounts(provider_id)
    except AgentProviderAccountValidationError as exc:
        raise DashboardBadRequestError(str(exc), code="unsupported_agent_provider") from exc
    return AgentProviderAccountsResponse(accounts=[_to_response(row) for row in rows])


@router.post("/gemini/accounts", response_model=AgentProviderAccountResponse)
async def create_gemini_provider_account(
    payload: GeminiProviderAccountCreateRequest = Body(...),
    context: AgentProviderAccountsContext = Depends(get_agent_provider_accounts_context),
) -> AgentProviderAccountResponse:
    try:
        row = await context.service.create_gemini_account(
            GeminiProviderAccountCreateData(
                display_name=payload.display_name,
                api_key=payload.api_key,
                external_account_id=payload.external_account_id,
                project_id=payload.project_id,
                location=payload.location,
            )
        )
    except AgentProviderAccountValidationError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_agent_provider_account") from exc
    except AgentProviderAccountDuplicateError as exc:
        raise DashboardConflictError(str(exc), code="duplicate_agent_provider_account") from exc
    return _to_response(row)


@router.post("/antigravity/accounts", response_model=AgentProviderAccountResponse)
async def create_antigravity_provider_account(
    payload: AntigravityProviderAccountCreateRequest = Body(...),
    context: AgentProviderAccountsContext = Depends(get_agent_provider_accounts_context),
) -> AgentProviderAccountResponse:
    try:
        row = await context.service.create_antigravity_account(
            AntigravityProviderAccountCreateData(
                display_name=payload.display_name,
                external_account_id=payload.external_account_id,
                auth_mode=payload.auth_mode,
                api_key=payload.api_key,
                project_id=payload.project_id,
                location=payload.location,
            )
        )
    except AgentProviderAccountValidationError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_agent_provider_account") from exc
    except AgentProviderAccountDuplicateError as exc:
        raise DashboardConflictError(str(exc), code="duplicate_agent_provider_account") from exc
    return _to_response(row)


@router.patch("/{provider_id}/accounts/{account_id}", response_model=AgentProviderAccountResponse)
async def update_provider_account(
    provider_id: str,
    account_id: str,
    payload: AgentProviderAccountUpdateRequest = Body(...),
    context: AgentProviderAccountsContext = Depends(get_agent_provider_accounts_context),
) -> AgentProviderAccountResponse:
    try:
        row = await context.service.update_account(
            provider_id,
            account_id,
            AgentProviderAccountUpdateData(
                display_name=payload.display_name,
                status=payload.status,
                api_key=payload.api_key,
                external_account_id=payload.external_account_id,
                project_id=payload.project_id,
                project_id_set="project_id" in payload.model_fields_set,
                location=payload.location,
                location_set="location" in payload.model_fields_set,
            ),
        )
    except AgentProviderAccountNotFoundError as exc:
        raise DashboardNotFoundError(str(exc), code="agent_provider_account_not_found") from exc
    except AgentProviderAccountValidationError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_agent_provider_account") from exc
    except AgentProviderAccountDuplicateError as exc:
        raise DashboardConflictError(str(exc), code="duplicate_agent_provider_account") from exc
    return _to_response(row)
