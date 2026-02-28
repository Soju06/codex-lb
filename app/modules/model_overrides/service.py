from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.db.models import ModelOverride
from app.modules.model_overrides.repository import ModelOverridesRepository

MatchType = Literal["ip", "app", "api_key"]


class ModelOverrideNotFoundError(ValueError):
    pass


class ModelOverrideConflictError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ModelOverrideData:
    id: int
    match_type: MatchType
    match_value: str
    forced_model: str
    forced_reasoning_effort: str | None
    enabled: bool
    note: str | None
    created_at: object
    updated_at: object


@dataclass(frozen=True, slots=True)
class ModelOverrideCreateData:
    match_type: MatchType
    match_value: str
    forced_model: str
    forced_reasoning_effort: str | None
    enabled: bool
    note: str | None


@dataclass(frozen=True, slots=True)
class ModelOverrideUpdateData:
    match_value: str | None = None
    forced_model: str | None = None
    forced_reasoning_effort: str | None = None
    enabled: bool | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class RequestActorContext:
    client_ip: str | None
    client_app: str | None
    api_key_identifier: str | None


@dataclass(frozen=True, slots=True)
class ModelOverrideMatch:
    override_id: int
    match_type: MatchType
    match_value: str
    forced_model: str
    forced_reasoning_effort: str | None


class ModelOverridesService:
    _ALLOWED_MATCH_TYPES: set[str] = {"ip", "app", "api_key"}

    def __init__(self, repository: ModelOverridesRepository) -> None:
        self._repository = repository

    async def list_overrides(self) -> list[ModelOverrideData]:
        rows = await self._repository.list_all()
        return [_to_data(row) for row in rows]

    async def create_override(self, payload: ModelOverrideCreateData) -> ModelOverrideData:
        row = ModelOverride(
            match_type=_normalize_match_type(payload.match_type),
            match_value=_normalize_match_value(payload.match_type, payload.match_value),
            forced_model=_normalize_required(payload.forced_model),
            forced_reasoning_effort=_normalize_reasoning_effort(payload.forced_reasoning_effort),
            enabled=payload.enabled,
            note=_normalize_optional(payload.note),
        )
        try:
            created = await self._repository.create(row)
        except Exception as exc:
            await self._repository.rollback()
            if self._repository.is_unique_violation(exc):
                raise ModelOverrideConflictError("Override already exists for this matcher") from exc
            raise
        return _to_data(created)

    async def update_override(self, override_id: int, payload: ModelOverrideUpdateData) -> ModelOverrideData:
        row = await self._repository.get(override_id)
        if row is None:
            raise ModelOverrideNotFoundError("Override not found")
        if payload.match_value is not None:
            row.match_value = _normalize_match_value(row.match_type, payload.match_value)
        if payload.forced_model is not None:
            row.forced_model = _normalize_required(payload.forced_model)
        if payload.forced_reasoning_effort is not None:
            row.forced_reasoning_effort = _normalize_reasoning_effort(payload.forced_reasoning_effort)
        if payload.enabled is not None:
            row.enabled = payload.enabled
        if payload.note is not None:
            row.note = _normalize_optional(payload.note)
        try:
            updated = await self._repository.update(row)
        except Exception as exc:
            await self._repository.rollback()
            if self._repository.is_unique_violation(exc):
                raise ModelOverrideConflictError("Override already exists for this matcher") from exc
            raise
        return _to_data(updated)

    async def delete_override(self, override_id: int) -> None:
        row = await self._repository.get(override_id)
        if row is None:
            raise ModelOverrideNotFoundError("Override not found")
        await self._repository.delete(row)

    async def resolve(self, actor: RequestActorContext) -> ModelOverrideMatch | None:
        candidates: list[tuple[MatchType, str]] = []
        if actor.api_key_identifier:
            candidates.append(("api_key", _normalize_match_value("api_key", actor.api_key_identifier)))
        if actor.client_app:
            candidates.append(("app", _normalize_match_value("app", actor.client_app)))
        if actor.client_ip:
            candidates.append(("ip", _normalize_match_value("ip", actor.client_ip)))

        for match_type, value in candidates:
            row = await self._repository.find_first_enabled(match_type, value)
            if row is None:
                continue
            return ModelOverrideMatch(
                override_id=row.id,
                match_type=match_type,
                match_value=row.match_value,
                forced_model=row.forced_model,
                forced_reasoning_effort=row.forced_reasoning_effort,
            )
        return None


def _normalize_match_type(value: str) -> MatchType:
    normalized = (value or "").strip().lower()
    if normalized not in ModelOverridesService._ALLOWED_MATCH_TYPES:
        raise ValueError("Unsupported match_type")
    return normalized  # type: ignore[return-value]


def _normalize_required(value: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise ValueError("Value is required")
    return normalized


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_reasoning_effort(value: str | None) -> str | None:
    normalized = _normalize_optional(value)
    if normalized is None:
        return None
    lowered = normalized.lower()
    if lowered == "normal":
        return "medium"
    return lowered


def _normalize_match_value(match_type: str, value: str) -> str:
    normalized = _normalize_required(value)
    if match_type in {"app", "api_key"}:
        return normalized.lower()
    return normalized


def _to_data(row: ModelOverride) -> ModelOverrideData:
    return ModelOverrideData(
        id=row.id,
        match_type=row.match_type,  # type: ignore[arg-type]
        match_value=row.match_value,
        forced_model=row.forced_model,
        forced_reasoning_effort=row.forced_reasoning_effort,
        enabled=row.enabled,
        note=row.note,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )

