from __future__ import annotations

import json
import uuid
from urllib.parse import urlparse

from app.core.crypto import TokenEncryptor
from app.db.models import ModelSource, ModelSourceModel
from app.modules.model_sources.repository import ModelSourcesRepository
from app.modules.model_sources.schemas import (
    ModelSourceCreateRequest,
    ModelSourceModelInput,
    ModelSourceModelResponse,
    ModelSourceResponse,
    ModelSourceUpdateRequest,
)

MODEL_SOURCE_KIND_OPENAI_COMPATIBLE = "openai_compatible"
MODEL_SOURCE_HEALTH_UNKNOWN = "unknown"


class ModelSourceNotFoundError(ValueError):
    pass


class ModelSourceValidationError(ValueError):
    pass


class ModelSourcesService:
    def __init__(
        self,
        repository: ModelSourcesRepository,
        *,
        encryptor: TokenEncryptor | None = None,
    ) -> None:
        self._repository = repository
        self._encryptor = encryptor or TokenEncryptor()

    async def list_sources(self) -> list[ModelSourceResponse]:
        rows = await self._repository.list_sources()
        return [_to_response(row) for row in rows]

    async def list_enabled_sources(self) -> list[ModelSource]:
        return await self._repository.list_enabled_sources()

    async def create_source(self, payload: ModelSourceCreateRequest) -> ModelSourceResponse:
        model_rows = _model_inputs_to_rows(payload.models)
        row = ModelSource(
            id=f"src_{uuid.uuid4().hex}",
            name=_normalize_name(payload.name),
            kind=MODEL_SOURCE_KIND_OPENAI_COMPATIBLE,
            base_url=_normalize_base_url(payload.base_url),
            api_key_encrypted=_encrypt_optional(self._encryptor, payload.api_key),
            is_enabled=True,
            health_status=MODEL_SOURCE_HEALTH_UNKNOWN,
            supports_chat_completions=payload.supports_chat_completions,
            supports_responses=payload.supports_responses,
            supports_audio_transcriptions=payload.supports_audio_transcriptions,
            timeout_seconds=payload.timeout_seconds,
            max_concurrency=payload.max_concurrency,
            models=model_rows,
        )
        try:
            created = await self._repository.create(row, commit=True)
        except Exception:
            await self._repository.rollback()
            raise
        return _to_response(created)

    async def update_source(self, source_id: str, payload: ModelSourceUpdateRequest) -> ModelSourceResponse:
        row = await self._repository.get_by_id(source_id)
        if row is None:
            raise ModelSourceNotFoundError(f"Model source not found: {source_id}")

        fields = payload.model_fields_set
        if "name" in fields and payload.name is not None:
            row.name = _normalize_name(payload.name)
        if "base_url" in fields and payload.base_url is not None:
            row.base_url = _normalize_base_url(payload.base_url)
        if "api_key" in fields:
            row.api_key_encrypted = _encrypt_optional(self._encryptor, payload.api_key)
        if "is_enabled" in fields and payload.is_enabled is not None:
            row.is_enabled = payload.is_enabled
        if "supports_chat_completions" in fields and payload.supports_chat_completions is not None:
            row.supports_chat_completions = payload.supports_chat_completions
        if "supports_responses" in fields and payload.supports_responses is not None:
            row.supports_responses = payload.supports_responses
        if "supports_audio_transcriptions" in fields and payload.supports_audio_transcriptions is not None:
            row.supports_audio_transcriptions = payload.supports_audio_transcriptions
        if "timeout_seconds" in fields:
            row.timeout_seconds = payload.timeout_seconds
        if "max_concurrency" in fields:
            row.max_concurrency = payload.max_concurrency

        models_replaced = False
        try:
            if "models" in fields and payload.models is not None:
                await self._repository.replace_models(row, _model_inputs_to_rows(payload.models), commit=False)
                models_replaced = True
            await self._repository.commit()
        except Exception:
            await self._repository.rollback()
            raise

        if models_replaced:
            # ``replace_models`` bulk-deletes and re-inserts child rows without
            # touching the identity-mapped parent's already-loaded ``models``
            # collection, so the post-commit read would return the stale
            # pre-update list without an explicit refresh.
            await self._repository.refresh_models(row)
        refreshed = await self._repository.get_by_id(source_id)
        if refreshed is None:
            raise ModelSourceNotFoundError(f"Model source not found: {source_id}")
        return _to_response(refreshed)

    async def delete_source(self, source_id: str) -> None:
        deleted = await self._repository.delete(source_id)
        if not deleted:
            raise ModelSourceNotFoundError(f"Model source not found: {source_id}")


def _normalize_name(value: str) -> str:
    name = value.strip()
    if not name:
        raise ModelSourceValidationError("Model source name is required")
    return name


def _normalize_base_url(value: str) -> str:
    url = value.strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ModelSourceValidationError("Model source base_url must be an absolute HTTP(S) URL")
    return url


def _normalize_model_slug(value: str) -> str:
    model = value.strip()
    if not model:
        raise ModelSourceValidationError("Model source model name is required")
    return model


def _validate_raw_metadata_json(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ModelSourceValidationError("raw_metadata_json must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ModelSourceValidationError("raw_metadata_json must be a JSON object")
    return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _model_inputs_to_rows(models: list[ModelSourceModelInput]) -> list[ModelSourceModel]:
    seen: set[str] = set()
    rows: list[ModelSourceModel] = []
    for item in models:
        model = _normalize_model_slug(item.model)
        if model in seen:
            raise ModelSourceValidationError(f"Duplicate model source model: {model}")
        seen.add(model)
        rows.append(
            ModelSourceModel(
                model=model,
                display_name=item.display_name.strip() if item.display_name else None,
                context_window=item.context_window,
                max_output_tokens=item.max_output_tokens,
                supports_streaming=item.supports_streaming,
                supports_tools=item.supports_tools,
                supports_vision=item.supports_vision,
                input_per_1m=item.input_per_1m,
                cached_input_per_1m=item.cached_input_per_1m,
                output_per_1m=item.output_per_1m,
                audio_per_minute=item.audio_per_minute,
                raw_metadata_json=_validate_raw_metadata_json(item.raw_metadata_json),
                is_enabled=item.is_enabled,
            )
        )
    return rows


def _encrypt_optional(encryptor: TokenEncryptor, value: str | None) -> bytes | None:
    if value is None:
        return None
    secret = value.strip()
    if not secret:
        return None
    return encryptor.encrypt(secret)


def _to_model_response(row: ModelSourceModel) -> ModelSourceModelResponse:
    return ModelSourceModelResponse(
        id=row.id,
        source_id=row.source_id,
        model=row.model,
        display_name=row.display_name,
        context_window=row.context_window,
        max_output_tokens=row.max_output_tokens,
        supports_streaming=row.supports_streaming,
        supports_tools=row.supports_tools,
        supports_vision=row.supports_vision,
        input_per_1m=row.input_per_1m,
        cached_input_per_1m=row.cached_input_per_1m,
        output_per_1m=row.output_per_1m,
        audio_per_minute=row.audio_per_minute,
        raw_metadata_json=row.raw_metadata_json,
        is_enabled=row.is_enabled,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_response(row: ModelSource) -> ModelSourceResponse:
    return ModelSourceResponse(
        id=row.id,
        name=row.name,
        kind=row.kind,
        base_url=row.base_url,
        is_enabled=row.is_enabled,
        health_status=row.health_status,
        supports_chat_completions=row.supports_chat_completions,
        supports_responses=row.supports_responses,
        supports_audio_transcriptions=row.supports_audio_transcriptions,
        timeout_seconds=row.timeout_seconds,
        max_concurrency=row.max_concurrency,
        created_at=row.created_at,
        updated_at=row.updated_at,
        models=[_to_model_response(model) for model in row.models],
    )
