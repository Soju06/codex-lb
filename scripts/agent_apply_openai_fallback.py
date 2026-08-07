from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text()


def write(path: str, text: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one anchor in {path}, found {count}: {old[:120]!r}")
    write(path, text.replace(old, new, 1))


# --- Database model -------------------------------------------------------
replace_once(
    "app/db/models.py",
    """    supports_responses: Mapped[bool] = mapped_column(\n        Boolean,\n        default=False,\n        server_default=false(),\n        nullable=False,\n    )\n    supports_audio_transcriptions: Mapped[bool] = mapped_column(\n""",
    """    supports_responses: Mapped[bool] = mapped_column(\n        Boolean,\n        default=False,\n        server_default=false(),\n        nullable=False,\n    )\n    is_subscription_fallback: Mapped[bool] = mapped_column(\n        Boolean,\n        default=False,\n        server_default=false(),\n        nullable=False,\n    )\n    fallback_model: Mapped[str | None] = mapped_column(String(255), nullable=True)\n    supports_audio_transcriptions: Mapped[bool] = mapped_column(\n""",
)

# --- Alembic migration ----------------------------------------------------
head = os.environ.get("ALEMBIC_HEAD", "").strip()
if not head:
    raise RuntimeError("ALEMBIC_HEAD must be provided by the workflow")
migration_path = "app/db/alembic/versions/20260807_163500_add_model_source_subscription_fallback.py"
write(
    migration_path,
    f'''"""add model source subscription fallback\n\nRevision ID: 20260807_163500_add_model_source_subscription_fallback\nRevises: {head}\n"""\n\nfrom alembic import op\nimport sqlalchemy as sa\n\nrevision = "20260807_163500_add_model_source_subscription_fallback"\ndown_revision = "{head}"\nbranch_labels = None\ndepends_on = None\n\n\ndef upgrade() -> None:\n    op.add_column(\n        "model_sources",\n        sa.Column("is_subscription_fallback", sa.Boolean(), nullable=False, server_default=sa.false()),\n    )\n    op.add_column("model_sources", sa.Column("fallback_model", sa.String(length=255), nullable=True))\n\n\ndef downgrade() -> None:\n    op.drop_column("model_sources", "fallback_model")\n    op.drop_column("model_sources", "is_subscription_fallback")\n''',
)

# --- Model Source schemas -------------------------------------------------
replace_once(
    "app/modules/model_sources/schemas.py",
    """    supports_chat_completions: bool = True\n    supports_responses: bool = False\n    supports_audio_transcriptions: bool = False\n""",
    """    supports_chat_completions: bool = True\n    supports_responses: bool = False\n    is_subscription_fallback: bool = False\n    fallback_model: str | None = Field(default=None, max_length=255)\n    supports_audio_transcriptions: bool = False\n""",
)
replace_once(
    "app/modules/model_sources/schemas.py",
    """    supports_chat_completions: bool | None = None\n    supports_responses: bool | None = None\n    supports_audio_transcriptions: bool | None = None\n""",
    """    supports_chat_completions: bool | None = None\n    supports_responses: bool | None = None\n    is_subscription_fallback: bool | None = None\n    fallback_model: str | None = Field(default=None, max_length=255)\n    supports_audio_transcriptions: bool | None = None\n""",
)
replace_once(
    "app/modules/model_sources/schemas.py",
    """    supports_chat_completions: bool\n    supports_responses: bool\n    supports_audio_transcriptions: bool\n""",
    """    supports_chat_completions: bool\n    supports_responses: bool\n    is_subscription_fallback: bool\n    fallback_model: str | None\n    supports_audio_transcriptions: bool\n""",
)

# --- Model Source repository ----------------------------------------------
replace_once(
    "app/modules/model_sources/repository.py",
    "from sqlalchemy import delete, select\n",
    "from sqlalchemy import delete, select, update\n",
)
replace_once(
    "app/modules/model_sources/repository.py",
    """    async def find_audio_transcriptions_source_for_model(\n""",
    """    async def get_subscription_fallback(\n        self,\n        *,\n        allowed_source_ids: set[str] | None = None,\n    ) -> ModelSource | None:\n        if allowed_source_ids is not None and not allowed_source_ids:\n            return None\n        stmt = (\n            select(ModelSource)\n            .options(selectinload(ModelSource.models))\n            .where(ModelSource.kind == \"openai_compatible\")\n            .where(ModelSource.is_enabled.is_(True))\n            .where(ModelSource.supports_responses.is_(True))\n            .where(ModelSource.is_subscription_fallback.is_(True))\n            .order_by(ModelSource.name, ModelSource.id)\n            .limit(1)\n        )\n        if allowed_source_ids is not None:\n            stmt = stmt.where(ModelSource.id.in_(allowed_source_ids))\n        result = await self._session.execute(stmt)\n        return result.scalar_one_or_none()\n\n    async def clear_subscription_fallback(\n        self,\n        *,\n        except_source_id: str | None = None,\n        commit: bool = False,\n    ) -> None:\n        stmt = update(ModelSource).where(ModelSource.is_subscription_fallback.is_(True))\n        if except_source_id is not None:\n            stmt = stmt.where(ModelSource.id != except_source_id)\n        await self._session.execute(stmt.values(is_subscription_fallback=False))\n        if commit:\n            await self._session.commit()\n\n    async def find_audio_transcriptions_source_for_model(\n""",
)

# --- Model Source service -------------------------------------------------
replace_once(
    "app/modules/model_sources/service.py",
    """        model_rows = _model_inputs_to_rows(payload.models)\n        row = ModelSource(\n""",
    """        model_rows = _model_inputs_to_rows(payload.models)\n        fallback_model = _normalize_optional_model_slug(payload.fallback_model)\n        _validate_fallback_configuration(\n            is_subscription_fallback=payload.is_subscription_fallback,\n            is_enabled=True,\n            supports_responses=payload.supports_responses,\n            fallback_model=fallback_model,\n            models=model_rows,\n        )\n        row = ModelSource(\n""",
)
replace_once(
    "app/modules/model_sources/service.py",
    """            supports_chat_completions=payload.supports_chat_completions,\n            supports_responses=payload.supports_responses,\n            supports_audio_transcriptions=payload.supports_audio_transcriptions,\n""",
    """            supports_chat_completions=payload.supports_chat_completions,\n            supports_responses=payload.supports_responses,\n            is_subscription_fallback=payload.is_subscription_fallback,\n            fallback_model=fallback_model,\n            supports_audio_transcriptions=payload.supports_audio_transcriptions,\n""",
)
replace_once(
    "app/modules/model_sources/service.py",
    """        try:\n            created = await self._repository.create(row, commit=True)\n""",
    """        try:\n            if row.is_subscription_fallback:\n                await self._repository.clear_subscription_fallback(except_source_id=row.id, commit=False)\n            created = await self._repository.create(row, commit=True)\n""",
)
replace_once(
    "app/modules/model_sources/service.py",
    """        if \"supports_responses\" in fields and payload.supports_responses is not None:\n            row.supports_responses = payload.supports_responses\n        if \"supports_audio_transcriptions\" in fields and payload.supports_audio_transcriptions is not None:\n""",
    """        if \"supports_responses\" in fields and payload.supports_responses is not None:\n            row.supports_responses = payload.supports_responses\n        if \"is_subscription_fallback\" in fields and payload.is_subscription_fallback is not None:\n            row.is_subscription_fallback = payload.is_subscription_fallback\n        if \"fallback_model\" in fields:\n            row.fallback_model = _normalize_optional_model_slug(payload.fallback_model)\n        if \"supports_audio_transcriptions\" in fields and payload.supports_audio_transcriptions is not None:\n""",
)
replace_once(
    "app/modules/model_sources/service.py",
    """        models_replaced = False\n        try:\n            if \"models\" in fields and payload.models is not None:\n                await self._repository.replace_models(row, _model_inputs_to_rows(payload.models), commit=False)\n                models_replaced = True\n            await self._repository.commit()\n""",
    """        replacement_models = _model_inputs_to_rows(payload.models) if \"models\" in fields and payload.models is not None else None\n        _validate_fallback_configuration(\n            is_subscription_fallback=row.is_subscription_fallback,\n            is_enabled=row.is_enabled,\n            supports_responses=row.supports_responses,\n            fallback_model=row.fallback_model,\n            models=replacement_models if replacement_models is not None else row.models,\n        )\n\n        models_replaced = False\n        try:\n            if row.is_subscription_fallback:\n                await self._repository.clear_subscription_fallback(except_source_id=row.id, commit=False)\n            if replacement_models is not None:\n                await self._repository.replace_models(row, replacement_models, commit=False)\n                models_replaced = True\n            await self._repository.commit()\n""",
)
replace_once(
    "app/modules/model_sources/service.py",
    """def _normalize_model_slug(value: str) -> str:\n    model = value.strip()\n    if not model:\n        raise ModelSourceValidationError(\"Model source model name is required\")\n    return model\n\n\ndef _validate_raw_metadata_json""",
    """def _normalize_model_slug(value: str) -> str:\n    model = value.strip()\n    if not model:\n        raise ModelSourceValidationError(\"Model source model name is required\")\n    return model\n\n\ndef _normalize_optional_model_slug(value: str | None) -> str | None:\n    if value is None:\n        return None\n    model = value.strip()\n    return model or None\n\n\ndef _validate_fallback_configuration(\n    *,\n    is_subscription_fallback: bool,\n    is_enabled: bool,\n    supports_responses: bool,\n    fallback_model: str | None,\n    models: list[ModelSourceModel],\n) -> None:\n    if not is_subscription_fallback:\n        return\n    if not is_enabled:\n        raise ModelSourceValidationError(\"Subscription fallback model source must be enabled\")\n    if not supports_responses:\n        raise ModelSourceValidationError(\"Subscription fallback model source must support Responses API\")\n    if fallback_model is None:\n        return\n    if not any(model.model == fallback_model and model.is_enabled for model in models):\n        raise ModelSourceValidationError(\n            f\"Fallback model '{fallback_model}' must be an enabled model on the source\"\n        )\n\n\ndef _validate_raw_metadata_json""",
)
replace_once(
    "app/modules/model_sources/service.py",
    """        supports_chat_completions=row.supports_chat_completions,\n        supports_responses=row.supports_responses,\n        supports_audio_transcriptions=row.supports_audio_transcriptions,\n""",
    """        supports_chat_completions=row.supports_chat_completions,\n        supports_responses=row.supports_responses,\n        is_subscription_fallback=row.is_subscription_fallback,\n        fallback_model=row.fallback_model,\n        supports_audio_transcriptions=row.supports_audio_transcriptions,\n""",
)

# --- Fallback transfer context -------------------------------------------
write(
    "app/modules/proxy/subscription_fallback.py",
    '''from __future__ import annotations\n\nfrom contextlib import contextmanager\nfrom contextvars import ContextVar\nfrom collections.abc import Iterator\n\n_usage_limit_reservation_transfer: ContextVar[bool] = ContextVar(\n    "codex_lb_usage_limit_reservation_transfer", default=False\n)\n\n\n@contextmanager\ndef usage_limit_reservation_transfer(enabled: bool) -> Iterator[None]:\n    token = _usage_limit_reservation_transfer.set(enabled)\n    try:\n        yield\n    finally:\n        _usage_limit_reservation_transfer.reset(token)\n\n\ndef usage_limit_reservation_transfer_enabled() -> bool:\n    return _usage_limit_reservation_transfer.get()\n''',
)

# --- Streaming replay safety + reservation ownership transfer -------------
replace_once(
    "app/modules/proxy/_service/streaming/retry.py",
    "from app.modules.proxy.selection_errors import USAGE_LIMIT_REACHED, selection_failure_response\n",
    "from app.modules.proxy.replay_safety import responses_payload_is_account_neutral_fresh_replay\nfrom app.modules.proxy.selection_errors import USAGE_LIMIT_REACHED, selection_failure_response\nfrom app.modules.proxy.subscription_fallback import usage_limit_reservation_transfer_enabled\n",
)
replace_once(
    "app/modules/proxy/_service/streaming/retry.py",
    """class _StreamingRetryMixin:\n    async def _stream_with_retry(\n""",
    """class _StreamingRetryMixin:\n    def external_fallback_replay_payload(\n        self,\n        payload: ResponsesRequest,\n        headers: Mapping[str, str],\n        *,\n        api_key: ApiKeyData | None,\n    ) -> ResponsesRequest | None:\n        proxy = cast(_StreamingServiceProtocol, self)\n        candidate = payload.model_copy(deep=True)\n        if payload.previous_response_id is not None:\n            candidate = _verified_cross_transport_fresh_replay(\n                proxy, payload=payload, headers=headers, api_key=api_key\n            )\n            if candidate is None:\n                return None\n        try:\n            wire_payload = candidate.to_payload()\n        except Exception:\n            return None\n        if not responses_payload_is_account_neutral_fresh_replay(wire_payload):\n            return None\n        return candidate\n\n    async def _stream_with_retry(\n""",
)
replace_once(
    "app/modules/proxy/_service/streaming/retry.py",
    """                    if selection.error_code == USAGE_LIMIT_REACHED:\n                        await _drain_pending_post_refresh_penalty_on_terminal(settlement)\n                        no_accounts_msg = selection.error_message or \"Usage limit reached\"\n                        status_code, error_payload = selection_failure_response(selection)\n                        await proxy._write_request_log(\n""",
    """                    if selection.error_code == USAGE_LIMIT_REACHED:\n                        await _drain_pending_post_refresh_penalty_on_terminal(settlement)\n                        no_accounts_msg = selection.error_message or \"Usage limit reached\"\n                        status_code, error_payload = selection_failure_response(selection)\n                        if propagate_http_errors and usage_limit_reservation_transfer_enabled():\n                            # The route already proved that a replay-safe Model Source fallback is\n                            # available. Transfer the existing API-key reservation to that path\n                            # instead of releasing it in this generator's finally block.\n                            settlement.usage_settlement_transferred = True\n                            raise ProxyResponseError(status_code, error_payload)\n                        await proxy._write_request_log(\n""",
)

# --- Proxy API fallback preparation/dispatch ------------------------------
replace_once(
    "app/modules/proxy/api.py",
    "from app.modules.proxy.selection_errors import USAGE_LIMIT_REACHED\n",
    "from app.modules.proxy.selection_errors import USAGE_LIMIT_REACHED\nfrom app.modules.proxy.subscription_fallback import usage_limit_reservation_transfer\n",
)
replace_once(
    "app/modules/proxy/api.py",
    """async def _select_audio_transcriptions_model_source(model: str, api_key: ApiKeyData | None) -> ModelSource | None:\n""",
    """@dataclass(frozen=True)\nclass _PreparedSubscriptionFallback:\n    source: ModelSource\n    payload: ResponsesRequest\n\n\ndef _source_has_fallback_model(source: ModelSource, model: str, *, require_streaming: bool) -> bool:\n    return any(\n        item.model == model\n        and item.is_enabled\n        and (not require_streaming or item.supports_streaming)\n        for item in source.models\n    )\n\n\nasync def _prepare_subscription_fallback(\n    context: ProxyContext,\n    payload: ResponsesRequest,\n    headers: Mapping[str, str],\n    api_key: ApiKeyData | None,\n    *,\n    require_streaming: bool,\n) -> _PreparedSubscriptionFallback | None:\n    try:\n        replay_payload = context.service.external_fallback_replay_payload(\n            payload, headers, api_key=api_key\n        )\n    except AttributeError:\n        return None\n    if replay_payload is None:\n        return None\n    allowed_source_ids = _allowed_source_ids_for_api_key(api_key)\n    async with get_background_session() as session:\n        source = await ModelSourcesRepository(session).get_subscription_fallback(\n            allowed_source_ids=allowed_source_ids\n        )\n        if source is None:\n            return None\n        effective_model = source.fallback_model or replay_payload.model\n        if not _source_has_fallback_model(source, effective_model, require_streaming=require_streaming):\n            return None\n        prepared_payload = replay_payload.model_copy(update={\"model\": effective_model}, deep=True)\n        detach_session_objects(session)\n    return _PreparedSubscriptionFallback(source=source, payload=prepared_payload)\n\n\ndef _is_usage_limit_proxy_error(exc: ProxyResponseError) -> bool:\n    envelope = _parse_error_envelope(exc.payload)\n    error = envelope.error\n    return error is not None and (error.code == USAGE_LIMIT_REACHED or error.type == USAGE_LIMIT_REACHED)\n\n\nasync def _select_audio_transcriptions_model_source(model: str, api_key: ApiKeyData | None) -> ModelSource | None:\n""",
)

# Source response helper accepts transferred reservation.
replace_once(
    "app/modules/proxy/api.py",
    """async def _source_responses_response(\n    request: Request,\n    payload: ResponsesRequest,\n    *,\n    source: ModelSource,\n    api_key: ApiKeyData | None,\n    rate_limit_headers: Mapping[str, str],\n) -> Response:\n    reservation = await _enforce_request_limits(\n        api_key,\n        request_model=payload.model,\n        request_service_tier=payload.service_tier,\n        request_usage_budget=estimate_api_key_request_usage(payload),\n    )\n""",
    """async def _source_responses_response(\n    request: Request,\n    payload: ResponsesRequest,\n    *,\n    source: ModelSource,\n    api_key: ApiKeyData | None,\n    rate_limit_headers: Mapping[str, str],\n    reservation_override: ApiKeyUsageReservationData | None = None,\n    reuse_reservation: bool = False,\n) -> Response:\n    reservation = (\n        reservation_override\n        if reuse_reservation\n        else await _enforce_request_limits(\n            api_key,\n            request_model=payload.model,\n            request_service_tier=payload.service_tier,\n            request_usage_budget=estimate_api_key_request_usage(payload),\n        )\n    )\n""",
)

# Streaming Responses: prepare fallback before dispatch and transfer on strict usage-limit startup failure.
replace_once(
    "app/modules/proxy/api.py",
    """    capacity_wait_event = asyncio.Event()\n    capacity_ready_event = _CapacityStartupReadyEvent()\n    payload.stream = True\n""",
    """    capacity_wait_event = asyncio.Event()\n    capacity_ready_event = _CapacityStartupReadyEvent()\n    fallback = await _prepare_subscription_fallback(\n        context, payload, effective_headers, api_key, require_streaming=True\n    )\n    payload.stream = True\n""",
)
replace_once(
    "app/modules/proxy/api.py",
    """    try:\n        stream, startup_error = await _probe_stream_startup_error(\n            stream,\n            convert_event_errors=bridge_active and enforce_openai_sdk_contract,\n            timeout_seconds=(\n                _HTTP_BRIDGE_STARTUP_ERROR_PROBE_SECONDS if prefer_http_bridge else _STREAM_STARTUP_ERROR_PROBE_SECONDS\n            ),\n            capacity_wait_event=capacity_wait_event,\n            capacity_ready_event=capacity_ready_event,\n        )\n    finally:\n        _reset_propagated_capacity_startup_ready(capacity_ready_token)\n        _reset_propagated_capacity_startup_wait(capacity_wait_token)\n    if startup_error is not None:\n        if owns_reservation:\n            await _release_reservation(reservation)\n        return _stream_startup_error_response(\n""",
    """    try:\n        with usage_limit_reservation_transfer(fallback is not None):\n            stream, startup_error = await _probe_stream_startup_error(\n                stream,\n                convert_event_errors=bridge_active and enforce_openai_sdk_contract,\n                timeout_seconds=(\n                    _HTTP_BRIDGE_STARTUP_ERROR_PROBE_SECONDS\n                    if prefer_http_bridge\n                    else _STREAM_STARTUP_ERROR_PROBE_SECONDS\n                ),\n                capacity_wait_event=capacity_wait_event,\n                capacity_ready_event=capacity_ready_event,\n            )\n    finally:\n        _reset_propagated_capacity_startup_ready(capacity_ready_token)\n        _reset_propagated_capacity_startup_wait(capacity_wait_token)\n    if startup_error is not None:\n        if fallback is not None and _is_usage_limit_proxy_error(startup_error):\n            logger.info(\n                \"subscription_fallback_dispatch source_id=%s requested_model=%s fallback_model=%s\",\n                fallback.source.id,\n                payload.model,\n                fallback.payload.model,\n            )\n            fallback.payload.stream = True\n            return await _source_responses_response(\n                request,\n                fallback.payload,\n                source=fallback.source,\n                api_key=api_key,\n                rate_limit_headers={**turn_state_headers, **rate_limit_headers},\n                reservation_override=reservation,\n                reuse_reservation=True,\n            )\n        if owns_reservation:\n            await _release_reservation(reservation)\n        return _stream_startup_error_response(\n""",
)

# Non-streaming Responses collection: same strict trigger, same reservation.
replace_once(
    "app/modules/proxy/api.py",
    """    payload.stream = True\n    if prefer_http_bridge:\n        stream = context.service.stream_http_responses(\n""",
    """    fallback = await _prepare_subscription_fallback(\n        context, payload, request.headers, api_key, require_streaming=False\n    )\n    payload.stream = True\n    if prefer_http_bridge:\n        stream = context.service.stream_http_responses(\n""",
)
replace_once(
    "app/modules/proxy/api.py",
    """    try:\n        response_payload = await _collect_responses_payload(\n            stream,\n            captured_turn_state_headers=captured_turn_state_headers,\n        )\n    except ProxyResponseError as exc:\n        await _release_reservation(reservation)\n        error = _parse_error_envelope(exc.payload)\n""",
    """    try:\n        with usage_limit_reservation_transfer(fallback is not None):\n            response_payload = await _collect_responses_payload(\n                stream,\n                captured_turn_state_headers=captured_turn_state_headers,\n            )\n    except ProxyResponseError as exc:\n        if fallback is not None and _is_usage_limit_proxy_error(exc):\n            logger.info(\n                \"subscription_fallback_dispatch source_id=%s requested_model=%s fallback_model=%s\",\n                fallback.source.id,\n                payload.model,\n                fallback.payload.model,\n            )\n            fallback.payload.stream = False\n            return await _source_responses_response(\n                request,\n                fallback.payload,\n                source=fallback.source,\n                api_key=api_key,\n                rate_limit_headers={**turn_state_headers, **rate_limit_headers},\n                reservation_override=reservation,\n                reuse_reservation=True,\n            )\n        await _release_reservation(reservation)\n        error = _parse_error_envelope(exc.payload)\n""",
)

# --- Frontend schemas -----------------------------------------------------
replace_once(
    "frontend/src/features/model-sources/schemas.ts",
    """  supportsChatCompletions: z.boolean(),\n  supportsResponses: z.boolean(),\n  supportsAudioTranscriptions: z.boolean().default(false),\n""",
    """  supportsChatCompletions: z.boolean(),\n  supportsResponses: z.boolean(),\n  isSubscriptionFallback: z.boolean().default(false),\n  fallbackModel: z.string().nullable().default(null),\n  supportsAudioTranscriptions: z.boolean().default(false),\n""",
)
replace_once(
    "frontend/src/features/model-sources/schemas.ts",
    """  supportsChatCompletions: z.boolean().optional(),\n  supportsResponses: z.boolean().optional(),\n  supportsAudioTranscriptions: z.boolean().optional(),\n""",
    """  supportsChatCompletions: z.boolean().optional(),\n  supportsResponses: z.boolean().optional(),\n  isSubscriptionFallback: z.boolean().optional(),\n  fallbackModel: z.string().max(255).nullable().optional(),\n  supportsAudioTranscriptions: z.boolean().optional(),\n""",
)
# Same anchor occurs in update; replace second occurrence now.
replace_once(
    "frontend/src/features/model-sources/schemas.ts",
    """  supportsChatCompletions: z.boolean().optional(),\n  supportsResponses: z.boolean().optional(),\n  supportsAudioTranscriptions: z.boolean().optional(),\n""",
    """  supportsChatCompletions: z.boolean().optional(),\n  supportsResponses: z.boolean().optional(),\n  isSubscriptionFallback: z.boolean().optional(),\n  fallbackModel: z.string().max(255).nullable().optional(),\n  supportsAudioTranscriptions: z.boolean().optional(),\n""",
)

# Draft + form fields.
replace_once(
    "frontend/src/features/model-sources/components/model-source-form.ts",
    """  supportsResponses: boolean;\n  supportsAudioTranscriptions: boolean;\n""",
    """  supportsResponses: boolean;\n  isSubscriptionFallback: boolean;\n  fallbackModel: string;\n  supportsAudioTranscriptions: boolean;\n""",
)
replace_once(
    "frontend/src/features/model-sources/components/model-source-form.ts",
    """  supportsResponses: false,\n  supportsAudioTranscriptions: false,\n""",
    """  supportsResponses: false,\n  isSubscriptionFallback: false,\n  fallbackModel: \"\",\n  supportsAudioTranscriptions: false,\n""",
)
replace_once(
    "frontend/src/features/model-sources/components/model-source-form.ts",
    """    supportsResponses: source.supportsResponses,\n    supportsAudioTranscriptions: source.supportsAudioTranscriptions,\n""",
    """    supportsResponses: source.supportsResponses,\n    isSubscriptionFallback: source.isSubscriptionFallback,\n    fallbackModel: source.fallbackModel ?? \"\",\n    supportsAudioTranscriptions: source.supportsAudioTranscriptions,\n""",
)
replace_once(
    "frontend/src/features/model-sources/components/model-source-form-fields.tsx",
    """      <div className=\"grid gap-2 sm:grid-cols-2\">\n\t        {CAPABILITY_TOGGLES.map(([key, labelKey]) => (\n""",
    """      <div className=\"space-y-2 rounded-md border p-3\">\n        <label className=\"flex items-center gap-2 text-sm font-medium\">\n          <Checkbox\n            checked={draft.isSubscriptionFallback}\n            onCheckedChange={(checked) =>\n              updateDraft({\n                isSubscriptionFallback: checked === true,\n                supportsResponses: checked === true ? true : draft.supportsResponses,\n              })\n            }\n          />\n          Use as subscription fallback\n        </label>\n        <p className=\"text-xs text-muted-foreground\">\n          Used only after all eligible ChatGPT accounts report upstream usage exhaustion.\n        </p>\n        {draft.isSubscriptionFallback ? (\n          <div className=\"space-y-1\">\n            <label className=\"text-xs text-muted-foreground\">Fallback model override (optional)</label>\n            <Input\n              value={draft.fallbackModel}\n              onChange={(event) => updateDraft({ fallbackModel: event.target.value })}\n              placeholder=\"Leave blank to preserve the requested model\"\n              autoComplete=\"off\"\n            />\n          </div>\n        ) : null}\n      </div>\n\n      <div className=\"grid gap-2 sm:grid-cols-2\">\n\t        {CAPABILITY_TOGGLES.map(([key, labelKey]) => (\n""",
)
replace_once(
    "frontend/src/features/model-sources/components/model-source-create-dialog.tsx",
    """      supportsResponses: draft.supportsResponses,\n      supportsAudioTranscriptions: draft.supportsAudioTranscriptions,\n""",
    """      supportsResponses: draft.supportsResponses,\n      isSubscriptionFallback: draft.isSubscriptionFallback,\n      fallbackModel: draft.fallbackModel.trim() || null,\n      supportsAudioTranscriptions: draft.supportsAudioTranscriptions,\n""",
)
replace_once(
    "frontend/src/features/model-sources/components/model-source-edit-dialog.tsx",
    """      supportsResponses: draft.supportsResponses,\n      supportsAudioTranscriptions: draft.supportsAudioTranscriptions,\n""",
    """      supportsResponses: draft.supportsResponses,\n      isSubscriptionFallback: draft.isSubscriptionFallback,\n      fallbackModel: draft.fallbackModel.trim() || null,\n      supportsAudioTranscriptions: draft.supportsAudioTranscriptions,\n""",
)

# --- Tests ----------------------------------------------------------------
write(
    "tests/unit/test_subscription_fallback.py",
    '''from __future__ import annotations\n\nimport pytest\n\nfrom app.db.models import ModelSourceModel\nfrom app.modules.model_sources.service import ModelSourceValidationError, _validate_fallback_configuration\nfrom app.modules.proxy.subscription_fallback import (\n    usage_limit_reservation_transfer,\n    usage_limit_reservation_transfer_enabled,\n)\n\n\ndef _model(name: str, *, enabled: bool = True) -> ModelSourceModel:\n    return ModelSourceModel(model=name, is_enabled=enabled)\n\n\ndef test_usage_limit_reservation_transfer_is_scoped() -> None:\n    assert usage_limit_reservation_transfer_enabled() is False\n    with usage_limit_reservation_transfer(True):\n        assert usage_limit_reservation_transfer_enabled() is True\n    assert usage_limit_reservation_transfer_enabled() is False\n\n\ndef test_fallback_requires_responses_capability() -> None:\n    with pytest.raises(ModelSourceValidationError, match="Responses API"):\n        _validate_fallback_configuration(\n            is_subscription_fallback=True,\n            is_enabled=True,\n            supports_responses=False,\n            fallback_model=None,\n            models=[_model("gpt-5")],\n        )\n\n\ndef test_fallback_model_override_must_exist_and_be_enabled() -> None:\n    with pytest.raises(ModelSourceValidationError, match="must be an enabled model"):\n        _validate_fallback_configuration(\n            is_subscription_fallback=True,\n            is_enabled=True,\n            supports_responses=True,\n            fallback_model="missing",\n            models=[_model("present")],\n        )\n\n    _validate_fallback_configuration(\n        is_subscription_fallback=True,\n        is_enabled=True,\n        supports_responses=True,\n        fallback_model="present",\n        models=[_model("present")],\n    )\n''',
)

# Mark core implementation tasks complete; full CI/PR tasks remain for the workflow/operator.
tasks_path = "openspec/changes/openai-api-fallback/tasks.md"
tasks = read(tasks_path)
for label in [
    "Add persisted Model Source fallback designation and optional model override.",
    "Expose fallback fields through the Model Source dashboard API without exposing stored credentials.",
    "Add dashboard controls to configure the fallback Model Source.",
    "Validate that the fallback source is enabled, Responses-capable, and has an eligible target model.",
    "Select the designated fallback only after aggregate `usage_limit_reached`.",
    "Preserve API-key Model Source assignment restrictions.",
    "Reuse the Model Source Responses forwarding path for fallback dispatch.",
    "Keep fallback provider errors terminal; never loop them into subscription selection.",
    "Reject fallback for file-pinned/account-owned requests.",
    "Reject unverified `previous_response_id` requests.",
    "Permit verified self-contained fresh replay and remove the ChatGPT-owned response anchor.",
    "Transfer API-key reservation ownership exactly once from subscription routing to source routing.",
    "Avoid writing a terminal `usage_limit_reached` request log when fallback dispatch takes ownership.",
]:
    tasks = tasks.replace(f"- [ ] {label}", f"- [x] {label}")
write(tasks_path, tasks)

# Temporary inspection/build artifacts must never land in the feature commit.
for temporary in [
    ROOT / "agent-openai-fallback-inspect.txt",
    ROOT / ".github/workflows/agent-openai-fallback-inspect.yml",
    Path(__file__).resolve(),
]:
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass
