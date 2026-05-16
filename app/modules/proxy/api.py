from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from datetime import datetime, timezone
from typing import Final, cast

from fastapi import APIRouter, Body, Depends, File, Form, Path, Request, Response, Security, UploadFile, WebSocket
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
from app.core.clients.files import FileProxyError
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
from app.core.openai.images import V1ImageResponse, V1ImagesEditsForm, V1ImagesGenerationsRequest
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
from app.core.utils.sse import format_sse_event, inject_sse_keepalives, parse_sse_data_json
from app.db.models import Account, AccountStatus, UsageHistory
from app.db.session import get_background_session
from app.dependencies import ProxyContext, get_proxy_context, get_proxy_websocket_context
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import (
    ApiKeyData,
    ApiKeyInvalidError,
    ApiKeyRateLimitExceededError,
    ApiKeySelfLimitData,
    ApiKeysService,
    ApiKeyUsageReservationData,
)
from app.modules.firewall.repository import FirewallRepository
from app.modules.firewall.service import FirewallRepositoryPort, FirewallService
from app.modules.proxy import images_service as images_service_module
from app.modules.proxy import service as proxy_service_module
from app.modules.proxy.helpers import _rate_limit_details
from app.modules.proxy.http_bridge_forwarding import parse_forwarded_request
from app.modules.proxy.request_policy import (
    apply_api_key_enforcement,
    enforce_strict_text_format,
    openai_client_payload_error,
    openai_validation_error,
    validate_model_access,
)
from app.modules.proxy.schemas import (
    CodexModelEntry,
    CodexModelsResponse,
    FileCreateRequest,
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
files_router = APIRouter(
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
_STREAM_STARTUP_ERROR_PROBE_SECONDS = 0.05
# Keep bridge startup probing above tiny event-loop scheduling jitter:
# PostgreSQL-backed failures may need a DB round trip before the first item.
_HTTP_BRIDGE_STARTUP_ERROR_PROBE_SECONDS = 0.5

# OpenAI error ``type`` -> HTTP status for the /v1/images/* non-streaming
# error path. The /v1/responses path has its own ``_status_for_error``
# helper that operates on a parsed ``OpenAIError`` model; the image
# adapter works with raw envelope dicts so we map directly here.
_IMAGE_ERROR_TYPE_STATUS: Final[dict[str, int]] = {
    "invalid_request_error": 400,
    "authentication_error": 401,
    "permission_error": 403,
    "not_found_error": 404,
    "rate_limit_error": 429,
    "insufficient_quota": 429,
}

# OpenAI error ``code`` -> HTTP status, applied as a higher-precedence
# override before the type-based mapping above.
_IMAGE_ERROR_CODE_STATUS: Final[dict[str, int]] = {
    "content_policy_violation": 400,
    "rate_limit_exceeded": 429,
    "insufficient_quota": 429,
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
    return await _stream_responses(
        request,
        payload,
        context,
        api_key,
        codex_session_affinity=True,
        openai_cache_affinity=True,
        prefer_http_bridge=True,
        # The Codex CLI consumes codex.* vendor events and the upstream's
        # native event ordering (it does not use the OpenAI Python SDK parser);
        # forward the stream verbatim instead of enforcing the OpenAI SDK
        # contract that /v1/responses applies.
        enforce_openai_sdk_contract=False,
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
        enforce_strict_text_format(responses_payload)
    except ClientPayloadError as exc:
        error = openai_client_payload_error(exc)
        return _logged_error_json_response(request, 400, error)
    except ValidationError as exc:
        error = openai_validation_error(exc)
        return _logged_error_json_response(request, 400, error)
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
        # The OpenAI-SDK contract rewrites (drop ``codex.*``, backfill terminal
        # output, synthesize ``response.created``) MUST be applied by the
        # origin instance — the one that actually responds to the client — so
        # they can honour the original route's ``enforce_openai_sdk_contract``
        # decision. This handler runs on the owner instance after the origin
        # forwarded the request via the internal bridge; if we re-applied them
        # here, a forwarded ``/backend-api/codex/responses`` request would
        # lose ``codex.*`` events (and gain a synthetic ``response.created``)
        # before the origin ever sees the stream. Forward verbatim and let
        # the origin run its own normalization.
        enforce_openai_sdk_contract=False,
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
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    return await _build_codex_models_response(api_key)


@v1_router.get("/models", response_model=ModelListResponse)
async def v1_models(
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
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
        limits=[_to_v1_usage_limit_response(limit) for limit in usage.limits],
        upstream_limits=_ordered_aggregate_limits(aggregate_limits),
    )


def _ordered_aggregate_limits(aggregate_limits: dict[str, V1UsageLimitResponse]) -> list[V1UsageLimitResponse]:
    return [limit for window in ("5h", "7d") if (limit := aggregate_limits.get(window)) is not None]


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


# Synthetic ``model`` strings used for API-key limit accounting +
# request-log filtering on the file upload protocol. They never reach
# upstream -- this is a proxy-internal name only.
_FILES_CREATE_LIMIT_MODEL: Final = "files-create"
_FILES_FINALIZE_LIMIT_MODEL: Final = "files-finalize"


@files_router.post("/files")
async def backend_files_create(
    request: Request,
    payload: FileCreateRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> JSONResponse:
    """Forward a `POST /backend-api/files` upload registration to upstream.

    Accepts ``{file_name, file_size, use_case}`` and returns the upstream
    JSON verbatim (typically ``{file_id, upload_url}``) so callers can
    PUT the bytes directly to the SAS upload URL without going through
    the proxy. The 16 MiB websocket ceiling on ``/responses`` does not
    apply here -- upstream caps file size at 512 MiB which we enforce in
    ``FileCreateRequest``.
    """
    reservation = await _enforce_request_limits(
        api_key,
        request_model=_FILES_CREATE_LIMIT_MODEL,
        request_service_tier=None,
    )
    try:
        result = await context.service.create_file(
            payload.model_dump(mode="json", exclude_none=True),
            request.headers,
            api_key=api_key,
        )
    except FileProxyError as exc:
        error = _parse_error_envelope(exc.payload)
        return _logged_error_json_response(
            request,
            exc.status_code,
            error.model_dump(mode="json", exclude_none=True),
        )
    except ProxyResponseError as exc:
        error = _parse_error_envelope(exc.payload)
        return _logged_error_json_response(
            request,
            exc.status_code,
            error.model_dump(mode="json", exclude_none=True),
        )
    finally:
        await _release_reservation(reservation)
    return JSONResponse(content=result)


@files_router.post("/files/{file_id}/uploaded")
async def backend_files_finalize(
    request: Request,
    file_id: str = Path(..., min_length=1),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> JSONResponse:
    """Forward a `POST /backend-api/files/{file_id}/uploaded` finalize call.

    The upstream contract returns ``{status: success|retry|failed,
    download_url, file_name, mime_type, ...}``. ``service.finalize_file``
    polls upstream for up to 30 s while ``status == "retry"``; we return
    the final payload verbatim so the caller sees what upstream saw.
    """
    reservation = await _enforce_request_limits(
        api_key,
        request_model=_FILES_FINALIZE_LIMIT_MODEL,
        request_service_tier=None,
    )
    try:
        result = await context.service.finalize_file(
            file_id,
            request.headers,
            api_key=api_key,
        )
    except FileProxyError as exc:
        error = _parse_error_envelope(exc.payload)
        return _logged_error_json_response(
            request,
            exc.status_code,
            error.model_dump(mode="json", exclude_none=True),
        )
    except ProxyResponseError as exc:
        error = _parse_error_envelope(exc.payload)
        return _logged_error_json_response(
            request,
            exc.status_code,
            error.model_dump(mode="json", exclude_none=True),
        )
    finally:
        await _release_reservation(reservation)
    return JSONResponse(content=result)


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


@v1_router.post("/images/generations", response_model=None)
async def v1_images_generations(
    request: Request,
    payload: V1ImagesGenerationsRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    return await _proxy_images_generation_request(
        request=request,
        payload=payload,
        context=context,
        api_key=api_key,
    )


@v1_router.post("/images/edits", response_model=None)
async def v1_images_edits(
    request: Request,
    # All typed form fields below are bound as raw strings so FastAPI
    # never 422s on malformed input (e.g. ``n=abc``). Pydantic on
    # ``V1ImagesEditsForm`` coerces and validates them and surfaces any
    # failure as an OpenAI-shape ``invalid_request_error`` envelope.
    model: str | None = Form(None),
    prompt: str = Form(...),
    # Accept either the OpenAI canonical ``image`` form key (single or
    # repeated) or the ``image[]`` array-style key that some OpenAI SDKs
    # / HTTP clients emit when sending multiple files. Both are bound as
    # ``list[UploadFile] = File(None)`` and merged below; at least one
    # entry must be present after the merge.
    image: list[UploadFile] | None = File(None),
    image_brackets: list[UploadFile] | None = File(None, alias="image[]"),
    mask: UploadFile | None = File(None),
    n: str | None = Form(None),
    size: str | None = Form(None),
    quality: str | None = Form(None),
    background: str | None = Form(None),
    output_format: str | None = Form(None),
    output_compression: str | None = Form(None),
    moderation: str | None = Form(None),
    partial_images: str | None = Form(None),
    stream: str | None = Form(None),
    input_fidelity: str | None = Form(None),
    user: str | None = Form(None),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    raw_form: dict[str, object] = {
        "model": model,
        "prompt": prompt,
        "size": size if size is not None else "auto",
        "quality": quality if quality is not None else "auto",
        "background": background if background is not None else "auto",
        "output_format": output_format if output_format is not None else "png",
        "moderation": moderation if moderation is not None else "auto",
        "input_fidelity": input_fidelity,
        "user": user,
    }
    # Pydantic coerces these scalar fields from strings on its own as
    # long as the value is a valid representation (e.g. "1", "true");
    # invalid values land in ValidationError below and we map to
    # ``invalid_request_error`` rather than letting FastAPI 422.
    if n is not None:
        raw_form["n"] = n
    else:
        raw_form["n"] = 1
    if output_compression is not None:
        raw_form["output_compression"] = output_compression
    else:
        raw_form["output_compression"] = 100
    if partial_images is not None:
        raw_form["partial_images"] = partial_images
    if stream is not None:
        raw_form["stream"] = stream
    else:
        raw_form["stream"] = False
    try:
        form_payload = V1ImagesEditsForm.model_validate(raw_form)
    except ValidationError as exc:
        return _logged_error_json_response(request, 400, openai_validation_error(exc))

    # Merge ``image`` and ``image[]`` into a single ordered list. Both
    # form keys are accepted so OpenAI SDKs and HTTP clients that pick
    # either convention work without modification.
    merged_images: list[UploadFile] = []
    if image:
        merged_images.extend(image)
    if image_brackets:
        merged_images.extend(image_brackets)
    if not merged_images:
        return _logged_error_json_response(
            request,
            400,
            images_service_module.make_invalid_request_error(
                "At least one ``image`` (or ``image[]``) multipart part is required.",
                param="image",
            ),
        )

    images_payload: list[tuple[bytes, str | None]] = []
    for upload in merged_images:
        try:
            data = await upload.read()
        finally:
            await upload.close()
        if not data:
            return _logged_error_json_response(
                request,
                400,
                images_service_module.make_invalid_request_error(
                    "image part is empty",
                    param="image",
                ),
            )
        images_payload.append((data, upload.content_type))

    mask_payload: tuple[bytes, str | None] | None = None
    if mask is not None:
        try:
            data = await mask.read()
        finally:
            await mask.close()
        if not data:
            return _logged_error_json_response(
                request,
                400,
                images_service_module.make_invalid_request_error(
                    "mask part is empty",
                    param="mask",
                ),
            )
        mask_payload = (data, mask.content_type)

    return await _proxy_images_edit_request(
        request=request,
        payload=form_payload,
        images=images_payload,
        mask=mask_payload,
        context=context,
        api_key=api_key,
    )


@v1_router.post("/images/variations", include_in_schema=False)
async def v1_images_variations(
    request: Request,
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    # ``api_key`` is captured purely so the standard
    # ``Security(validate_proxy_api_key)`` dependency runs and rejects
    # unauthenticated callers with the same policy as every other
    # /v1/images/* route (and the rest of /v1). Without it, this
    # endpoint would return a public 404 even when proxy API-key auth
    # is enabled, which is an inconsistent auth surface.
    del api_key
    return _logged_error_json_response(
        request,
        status_code=404,
        content=images_service_module.make_not_found_error(
            "/v1/images/variations is not supported by codex-lb. Use /v1/images/edits with an explicit prompt instead."
        ),
    )


async def _prime_upstream_stream(
    request: Request,
    upstream: AsyncIterator[str],
    rate_limit_headers: Mapping[str, str],
    *,
    on_error: Callable[[], Awaitable[None]] | None = None,
) -> tuple[AsyncIterator[str] | None, Response | None]:
    """Pull the first chunk from ``upstream`` so any error raised before the
    first SSE event is surfaced as a structured OpenAI error envelope
    instead of a broken/truncated stream.

    Returns ``(primed_iterator, None)`` on success, where the returned
    iterator yields the captured first chunk followed by the rest of
    ``upstream``. Returns ``(None, error_response)`` when the upstream
    raised before yielding anything; in that case ``on_error`` is called
    so the caller can release reservations.
    """
    iterator = upstream.__aiter__()
    try:
        first_chunk = await iterator.__anext__()
    except StopAsyncIteration:
        first_chunk = None
    except ProxyResponseError as exc:
        if on_error is not None:
            await on_error()
        return None, _logged_error_json_response(
            request,
            exc.status_code,
            exc.payload,
            headers=dict(rate_limit_headers),
        )

    async def _replay() -> AsyncIterator[str]:
        if first_chunk is not None:
            yield first_chunk
        async for chunk in iterator:
            yield chunk

    return _replay(), None


async def _proxy_images_generation_request(
    *,
    request: Request,
    payload: V1ImagesGenerationsRequest,
    context: ProxyContext,
    api_key: ApiKeyData | None,
) -> Response:
    # Apply the API key's enforced model BEFORE running the cross-field
    # validation matrix. Otherwise a request that passes validation
    # under the client-supplied ``model`` (e.g. gpt-image-2 with a 16-
    # multiple custom size) could silently be swapped to a different
    # ``gpt-image-*`` variant whose validation matrix it does not
    # satisfy, leading to a non-canonical upstream failure instead of
    # a deterministic 400 at the API boundary.
    settings = proxy_service_module.get_settings()
    requested_model = payload.model  # may be None; resolved below.
    effective_model = _effective_model_for_api_key(
        api_key,
        requested_model or settings.images_default_model,
    )
    if not images_service_module.is_supported_image_model(effective_model):
        return _logged_error_json_response(
            request,
            400,
            images_service_module.make_invalid_request_error(
                f"Effective model '{effective_model}' is not a 'gpt-image-*' model. "
                f"This API key is pinned to '{effective_model}' which cannot be used on "
                f"/v1/images/* routes; use a key that allows gpt-image models.",
                param="model",
            ),
        )
    if effective_model != requested_model:
        # Rebind ``payload.model`` so the validation matrix below, the
        # downstream translation, request logging, and tool config all
        # see the enforced (or default-resolved) value.
        payload = payload.model_copy(update={"model": effective_model})

    try:
        payload = images_service_module.validate_generations_payload(payload)
    except ClientPayloadError as exc:
        return _logged_error_json_response(request, 400, openai_client_payload_error(exc))

    public_model = payload.model
    assert public_model is not None
    host_model = settings.images_host_model

    try:
        validate_model_access(api_key, effective_model)
    except Exception:
        # Re-raise so the global handler maps to 403.
        raise

    rate_limit_headers = await context.service.rate_limit_headers()
    reservation = await _enforce_request_limits(
        api_key,
        request_model=effective_model,
        request_service_tier=None,
    )

    try:
        responses_payload = images_service_module.images_generation_to_responses_request(payload, host_model=host_model)
    except ValidationError as exc:
        await _release_reservation(reservation)
        return _logged_error_json_response(
            request,
            400,
            openai_validation_error(exc),
            headers=rate_limit_headers,
        )

    # We always need an upstream stream because tool_usage.image_gen only
    # appears on response.completed. For non-streaming clients we drain the
    # stream and translate to a JSON envelope.
    # Pass ``api_key_reservation=None`` so the standard stream settlement
    # in ``_settle_stream_api_key_usage`` does NOT release/finalize the
    # reservation from ``response.usage`` (which is typically empty for
    # the image_generation tool path). The image route owns the
    # reservation lifecycle and finalizes it from the captured
    # ``tool_usage.image_gen`` tokens via ``_finalize_image_reservation``,
    # which avoids the double-billing scenario where standard settlement
    # would charge ``response.usage`` and we would also charge the image
    # tokens.
    upstream = context.service.stream_responses(
        responses_payload,
        request.headers,
        codex_session_affinity=False,
        propagate_http_errors=True,
        openai_cache_affinity=True,
        api_key=api_key,
        api_key_reservation=None,
    )

    # ``images_service`` populates ``response_id`` once the upstream stream
    # surfaces the Responses id, so we can rewrite the request log's model
    # column from the internal host model to the public ``gpt-image-*``
    # value the client actually requested.
    captured: dict[str, object] = {}

    # Prime the upstream stream so that errors raised before the first
    # chunk (e.g. exhausted retries propagating a ProxyResponseError) are
    # surfaced as structured OpenAI error envelopes instead of broken /
    # truncated SSE streams. ``_prime_upstream_stream`` returns either
    # ``(primed_iterator, None)`` on success or ``(None, error_response)``
    # when the upstream raised before yielding anything.
    primed_upstream, prime_error = await _prime_upstream_stream(
        request,
        upstream,
        rate_limit_headers,
        on_error=lambda: _release_reservation(reservation),
    )
    if prime_error is not None:
        return prime_error
    assert primed_upstream is not None

    if payload.stream:
        translated = images_service_module.translate_responses_stream_to_images_stream(
            primed_upstream, captured=captured
        )

        async def _stream_with_log_rewrite() -> AsyncIterator[bytes]:
            try:
                async for chunk in translated:
                    yield chunk.encode("utf-8") if isinstance(chunk, str) else chunk
            finally:
                # Run the request-log model rewrite even when the stream
                # is cancelled mid-flight (e.g. client disconnect). Without
                # this, an interrupted SSE response would leave the
                # request_logs row pinned to the internal host model.
                response_id = captured.get("response_id")
                if response_id and isinstance(response_id, str):
                    await context.service.rewrite_request_log_model(response_id, public_model)
                # Finalize the reservation from the captured
                # ``tool_usage.image_gen`` tokens (or release if
                # upstream never produced a usable image). This is the
                # single point where the image API charges API-key
                # limits; standard stream settlement is bypassed via
                # ``api_key_reservation=None`` above.
                _input = captured.get("image_input_tokens")
                _output = captured.get("image_output_tokens")
                _cached = captured.get("image_cached_input_tokens")
                await _finalize_image_reservation(
                    reservation,
                    model=public_model,
                    input_tokens=_input if isinstance(_input, int) else None,
                    output_tokens=_output if isinstance(_output, int) else None,
                    cached_input_tokens=_cached if isinstance(_cached, int) else None,
                )

        return StreamingResponse(
            _stream_with_log_rewrite(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", **rate_limit_headers},
        )

    try:
        response_payload, error_envelope = await images_service_module.collect_responses_stream_for_images(
            primed_upstream,
            captured=captured,
        )
    except ProxyResponseError as exc:
        await _release_reservation(reservation)
        return _logged_error_json_response(
            request,
            exc.status_code,
            exc.payload,
            headers=rate_limit_headers,
        )

    response_id = captured.get("response_id")
    if response_id and isinstance(response_id, str):
        await context.service.rewrite_request_log_model(response_id, public_model)
    _input = captured.get("image_input_tokens")
    _output = captured.get("image_output_tokens")
    _cached = captured.get("image_cached_input_tokens")
    await _finalize_image_reservation(
        reservation,
        model=public_model,
        input_tokens=_input if isinstance(_input, int) else None,
        output_tokens=_output if isinstance(_output, int) else None,
        cached_input_tokens=_cached if isinstance(_cached, int) else None,
    )

    if error_envelope is not None:
        return _logged_error_json_response(
            request,
            _status_for_image_error_envelope(error_envelope),
            error_envelope,
            headers=rate_limit_headers,
        )
    assert response_payload is not None
    images_result = images_service_module.images_response_from_responses(response_payload)
    if not isinstance(images_result, V1ImageResponse):
        return _logged_error_json_response(
            request,
            _status_for_image_error_envelope(images_result),
            images_result,
            headers=rate_limit_headers,
        )
    return JSONResponse(
        content=images_result.model_dump(mode="json", exclude_none=True),
        headers=rate_limit_headers,
    )


async def _proxy_images_edit_request(
    *,
    request: Request,
    payload: V1ImagesEditsForm,
    images: list[tuple[bytes, str | None]],
    mask: tuple[bytes, str | None] | None,
    context: ProxyContext,
    api_key: ApiKeyData | None,
) -> Response:
    # Apply the API key's enforced model BEFORE validating the
    # cross-field matrix, so the matrix is checked against the model we
    # will actually send upstream. See the matching comment in
    # ``_proxy_images_generation_request``.
    settings = proxy_service_module.get_settings()
    requested_model = payload.model
    effective_model = _effective_model_for_api_key(
        api_key,
        requested_model or settings.images_default_model,
    )
    if not images_service_module.is_supported_image_model(effective_model):
        return _logged_error_json_response(
            request,
            400,
            images_service_module.make_invalid_request_error(
                f"Effective model '{effective_model}' is not a 'gpt-image-*' model. "
                f"This API key is pinned to '{effective_model}' which cannot be used on "
                f"/v1/images/* routes; use a key that allows gpt-image models.",
                param="model",
            ),
        )
    if effective_model != requested_model:
        payload = payload.model_copy(update={"model": effective_model})

    try:
        payload = images_service_module.validate_edits_payload(payload)
    except ClientPayloadError as exc:
        return _logged_error_json_response(request, 400, openai_client_payload_error(exc))

    public_model = payload.model
    assert public_model is not None
    host_model = settings.images_host_model

    validate_model_access(api_key, effective_model)

    rate_limit_headers = await context.service.rate_limit_headers()
    reservation = await _enforce_request_limits(
        api_key,
        request_model=effective_model,
        request_service_tier=None,
    )

    try:
        responses_payload = images_service_module.images_edit_to_responses_request(
            payload,
            host_model=host_model,
            images=images,
            mask=mask,
        )
    except (ValidationError, ValueError) as exc:
        await _release_reservation(reservation)
        if isinstance(exc, ValidationError):
            return _logged_error_json_response(
                request,
                400,
                openai_validation_error(exc),
                headers=rate_limit_headers,
            )
        return _logged_error_json_response(
            request,
            400,
            images_service_module.make_invalid_request_error(str(exc)),
            headers=rate_limit_headers,
        )

    # See ``_proxy_images_generation_request`` for why we pass
    # ``api_key_reservation=None`` and finalize via
    # ``_finalize_image_reservation`` instead.
    upstream = context.service.stream_responses(
        responses_payload,
        request.headers,
        codex_session_affinity=False,
        propagate_http_errors=True,
        openai_cache_affinity=True,
        api_key=api_key,
        api_key_reservation=None,
    )

    captured: dict[str, object] = {}

    primed_upstream, prime_error = await _prime_upstream_stream(
        request,
        upstream,
        rate_limit_headers,
        on_error=lambda: _release_reservation(reservation),
    )
    if prime_error is not None:
        return prime_error
    assert primed_upstream is not None

    if payload.stream:
        translated = images_service_module.translate_responses_stream_to_images_stream(
            primed_upstream, captured=captured, is_edit=True
        )

        async def _stream_with_log_rewrite() -> AsyncIterator[bytes]:
            try:
                async for chunk in translated:
                    yield chunk.encode("utf-8") if isinstance(chunk, str) else chunk
            finally:
                # Run the request-log model rewrite even when the stream
                # is cancelled mid-flight (e.g. client disconnect). Without
                # this, an interrupted SSE response would leave the
                # request_logs row pinned to the internal host model.
                response_id = captured.get("response_id")
                if response_id and isinstance(response_id, str):
                    await context.service.rewrite_request_log_model(response_id, public_model)
                # Finalize the reservation from the captured
                # ``tool_usage.image_gen`` tokens (or release if
                # upstream never produced a usable image). This is the
                # single point where the image API charges API-key
                # limits; standard stream settlement is bypassed via
                # ``api_key_reservation=None`` above.
                _input = captured.get("image_input_tokens")
                _output = captured.get("image_output_tokens")
                _cached = captured.get("image_cached_input_tokens")
                await _finalize_image_reservation(
                    reservation,
                    model=public_model,
                    input_tokens=_input if isinstance(_input, int) else None,
                    output_tokens=_output if isinstance(_output, int) else None,
                    cached_input_tokens=_cached if isinstance(_cached, int) else None,
                )

        return StreamingResponse(
            _stream_with_log_rewrite(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", **rate_limit_headers},
        )

    try:
        response_payload, error_envelope = await images_service_module.collect_responses_stream_for_images(
            primed_upstream,
            captured=captured,
        )
    except ProxyResponseError as exc:
        await _release_reservation(reservation)
        return _logged_error_json_response(
            request,
            exc.status_code,
            exc.payload,
            headers=rate_limit_headers,
        )

    response_id = captured.get("response_id")
    if response_id and isinstance(response_id, str):
        await context.service.rewrite_request_log_model(response_id, public_model)
    _input = captured.get("image_input_tokens")
    _output = captured.get("image_output_tokens")
    _cached = captured.get("image_cached_input_tokens")
    await _finalize_image_reservation(
        reservation,
        model=public_model,
        input_tokens=_input if isinstance(_input, int) else None,
        output_tokens=_output if isinstance(_output, int) else None,
        cached_input_tokens=_cached if isinstance(_cached, int) else None,
    )

    if error_envelope is not None:
        return _logged_error_json_response(
            request,
            _status_for_image_error_envelope(error_envelope),
            error_envelope,
            headers=rate_limit_headers,
        )
    assert response_payload is not None
    images_result = images_service_module.images_response_from_responses(response_payload)
    if not isinstance(images_result, V1ImageResponse):
        return _logged_error_json_response(
            request,
            _status_for_image_error_envelope(images_result),
            images_result,
            headers=rate_limit_headers,
        )
    return JSONResponse(
        content=images_result.model_dump(mode="json", exclude_none=True),
        headers=rate_limit_headers,
    )


async def _build_codex_models_response(api_key: ApiKeyData | None) -> Response:
    reservation = await _enforce_request_limits(
        api_key,
        request_model=None,
        request_service_tier=None,
    )

    allowed_models = _allowed_models_for_api_key(api_key)

    registry = get_model_registry()
    models = registry.get_models_with_fallback()

    if not models:
        await _release_reservation(reservation)
        return JSONResponse(content=CodexModelsResponse(models=[]).model_dump(mode="json"))

    entries: list[CodexModelEntry] = []
    for slug, model in models.items():
        if not is_public_model(model, allowed_models):
            continue
        entries.append(_to_codex_model_entry(model))
    await _release_reservation(reservation)
    return JSONResponse(content=CodexModelsResponse(models=entries).model_dump(mode="json"))


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

    # If context_window is overridden, also override max_context_window to match
    effective_cw = _effective_context_window(model)
    if effective_cw != model.context_window and "max_context_window" in extra:
        extra["max_context_window"] = effective_cw

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
        enforce_strict_text_format(responses_payload)
    except ClientPayloadError as exc:
        error = openai_client_payload_error(exc)
        return _logged_error_json_response(request, 400, error, headers=rate_limit_headers)
    except ValidationError as exc:
        error = openai_validation_error(exc)
        return _logged_error_json_response(request, 400, error, headers=rate_limit_headers)
    reservation = await _enforce_request_limits(
        api_key,
        request_model=effective_model,
        request_service_tier=responses_payload.service_tier,
    )
    responses_payload.stream = True
    apply_api_key_enforcement(responses_payload, api_key)
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
    stream, startup_error = await _probe_stream_startup_error(stream)
    if startup_error is not None:
        return _stream_startup_error_response(request, startup_error, headers=rate_limit_headers)
    if payload.stream:
        stream_options = payload.stream_options
        include_usage = bool(stream_options and stream_options.include_usage)
        return StreamingResponse(
            inject_sse_keepalives(
                stream_chat_chunks(
                    _stream_proxy_errors_as_response_failed(stream),
                    model=responses_payload.model,
                    include_usage=include_usage,
                ),
                get_settings().sse_keepalive_interval_seconds,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", **rate_limit_headers},
        )

    try:
        first = await stream.__anext__()
    except StopAsyncIteration:
        first = None
    except ProxyResponseError as exc:
        return _logged_error_json_response(request, exc.status_code, exc.payload, headers=rate_limit_headers)

    stream_with_first = _prepend_first(first, stream)
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
    enforce_openai_sdk_contract: bool = True,
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
    stream, startup_error = await _probe_stream_startup_error(stream)
    if startup_error is not None:
        if owns_reservation:
            await _release_reservation(reservation)
        return _stream_startup_error_response(
            request,
            startup_error,
            headers=rate_limit_headers,
        )
    stream = _normalize_public_responses_stream(
        _stream_response_error_events(
            stream,
            owns_reservation=owns_reservation,
            reservation=reservation,
        ),
        enforce_openai_sdk_contract=enforce_openai_sdk_contract,
    )
    return StreamingResponse(
        inject_sse_keepalives(
            stream,
            get_settings().sse_keepalive_interval_seconds,
        ),
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
        status_code, error = _mask_previous_response_not_found_error(error, default_status=exc.status_code)
        return _logged_error_json_response(
            request,
            status_code,
            error.model_dump(mode="json", exclude_none=True),
            headers=rate_limit_headers,
        )
    if isinstance(response_payload, OpenAIResponsePayload):
        if response_payload.status == "failed":
            error_payload = _error_envelope_from_response(response_payload.error)
            status_code, error_payload = _mask_previous_response_not_found_error(error_payload)
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
    status_code, response_payload = _mask_previous_response_not_found_error(response_payload)
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
        request, payload, context, api_key, codex_session_affinity=True, openai_cache_affinity=True
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
        error = openai_client_payload_error(exc)
        return _logged_error_json_response(request, 400, error)
    except ValidationError as exc:
        error = openai_validation_error(exc)
        return _logged_error_json_response(request, 400, error)
    return await _compact_responses(
        request,
        compact_payload,
        context,
        api_key,
        codex_session_affinity=False,
        openai_cache_affinity=True,
    )


async def _compact_responses(
    request: Request,
    payload: ResponsesCompactRequest,
    context: ProxyContext,
    api_key: ApiKeyData | None,
    codex_session_affinity: bool = False,
    openai_cache_affinity: bool = False,
) -> JSONResponse:
    apply_api_key_enforcement(payload, api_key)
    validate_model_access(api_key, payload.model)
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
        )
    except NotImplementedError:
        error = OpenAIErrorEnvelopeModel(
            error=OpenAIError(
                message="responses/compact is not implemented",
                type="server_error",
                code="not_implemented",
            )
        )
        return _logged_error_json_response(
            request,
            501,
            error.model_dump(mode="json", exclude_none=True),
            headers=rate_limit_headers,
        )
    except ProxyResponseError as exc:
        error = _parse_error_envelope(exc.payload)
        status_code, error = _mask_previous_response_not_found_error(error, default_status=exc.status_code)
        return _logged_error_json_response(
            request,
            status_code,
            error.model_dump(mode="json", exclude_none=True),
            headers=rate_limit_headers,
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


async def _probe_stream_startup_error(
    stream: AsyncIterator[str],
    *,
    convert_event_errors: bool = False,
    timeout_seconds: float = _STREAM_STARTUP_ERROR_PROBE_SECONDS,
) -> tuple[AsyncIterator[str], ProxyResponseError | OpenAIErrorEnvelopeModel | None]:
    first_task = asyncio.create_task(anext(stream))
    try:
        first = await asyncio.wait_for(
            asyncio.shield(first_task),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        return _prepend_first_task(first_task, stream), None
    except StopAsyncIteration:
        return _prepend_first(None, stream), None
    except ProxyResponseError as exc:
        return _prepend_first(None, stream), exc
    if convert_event_errors:
        first_error = _stream_event_error_envelope(first)
        if first_error is not None:
            aclose = getattr(stream, "aclose", None)
            if callable(aclose):
                await aclose()
            return _prepend_first(None, stream), first_error
    return _prepend_first(first, stream), None


async def _prepend_first_task(first_task: asyncio.Task[str], stream: AsyncIterator[str]) -> AsyncIterator[str]:
    try:
        yield await first_task
    except StopAsyncIteration:
        return
    async for line in stream:
        yield line


async def _stream_proxy_errors_as_response_failed(stream: AsyncIterator[str]) -> AsyncIterator[str]:
    async for line in _stream_response_error_events(stream, owns_reservation=False, reservation=None):
        yield line


async def _stream_response_error_events(
    stream: AsyncIterator[str],
    *,
    owns_reservation: bool,
    reservation: ApiKeyUsageReservationData | None,
) -> AsyncIterator[str]:
    try:
        async for line in stream:
            yield line
    except ProxyResponseError as exc:
        if owns_reservation:
            await _release_reservation(reservation)
        envelope = _parse_error_envelope(exc.payload)
        _, envelope = _mask_previous_response_not_found_error(envelope, default_status=exc.status_code)
        error = envelope.error
        yield format_sse_event(
            response_failed_event(
                error.code if error and error.code else "upstream_error",
                error.message if error and error.message else "Upstream error",
                error.type if error and error.type else "server_error",
                error_param=error.param if error else None,
            )
        )


def _stream_startup_error_response(
    request: Request,
    error: ProxyResponseError | OpenAIErrorEnvelopeModel,
    *,
    headers: Mapping[str, str],
) -> JSONResponse:
    if isinstance(error, ProxyResponseError):
        envelope = _parse_error_envelope(error.payload)
        status_code, envelope = _mask_previous_response_not_found_error(envelope, default_status=error.status_code)
        return _logged_error_json_response(
            request,
            status_code,
            envelope.model_dump(mode="json", exclude_none=True),
            headers=headers,
        )
    status_code, envelope = _mask_previous_response_not_found_error(error)
    return _logged_error_json_response(
        request,
        status_code,
        envelope.model_dump(mode="json", exclude_none=True),
        headers=headers,
    )


def _stream_event_error_envelope(event_block: str) -> OpenAIErrorEnvelopeModel | None:
    payload = _parse_sse_payload(event_block)
    if payload is None:
        return None
    event_type = payload.get("type")
    if event_type == "error":
        return _parse_event_error_envelope(payload)
    if event_type != "response.failed":
        return None
    response = payload.get("response")
    if not isinstance(response, dict):
        return _default_error_envelope()
    error_value = response.get("error")
    if isinstance(error_value, dict):
        try:
            return OpenAIErrorEnvelopeModel.model_validate({"error": error_value})
        except ValidationError:
            return _default_error_envelope()
    parsed = parse_response_payload(response)
    if parsed is not None and parsed.error is not None:
        return _error_envelope_from_response(parsed.error)
    return _default_error_envelope()


def _parse_sse_payload(line: str) -> dict[str, JsonValue] | None:
    return parse_sse_data_json(line)


def _logged_error_json_response(
    request: Request,
    status_code: int,
    content: Mapping[str, JsonValue] | OpenAIErrorEnvelopeModel | OpenAIErrorEnvelope,
    *,
    headers: Mapping[str, str] | None = None,
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
    async with get_background_session() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        await service.release_usage_reservation(reservation.reservation_id)


async def _finalize_image_reservation(
    reservation: ApiKeyUsageReservationData | None,
    *,
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
    cached_input_tokens: int | None = None,
) -> None:
    """Finalize the API-key usage reservation for a ``/v1/images/*`` call.

    The image adapter bypasses the standard stream settlement (``stream_responses``
    is invoked with ``api_key_reservation=None``) because the ``image_generation``
    tool path typically leaves ``response.usage`` empty; charging from
    ``tool_usage.image_gen`` is the only source of truth. This helper
    finalizes the reservation with the captured image tokens when present,
    otherwise releases it. Calling this exactly once per request prevents
    the double-billing scenario where both the standard settlement and
    the post-hoc image record_usage path increment limits.

    Persistence errors are caught and logged so a transient DB/session
    failure during the tail accounting cannot turn a successfully
    generated image into a user-facing 500 (non-streaming) or an
    abrupt stream termination (streaming). This mirrors the
    best-effort accounting policy used by
    ``ProxyService._settle_stream_api_key_usage``.
    """
    if reservation is None:
        return
    try:
        if not input_tokens and not output_tokens:
            await _release_reservation(reservation)
            return
        async with get_background_session() as session:
            service = ApiKeysService(ApiKeysRepository(session))
            await service.finalize_usage_reservation(
                reservation.reservation_id,
                model=model,
                input_tokens=int(input_tokens or 0),
                output_tokens=int(output_tokens or 0),
                cached_input_tokens=int(cached_input_tokens or 0),
                service_tier=None,
            )
    except Exception:
        logger.warning(
            "failed to finalize image reservation reservation_id=%s model=%s",
            reservation.reservation_id,
            model,
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


async def _normalize_public_responses_stream(
    stream: AsyncIterator[str],
    *,
    enforce_openai_sdk_contract: bool = True,
) -> AsyncIterator[str]:
    """Normalize the upstream SSE event stream for the public /v1 surface.

    Args:
        stream: the upstream SSE event blocks (post-error-conversion).
        enforce_openai_sdk_contract: when True (the default, used for /v1),
            apply OpenAI Responses SSE contract enforcement: drop Codex
            vendor events (codex.*), backfill terminal output from streamed
            item events, and synthesize a leading response.created event
            when the upstream stream's first standard event is not
            response.created. When False (used for /backend-api/codex/*,
            which feeds the Codex CLI), all events including codex.* are
            forwarded verbatim and no synthesis happens — the Codex CLI
            relies on the upstream's native event shape.
    """
    terminal_seen = False
    contract_violation_kind: str | None = None
    seen_text_delta_keys: set[tuple[str | None, int | None]] = set()
    # Collect output items from streamed ``response.output_item.added`` /
    # ``response.output_item.done`` events so the terminal
    # ``response.completed`` / ``response.incomplete`` payload can be
    # backfilled when the upstream Codex backend leaves ``response.output``
    # empty. This mirrors the existing non-streaming behavior in
    # ``_collect_responses_payload`` so OpenAI SDK consumers calling
    # ``stream.get_final_response().output`` see the same items the
    # non-streaming endpoint returns.
    output_items: dict[int, dict[str, JsonValue]] = {}
    # Track whether the first standard ``response.*`` event the public stream
    # emits is ``response.created``. The OpenAI Responses SSE contract requires
    # ``response.created`` to be the first event. The upstream Codex backend
    # sometimes drops straight to a terminal event (e.g. ``response.failed``
    # when upstream rejects the request mid-stream) without emitting
    # ``response.created`` first, which makes the OpenAI SDK's
    # ``_create_initial_response`` raise ``RuntimeError``. When that happens
    # we synthesize a ``response.created`` snapshot from the terminal event's
    # ``response`` envelope so the SDK parser can complete the stream.
    created_emitted = False
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
        _collect_output_item_event(payload, output_items)
        raw_event_type = payload.get("type")
        if (
            enforce_openai_sdk_contract
            and isinstance(raw_event_type, str)
            and raw_event_type
            in (
                "response.completed",
                "response.incomplete",
            )
        ):
            response_obj = payload.get("response")
            if is_json_mapping(response_obj):
                existing_output = response_obj.get("output")
                needs_backfill = not (isinstance(existing_output, list) and existing_output)
                if needs_backfill and output_items:
                    merged_response = _merge_collected_output_items(response_obj, output_items)
                    payload = dict(payload)
                    payload["response"] = merged_response
        normalized_payload, violation_kind = _normalize_public_stream_payload(
            payload,
            enforce_openai_sdk_contract=enforce_openai_sdk_contract,
        )
        if violation_kind is not None:
            contract_violation_kind = contract_violation_kind or violation_kind
        if normalized_payload is None:
            continue
        event_type = normalized_payload.get("type")
        if event_type == "response.output_text.delta":
            seen_text_delta_keys.add(_text_delta_stream_key(normalized_payload))
        # Ensure the public stream always starts with ``response.created``.
        # When the upstream stream jumps straight to a non-created event
        # (terminal failure, or any other ordering quirk), synthesize a
        # ``response.created`` envelope from whatever ``response`` envelope is
        # available on the current event so the OpenAI SDK parser can begin.
        # Only enforced on the OpenAI SDK contract path; the Codex CLI route
        # forwards the upstream sequence verbatim.
        if (
            enforce_openai_sdk_contract
            and not created_emitted
            and isinstance(event_type, str)
            and event_type != "response.created"
        ):
            synthetic_created = _synthetic_response_created_envelope(normalized_payload)
            if synthetic_created is not None:
                yield format_sse_event(synthetic_created)
                created_emitted = True
        if event_type == "response.created":
            created_emitted = True
        for synthetic_payload in _synthetic_text_delta_events(normalized_payload, seen_text_delta_keys):
            yield format_sse_event(synthetic_payload)
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
    *,
    enforce_openai_sdk_contract: bool = True,
) -> tuple[dict[str, JsonValue] | None, str | None]:
    event_type = payload.get("type")
    # Drop Codex-internal vendor events on the public /v1 surface only. The
    # upstream Codex backend emits non-standard events (notably
    # ``codex.rate_limits``, which is throttled per rate-limit window and so
    # leaks intermittently before ``response.created``). The OpenAI Responses
    # SSE contract does not define any ``codex.*`` event type, and the OpenAI
    # SDK's stream parser raises ``RuntimeError`` if any other event arrives
    # first. The Codex CLI routes under ``/backend-api/codex/*`` legitimately
    # consume these events and pass ``enforce_openai_sdk_contract=False`` so
    # they continue to forward unchanged.
    if enforce_openai_sdk_contract and isinstance(event_type, str) and event_type.startswith("codex."):
        return None, None
    if event_type == "error":
        parsed_error = _parse_event_error_envelope(payload)
        if _is_previous_response_not_found_public_error(parsed_error.error):
            return (
                cast(
                    dict[str, JsonValue],
                    response_failed_event(
                        "stream_incomplete",
                        "Upstream websocket closed before response.completed",
                    ),
                ),
                None,
            )
        return payload, None
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


def _synthetic_response_created_envelope(
    payload: Mapping[str, JsonValue],
) -> dict[str, JsonValue] | None:
    """Synthesize a ``response.created`` SSE payload from a non-created event.

    Used by ``_normalize_public_responses_stream`` when the upstream's first
    standard event is not ``response.created`` (for example, the Codex backend
    sometimes jumps straight to ``response.failed`` when upstream rejects the
    request mid-stream). The OpenAI Responses SSE contract requires
    ``response.created`` to be the first event the stream emits — the OpenAI
    Python SDK's ``ResponseStreamState._create_initial_response`` raises
    ``RuntimeError`` otherwise.

    Returns ``None`` when no ``response`` envelope is available on the source
    event (in that case the caller forwards the event verbatim; the SDK
    consumer will still see a parser error, but the stream contract is at
    least not silently violated by our synthesis logic).
    """
    response = payload.get("response")
    if not is_json_mapping(response):
        return None
    created_envelope: dict[str, JsonValue] = dict(response)
    created_envelope["status"] = "in_progress"
    created_envelope["output"] = []
    synthetic: dict[str, JsonValue] = {
        "type": "response.created",
        "response": created_envelope,
    }
    sequence_number = payload.get("sequence_number")
    if isinstance(sequence_number, int):
        synthetic["sequence_number"] = sequence_number
    return synthetic


def _synthetic_text_delta_events(
    payload: Mapping[str, JsonValue],
    seen_text_delta_keys: set[tuple[str | None, int | None]],
) -> list[dict[str, JsonValue]]:
    event_type = payload.get("type")
    if event_type == "response.output_item.done":
        output_index = payload.get("output_index")
        item = payload.get("item")
        if isinstance(output_index, int) and is_json_mapping(item):
            synthetic = _synthetic_text_delta_for_output_item(output_index, item, seen_text_delta_keys)
            return [synthetic] if synthetic is not None else []
    if event_type not in {"response.completed", "response.incomplete"}:
        return []
    response = payload.get("response")
    if not is_json_mapping(response):
        return []
    output = response.get("output")
    if not isinstance(output, list):
        return []

    synthetic_events: list[dict[str, JsonValue]] = []
    for output_index, item in enumerate(output):
        if not is_json_mapping(item):
            continue
        synthetic = _synthetic_text_delta_for_output_item(output_index, item, seen_text_delta_keys)
        if synthetic is not None:
            synthetic_events.append(synthetic)
    return synthetic_events


def _synthetic_text_delta_for_output_item(
    output_index: int,
    item: Mapping[str, JsonValue],
    seen_text_delta_keys: set[tuple[str | None, int | None]],
) -> dict[str, JsonValue] | None:
    normalized_item = _normalize_public_output_item(item)
    if normalized_item is None:
        return None
    text = _extract_public_output_item_text(normalized_item)
    if text is None:
        return None
    key = _output_item_stream_key(output_index, normalized_item)
    if _seen_text_delta_for_output_item(key, seen_text_delta_keys):
        return None
    seen_text_delta_keys.add(key)

    event: dict[str, JsonValue] = {
        "type": "response.output_text.delta",
        "output_index": output_index,
        "content_index": 0,
        "delta": text,
    }
    item_id = normalized_item.get("id")
    if isinstance(item_id, str) and item_id:
        event["item_id"] = item_id
    return event


def _text_delta_stream_key(payload: Mapping[str, JsonValue]) -> tuple[str | None, int | None]:
    item_id = payload.get("item_id")
    output_index = payload.get("output_index")
    return (
        item_id if isinstance(item_id, str) and item_id else None,
        output_index if isinstance(output_index, int) else None,
    )


def _output_item_stream_key(
    output_index: int,
    item: Mapping[str, JsonValue],
) -> tuple[str | None, int | None]:
    item_id = item.get("id")
    return (item_id if isinstance(item_id, str) and item_id else None, output_index)


def _seen_text_delta_for_output_item(
    key: tuple[str | None, int | None],
    seen_text_delta_keys: set[tuple[str | None, int | None]],
) -> bool:
    item_id, output_index = key
    return any(
        candidate in seen_text_delta_keys
        for candidate in (
            key,
            (item_id, None) if item_id is not None else None,
            (None, output_index) if output_index is not None else None,
            (None, None),
        )
        if candidate is not None
    )


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
    if payload.get("type") == "error":
        return _parse_event_error_envelope(cast(dict[str, JsonValue], payload))
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


def _is_previous_response_not_found_public_error(error_value: OpenAIError | None) -> bool:
    if error_value is None:
        return False
    if error_value.code == "previous_response_not_found":
        return True
    message = error_value.message or ""
    return (
        error_value.code == "invalid_request_error"
        and error_value.param == "previous_response_id"
        and "previous response" in message.lower()
        and "not found" in message.lower()
    )


def _mask_previous_response_not_found_error(
    envelope: OpenAIErrorEnvelopeModel,
    *,
    default_status: int | None = None,
) -> tuple[int, OpenAIErrorEnvelopeModel]:
    if not _is_previous_response_not_found_public_error(envelope.error):
        return default_status if default_status is not None else _status_for_error(envelope.error), envelope
    return (
        502,
        OpenAIErrorEnvelopeModel(
            error=OpenAIError(
                message="Upstream websocket closed before response.completed",
                type="server_error",
                code="stream_incomplete",
            )
        ),
    )


def _status_for_error(error_value: OpenAIError | None) -> int:
    if error_value and error_value.code == "previous_response_not_found":
        return 502
    if error_value and error_value.code in _UNAVAILABLE_SELECTION_ERROR_CODES:
        return 503
    if error_value and error_value.code in {"rate_limit_exceeded", "usage_limit_reached", "insufficient_quota"}:
        return 429
    if error_value and error_value.code in {"invalid_api_key", "invalid_authentication"}:
        return 401
    if error_value and error_value.code == "invalid_request_error":
        return 400
    if error_value and error_value.type == "authentication_error":
        return 401
    if error_value and error_value.type == "invalid_request_error":
        return 400
    if error_value and error_value.type in {"rate_limit_error", "usage_limit_reached", "insufficient_quota"}:
        return 429
    return 502


def _status_for_image_error_envelope(envelope: object) -> int:
    """Map an OpenAI-shape error envelope dict to its canonical HTTP status
    for the ``/v1/images/*`` non-streaming response path.

    Returns 502 when no specific mapping matches (e.g. server_error or an
    unrecognised type), so transport-level failures still surface as
    upstream errors. Code matches take precedence over type matches.
    """
    if not isinstance(envelope, Mapping):
        return 502
    error = cast(Mapping[str, object], envelope).get("error")
    if not isinstance(error, Mapping):
        return 502
    error_map = cast(Mapping[str, object], error)
    code = error_map.get("code")
    if isinstance(code, str):
        if code in _IMAGE_ERROR_CODE_STATUS:
            return _IMAGE_ERROR_CODE_STATUS[code]
        if code in _UNAVAILABLE_SELECTION_ERROR_CODES:
            return 503
    error_type = error_map.get("type")
    if isinstance(error_type, str) and error_type in _IMAGE_ERROR_TYPE_STATUS:
        return _IMAGE_ERROR_TYPE_STATUS[error_type]
    return 502
