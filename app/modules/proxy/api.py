from __future__ import annotations

import inspect
import logging
import time
from collections.abc import AsyncIterator, Mapping
from datetime import datetime, timezone
from typing import cast

import anyio
from fastapi import APIRouter, Body, Depends, File, Form, Request, Response, Security, UploadFile, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import usage as usage_core
from app.core.auth.dependencies import (
    set_openai_error_format,
    validate_codex_usage_identity,
    validate_proxy_api_key,
    validate_proxy_api_key_authorization,
    validate_usage_api_key,
)
from app.core.clients.openai_platform import OpenAIPlatformError
from app.core.clients.proxy import ProxyResponseError
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.errors import OpenAIErrorEnvelope, openai_error, response_failed_event
from app.core.exceptions import ProxyAuthError, ProxyRateLimitError
from app.core.metrics.prometheus import PROMETHEUS_AVAILABLE, bridge_public_contract_error_total
from app.core.middleware.api_firewall import _parse_trusted_proxy_networks, resolve_connection_client_ip
from app.core.openai.chat_requests import ChatCompletionsRequest
from app.core.openai.chat_responses import ChatCompletionResult, collect_chat_completion, stream_chat_chunks
from app.core.openai.exceptions import ClientPayloadError
from app.core.openai.model_registry import UpstreamModel, get_model_registry, is_public_model
from app.core.openai.models import (
    CompactResponseResult,
    OpenAIError,
    OpenAIResponsePayload,
    OpenAIResponseResult,
)
from app.core.openai.models import (
    OpenAIErrorEnvelope as OpenAIErrorEnvelopeModel,
)
from app.core.openai.parsing import parse_response_payload
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.core.openai.v1_requests import V1ResponsesCompactRequest, V1ResponsesRequest
from app.core.resilience.overload import is_local_overload_error_code, merge_retry_after_headers
from app.core.runtime_logging import log_error_response
from app.core.types import JsonValue
from app.core.usage.types import UsageWindowRow
from app.core.utils.json_guards import is_json_mapping
from app.core.utils.request_id import ensure_request_id, get_request_id
from app.core.utils.sse import format_sse_event, parse_sse_data_json
from app.db.models import Account, AccountStatus, UsageHistory
from app.db.session import get_background_session
from app.dependencies import ProxyContext, get_proxy_context, get_proxy_websocket_context
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import (
    ApiKeyData,
    ApiKeyInvalidError,
    ApiKeyRateLimitExceededError,
    ApiKeySelfLimitData,
    ApiKeySelfUsageData,
    ApiKeysService,
    ApiKeyUsageReservationData,
)
from app.modules.firewall.repository import FirewallRepository
from app.modules.firewall.service import FirewallRepositoryPort, FirewallService
from app.modules.proxy import service as proxy_service_module
from app.modules.proxy.helpers import _rate_limit_details
from app.modules.proxy.http_bridge_forwarding import parse_forwarded_request
from app.modules.proxy.request_policy import (
    apply_api_key_enforcement,
    openai_invalid_payload_error,
    openai_validation_error,
    validate_model_access,
)
from app.modules.proxy.schemas import (
    CodexModelEntry,
    CodexModelsResponse,
    ModelListItem,
    ModelListResponse,
    ModelMetadata,
    RateLimitStatusPayload,
    ReasoningLevelSchema,
    V1UsageLimitResponse,
    V1UsageResponse,
)
from app.modules.proxy.types import (
    CreditStatusDetailsData,
    RateLimitStatusPayloadData,
    RateLimitWindowSnapshotData,
)
from app.modules.upstream_identities.types import (
    BACKEND_CODEX_HTTP_ROUTE_FAMILY,
    CHATGPT_PRIVATE_ROUTE_CLASS,
    OPENAI_PLATFORM_PROVIDER_KIND,
    OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
    OPENAI_PUBLIC_WS_ROUTE_CLASS,
    PUBLIC_MODELS_HTTP_ROUTE_FAMILY,
    PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY,
)
from app.modules.usage.repository import UsageRepository

logger = logging.getLogger(__name__)

_PUBLIC_RESPONSE_OUTPUT_ITEM_TYPES = frozenset(
    {
        "message",
        "function_call",
        "function_call_output",
        "reasoning",
        "web_search_call",
        "file_search_call",
        "computer_call",
        "code_interpreter_call",
        "mcp_approval_request",
        "mcp_list_tools",
        "output_image",
    }
)
_PUBLIC_RESPONSE_TEXT_PART_TYPES = frozenset({"output_text", "input_text", "text", "refusal"})

router = APIRouter(
    prefix="/backend-api/codex",
    tags=["proxy"],
    dependencies=[Security(validate_proxy_api_key), Depends(set_openai_error_format)],
)
ws_router = APIRouter(
    prefix="/backend-api/codex",
    tags=["proxy"],
)
v1_router = APIRouter(
    prefix="/v1",
    tags=["proxy"],
    dependencies=[Security(validate_proxy_api_key), Depends(set_openai_error_format)],
)
v1_ws_router = APIRouter(
    prefix="/v1",
    tags=["proxy"],
)
usage_router = APIRouter(
    tags=["proxy"],
    dependencies=[Depends(set_openai_error_format)],
)
transcribe_router = APIRouter(
    prefix="/backend-api",
    tags=["proxy"],
    dependencies=[Security(validate_proxy_api_key), Depends(set_openai_error_format)],
)
internal_router = APIRouter(
    prefix="/internal/bridge",
    tags=["proxy"],
    dependencies=[Depends(set_openai_error_format)],
)

_TRANSCRIPTION_MODEL = "gpt-4o-transcribe"
_UNAVAILABLE_SELECTION_ERROR_CODES = {
    "no_accounts",
    "no_plan_support_for_model",
    "additional_quota_data_unavailable",
    "no_additional_quota_eligible_accounts",
}


@router.post(
    "/responses",
    responses={
        200: {
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string"},
                }
            }
        }
    },
)
async def responses(
    request: Request,
    payload: ResponsesRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    platform_response = await _maybe_handle_platform_backend_codex_responses(
        request=request,
        payload=payload,
        context=context,
        api_key=api_key,
    )
    if platform_response is not None:
        return platform_response
    return await _stream_responses(
        request,
        payload,
        context,
        api_key,
        codex_session_affinity=True,
        openai_cache_affinity=True,
        prefer_http_bridge=True,
        codex_session_budget_reallocation_enabled=not _backend_codex_turn_state_header_present(request.headers),
    )


@ws_router.websocket("/responses")
async def responses_websocket(
    websocket: WebSocket,
    context: ProxyContext = Depends(get_proxy_websocket_context),
) -> None:
    api_key, denial = await _validate_proxy_websocket_request(websocket)
    if denial is not None:
        await websocket.send_denial_response(denial)
        return
    websocket_rejection = await _websocket_provider_rejection(
        websocket,
        context,
        api_key,
        route_family=BACKEND_CODEX_HTTP_ROUTE_FAMILY,
        route_class=CHATGPT_PRIVATE_ROUTE_CLASS,
        error_code="provider_transport_unsupported",
        error_message=(
            "OpenAI Platform identities do not support downstream websocket "
            "/backend-api/codex/responses in this increment."
        ),
    )
    if websocket_rejection is not None:
        await websocket.send_denial_response(websocket_rejection)
        return
    turn_state = proxy_service_module.ensure_downstream_turn_state(websocket.headers)
    await websocket.accept(headers=proxy_service_module.build_downstream_turn_state_accept_headers(turn_state))
    forwarded_headers = dict(websocket.headers)
    forwarded_headers.setdefault("x-codex-turn-state", turn_state)
    await context.service.proxy_responses_websocket(
        websocket,
        forwarded_headers,
        codex_session_affinity=True,
        openai_cache_affinity=True,
        api_key=api_key,
    )


@v1_router.post(
    "/responses",
    response_model=OpenAIResponseResult,
    responses={
        200: {
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string"},
                }
            }
        }
    },
)
async def v1_responses(
    request: Request,
    payload: V1ResponsesRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    try:
        responses_payload = payload.to_responses_request()
    except ClientPayloadError as exc:
        error = openai_invalid_payload_error(exc.param)
        await _persist_proxy_error_log_from_content(
            context=context,
            api_key=api_key,
            model=payload.model,
            route_class=OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
            rejection_reason="invalid_responses_payload",
            content=error,
        )
        return _logged_error_json_response(
            request,
            400,
            error,
            route_class=OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
            rejection_reason="invalid_responses_payload",
        )
    except ValidationError as exc:
        error = openai_validation_error(exc)
        await _persist_proxy_error_log_from_content(
            context=context,
            api_key=api_key,
            model=payload.model,
            route_class=OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
            rejection_reason="invalid_responses_payload",
            content=error,
        )
        return _logged_error_json_response(
            request,
            400,
            error,
            route_class=OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
            rejection_reason="invalid_responses_payload",
        )
    platform_response = await _maybe_handle_platform_v1_responses(
        request=request,
        payload=responses_payload,
        context=context,
        api_key=api_key,
    )
    if platform_response is not None:
        return platform_response
    if responses_payload.stream:
        return await _stream_responses(
            request,
            responses_payload,
            context,
            api_key,
            codex_session_affinity=False,
            openai_cache_affinity=True,
            prefer_http_bridge=True,
        )
    return await _collect_responses(
        request,
        responses_payload,
        context,
        api_key,
        codex_session_affinity=False,
        openai_cache_affinity=True,
        prefer_http_bridge=True,
    )


@internal_router.post(
    "/responses",
    include_in_schema=False,
    responses={
        200: {
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string"},
                }
            }
        }
    },
)
async def internal_bridge_responses(
    request: Request,
    payload: ResponsesRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
) -> Response:
    forwarded_request_context, internal_error = parse_forwarded_request(
        request.headers,
        payload=payload,
        current_instance=get_settings().http_responses_session_bridge_instance_id,
    )
    if internal_error is not None or forwarded_request_context is None:
        assert internal_error is not None
        return _logged_error_json_response(request, internal_error.status_code, internal_error.payload)
    api_key, auth_error = await _validate_internal_bridge_api_key(request)
    if auth_error is not None:
        return auth_error
    skip_limit_enforcement = api_key is None or forwarded_request_context.context.reservation is not None
    forwarded_headers = _strip_internal_bridge_headers(request.headers)
    return await _stream_responses(
        request,
        payload,
        context,
        api_key,
        codex_session_affinity=forwarded_request_context.context.codex_session_affinity,
        openai_cache_affinity=True,
        prefer_http_bridge=True,
        skip_limit_enforcement=skip_limit_enforcement,
        api_key_reservation_override=forwarded_request_context.context.reservation,
        include_rate_limit_headers=False,
        forwarded_request=True,
        forwarded_headers=forwarded_headers,
        forwarded_downstream_turn_state=forwarded_request_context.context.downstream_turn_state,
        forwarded_affinity_kind=forwarded_request_context.context.original_affinity_kind,
        forwarded_affinity_key=forwarded_request_context.context.original_affinity_key,
        codex_session_budget_reallocation_enabled=forwarded_request_context.context.downstream_turn_state is None,
    )


@v1_ws_router.websocket("/responses")
async def v1_responses_websocket(
    websocket: WebSocket,
    context: ProxyContext = Depends(get_proxy_websocket_context),
) -> None:
    api_key, denial = await _validate_proxy_websocket_request(websocket)
    if denial is not None:
        await websocket.send_denial_response(denial)
        return
    websocket_rejection = await _websocket_provider_rejection(
        websocket,
        context,
        api_key,
        route_family=PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY,
        route_class=OPENAI_PUBLIC_WS_ROUTE_CLASS,
        error_code="provider_transport_unsupported",
        error_message="OpenAI Platform identities do not support downstream websocket /v1/responses in phase 1.",
    )
    if websocket_rejection is not None:
        await websocket.send_denial_response(websocket_rejection)
        return
    turn_state = proxy_service_module.ensure_downstream_turn_state(websocket.headers)
    await websocket.accept(headers=proxy_service_module.build_downstream_turn_state_accept_headers(turn_state))
    forwarded_headers = dict(websocket.headers)
    forwarded_headers.setdefault("x-codex-turn-state", turn_state)
    await context.service.proxy_responses_websocket(
        websocket,
        forwarded_headers,
        codex_session_affinity=False,
        openai_cache_affinity=True,
        api_key=api_key,
    )


@router.get("/models", response_model=CodexModelsResponse)
async def models(
    request: Request,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    platform_response = await _maybe_build_platform_codex_models_response(
        request=request,
        context=context,
        api_key=api_key,
    )
    if platform_response is not None:
        return platform_response
    return await _build_codex_models_response(request=request, context=context, api_key=api_key)


@v1_router.get("/models", response_model=ModelListResponse)
async def v1_models(
    request: Request,
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
    context: ProxyContext = Depends(get_proxy_context),
) -> Response:
    platform_response = await _maybe_build_platform_models_response(request=request, context=context, api_key=api_key)
    if platform_response is not None:
        return platform_response
    return await _build_models_response(api_key)


@v1_router.get("/usage", response_model=V1UsageResponse)
async def v1_usage(
    api_key: ApiKeyData = Security(validate_usage_api_key),
) -> V1UsageResponse:
    async with get_background_session() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        usage = await service.get_key_usage_summary_for_self(api_key.id)
        aggregate_limits = await _build_aggregate_credit_limits(session)

    if usage is None:
        raise ProxyAuthError("Invalid API key")

    return V1UsageResponse(
        request_count=usage.request_count,
        total_tokens=usage.total_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        total_cost_usd=usage.total_cost_usd,
        limits=_build_v1_usage_limits(usage, aggregate_limits),
    )


def _build_v1_usage_limits(
    usage: ApiKeySelfUsageData,
    aggregate_limits: dict[str, V1UsageLimitResponse],
) -> list[V1UsageLimitResponse]:
    raw_limits = [_to_v1_usage_limit_response(limit) for limit in usage.limits]
    credit_overrides = {
        limit.limit_window: limit
        for limit in usage.limits
        if limit.limit_type == "credits" and limit.model_filter is None
    }

    if aggregate_limits:
        merged: list[V1UsageLimitResponse] = []
        for window in ("5h", "7d"):
            aggregate = aggregate_limits.get(window)
            if aggregate is None:
                continue
            merged.append(_apply_credit_override(aggregate, credit_overrides.get(window)))
        if {item.limit_window for item in merged} == {"5h", "7d"}:
            return merged

    return raw_limits


def _to_v1_usage_limit_response(limit: ApiKeySelfLimitData) -> V1UsageLimitResponse:
    current_value = max(0, min(limit.current_value, limit.max_value))
    return V1UsageLimitResponse(
        limit_type=limit.limit_type,
        limit_window=limit.limit_window,
        max_value=limit.max_value,
        current_value=current_value,
        remaining_value=max(0, limit.max_value - current_value),
        model_filter=limit.model_filter,
        reset_at=limit.reset_at.isoformat() + "Z",
        source=limit.source,
    )


def _apply_credit_override(
    aggregate_limit: V1UsageLimitResponse,
    override_limit: ApiKeySelfLimitData | None,
) -> V1UsageLimitResponse:
    if override_limit is None:
        return aggregate_limit

    override_max = max(0, override_limit.max_value)
    current_value = max(0, min(aggregate_limit.current_value, override_max))
    return V1UsageLimitResponse(
        limit_type="credits",
        limit_window=aggregate_limit.limit_window,
        max_value=override_max,
        current_value=current_value,
        remaining_value=max(0, override_max - current_value),
        model_filter=None,
        reset_at=aggregate_limit.reset_at,
        source="api_key_override",
    )


async def _build_codex_usage_payload_for_api_key(api_key: ApiKeyData) -> RateLimitStatusPayloadData:
    async with get_background_session() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        usage = await service.get_key_usage_summary_for_self(api_key.id)

    if usage is None:
        raise ProxyAuthError("Invalid API key")

    key_limits = [_to_v1_usage_limit_response(limit) for limit in usage.limits]
    primary_credit_limit = _select_codex_usage_limit(key_limits, "5h") or _select_codex_usage_limit(key_limits, "daily")
    secondary_credit_limit = (
        _select_codex_usage_limit(key_limits, "7d")
        or _select_codex_usage_limit(key_limits, "weekly")
        or _select_codex_usage_limit(key_limits, "monthly")
    )

    return RateLimitStatusPayloadData(
        plan_type="api_key",
        rate_limit=_rate_limit_details(
            _codex_usage_window_snapshot(primary_credit_limit),
            _codex_usage_window_snapshot(secondary_credit_limit),
        ),
        credits=_codex_usage_credit_snapshot(primary_credit_limit, secondary_credit_limit),
    )


def _select_codex_usage_limit(
    limits: list[V1UsageLimitResponse],
    window: str,
) -> V1UsageLimitResponse | None:
    candidates = [
        limit
        for limit in limits
        if limit.limit_window == window and limit.model_filter is None and limit.limit_type == "credits"
    ]
    return candidates[0] if candidates else None


def _codex_usage_window_snapshot(limit: V1UsageLimitResponse | None) -> RateLimitWindowSnapshotData | None:
    if limit is None or limit.max_value <= 0:
        return None
    reset_at = datetime.fromisoformat(limit.reset_at.replace("Z", "+00:00"))
    reset_epoch = int(reset_at.timestamp())
    now_epoch = int(time.time())
    used_percent = max(0, min(100, int((limit.current_value / limit.max_value) * 100)))
    window_seconds = {"5h": 18000, "daily": 86400, "7d": 604800, "weekly": 604800, "monthly": 2592000}.get(
        limit.limit_window
    )
    return RateLimitWindowSnapshotData(
        used_percent=used_percent,
        limit_window_seconds=window_seconds,
        reset_after_seconds=max(0, reset_epoch - now_epoch),
        reset_at=reset_epoch,
    )


def _codex_usage_credit_snapshot(
    primary_limit: V1UsageLimitResponse | None,
    secondary_limit: V1UsageLimitResponse | None,
) -> CreditStatusDetailsData | None:
    preferred = secondary_limit or primary_limit
    if preferred is None or preferred.limit_type != "credits":
        return None
    return CreditStatusDetailsData(
        has_credits=preferred.remaining_value > 0,
        unlimited=False,
        balance=str(preferred.remaining_value),
        approx_local_messages=None,
        approx_cloud_messages=None,
    )


async def _build_aggregate_credit_limits(session: AsyncSession) -> dict[str, V1UsageLimitResponse]:
    usage_repository = UsageRepository(session)
    primary_latest = await usage_repository.latest_by_account(window="primary")
    secondary_latest = await usage_repository.latest_by_account(window="secondary")

    primary_rows = [_usage_entry_to_window_row(entry) for entry in primary_latest.values()]
    secondary_rows = [_usage_entry_to_window_row(entry) for entry in secondary_latest.values()]
    primary_rows, secondary_rows = usage_core.normalize_weekly_only_rows(primary_rows, secondary_rows)

    account_ids = {row.account_id for row in primary_rows} | {row.account_id for row in secondary_rows}
    if not account_ids:
        return {}

    account_map = {account.id: account for account in await _load_accounts_by_id(session, account_ids)}
    if not account_map:
        return {}

    active_account_ids = set(account_map)
    primary_rows = [row for row in primary_rows if row.account_id in active_account_ids]
    secondary_rows = [row for row in secondary_rows if row.account_id in active_account_ids]
    limits: dict[str, V1UsageLimitResponse] = {}

    for window_key, rows, label in (("primary", primary_rows, "5h"), ("secondary", secondary_rows, "7d")):
        if not rows:
            continue
        summary = usage_core.summarize_usage_window(rows, account_map, window_key)
        max_value = max(0, int(round(summary.capacity_credits or 0.0)))
        if max_value <= 0:
            continue
        if summary.reset_at is None:
            continue
        current_value = max(0, min(int(round(summary.used_credits or 0.0)), max_value))
        limits[label] = V1UsageLimitResponse(
            limit_type="credits",
            limit_window=label,
            max_value=max_value,
            current_value=current_value,
            remaining_value=max(0, max_value - current_value),
            model_filter=None,
            reset_at=datetime.fromtimestamp(summary.reset_at, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
            source="aggregate",
        )

    return limits


async def _load_accounts_by_id(session: AsyncSession, account_ids: set[str]) -> list[Account]:
    if not account_ids:
        return []
    result = await session.execute(
        select(Account).where(
            Account.id.in_(account_ids),
            Account.status.notin_((AccountStatus.DEACTIVATED, AccountStatus.PAUSED)),
        )
    )
    return list(result.scalars().all())


def _usage_entry_to_window_row(entry: UsageHistory) -> UsageWindowRow:
    return UsageWindowRow(
        account_id=entry.account_id,
        used_percent=entry.used_percent,
        reset_at=entry.reset_at,
        window_minutes=entry.window_minutes,
        recorded_at=entry.recorded_at,
    )


@transcribe_router.post("/transcribe")
async def backend_transcribe(
    request: Request,
    file: UploadFile = File(...),
    prompt: str | None = Form(None),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> JSONResponse:
    return await _transcribe_request(
        request=request,
        file=file,
        prompt=prompt,
        context=context,
        api_key=api_key,
    )


@v1_router.post("/audio/transcriptions")
async def v1_audio_transcriptions(
    request: Request,
    model: str = Form(...),
    file: UploadFile = File(...),
    prompt: str | None = Form(None),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> JSONResponse:
    if model != _TRANSCRIPTION_MODEL:
        return _logged_error_json_response(
            request,
            status_code=400,
            content=_openai_invalid_transcription_model_error(model),
        )
    return await _transcribe_request(
        request=request,
        file=file,
        prompt=prompt,
        context=context,
        api_key=api_key,
    )


def _build_registry_codex_models_payload(api_key: ApiKeyData | None) -> CodexModelsResponse:
    allowed_models = _allowed_models_for_api_key(api_key)
    registry = get_model_registry()
    models = registry.get_models_with_fallback()
    if not models:
        return CodexModelsResponse(models=[])

    entries: list[CodexModelEntry] = []
    for slug, model in models.items():
        if not is_public_model(model, allowed_models):
            continue
        entries.append(_to_codex_model_entry(model))
    return CodexModelsResponse(models=entries)


def _build_live_codex_models_payload(
    payload: Mapping[str, JsonValue],
    *,
    api_key: ApiKeyData | None,
) -> CodexModelsResponse:
    allowed_models = _allowed_models_for_api_key(api_key)
    raw_items = payload.get("models")
    if not isinstance(raw_items, list):
        return CodexModelsResponse(models=[])

    context_window_overrides = get_settings().model_context_window_overrides
    entries: list[CodexModelEntry] = []
    for item in raw_items:
        if not is_json_mapping(item):
            continue
        slug = item.get("slug")
        if not isinstance(slug, str) or not slug:
            continue
        if allowed_models is not None and slug not in allowed_models:
            continue
        raw_entry = dict(item)
        override_context_window = context_window_overrides.get(slug)
        if override_context_window is not None:
            raw_entry["context_window"] = override_context_window
        try:
            entries.append(CodexModelEntry.model_validate(raw_entry))
        except ValidationError:
            logger.warning("Failed to parse live upstream Codex model entry slug=%s", slug, exc_info=True)
            continue
    return CodexModelsResponse(models=entries)


async def _build_codex_models_response(
    *,
    request: Request,
    context: ProxyContext,
    api_key: ApiKeyData | None,
) -> Response:
    reservation = await _enforce_request_limits(
        api_key,
        request_model=None,
        request_service_tier=None,
    )
    try:
        live_response = await context.service.fetch_chatgpt_codex_models(
            api_key,
            headers=request.headers,
        )
        if live_response is not None:
            content = _build_live_codex_models_payload(
                live_response.payload,
                api_key=api_key,
            ).model_dump(mode="json")
            return JSONResponse(content=content)

        content = _build_registry_codex_models_payload(api_key).model_dump(mode="json")
        return JSONResponse(content=content)
    finally:
        await _release_reservation(reservation)


async def _build_models_response(api_key: ApiKeyData | None) -> Response:
    reservation = await _enforce_request_limits(
        api_key,
        request_model=None,
        request_service_tier=None,
    )

    allowed_models = _allowed_models_for_api_key(api_key)
    created = int(time.time())

    registry = get_model_registry()
    models = registry.get_models_with_fallback()

    if not models:
        await _release_reservation(reservation)
        return JSONResponse(content=ModelListResponse(data=[]).model_dump(mode="json"))

    items: list[ModelListItem] = []
    for slug, model in models.items():
        if not is_public_model(model, allowed_models):
            continue
        items.append(
            ModelListItem(
                id=slug,
                created=created,
                owned_by="codex-lb",
                metadata=_to_model_metadata(model),
            )
        )
    await _release_reservation(reservation)
    return JSONResponse(content=ModelListResponse(data=items).model_dump(mode="json"))


def _build_platform_model_list_response(
    payload: Mapping[str, JsonValue],
    *,
    api_key: ApiKeyData | None,
) -> ModelListResponse:
    allowed_models = _allowed_models_for_api_key(api_key)
    created = int(time.time())
    registry = get_model_registry()
    models = registry.get_models_with_fallback()
    if not models:
        return ModelListResponse(data=[])

    raw_items = payload.get("data")
    if not isinstance(raw_items, list):
        return ModelListResponse(data=[])

    filtered_items: list[ModelListItem] = []
    for item in raw_items:
        if not is_json_mapping(item):
            continue
        model_id = item.get("id")
        if not isinstance(model_id, str):
            continue
        model = models.get(model_id)
        if model is None or not is_public_model(model, allowed_models):
            continue
        raw_created = item.get("created")
        item_created = raw_created if isinstance(raw_created, int) else created
        filtered_items.append(
            ModelListItem(
                id=model_id,
                created=item_created,
                owned_by="codex-lb",
                metadata=_to_model_metadata(model),
            )
        )
    return ModelListResponse(data=filtered_items)


def _allowed_models_for_api_key(api_key: ApiKeyData | None) -> set[str] | None:
    allowed_models = set(api_key.allowed_models) if api_key and api_key.allowed_models else None
    if api_key and api_key.enforced_model:
        forced = {api_key.enforced_model}
        return forced if allowed_models is None else (allowed_models & forced)
    return allowed_models


def _to_codex_model_entry(model: UpstreamModel) -> CodexModelEntry:
    raw = model.raw

    extra: dict[str, JsonValue] = {}
    skip_keys = {
        "slug",
        "display_name",
        "description",
        "base_instructions",
        "default_reasoning_level",
        "supported_reasoning_levels",
        "supported_in_api",
        "priority",
        "minimal_client_version",
        "supports_reasoning_summaries",
        "support_verbosity",
        "default_verbosity",
        "supports_parallel_tool_calls",
        "context_window",
        "input_modalities",
        "available_in_plans",
        "prefer_websockets",
        "visibility",
    }
    for key, value in raw.items():
        if key not in skip_keys and isinstance(value, (bool, int, float, str, type(None), list, Mapping)):
            extra[key] = value

    return CodexModelEntry(
        slug=model.slug,
        display_name=model.display_name,
        description=model.description,
        base_instructions=model.base_instructions,
        default_reasoning_level=model.default_reasoning_level,
        supported_reasoning_levels=[
            ReasoningLevelSchema(effort=rl.effort, description=rl.description)
            for rl in model.supported_reasoning_levels
        ],
        supported_in_api=model.supported_in_api,
        priority=model.priority,
        minimal_client_version=model.minimal_client_version,
        supports_reasoning_summaries=model.supports_reasoning_summaries,
        support_verbosity=model.support_verbosity,
        default_verbosity=model.default_verbosity,
        supports_parallel_tool_calls=model.supports_parallel_tool_calls,
        context_window=_effective_context_window(model),
        input_modalities=list(model.input_modalities),
        available_in_plans=sorted(model.available_in_plans),
        prefer_websockets=model.prefer_websockets,
        visibility=_model_visibility(model),
        **extra,
    )


def _effective_context_window(model: UpstreamModel) -> int:
    overrides = get_settings().model_context_window_overrides
    return overrides.get(model.slug, model.context_window)


def _model_visibility(model: UpstreamModel) -> str:
    visibility = model.raw.get("visibility")
    return visibility if isinstance(visibility, str) else "list"


def _to_model_metadata(model: UpstreamModel) -> ModelMetadata:
    return ModelMetadata(
        display_name=model.display_name,
        description=model.description,
        context_window=_effective_context_window(model),
        input_modalities=list(model.input_modalities),
        supported_reasoning_levels=[
            ReasoningLevelSchema(effort=rl.effort, description=rl.description)
            for rl in model.supported_reasoning_levels
        ],
        default_reasoning_level=model.default_reasoning_level,
        supports_reasoning_summaries=model.supports_reasoning_summaries,
        support_verbosity=model.support_verbosity,
        default_verbosity=model.default_verbosity,
        prefer_websockets=model.prefer_websockets,
        supports_parallel_tool_calls=model.supports_parallel_tool_calls,
        supported_in_api=model.supported_in_api,
        minimal_client_version=model.minimal_client_version,
        priority=model.priority,
    )


@v1_router.post(
    "/chat/completions",
    response_model=ChatCompletionResult,
    responses={
        200: {
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string"},
                }
            }
        }
    },
)
async def v1_chat_completions(
    request: Request,
    payload: ChatCompletionsRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    effective_model = _effective_model_for_api_key(api_key, payload.model)
    validate_model_access(api_key, effective_model)

    rate_limit_headers = await context.service.rate_limit_headers()
    try:
        responses_payload = payload.to_responses_request()
    except ClientPayloadError as exc:
        error = openai_invalid_payload_error(exc.param)
        return _logged_error_json_response(
            request,
            400,
            error,
            headers=rate_limit_headers,
            route_class=OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
            rejection_reason="chat_completions_platform_unsupported",
        )
    except ValidationError as exc:
        error = openai_validation_error(exc)
        return _logged_error_json_response(request, 400, error, headers=rate_limit_headers)
    if await _should_reject_platform_only_route(
        context=context,
        api_key=api_key,
        route_family=PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY,
        model=effective_model,
    ):
        error = _provider_error(
            "provider_feature_unsupported",
            "OpenAI Platform identities do not support /v1/chat/completions in phase 1.",
        )
        await context.service.write_provider_rejection_log(
            api_key=api_key,
            request_id=ensure_request_id(),
            model=effective_model,
            error_code="provider_feature_unsupported",
            error_message=error["error"]["message"],
            route_class=OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
            rejection_reason="chat_completions_platform_unsupported",
        )
        return _logged_error_json_response(request, 400, error, headers=rate_limit_headers)
    responses_payload.stream = True
    apply_api_key_enforcement(responses_payload, api_key)
    reservation = await _enforce_request_limits(
        api_key,
        request_model=responses_payload.model,
        request_service_tier=responses_payload.service_tier,
    )
    stream = context.service.stream_responses(
        responses_payload,
        request.headers,
        codex_session_affinity=False,
        propagate_http_errors=True,
        openai_cache_affinity=True,
        api_key=api_key,
        api_key_reservation=reservation,
        suppress_text_done_events=True,
    )
    try:
        first = await stream.__anext__()
    except StopAsyncIteration:
        first = None
    except ProxyResponseError as exc:
        return _logged_error_json_response(request, exc.status_code, exc.payload, headers=rate_limit_headers)

    stream_with_first = _prepend_first(first, stream)
    if payload.stream:
        stream_options = payload.stream_options
        include_usage = bool(stream_options and stream_options.include_usage)
        return StreamingResponse(
            stream_chat_chunks(stream_with_first, model=responses_payload.model, include_usage=include_usage),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", **rate_limit_headers},
        )

    result = await collect_chat_completion(stream_with_first, model=responses_payload.model)
    if isinstance(result, OpenAIErrorEnvelopeModel):
        error = result.error
        code = error.code if error else None
        status_code = 503 if code in _UNAVAILABLE_SELECTION_ERROR_CODES else 502
        return _logged_error_json_response(
            request,
            status_code,
            content=result.model_dump(mode="json", exclude_none=True),
            headers=rate_limit_headers,
        )
    return JSONResponse(
        content=result.model_dump(mode="json", exclude_none=True),
        status_code=200,
        headers=rate_limit_headers,
    )


async def _stream_responses(
    request: Request,
    payload: ResponsesRequest,
    context: ProxyContext,
    api_key: ApiKeyData | None,
    *,
    codex_session_affinity: bool = False,
    openai_cache_affinity: bool = False,
    suppress_text_done_events: bool = False,
    prefer_http_bridge: bool = False,
    skip_limit_enforcement: bool = False,
    api_key_reservation_override: ApiKeyUsageReservationData | None = None,
    include_rate_limit_headers: bool = True,
    forwarded_request: bool = False,
    forwarded_headers: Mapping[str, str] | None = None,
    forwarded_downstream_turn_state: str | None = None,
    forwarded_affinity_kind: str | None = None,
    forwarded_affinity_key: str | None = None,
    codex_session_budget_reallocation_enabled: bool = True,
) -> Response:
    apply_api_key_enforcement(payload, api_key)
    validate_model_access(api_key, payload.model)
    owns_reservation = api_key_reservation_override is None
    reservation = (
        api_key_reservation_override
        if skip_limit_enforcement
        else await _enforce_request_limits(
            api_key,
            request_model=payload.model,
            request_service_tier=payload.service_tier,
        )
    )

    rate_limit_headers = await context.service.rate_limit_headers() if include_rate_limit_headers else {}
    bridge_active = prefer_http_bridge and proxy_service_module.get_settings().http_responses_session_bridge_enabled
    effective_headers = forwarded_headers or request.headers
    downstream_turn_state = (
        forwarded_downstream_turn_state
        if bridge_active and forwarded_downstream_turn_state is not None
        else proxy_service_module.ensure_http_downstream_turn_state(effective_headers)
        if bridge_active
        else None
    )
    turn_state_headers = (
        proxy_service_module.build_downstream_turn_state_response_headers(downstream_turn_state)
        if downstream_turn_state is not None
        else {}
    )
    payload.stream = True
    if prefer_http_bridge:
        stream = context.service.stream_http_responses(
            payload,
            effective_headers,
            codex_session_affinity=codex_session_affinity,
            propagate_http_errors=True,
            openai_cache_affinity=openai_cache_affinity,
            api_key=api_key,
            api_key_reservation=reservation,
            suppress_text_done_events=suppress_text_done_events,
            downstream_turn_state=downstream_turn_state,
            forwarded_request=forwarded_request,
            forwarded_affinity_kind=forwarded_affinity_kind,
            forwarded_affinity_key=forwarded_affinity_key,
            codex_session_budget_reallocation_enabled=codex_session_budget_reallocation_enabled,
        )
    else:
        stream = context.service.stream_responses(
            payload,
            request.headers,
            codex_session_affinity=codex_session_affinity,
            propagate_http_errors=True,
            openai_cache_affinity=openai_cache_affinity,
            api_key=api_key,
            api_key_reservation=reservation,
            suppress_text_done_events=suppress_text_done_events,
            codex_session_budget_reallocation_enabled=codex_session_budget_reallocation_enabled,
        )
    stream = _normalize_public_responses_stream(stream)
    try:
        first = await stream.__anext__()
    except StopAsyncIteration:
        return StreamingResponse(
            _prepend_first(None, stream),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", **rate_limit_headers},
        )
    except ProxyResponseError as exc:
        if owns_reservation:
            await _release_reservation(reservation)
        return _logged_error_json_response(
            request,
            exc.status_code,
            exc.payload,
            headers=rate_limit_headers,
        )
    return StreamingResponse(
        _prepend_first(first, stream),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", **turn_state_headers, **rate_limit_headers},
    )


def _strip_internal_bridge_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in headers.items() if not key.lower().startswith("x-codex-bridge-")}


async def _collect_responses(
    request: Request,
    payload: ResponsesRequest,
    context: ProxyContext,
    api_key: ApiKeyData | None,
    *,
    codex_session_affinity: bool = False,
    openai_cache_affinity: bool = False,
    suppress_text_done_events: bool = False,
    prefer_http_bridge: bool = False,
) -> Response:
    apply_api_key_enforcement(payload, api_key)
    validate_model_access(api_key, payload.model)
    reservation = await _enforce_request_limits(
        api_key,
        request_model=payload.model,
        request_service_tier=payload.service_tier,
    )

    rate_limit_headers = await context.service.rate_limit_headers()
    bridge_active = prefer_http_bridge and proxy_service_module.get_settings().http_responses_session_bridge_enabled
    downstream_turn_state = (
        proxy_service_module.ensure_http_downstream_turn_state(request.headers) if bridge_active else None
    )
    turn_state_headers = (
        proxy_service_module.build_downstream_turn_state_response_headers(downstream_turn_state)
        if downstream_turn_state is not None
        else {}
    )
    payload.stream = True
    if prefer_http_bridge:
        stream = context.service.stream_http_responses(
            payload,
            request.headers,
            codex_session_affinity=codex_session_affinity,
            propagate_http_errors=True,
            openai_cache_affinity=openai_cache_affinity,
            api_key=api_key,
            api_key_reservation=reservation,
            suppress_text_done_events=suppress_text_done_events,
            downstream_turn_state=downstream_turn_state,
        )
    else:
        stream = context.service.stream_responses(
            payload,
            request.headers,
            codex_session_affinity=codex_session_affinity,
            propagate_http_errors=True,
            openai_cache_affinity=openai_cache_affinity,
            api_key=api_key,
            api_key_reservation=reservation,
            suppress_text_done_events=suppress_text_done_events,
        )
    try:
        response_payload = await _collect_responses_payload(stream)
    except ProxyResponseError as exc:
        await _release_reservation(reservation)
        error = _parse_error_envelope(exc.payload)
        return _logged_error_json_response(
            request,
            exc.status_code,
            error.model_dump(mode="json", exclude_none=True),
            headers=rate_limit_headers,
        )
    if isinstance(response_payload, OpenAIResponsePayload):
        if response_payload.status == "failed":
            error_payload = _error_envelope_from_response(response_payload.error)
            status_code = _status_for_error(error_payload.error)
            return _logged_error_json_response(
                request,
                status_code,
                error_payload.model_dump(mode="json", exclude_none=True),
                headers={**turn_state_headers, **rate_limit_headers},
            )
        return JSONResponse(
            content=response_payload.model_dump(mode="json", exclude_none=True),
            headers={**turn_state_headers, **rate_limit_headers},
        )
    status_code = _status_for_error(response_payload.error)
    return _logged_error_json_response(
        request,
        status_code,
        response_payload.model_dump(mode="json", exclude_none=True),
        headers={**turn_state_headers, **rate_limit_headers},
    )


@router.post("/responses/compact", response_model=CompactResponseResult)
async def responses_compact(
    request: Request,
    payload: ResponsesCompactRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> JSONResponse:
    return await _compact_responses(
        request,
        payload,
        context,
        api_key,
        codex_session_affinity=True,
        openai_cache_affinity=True,
        route_family=BACKEND_CODEX_HTTP_ROUTE_FAMILY,
        route_class=CHATGPT_PRIVATE_ROUTE_CLASS,
    )


@v1_router.post("/responses/compact", response_model=CompactResponseResult)
async def v1_responses_compact(
    request: Request,
    payload: V1ResponsesCompactRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> JSONResponse:
    try:
        compact_payload = payload.to_compact_request()
    except ClientPayloadError as exc:
        error = openai_invalid_payload_error(exc.param)
        await _persist_proxy_error_log_from_content(
            context=context,
            api_key=api_key,
            model=payload.model,
            route_class=OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
            rejection_reason="invalid_compact_payload",
            content=error,
        )
        return _logged_error_json_response(
            request,
            400,
            error,
            route_class=OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
            rejection_reason="invalid_compact_payload",
        )
    except ValidationError as exc:
        error = openai_validation_error(exc)
        await _persist_proxy_error_log_from_content(
            context=context,
            api_key=api_key,
            model=payload.model,
            route_class=OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
            rejection_reason="invalid_compact_payload",
            content=error,
        )
        return _logged_error_json_response(
            request,
            400,
            error,
            route_class=OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
            rejection_reason="invalid_compact_payload",
        )
    return await _compact_responses(
        request,
        compact_payload,
        context,
        api_key,
        codex_session_affinity=False,
        openai_cache_affinity=True,
        route_family=PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY,
        route_class=OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
    )


async def _compact_responses(
    request: Request,
    payload: ResponsesCompactRequest,
    context: ProxyContext,
    api_key: ApiKeyData | None,
    codex_session_affinity: bool = False,
    openai_cache_affinity: bool = False,
    route_family: str = BACKEND_CODEX_HTTP_ROUTE_FAMILY,
    route_class: str = CHATGPT_PRIVATE_ROUTE_CLASS,
) -> JSONResponse:
    apply_api_key_enforcement(payload, api_key)
    validate_model_access(api_key, payload.model)
    effective_model = _effective_model_for_api_key(api_key, payload.model)
    affinity = await _selection_affinity_for_compact_request(
        payload,
        request.headers,
        codex_session_affinity=codex_session_affinity,
        openai_cache_affinity=openai_cache_affinity,
        api_key=api_key,
    )
    selection = await context.service.select_routing_subject(
        capabilities=_derive_request_capabilities(
            route_family=route_family,
            route_class=route_class,
            transport="http",
            model=effective_model,
            headers=request.headers,
        ),
        api_key=api_key,
        sticky_key=affinity.key,
        sticky_kind=affinity.kind,
        reallocate_sticky=affinity.reallocate_sticky,
        sticky_max_age_seconds=affinity.max_age_seconds,
    )
    if selection.failure is not None:
        return await _provider_selection_failure_response(
            request=request,
            context=context,
            api_key=api_key,
            model=effective_model,
            failure=selection.failure,
        )
    reservation = await _enforce_request_limits(
        api_key,
        request_model=payload.model,
        request_service_tier=_compact_request_service_tier(payload),
    )

    rate_limit_headers = await context.service.rate_limit_headers()
    try:
        result = await context.service.compact_responses(
            payload,
            request.headers,
            codex_session_affinity=codex_session_affinity,
            openai_cache_affinity=openai_cache_affinity,
            api_key=api_key,
            api_key_reservation=reservation,
            selected_subject=selection.selected,
            route_family=route_family,
            route_class=route_class,
        )
    except NotImplementedError:
        error = OpenAIErrorEnvelopeModel(
            error=OpenAIError(
                message="responses/compact is not implemented",
                type="server_error",
                code="not_implemented",
            )
        )
        selected_fields = _selected_subject_log_fields(selection.selected)
        return _logged_error_json_response(
            request,
            501,
            error.model_dump(mode="json", exclude_none=True),
            headers=rate_limit_headers,
            route_class=route_class,
            rejection_reason="provider_compact_not_implemented",
            **selected_fields,
        )
    except ProxyResponseError as exc:
        error = _parse_error_envelope(exc.payload)
        selected_fields = _selected_subject_log_fields(selection.selected)
        if getattr(exc, "provider_kind", None) is not None:
            selected_fields["provider_kind"] = exc.provider_kind
        if getattr(exc, "routing_subject_id", None) is not None:
            selected_fields["routing_subject_id"] = exc.routing_subject_id
        return _logged_error_json_response(
            request,
            exc.status_code,
            error.model_dump(mode="json", exclude_none=True),
            headers=rate_limit_headers,
            route_class=route_class,
            upstream_request_id=getattr(exc, "upstream_request_id", None),
            **selected_fields,
        )
    finally:
        await _release_reservation(reservation)
    return JSONResponse(
        content=result.model_dump(mode="json", exclude_none=True),
        headers=rate_limit_headers,
    )


async def _transcribe_request(
    *,
    request: Request,
    file: UploadFile,
    prompt: str | None,
    context: ProxyContext,
    api_key: ApiKeyData | None,
) -> JSONResponse:
    validate_model_access(api_key, _TRANSCRIPTION_MODEL)
    reservation = await _enforce_request_limits(
        api_key,
        request_model=_TRANSCRIPTION_MODEL,
        request_service_tier=None,
    )
    rate_limit_headers = await context.service.rate_limit_headers()
    try:
        audio_bytes = await file.read()
        result = await context.service.transcribe(
            audio_bytes=audio_bytes,
            filename=file.filename or "audio.wav",
            content_type=file.content_type,
            prompt=prompt,
            headers=request.headers,
            api_key=api_key,
        )
    except ProxyResponseError as exc:
        error = _parse_error_envelope(exc.payload)
        return _logged_error_json_response(
            request,
            exc.status_code,
            error.model_dump(mode="json", exclude_none=True),
            headers=rate_limit_headers,
        )
    finally:
        await _release_reservation(reservation)
    return JSONResponse(content=result, headers=rate_limit_headers)


@usage_router.get("/api/codex/usage", response_model=RateLimitStatusPayload)
@usage_router.get("/api/codex/usage/", response_model=RateLimitStatusPayload, include_in_schema=False)
async def codex_usage(
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Depends(validate_codex_usage_identity),
) -> RateLimitStatusPayload:
    payload = (
        await _build_codex_usage_payload_for_api_key(api_key)
        if api_key is not None
        else await context.service.get_rate_limit_payload()
    )
    return RateLimitStatusPayload.from_data(payload)


async def _prepend_first(first: str | None, stream: AsyncIterator[str]) -> AsyncIterator[str]:
    if first is not None:
        yield first
    async for line in stream:
        yield line


def _parse_sse_payload(line: str) -> dict[str, JsonValue] | None:
    return parse_sse_data_json(line)


def _backend_codex_turn_state_header_present(headers: Mapping[str, str]) -> bool:
    value = headers.get("x-codex-turn-state")
    return isinstance(value, str) and bool(value.strip())


def _derive_request_capabilities(
    *,
    route_family: str,
    route_class: str,
    transport: str,
    model: str | None,
    payload: ResponsesRequest | None = None,
    headers: Mapping[str, str] | None = None,
) -> proxy_service_module.RequestCapabilities:
    continuity_param = None
    continuity_hint = None
    if headers is not None:
        if payload is not None:
            continuity_param = _platform_continuity_param(route_family, payload, headers)
            continuity_hint = _platform_continuity_hint(route_family, payload, headers)
        else:
            continuity_hint = _platform_session_header_continuity_hint(route_family, headers)
    return proxy_service_module.RequestCapabilities(
        route_family=route_family,
        route_class=route_class,
        transport=transport,
        model=model,
        continuity_param=continuity_param,
        continuity_hint=continuity_hint,
    )


async def _selection_affinity_for_compact_request(
    payload: ResponsesCompactRequest,
    headers: Mapping[str, str],
    *,
    codex_session_affinity: bool,
    openai_cache_affinity: bool,
    api_key: ApiKeyData | None,
):
    settings = await proxy_service_module.get_settings_cache().get()
    return proxy_service_module._sticky_key_for_compact_request(
        payload,
        headers,
        codex_session_affinity=codex_session_affinity,
        openai_cache_affinity=openai_cache_affinity,
        openai_cache_affinity_max_age_seconds=settings.openai_cache_affinity_max_age_seconds,
        sticky_threads_enabled=settings.sticky_threads_enabled,
        api_key=api_key,
    )


async def _selection_affinity_for_responses_request(
    payload: ResponsesRequest,
    headers: Mapping[str, str],
    *,
    codex_session_affinity: bool,
    openai_cache_affinity: bool,
    api_key: ApiKeyData | None,
):
    settings = await proxy_service_module.get_settings_cache().get()
    return proxy_service_module._sticky_key_for_responses_request(
        payload,
        headers,
        codex_session_affinity=codex_session_affinity,
        openai_cache_affinity=openai_cache_affinity,
        openai_cache_affinity_max_age_seconds=settings.openai_cache_affinity_max_age_seconds,
        sticky_threads_enabled=settings.sticky_threads_enabled,
        api_key=api_key,
    )


def _selected_subject_log_fields(
    selected: proxy_service_module.SelectedChatGPTSubject | proxy_service_module.SelectedPlatformSubject | None,
) -> dict[str, str | None]:
    if selected is None:
        return {
            "provider_kind": None,
            "routing_subject_id": None,
        }
    return {
        "provider_kind": selected.provider_kind,
        "routing_subject_id": selected.routing_subject_id,
    }


async def _persist_proxy_error_log_from_content(
    *,
    context: ProxyContext,
    api_key: ApiKeyData | None,
    model: str | None,
    route_class: str | None,
    rejection_reason: str | None,
    content: Mapping[str, JsonValue] | OpenAIErrorEnvelopeModel | OpenAIErrorEnvelope,
    provider_kind: str | None = None,
    routing_subject_id: str | None = None,
    upstream_request_id: str | None = None,
) -> None:
    error_code, error_message = _error_details_from_content(content)
    await context.service.write_proxy_error_log(
        account_id=None,
        provider_kind=provider_kind,
        routing_subject_id=routing_subject_id,
        api_key=api_key,
        request_id=ensure_request_id(),
        model=model,
        error_code=error_code or "server_error",
        error_message=error_message or "Proxy request failed",
        route_class=route_class,
        rejection_reason=rejection_reason,
        upstream_request_id=upstream_request_id,
    )


async def _provider_selection_failure_response(
    *,
    request: Request,
    context: ProxyContext,
    api_key: ApiKeyData | None,
    model: str | None,
    failure: proxy_service_module.ProviderSelectionFailure,
) -> JSONResponse:
    error = _provider_error(
        failure.error_code,
        failure.error_message,
        param=failure.error_param,
    )
    await context.service.write_provider_rejection_log(
        api_key=api_key,
        request_id=ensure_request_id(),
        model=model,
        error_code=failure.error_code,
        error_message=error["error"]["message"],
        route_class=failure.route_class,
        rejection_reason=failure.rejection_reason,
    )
    return _logged_error_json_response(
        request,
        failure.http_status,
        error,
        route_class=failure.route_class,
        rejection_reason=failure.rejection_reason,
    )


def _build_platform_codex_models_response(
    payload: Mapping[str, JsonValue],
    *,
    api_key: ApiKeyData | None,
) -> CodexModelsResponse:
    allowed_models = _allowed_models_for_api_key(api_key)
    registry = get_model_registry()
    models = registry.get_models_with_fallback()
    if not models:
        return CodexModelsResponse(models=[])

    raw_items = payload.get("data")
    if not isinstance(raw_items, list):
        return CodexModelsResponse(models=[])

    entries: list[CodexModelEntry] = []
    for item in raw_items:
        if not is_json_mapping(item):
            continue
        model_id = item.get("id")
        if not isinstance(model_id, str):
            continue
        model = models.get(model_id)
        if model is None or not is_public_model(model, allowed_models):
            continue
        entries.append(_to_codex_model_entry(model))
    return CodexModelsResponse(models=entries)


async def _maybe_build_platform_models_response(
    *,
    request: Request,
    context: ProxyContext,
    api_key: ApiKeyData | None,
    route_family: str = PUBLIC_MODELS_HTTP_ROUTE_FAMILY,
    route_class: str = OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
    codex_shape: bool = False,
) -> Response | None:
    selection = await context.service.select_routing_subject(
        capabilities=_derive_request_capabilities(
            route_family=route_family,
            route_class=route_class,
            transport="http",
            model=None,
        ),
        api_key=api_key,
    )
    if selection.failure is not None:
        return await _provider_selection_failure_response(
            request=request,
            context=context,
            api_key=api_key,
            model=None,
            failure=selection.failure,
        )
    if not selection.is_platform:
        return None
    selected = cast(proxy_service_module.SelectedPlatformSubject, selection.selected)
    reservation = await _enforce_request_limits(
        api_key,
        request_model=None,
        request_service_tier=None,
    )
    try:
        response = await context.service.fetch_platform_models(
            api_key,
            identity=selected.identity,
            route_family=route_family,
            route_class=route_class,
        )
    except OpenAIPlatformError as exc:
        await _release_reservation(reservation)
        return _logged_error_json_response(
            request,
            exc.status_code,
            exc.payload,
            provider_kind=OPENAI_PLATFORM_PROVIDER_KIND,
            routing_subject_id=selected.identity.id,
            route_class=route_class,
            rejection_reason="platform_models_request_failed",
            upstream_request_id=exc.upstream_request_id,
        )
    await _release_reservation(reservation)
    if response is None:
        return None
    if codex_shape:
        content = _build_platform_codex_models_response(response.payload, api_key=api_key).model_dump(mode="json")
    else:
        content = _build_platform_model_list_response(response.payload, api_key=api_key).model_dump(mode="json")
    return JSONResponse(content=content)


async def _maybe_handle_platform_responses(
    *,
    request: Request,
    payload: ResponsesRequest,
    context: ProxyContext,
    api_key: ApiKeyData | None,
    route_family: str,
    route_class: str,
) -> Response | None:
    effective_model = _effective_model_for_api_key(api_key, payload.model)
    affinity = await _selection_affinity_for_responses_request(
        payload,
        request.headers,
        codex_session_affinity=route_family == BACKEND_CODEX_HTTP_ROUTE_FAMILY,
        openai_cache_affinity=True,
        api_key=api_key,
    )
    selection = await context.service.select_routing_subject(
        capabilities=_derive_request_capabilities(
            route_family=route_family,
            route_class=route_class,
            transport="http",
            model=effective_model,
            payload=payload,
            headers=request.headers,
        ),
        api_key=api_key,
        sticky_key=affinity.key,
        sticky_kind=affinity.kind,
        reallocate_sticky=affinity.reallocate_sticky,
        sticky_max_age_seconds=affinity.max_age_seconds,
    )
    if selection.failure is not None:
        return await _provider_selection_failure_response(
            request=request,
            context=context,
            api_key=api_key,
            model=effective_model,
            failure=selection.failure,
        )
    if not selection.is_platform:
        return None
    selected = cast(proxy_service_module.SelectedPlatformSubject, selection.selected)

    apply_api_key_enforcement(payload, api_key)
    validate_model_access(api_key, payload.model)
    reasoning_effort = payload.reasoning.effort if payload.reasoning else None
    requested_service_tier = _normalize_service_tier_value(payload.service_tier)
    forwarded_service_tier = payload.platform_forwarded_service_tier()
    reservation = await _enforce_request_limits(
        api_key,
        request_model=payload.model,
        request_service_tier=forwarded_service_tier,
    )
    rate_limit_headers = await context.service.rate_limit_headers()
    request_id = ensure_request_id()
    start = time.monotonic()
    if payload.stream:
        identity = None
        upstream_response = None
        try:
            identity, upstream_response = await context.service.stream_platform_response_events(
                payload=payload,
                api_key=api_key,
                identity=selected.identity,
                route_family=route_family,
                route_class=route_class,
            )
            if identity is None or upstream_response is None:
                await _release_reservation(reservation)
                return None
            first = await upstream_response.event_stream.__anext__()
        except StopAsyncIteration:
            await _release_reservation(reservation)
            await context.service.write_proxy_error_log(
                account_id=None,
                provider_kind=OPENAI_PLATFORM_PROVIDER_KIND,
                routing_subject_id=selected.identity.id,
                api_key=api_key,
                request_id=request_id,
                model=payload.model,
                error_code="upstream_unavailable",
                error_message="Failed to receive the initial OpenAI Platform streaming response.",
                route_class=route_class,
                rejection_reason="platform_stream_start_failed",
                upstream_request_id=(upstream_response.upstream_request_id if upstream_response is not None else None),
                transport="http",
                latency_ms=int((time.monotonic() - start) * 1000),
            )
            return _logged_error_json_response(
                request,
                502,
                openai_error(
                    "upstream_unavailable",
                    "Failed to receive the initial OpenAI Platform streaming response.",
                ),
                headers=rate_limit_headers,
                provider_kind=OPENAI_PLATFORM_PROVIDER_KIND,
                routing_subject_id=selected.identity.id,
                route_class=route_class,
                rejection_reason="platform_stream_start_failed",
                upstream_request_id=(upstream_response.upstream_request_id if upstream_response is not None else None),
            )
        except OpenAIPlatformError as exc:
            await _release_reservation(reservation)
            return _logged_error_json_response(
                request,
                exc.status_code,
                exc.payload,
                headers=rate_limit_headers,
                provider_kind=OPENAI_PLATFORM_PROVIDER_KIND,
                routing_subject_id=selected.identity.id,
                route_class=route_class,
                rejection_reason="platform_stream_request_failed",
                upstream_request_id=exc.upstream_request_id,
            )
        except Exception as exc:
            await _release_reservation(reservation)
            await context.service._write_request_log(
                account_id=None,
                provider_kind=OPENAI_PLATFORM_PROVIDER_KIND,
                routing_subject_id=selected.identity.id,
                api_key=api_key,
                request_id=request_id,
                model=payload.model,
                latency_ms=int((time.monotonic() - start) * 1000),
                status="error",
                error_code="upstream_unavailable",
                error_message=str(exc) or "Failed to receive the initial OpenAI Platform streaming response.",
                reasoning_effort=reasoning_effort,
                service_tier=forwarded_service_tier,
                requested_service_tier=requested_service_tier,
                route_class=route_class,
                rejection_reason="platform_stream_start_failed",
                transport="http",
            )
            return _logged_error_json_response(
                request,
                502,
                openai_error(
                    "upstream_unavailable",
                    "Failed to receive the initial OpenAI Platform streaming response.",
                ),
                headers=rate_limit_headers,
                provider_kind=OPENAI_PLATFORM_PROVIDER_KIND,
                routing_subject_id=selected.identity.id,
                route_class=route_class,
                rejection_reason="platform_stream_start_failed",
            )
        except BaseException:
            await _release_reservation(reservation)
            raise
        stream = _instrument_platform_stream(
            context=context,
            upstream_stream=upstream_response.event_stream,
            first_line=first,
            request_id=request_id,
            model=payload.model,
            api_key=api_key,
            routing_subject_id=identity.id,
            reservation=reservation,
            start=start,
            upstream_request_id=upstream_response.upstream_request_id,
            route_class=route_class,
            reasoning_effort=reasoning_effort,
            requested_service_tier=requested_service_tier,
            forwarded_service_tier=forwarded_service_tier,
        )
        return StreamingResponse(
            stream,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", **rate_limit_headers},
        )

    try:
        identity, result = await context.service.create_platform_response(
            payload=payload,
            api_key=api_key,
            identity=selected.identity,
            route_family=route_family,
            route_class=route_class,
        )
    except OpenAIPlatformError as exc:
        await _release_reservation(reservation)
        return _logged_error_json_response(
            request,
            exc.status_code,
            exc.payload,
            headers=rate_limit_headers,
            provider_kind=OPENAI_PLATFORM_PROVIDER_KIND,
            routing_subject_id=selected.identity.id,
            route_class=route_class,
            rejection_reason="platform_response_request_failed",
            upstream_request_id=exc.upstream_request_id,
        )

    await _release_reservation(reservation)
    if identity is None or result is None:
        return None
    if isinstance(result.payload, OpenAIResponsePayload):
        result_payload = result.payload.model_dump(mode="json", exclude_none=True)
        parsed_result = result.payload
    else:
        result_payload = result.payload
        parsed_result = parse_response_payload(result.payload)
    status = "success"
    error_code: str | None = None
    error_message: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    actual_service_tier: str | None = None
    if parsed_result is not None:
        usage = parsed_result.usage
        actual_service_tier = _service_tier_from_response_payload(parsed_result)
        if parsed_result.status == "failed":
            status = "error"
            error_code = parsed_result.error.code if parsed_result.error else None
            error_message = parsed_result.error.message if parsed_result.error else None
        if usage is not None:
            input_tokens = usage.input_tokens
            output_tokens = usage.output_tokens
            cached_input_tokens = usage.input_tokens_details.cached_tokens if usage.input_tokens_details else None
            reasoning_tokens = usage.output_tokens_details.reasoning_tokens if usage.output_tokens_details else None
    await context.service._write_request_log(
        account_id=None,
        provider_kind=OPENAI_PLATFORM_PROVIDER_KIND,
        routing_subject_id=identity.id,
        api_key=api_key,
        request_id=request_id,
        model=payload.model,
        latency_ms=int((time.monotonic() - start) * 1000),
        status=status,
        error_code=error_code,
        error_message=error_message,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        reasoning_tokens=reasoning_tokens,
        reasoning_effort=reasoning_effort,
        transport="http",
        service_tier=_effective_service_tier(forwarded_service_tier, actual_service_tier),
        requested_service_tier=requested_service_tier,
        actual_service_tier=actual_service_tier,
        route_class=route_class,
        upstream_request_id=result.upstream_request_id,
    )
    return JSONResponse(content=result_payload, headers=rate_limit_headers)


async def _maybe_handle_platform_v1_responses(
    *,
    request: Request,
    payload: ResponsesRequest,
    context: ProxyContext,
    api_key: ApiKeyData | None,
) -> Response | None:
    return await _maybe_handle_platform_responses(
        request=request,
        payload=payload,
        context=context,
        api_key=api_key,
        route_family=PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY,
        route_class=OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
    )


async def _maybe_build_platform_codex_models_response(
    *,
    request: Request,
    context: ProxyContext,
    api_key: ApiKeyData | None,
) -> Response | None:
    return await _maybe_build_platform_models_response(
        request=request,
        context=context,
        api_key=api_key,
        route_family=BACKEND_CODEX_HTTP_ROUTE_FAMILY,
        route_class=CHATGPT_PRIVATE_ROUTE_CLASS,
        codex_shape=True,
    )


async def _maybe_handle_platform_backend_codex_responses(
    *,
    request: Request,
    payload: ResponsesRequest,
    context: ProxyContext,
    api_key: ApiKeyData | None,
) -> Response | None:
    return await _maybe_handle_platform_responses(
        request=request,
        payload=payload,
        context=context,
        api_key=api_key,
        route_family=BACKEND_CODEX_HTTP_ROUTE_FAMILY,
        route_class=CHATGPT_PRIVATE_ROUTE_CLASS,
    )


async def _instrument_platform_stream(
    *,
    context: ProxyContext,
    upstream_stream: AsyncIterator[str],
    first_line: str,
    request_id: str,
    model: str,
    api_key: ApiKeyData | None,
    routing_subject_id: str,
    reservation: ApiKeyUsageReservationData | None,
    start: float,
    upstream_request_id: str | None,
    route_class: str,
    reasoning_effort: str | None,
    requested_service_tier: str | None,
    forwarded_service_tier: str | None,
) -> AsyncIterator[str]:
    status = "success"
    error_code: str | None = None
    error_message: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    actual_service_tier: str | None = None

    async def _handle_line(line: str) -> str:
        nonlocal status, error_code, error_message, input_tokens, output_tokens, cached_input_tokens
        nonlocal reasoning_tokens, actual_service_tier
        payload = _parse_sse_payload(line)
        if payload is None:
            return line
        event_type = payload.get("type")
        response = payload.get("response")
        if event_type not in (
            "response.completed",
            "response.incomplete",
            "response.failed",
        ) or not isinstance(response, dict):
            return line
        parsed = parse_response_payload(response)
        if parsed is None:
            return line
        actual_service_tier = _service_tier_from_event_payload(payload)
        if parsed.status == "failed":
            status = "error"
            error_code = parsed.error.code if parsed.error else None
            error_message = parsed.error.message if parsed.error else None
        if parsed.usage is not None:
            input_tokens = parsed.usage.input_tokens
            output_tokens = parsed.usage.output_tokens
            cached_input_tokens = (
                parsed.usage.input_tokens_details.cached_tokens if parsed.usage.input_tokens_details else None
            )
            reasoning_tokens = (
                parsed.usage.output_tokens_details.reasoning_tokens if parsed.usage.output_tokens_details else None
            )
        return line

    try:
        yield await _handle_line(first_line)
        async for line in upstream_stream:
            yield await _handle_line(line)
    finally:
        await _release_reservation(reservation)
        await context.service._write_request_log(
            account_id=None,
            provider_kind=OPENAI_PLATFORM_PROVIDER_KIND,
            routing_subject_id=routing_subject_id,
            api_key=api_key,
            request_id=request_id,
            model=model,
            latency_ms=int((time.monotonic() - start) * 1000),
            status=status,
            error_code=error_code,
            error_message=error_message,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            reasoning_tokens=reasoning_tokens,
            reasoning_effort=reasoning_effort,
            transport="http",
            service_tier=_effective_service_tier(forwarded_service_tier, actual_service_tier),
            requested_service_tier=requested_service_tier,
            actual_service_tier=actual_service_tier,
            route_class=route_class,
            upstream_request_id=upstream_request_id,
        )


def _normalize_service_tier_value(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if stripped.lower() == "fast":
        return "priority"
    return stripped


def _service_tier_from_response_payload(response: OpenAIResponsePayload | None) -> str | None:
    if response is None:
        return None
    extra = response.model_extra
    if not isinstance(extra, Mapping):
        return None
    return _normalize_service_tier_value(extra.get("service_tier"))


def _service_tier_from_event_payload(payload: dict[str, JsonValue] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    response = payload.get("response")
    if not isinstance(response, dict):
        return None
    return _normalize_service_tier_value(response.get("service_tier"))


def _effective_service_tier(requested_service_tier: str | None, actual_service_tier: str | None) -> str | None:
    if isinstance(actual_service_tier, str):
        return actual_service_tier
    if isinstance(requested_service_tier, str):
        return requested_service_tier
    return None


async def _websocket_provider_rejection(
    websocket: WebSocket,
    context: ProxyContext,
    api_key: ApiKeyData | None,
    *,
    route_family: str,
    route_class: str,
    error_code: str,
    error_message: str,
) -> JSONResponse | None:
    if not await _should_reject_platform_only_route(
        context=context,
        api_key=api_key,
        route_family=route_family,
        model=None,
    ):
        return None
    error = _provider_error(
        error_code,
        error_message,
        param="transport" if error_code == "provider_transport_unsupported" else None,
    )
    await context.service.write_provider_rejection_log(
        api_key=api_key,
        request_id=ensure_request_id(),
        model=None,
        error_code=error_code,
        error_message=error["error"]["message"],
        route_class=route_class,
        rejection_reason=error_code,
        transport="websocket",
    )
    logger.warning(
        (
            "proxy_error_response request_id=%s method=%s path=%s status=%s code=%s message=%s "
            "provider_kind=%s routing_subject_id=%s route_class=%s rejection_reason=%s upstream_request_id=%s"
        ),
        get_request_id(),
        "WEBSOCKET",
        websocket.url.path,
        400,
        error_code,
        error["error"]["message"],
        None,
        None,
        route_class,
        error_code,
        None,
    )
    return JSONResponse(status_code=400, content=error)


async def _should_reject_platform_only_route(
    *,
    context: ProxyContext,
    api_key: ApiKeyData | None,
    route_family: str,
    model: str | None,
) -> bool:
    platform_identity = await context.service.select_platform_identity(route_family)
    if platform_identity is None:
        return False
    scoped_account_ids = (
        api_key.assigned_account_ids if api_key is not None and api_key.account_assignment_scope_enabled else None
    )
    return not await context.service.has_chatgpt_candidates(model, account_ids=scoped_account_ids)


def _platform_continuity_param(
    route_family: str,
    payload: ResponsesRequest,
    headers: Mapping[str, str],
) -> str | None:
    if payload.conversation:
        return "conversation"
    if payload.previous_response_id:
        return "previous_response_id"
    if route_family == BACKEND_CODEX_HTTP_ROUTE_FAMILY:
        return None
    for key in ("session_id", "x-codex-session-id", "x-codex-conversation-id", "x-codex-turn-state"):
        value = headers.get(key)
        if isinstance(value, str) and value.strip():
            return key
    return None


def _platform_continuity_hint(
    route_family: str,
    payload: ResponsesRequest,
    headers: Mapping[str, str],
) -> str | None:
    if payload.conversation or payload.previous_response_id:
        return None
    return _platform_session_header_continuity_hint(route_family, headers)


def _platform_session_header_continuity_hint(
    route_family: str,
    headers: Mapping[str, str],
) -> str | None:
    if route_family != BACKEND_CODEX_HTTP_ROUTE_FAMILY:
        return None
    for key in ("session_id", "x-codex-session-id", "x-codex-conversation-id", "x-codex-turn-state"):
        value = headers.get(key)
        if isinstance(value, str) and value.strip():
            return key
    return None


def _provider_error(code: str, message: str, *, param: str | None = None) -> OpenAIErrorEnvelope:
    payload = openai_error(code, message, error_type="invalid_request_error")
    if param is not None:
        payload["error"]["param"] = param
    return payload


def _openai_error_code(payload: Mapping[str, JsonValue]) -> str | None:
    error = payload.get("error")
    if not is_json_mapping(error):
        return None
    code = error.get("code")
    return code if isinstance(code, str) else None


def _openai_error_message(payload: Mapping[str, JsonValue]) -> str | None:
    error = payload.get("error")
    if not is_json_mapping(error):
        return None
    message = error.get("message")
    return message if isinstance(message, str) else None


def _logged_error_json_response(
    request: Request,
    status_code: int,
    content: Mapping[str, JsonValue] | OpenAIErrorEnvelopeModel | OpenAIErrorEnvelope,
    *,
    headers: Mapping[str, str] | None = None,
    provider_kind: str | None = None,
    routing_subject_id: str | None = None,
    route_class: str | None = None,
    rejection_reason: str | None = None,
    upstream_request_id: str | None = None,
) -> JSONResponse:
    code, message = _error_details_from_content(content)
    effective_headers = dict(headers or {})
    if status_code == 429 and is_local_overload_error_code(code):
        effective_headers = merge_retry_after_headers(effective_headers)
    log_error_response(
        logger,
        request,
        status_code,
        code,
        message,
        category="proxy_error_response",
        provider_kind=provider_kind,
        routing_subject_id=routing_subject_id,
        route_class=route_class,
        rejection_reason=rejection_reason,
        upstream_request_id=upstream_request_id,
    )
    return JSONResponse(status_code=status_code, content=content, headers=effective_headers or None)


def _error_details_from_content(
    content: Mapping[str, JsonValue] | OpenAIErrorEnvelopeModel | OpenAIErrorEnvelope,
) -> tuple[str | None, str | None]:
    if isinstance(content, OpenAIErrorEnvelopeModel):
        error = content.error
        if error is None:
            return None, None
        return error.code, error.message
    if not isinstance(content, Mapping):
        return None, None
    error = content.get("error")
    if not is_json_mapping(error):
        return None, None
    error_mapping = error
    code = error_mapping.get("code")
    message = error_mapping.get("message")
    return code if isinstance(code, str) else None, message if isinstance(message, str) else None


async def _validate_proxy_websocket_request(
    websocket: WebSocket,
) -> tuple[ApiKeyData | None, JSONResponse | None]:
    denial = await _websocket_firewall_denial_response(websocket)
    if denial is not None:
        return None, denial
    try:
        if "request" in inspect.signature(validate_proxy_api_key_authorization).parameters:
            api_key = await validate_proxy_api_key_authorization(
                websocket.headers.get("authorization"),
                request=websocket,
            )
        else:
            api_key = await validate_proxy_api_key_authorization(websocket.headers.get("authorization"))
    except ProxyAuthError as exc:
        return None, JSONResponse(
            status_code=exc.status_code,
            content=openai_error(exc.code, exc.message, error_type=exc.error_type),
        )
    return api_key, None


async def _validate_internal_bridge_api_key(
    request: Request,
) -> tuple[ApiKeyData | None, JSONResponse | None]:
    dashboard_settings = await get_settings_cache().get()
    if not dashboard_settings.api_key_auth_enabled:
        return None, None
    try:
        api_key = await validate_proxy_api_key_authorization(
            request.headers.get("authorization"),
            request=request,
        )
    except ProxyAuthError as exc:
        return None, JSONResponse(
            status_code=exc.status_code,
            content=openai_error(exc.code, exc.message, error_type=exc.error_type),
        )
    return api_key, None


async def _websocket_firewall_denial_response(websocket: WebSocket) -> JSONResponse | None:
    settings = get_settings()
    client_ip = resolve_connection_client_ip(
        websocket.headers,
        websocket.client.host if websocket.client else None,
        trust_proxy_headers=settings.firewall_trust_proxy_headers,
        trusted_proxy_networks=_parse_trusted_proxy_networks(settings.firewall_trusted_proxy_cidrs),
    )
    async with get_background_session() as session:
        repository = cast(FirewallRepositoryPort, FirewallRepository(session))
        service = FirewallService(repository)
        if await service.is_ip_allowed(client_ip):
            return None
    return JSONResponse(
        status_code=403,
        content=openai_error("ip_forbidden", "Access denied for client IP", error_type="access_error"),
    )


async def _enforce_request_limits(
    api_key: ApiKeyData | None,
    *,
    request_model: str | None,
    request_service_tier: str | None,
) -> ApiKeyUsageReservationData | None:
    if api_key is None:
        return None

    async with get_background_session() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        try:
            return await service.enforce_limits_for_request(
                api_key.id,
                request_model=request_model,
                request_service_tier=request_service_tier,
            )
        except ApiKeyRateLimitExceededError as exc:
            message = f"{exc}. Usage resets at {exc.reset_at.isoformat()}Z."
            raise ProxyRateLimitError(message) from exc
        except ApiKeyInvalidError as exc:
            raise ProxyAuthError(str(exc)) from exc


async def _release_reservation(reservation: ApiKeyUsageReservationData | None) -> None:
    if reservation is None:
        return
    try:
        with anyio.CancelScope(shield=True):
            async with get_background_session() as session:
                service = ApiKeysService(ApiKeysRepository(session))
                await service.release_usage_reservation(reservation.reservation_id)
    except BaseException:
        logger.warning(
            "Failed to release API key usage reservation reservation_id=%s",
            reservation.reservation_id,
            exc_info=True,
        )


def _effective_model_for_api_key(api_key: ApiKeyData | None, requested_model: str) -> str:
    if api_key is None or api_key.enforced_model is None:
        return requested_model
    return api_key.enforced_model


def _compact_request_service_tier(payload: ResponsesCompactRequest) -> str | None:
    value = payload.service_tier
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


async def _collect_responses_payload(stream: AsyncIterator[str]) -> OpenAIResponseResult:
    output_items: dict[int, dict[str, JsonValue]] = {}
    terminal_result: OpenAIResponseResult | None = None
    contract_violation_kind: str | None = None
    async for line in stream:
        payload = _parse_sse_payload(line)
        if not payload:
            if _looks_like_sse_data_block(line):
                contract_violation_kind = contract_violation_kind or "invalid_json"
            continue
        event_type = payload.get("type")
        _collect_output_item_event(payload, output_items)
        if terminal_result is not None:
            continue
        if event_type == "error":
            terminal_result = _parse_event_error_envelope(payload)
            continue
        if event_type == "response.failed":
            response = payload.get("response")
            if isinstance(response, dict):
                error_value = response.get("error")
                if isinstance(error_value, dict):
                    try:
                        terminal_result = OpenAIErrorEnvelopeModel.model_validate({"error": error_value})
                        continue
                    except ValidationError:
                        terminal_result = _default_error_envelope()
                        continue
                parsed = parse_response_payload(response)
                if parsed is not None and parsed.error is not None:
                    terminal_result = _error_envelope_from_response(parsed.error)
                    continue
            terminal_result = _default_error_envelope()
            continue
        if event_type in ("response.completed", "response.incomplete"):
            response = payload.get("response")
            if is_json_mapping(response):
                normalized_response, violation_kind = _normalize_public_response_mapping(response, output_items)
                if violation_kind is not None:
                    contract_violation_kind = contract_violation_kind or violation_kind
                if normalized_response is not None:
                    parsed = parse_response_payload(normalized_response)
                else:
                    parsed = None
                if parsed is not None:
                    terminal_result = parsed
                    continue
            error_kind = contract_violation_kind or "invalid_json"
            terminal_result = _public_contract_error_envelope(
                error_kind,
                _public_contract_error_message(error_kind),
            )

    if terminal_result is not None:
        return terminal_result
    error_kind = contract_violation_kind or "upstream_stream_truncated"
    return _public_contract_error_envelope(
        error_kind,
        _public_contract_error_message(error_kind),
    )


def _collect_output_item_event(
    payload: dict[str, JsonValue],
    output_items: dict[int, dict[str, JsonValue]],
) -> None:
    event_type = payload.get("type")
    if event_type not in ("response.output_item.added", "response.output_item.done"):
        return
    output_index = payload.get("output_index")
    item = payload.get("item")
    if not isinstance(output_index, int) or not isinstance(item, dict):
        return
    output_items[output_index] = dict(item)


def _merge_collected_output_items(
    response: Mapping[str, JsonValue],
    output_items: dict[int, dict[str, JsonValue]],
) -> dict[str, JsonValue]:
    merged = dict(response)
    if not output_items:
        return merged

    existing_output = response.get("output")
    if isinstance(existing_output, list) and existing_output:
        return merged

    merged["output"] = [item for _, item in sorted(output_items.items())]
    return merged


async def _normalize_public_responses_stream(stream: AsyncIterator[str]) -> AsyncIterator[str]:
    terminal_seen = False
    contract_violation_kind: str | None = None
    async for event_block in stream:
        if event_block.strip() == "data: [DONE]":
            if terminal_seen:
                yield event_block
            continue
        payload = _parse_sse_payload(event_block)
        if payload is None:
            if _looks_like_sse_data_block(event_block):
                contract_violation_kind = contract_violation_kind or "invalid_json"
            continue
        normalized_payload, violation_kind = _normalize_public_stream_payload(payload)
        if violation_kind is not None:
            contract_violation_kind = contract_violation_kind or violation_kind
        if normalized_payload is None:
            continue
        event_type = normalized_payload.get("type")
        if isinstance(event_type, str) and event_type in {
            "response.completed",
            "response.incomplete",
            "response.failed",
            "error",
        }:
            terminal_seen = True
        yield format_sse_event(normalized_payload)
    if terminal_seen:
        return
    error_kind = contract_violation_kind or "upstream_stream_truncated"
    yield format_sse_event(
        response_failed_event(
            error_kind,
            _public_contract_error_message(error_kind),
        )
    )


def _normalize_public_stream_payload(
    payload: dict[str, JsonValue],
) -> tuple[dict[str, JsonValue] | None, str | None]:
    event_type = payload.get("type")
    if event_type in ("response.completed", "response.incomplete"):
        response = payload.get("response")
        if not is_json_mapping(response):
            return (
                cast(
                    dict[str, JsonValue],
                    response_failed_event(
                        "invalid_json",
                        _public_contract_error_message("invalid_json"),
                    ),
                ),
                "invalid_json",
            )
        normalized_response, violation_kind = _normalize_public_response_mapping(response)
        if normalized_response is None:
            error_kind = violation_kind or "invalid_output_item"
            return (
                cast(
                    dict[str, JsonValue],
                    response_failed_event(
                        error_kind,
                        _public_contract_error_message(error_kind),
                    ),
                ),
                error_kind,
            )
        normalized_payload = dict(payload)
        normalized_payload["response"] = normalized_response
        return normalized_payload, violation_kind
    if event_type in ("response.output_item.added", "response.output_item.done"):
        item = payload.get("item")
        if not is_json_mapping(item):
            return None, "invalid_output_item"
        normalized_item = _normalize_public_output_item(item)
        if normalized_item is None:
            return None, "invalid_output_item"
        normalized_payload = dict(payload)
        normalized_payload["item"] = normalized_item
        violation_kind = None
        item_type = item.get("type")
        if isinstance(item_type, str) and not _is_public_passthrough_output_item_type(item_type):
            violation_kind = "invalid_output_item"
        return normalized_payload, violation_kind
    return payload, None


def _normalize_public_response_mapping(
    response: Mapping[str, JsonValue],
    output_items: dict[int, dict[str, JsonValue]] | None = None,
) -> tuple[dict[str, JsonValue] | None, str | None]:
    merged = _merge_collected_output_items(response, output_items or {})
    output = merged.get("output")
    if not isinstance(output, list):
        return merged, None
    normalized_output: list[JsonValue] = []
    dropped_items = 0
    for item in output:
        if not is_json_mapping(item):
            dropped_items += 1
            continue
        normalized_item = _normalize_public_output_item(item)
        if normalized_item is None:
            dropped_items += 1
            continue
        normalized_output.append(normalized_item)
    if output and not normalized_output:
        _record_public_contract_violation("invalid_output_item")
        return None, "invalid_output_item"
    normalized = dict(merged)
    normalized["output"] = normalized_output
    if dropped_items:
        _record_public_contract_violation("invalid_output_item")
        return normalized, "invalid_output_item"
    return normalized, None


def _normalize_public_output_item(item: Mapping[str, JsonValue]) -> dict[str, JsonValue] | None:
    item_type = item.get("type")
    if isinstance(item_type, str) and _is_public_passthrough_output_item_type(item_type):
        return dict(item)
    text_value = _extract_public_output_item_text(item)
    if text_value is None:
        return None
    normalized: dict[str, JsonValue] = {
        "type": "message",
        "role": "assistant",
        "status": item.get("status") if isinstance(item.get("status"), str) else "completed",
        "content": [{"type": "output_text", "text": text_value}],
    }
    item_id = item.get("id")
    if isinstance(item_id, str) and item_id:
        normalized["id"] = item_id
    return normalized


def _is_public_passthrough_output_item_type(item_type: str) -> bool:
    if item_type in _PUBLIC_RESPONSE_OUTPUT_ITEM_TYPES:
        return True
    return item_type.endswith("_call") or item_type.endswith("_call_output")


def _extract_public_output_item_text(item: Mapping[str, JsonValue]) -> str | None:
    direct_text = item.get("text")
    if isinstance(direct_text, str) and direct_text:
        return direct_text
    content = item.get("content")
    if is_json_mapping(content):
        content_parts: list[Mapping[str, JsonValue]] = [content]
    elif isinstance(content, list):
        content_parts = [part for part in content if is_json_mapping(part)]
    else:
        content_parts = []
    parts: list[str] = []
    for part in content_parts:
        part_type = part.get("type")
        if isinstance(part_type, str) and part_type in _PUBLIC_RESPONSE_TEXT_PART_TYPES:
            text = part.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
                continue
        text = part.get("text")
        if isinstance(text, str) and text:
            parts.append(text)
    if parts:
        return "".join(parts)
    summary = item.get("summary")
    if isinstance(summary, str) and summary:
        return summary
    return None


def _looks_like_sse_data_block(event_block: str) -> bool:
    return "data:" in event_block


def _public_contract_error_message(kind: str) -> str:
    if kind == "invalid_json":
        return "Responses stream produced an invalid JSON payload"
    if kind == "invalid_output_item":
        return "Responses stream produced unsupported output items"
    if kind == "upstream_stream_truncated":
        return "Responses stream ended before a terminal event"
    return "Responses stream violated the public contract"


def _public_contract_error_envelope(kind: str, message: str) -> OpenAIErrorEnvelopeModel:
    _record_public_contract_violation(kind)
    return OpenAIErrorEnvelopeModel(
        error=OpenAIError(
            message=message,
            type="server_error",
            code=kind,
        )
    )


def _record_public_contract_violation(kind: str) -> None:
    logger.warning("bridge_public_contract_violation kind=%s", kind)
    if PROMETHEUS_AVAILABLE and bridge_public_contract_error_total is not None:
        bridge_public_contract_error_total.labels(kind=kind).inc()


def _parse_event_error_envelope(payload: dict[str, JsonValue]) -> OpenAIErrorEnvelopeModel:
    error_value = payload.get("error")
    if isinstance(error_value, dict):
        try:
            return OpenAIErrorEnvelopeModel.model_validate({"error": error_value})
        except ValidationError:
            return _default_error_envelope()
    return _default_error_envelope()


def _default_error_envelope() -> OpenAIErrorEnvelopeModel:
    return OpenAIErrorEnvelopeModel(
        error=OpenAIError(
            message="Upstream error",
            type="server_error",
            code="upstream_error",
        )
    )


def _parse_error_envelope(payload: JsonValue | OpenAIErrorEnvelope) -> OpenAIErrorEnvelopeModel:
    if not isinstance(payload, dict):
        return _default_error_envelope()
    try:
        return OpenAIErrorEnvelopeModel.model_validate(payload)
    except ValidationError:
        return _default_error_envelope()


def _openai_invalid_transcription_model_error(model: str) -> OpenAIErrorEnvelope:
    error = openai_error(
        "invalid_request_error",
        f"Unsupported transcription model '{model}'. Only '{_TRANSCRIPTION_MODEL}' is supported.",
        error_type="invalid_request_error",
    )
    error["error"]["param"] = "model"
    return error


def _error_envelope_from_response(error_value: OpenAIError | None) -> OpenAIErrorEnvelopeModel:
    if error_value is None:
        return _default_error_envelope()
    return OpenAIErrorEnvelopeModel(error=error_value)


def _status_for_error(error_value: OpenAIError | None) -> int:
    if error_value and error_value.code == "previous_response_not_found":
        return 400
    if error_value and error_value.code in _UNAVAILABLE_SELECTION_ERROR_CODES:
        return 503
    return 502
