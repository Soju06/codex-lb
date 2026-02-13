from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Response
from fastapi.responses import JSONResponse

from app.core.errors import dashboard_error
from app.dependencies import ApiKeysContext, get_api_keys_context
from app.modules.api_keys.schemas import (
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyResponse,
    ApiKeyUpdateRequest,
)
from app.modules.api_keys.service import ApiKeyCreateData, ApiKeyNotFoundError, ApiKeyUpdateData

router = APIRouter(prefix="/api/api-keys", tags=["dashboard"])


@router.post("/", response_model=ApiKeyCreateResponse)
async def create_api_key(
    payload: ApiKeyCreateRequest = Body(...),
    context: ApiKeysContext = Depends(get_api_keys_context),
) -> ApiKeyCreateResponse | JSONResponse:
    try:
        created = await context.service.create_key(
            ApiKeyCreateData(
                name=payload.name,
                allowed_models=payload.allowed_models,
                weekly_token_limit=payload.weekly_token_limit,
                expires_at=payload.expires_at,
            )
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content=dashboard_error("invalid_api_key_payload", str(exc)),
        )
    return ApiKeyCreateResponse(
        id=created.id,
        name=created.name,
        key_prefix=created.key_prefix,
        allowed_models=created.allowed_models,
        weekly_token_limit=created.weekly_token_limit,
        weekly_tokens_used=created.weekly_tokens_used,
        weekly_reset_at=created.weekly_reset_at,
        expires_at=created.expires_at,
        is_active=created.is_active,
        created_at=created.created_at,
        last_used_at=created.last_used_at,
        key=created.key,
    )


@router.get("/", response_model=list[ApiKeyResponse])
async def list_api_keys(
    context: ApiKeysContext = Depends(get_api_keys_context),
) -> list[ApiKeyResponse]:
    rows = await context.service.list_keys()
    return [
        ApiKeyResponse(
            id=row.id,
            name=row.name,
            key_prefix=row.key_prefix,
            allowed_models=row.allowed_models,
            weekly_token_limit=row.weekly_token_limit,
            weekly_tokens_used=row.weekly_tokens_used,
            weekly_reset_at=row.weekly_reset_at,
            expires_at=row.expires_at,
            is_active=row.is_active,
            created_at=row.created_at,
            last_used_at=row.last_used_at,
        )
        for row in rows
    ]


@router.patch("/{key_id}", response_model=ApiKeyResponse)
async def update_api_key(
    key_id: str,
    payload: ApiKeyUpdateRequest = Body(...),
    context: ApiKeysContext = Depends(get_api_keys_context),
) -> ApiKeyResponse | JSONResponse:
    fields = payload.model_fields_set
    update = ApiKeyUpdateData(
        name=payload.name,
        name_set="name" in fields,
        allowed_models=payload.allowed_models,
        allowed_models_set="allowed_models" in fields,
        weekly_token_limit=payload.weekly_token_limit,
        weekly_token_limit_set="weekly_token_limit" in fields,
        expires_at=payload.expires_at,
        expires_at_set="expires_at" in fields,
        is_active=payload.is_active,
        is_active_set="is_active" in fields,
    )
    try:
        row = await context.service.update_key(key_id, update)
    except ApiKeyNotFoundError as exc:
        return JSONResponse(status_code=404, content=dashboard_error("not_found", str(exc)))
    except ValueError as exc:
        return JSONResponse(status_code=400, content=dashboard_error("invalid_api_key_payload", str(exc)))
    return ApiKeyResponse(
        id=row.id,
        name=row.name,
        key_prefix=row.key_prefix,
        allowed_models=row.allowed_models,
        weekly_token_limit=row.weekly_token_limit,
        weekly_tokens_used=row.weekly_tokens_used,
        weekly_reset_at=row.weekly_reset_at,
        expires_at=row.expires_at,
        is_active=row.is_active,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
    )


@router.delete("/{key_id}")
async def delete_api_key(
    key_id: str,
    context: ApiKeysContext = Depends(get_api_keys_context),
) -> Response:
    try:
        await context.service.delete_key(key_id)
    except ApiKeyNotFoundError as exc:
        return JSONResponse(status_code=404, content=dashboard_error("not_found", str(exc)))
    return Response(status_code=204)


@router.post("/{key_id}/regenerate", response_model=ApiKeyCreateResponse)
async def regenerate_api_key(
    key_id: str,
    context: ApiKeysContext = Depends(get_api_keys_context),
) -> ApiKeyCreateResponse | JSONResponse:
    try:
        row = await context.service.regenerate_key(key_id)
    except ApiKeyNotFoundError as exc:
        return JSONResponse(status_code=404, content=dashboard_error("not_found", str(exc)))
    return ApiKeyCreateResponse(
        id=row.id,
        name=row.name,
        key_prefix=row.key_prefix,
        allowed_models=row.allowed_models,
        weekly_token_limit=row.weekly_token_limit,
        weekly_tokens_used=row.weekly_tokens_used,
        weekly_reset_at=row.weekly_reset_at,
        expires_at=row.expires_at,
        is_active=row.is_active,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        key=row.key,
    )
