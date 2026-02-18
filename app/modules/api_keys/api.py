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
    LimitRuleResponse,
)
from app.modules.api_keys.service import (
    ApiKeyCreateData,
    ApiKeyData,
    ApiKeyNotFoundError,
    ApiKeyUpdateData,
    LimitRuleInput,
)

router = APIRouter(prefix="/api/api-keys", tags=["dashboard"])


def _to_response(row: ApiKeyData) -> ApiKeyResponse:
    return ApiKeyResponse(
        id=row.id,
        name=row.name,
        key_prefix=row.key_prefix,
        allowed_models=row.allowed_models,
        expires_at=row.expires_at,
        is_active=row.is_active,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        limits=[
            LimitRuleResponse(
                id=li.id,
                limit_type=li.limit_type,
                limit_window=li.limit_window,
                max_value=li.max_value,
                current_value=li.current_value,
                model_filter=li.model_filter,
                reset_at=li.reset_at,
            )
            for li in row.limits
        ],
    )


def _build_limit_inputs(payload: ApiKeyCreateRequest | ApiKeyUpdateRequest) -> list[LimitRuleInput]:
    limit_inputs: list[LimitRuleInput] = []

    if hasattr(payload, "limits") and payload.limits is not None:
        for lr in payload.limits:
            limit_inputs.append(
                LimitRuleInput(
                    limit_type=lr.limit_type,
                    limit_window=lr.limit_window,
                    max_value=lr.max_value,
                    model_filter=lr.model_filter,
                )
            )
    elif (
        hasattr(payload, "weekly_token_limit")
        and "weekly_token_limit" in payload.model_fields_set
        and payload.weekly_token_limit is not None
    ):
        # Legacy: convert weeklyTokenLimit to a limit rule
        limit_inputs.append(
            LimitRuleInput(
                limit_type="total_tokens",
                limit_window="weekly",
                max_value=payload.weekly_token_limit,
            )
        )

    return limit_inputs


@router.post("/", response_model=ApiKeyCreateResponse)
async def create_api_key(
    payload: ApiKeyCreateRequest = Body(...),
    context: ApiKeysContext = Depends(get_api_keys_context),
) -> ApiKeyCreateResponse | JSONResponse:
    limit_inputs = _build_limit_inputs(payload)

    try:
        created = await context.service.create_key(
            ApiKeyCreateData(
                name=payload.name,
                allowed_models=payload.allowed_models,
                expires_at=payload.expires_at,
                limits=limit_inputs,
            )
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content=dashboard_error("invalid_api_key_payload", str(exc)),
        )
    resp = _to_response(created)
    return ApiKeyCreateResponse(
        **resp.model_dump(),
        key=created.key,
    )


@router.get("/", response_model=list[ApiKeyResponse])
async def list_api_keys(
    context: ApiKeysContext = Depends(get_api_keys_context),
) -> list[ApiKeyResponse]:
    rows = await context.service.list_keys()
    return [_to_response(row) for row in rows]


@router.patch("/{key_id}", response_model=ApiKeyResponse)
async def update_api_key(
    key_id: str,
    payload: ApiKeyUpdateRequest = Body(...),
    context: ApiKeysContext = Depends(get_api_keys_context),
) -> ApiKeyResponse | JSONResponse:
    fields = payload.model_fields_set

    limits_set = "limits" in fields or "weekly_token_limit" in fields
    limit_inputs = _build_limit_inputs(payload) if limits_set else None

    update = ApiKeyUpdateData(
        name=payload.name,
        name_set="name" in fields,
        allowed_models=payload.allowed_models,
        allowed_models_set="allowed_models" in fields,
        expires_at=payload.expires_at,
        expires_at_set="expires_at" in fields,
        is_active=payload.is_active,
        is_active_set="is_active" in fields,
        limits=limit_inputs,
        limits_set=limits_set,
        reset_usage=bool(payload.reset_usage),
    )
    try:
        row = await context.service.update_key(key_id, update)
    except ApiKeyNotFoundError as exc:
        return JSONResponse(status_code=404, content=dashboard_error("not_found", str(exc)))
    except ValueError as exc:
        return JSONResponse(status_code=400, content=dashboard_error("invalid_api_key_payload", str(exc)))
    return _to_response(row)


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
    resp = _to_response(row)
    return ApiKeyCreateResponse(
        **resp.model_dump(),
        key=row.key,
    )
