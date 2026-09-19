from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from app.core.auth.api_key_cache import get_api_key_cache
from app.core.cache.invalidation import NAMESPACE_API_KEY, get_cache_invalidation_poller
from app.core.exceptions import (
    TeamMemberOverCapError,
    TeamMemberSuspendedError,
    TeamModelNotAllowedError,
)
from app.core.utils.time import to_utc_naive, utcnow
from app.db.models import ApiKey, TeamMember, TeamMemberStatus
from app.modules.api_keys.service import ApiKeyData
from app.modules.team.repository import (
    TeamRepository,
    TeamUsageDayRow,
    TeamUsageModelRow,
    TeamUsageTotals,
)
from app.modules.team.windows import TEAM_WINDOWS, window_end, window_start

_AGGREGATE_CACHE_TTL_SECONDS = 1.0
_NEAR_CAP_RATIO = 0.8

GATE_OK = "ok"
GATE_NEAR_CAP = "near_cap"
GATE_OVER_CAP = "over_cap"
GATE_SUSPENDED = "suspended"


class TeamMemberNotFoundError(ValueError):
    pass


class TeamValidationError(ValueError):
    pass


def _monotonic() -> float:
    return time.monotonic()


_aggregate_cache: dict[tuple[str, str], tuple[float, TeamUsageTotals]] = {}


def reset_team_usage_cache() -> None:
    _aggregate_cache.clear()


def invalidate_team_member_caches() -> None:
    """Drop cached member aggregates and force api-key cache re-reads."""

    reset_team_usage_cache()
    get_api_key_cache().clear()


async def bump_api_key_cache_namespace() -> None:
    poller = get_cache_invalidation_poller()
    if poller is not None:
        await poller.bump(NAMESPACE_API_KEY)


@dataclass(frozen=True, slots=True)
class TeamMemberCaps:
    cost_cap_day_usd: float | None = None
    cost_cap_week_usd: float | None = None
    cost_cap_month_usd: float | None = None
    token_cap_day: int | None = None
    token_cap_week: int | None = None
    token_cap_month: int | None = None


@dataclass(frozen=True, slots=True)
class TeamMemberCreateData:
    name: str
    email: str | None = None
    caps: TeamMemberCaps = field(default_factory=TeamMemberCaps)
    allowed_models: list[str] | None = None
    notes: str | None = None
    status: str = TeamMemberStatus.ACTIVE.value


@dataclass(frozen=True, slots=True)
class TeamMemberUpdateData:
    name: str | None = None
    name_set: bool = False
    email: str | None = None
    email_set: bool = False
    status: str | None = None
    status_set: bool = False
    cost_cap_day_usd: float | None = None
    cost_cap_day_usd_set: bool = False
    cost_cap_week_usd: float | None = None
    cost_cap_week_usd_set: bool = False
    cost_cap_month_usd: float | None = None
    cost_cap_month_usd_set: bool = False
    token_cap_day: int | None = None
    token_cap_day_set: bool = False
    token_cap_week: int | None = None
    token_cap_week_set: bool = False
    token_cap_month: int | None = None
    token_cap_month_set: bool = False
    allowed_models: list[str] | None = None
    allowed_models_set: bool = False
    notes: str | None = None
    notes_set: bool = False


@dataclass(frozen=True, slots=True)
class TeamMemberKeyData:
    id: str
    name: str
    key_prefix: str
    is_active: bool
    last_used_at: datetime | None


@dataclass(frozen=True, slots=True)
class TeamMemberData:
    id: str
    name: str
    email: str | None
    status: str
    cost_cap_day_usd: float | None
    cost_cap_week_usd: float | None
    cost_cap_month_usd: float | None
    token_cap_day: int | None
    token_cap_week: int | None
    token_cap_month: int | None
    allowed_models: list[str] | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
    usage: dict[str, TeamUsageTotals] = field(default_factory=dict)
    gate: str = GATE_OK
    keys: list[TeamMemberKeyData] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class TeamMemberUsageDetail:
    member_id: str
    window: str
    window_start: datetime
    window_end: datetime
    totals: TeamUsageTotals
    models: list[TeamUsageModelRow]
    series: list[TeamUsageDayRow]


def serialize_allowed_models(models: list[str] | None) -> str | None:
    """Same encoding the api_keys module uses for ``allowed_models``."""

    if not models:
        return None
    return json.dumps(models)


def deserialize_allowed_models(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, list):
        return None
    return [str(item) for item in parsed]


def _cap_for(member: TeamMember, window: str, kind: str) -> float | int | None:
    return getattr(member, f"{kind}_cap_{window}" + ("_usd" if kind == "cost" else ""), None)


class TeamService:
    def __init__(self, repository: TeamRepository) -> None:
        self._repository = repository

    # ── Gate ──

    async def check_member_gate(self, api_key: ApiKeyData, model: str | None) -> None:
        member_id = getattr(api_key, "member_id", None)
        if not member_id:
            return

        member = await self._repository.get_by_id(member_id)
        if member is None:
            return

        if _status_value(member.status) == TeamMemberStatus.SUSPENDED.value:
            raise TeamMemberSuspendedError(f"Team member '{member.name}' is suspended")

        allowed_models = deserialize_allowed_models(member.allowed_models)
        if allowed_models and model is not None and model not in allowed_models:
            raise TeamModelNotAllowedError(f"Model '{model}' is not allowed for team member '{member.name}'")

        now = utcnow()
        for window in TEAM_WINDOWS:
            cost_cap = _cap_for(member, window, "cost")
            token_cap = _cap_for(member, window, "token")
            if cost_cap is None and token_cap is None:
                continue

            totals = await self._usage_for_window(member.id, window, now=now)
            if cost_cap is not None and totals.cost_usd >= float(cost_cap):
                raise _over_cap_error(member, window, now, f"cost cap ${float(cost_cap):.2f}")
            if token_cap is not None and totals.tokens >= int(token_cap):
                raise _over_cap_error(member, window, now, f"token cap {int(token_cap)}")

    async def _usage_for_window(self, member_id: str, window: str, *, now: datetime) -> TeamUsageTotals:
        cache_key = (member_id, window)
        cached = _aggregate_cache.get(cache_key)
        clock = _monotonic()
        if cached is not None and clock < cached[0]:
            return cached[1]

        totals = await self._repository.aggregate_usage(member_id, since=window_start(window, now))
        _aggregate_cache[cache_key] = (clock + _AGGREGATE_CACHE_TTL_SECONDS, totals)
        return totals

    # ── CRUD ──

    async def list_members(self) -> list[TeamMemberData]:
        members = await self._repository.list_all()
        if not members:
            return []
        keys_by_member = await self._repository.list_keys_by_members([member.id for member in members])
        now = utcnow()
        results: list[TeamMemberData] = []
        for member in members:
            usage = {
                window: await self._repository.aggregate_usage(member.id, since=window_start(window, now))
                for window in TEAM_WINDOWS
            }
            results.append(
                _to_member_data(member, usage=usage, keys=keys_by_member.get(member.id, [])),
            )
        return results

    async def get_member(self, member_id: str) -> TeamMemberData:
        member = await self._require_member(member_id)
        now = utcnow()
        usage = {
            window: await self._repository.aggregate_usage(member.id, since=window_start(window, now))
            for window in TEAM_WINDOWS
        }
        keys = await self._repository.list_keys_by_member(member.id)
        return _to_member_data(member, usage=usage, keys=keys)

    async def create_member(self, payload: TeamMemberCreateData) -> TeamMemberData:
        name = _normalize_name(payload.name)
        if await self._repository.get_by_name(name) is not None:
            raise TeamValidationError(f"Team member already exists: {name}")
        now = utcnow()
        row = TeamMember(
            id=str(uuid.uuid4()),
            name=name,
            email=_normalize_optional_text(payload.email),
            status=TeamMemberStatus(_normalize_status(payload.status)),
            cost_cap_day_usd=payload.caps.cost_cap_day_usd,
            cost_cap_week_usd=payload.caps.cost_cap_week_usd,
            cost_cap_month_usd=payload.caps.cost_cap_month_usd,
            token_cap_day=payload.caps.token_cap_day,
            token_cap_week=payload.caps.token_cap_week,
            token_cap_month=payload.caps.token_cap_month,
            allowed_models=serialize_allowed_models(payload.allowed_models),
            notes=_normalize_optional_text(payload.notes),
            created_at=now,
            updated_at=now,
        )
        created = await self._repository.create(row)
        invalidate_team_member_caches()
        await bump_api_key_cache_namespace()
        return _to_member_data(created, usage=_empty_usage(), keys=[])

    async def update_member(self, member_id: str, payload: TeamMemberUpdateData) -> TeamMemberData:
        member = await self._require_member(member_id)

        if payload.name_set and payload.name is not None:
            name = _normalize_name(payload.name)
            existing = await self._repository.get_by_name(name)
            if existing is not None and existing.id != member.id:
                raise TeamValidationError(f"Team member already exists: {name}")
            member.name = name
        if payload.email_set:
            member.email = _normalize_optional_text(payload.email)
        if payload.status_set and payload.status is not None:
            member.status = TeamMemberStatus(_normalize_status(payload.status))
        if payload.cost_cap_day_usd_set:
            member.cost_cap_day_usd = payload.cost_cap_day_usd
        if payload.cost_cap_week_usd_set:
            member.cost_cap_week_usd = payload.cost_cap_week_usd
        if payload.cost_cap_month_usd_set:
            member.cost_cap_month_usd = payload.cost_cap_month_usd
        if payload.token_cap_day_set:
            member.token_cap_day = payload.token_cap_day
        if payload.token_cap_week_set:
            member.token_cap_week = payload.token_cap_week
        if payload.token_cap_month_set:
            member.token_cap_month = payload.token_cap_month
        if payload.allowed_models_set:
            member.allowed_models = serialize_allowed_models(payload.allowed_models)
        if payload.notes_set:
            member.notes = _normalize_optional_text(payload.notes)
        member.updated_at = utcnow()

        await self._repository.commit()
        invalidate_team_member_caches()
        await bump_api_key_cache_namespace()
        return await self.get_member(member.id)

    async def delete_member(self, member_id: str) -> None:
        await self._require_member(member_id)
        deleted = await self._repository.delete(member_id)
        if not deleted:
            raise TeamMemberNotFoundError(f"Team member not found: {member_id}")
        invalidate_team_member_caches()
        await bump_api_key_cache_namespace()

    async def get_member_usage(self, member_id: str, window: str) -> TeamMemberUsageDetail:
        member = await self._require_member(member_id)
        if window not in TEAM_WINDOWS:
            raise TeamValidationError(f"Unknown window: {window}")
        now = utcnow()
        since = window_start(window, now)
        return TeamMemberUsageDetail(
            member_id=member.id,
            window=window,
            window_start=since,
            window_end=window_end(window, now),
            totals=await self._repository.aggregate_usage(member.id, since=since),
            models=await self._repository.aggregate_usage_by_model(member.id, since=since),
            series=await self._repository.aggregate_usage_by_day(member.id, since=since),
        )

    async def _require_member(self, member_id: str) -> TeamMember:
        member = await self._repository.get_by_id(member_id)
        if member is None:
            raise TeamMemberNotFoundError(f"Team member not found: {member_id}")
        return member


def _status_value(status: object) -> str:
    if isinstance(status, TeamMemberStatus):
        return status.value
    return str(status)


def _normalize_status(status: str | None) -> str:
    value = (status or TeamMemberStatus.ACTIVE.value).strip().lower()
    if value not in {member.value for member in TeamMemberStatus}:
        raise TeamValidationError(f"Unknown team member status: {status}")
    return value


def _normalize_name(name: str) -> str:
    normalized = (name or "").strip()
    if not normalized:
        raise TeamValidationError("Team member name must not be blank")
    return normalized


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _empty_usage() -> dict[str, TeamUsageTotals]:
    return {window: TeamUsageTotals(cost_usd=0.0, tokens=0) for window in TEAM_WINDOWS}


def _over_cap_error(member: TeamMember, window: str, now: datetime, detail: str) -> TeamMemberOverCapError:
    return TeamMemberOverCapError(
        f"Team member '{member.name}' reached its {window} {detail}",
        window=window,
        reset_at=window_end(window, now),
    )


def _to_member_data(
    member: TeamMember,
    *,
    usage: dict[str, TeamUsageTotals],
    keys: list[ApiKey],
) -> TeamMemberData:
    status = _status_value(member.status)
    return TeamMemberData(
        id=member.id,
        name=member.name,
        email=member.email,
        status=status,
        cost_cap_day_usd=_optional_float(member.cost_cap_day_usd),
        cost_cap_week_usd=_optional_float(member.cost_cap_week_usd),
        cost_cap_month_usd=_optional_float(member.cost_cap_month_usd),
        token_cap_day=_optional_int(member.token_cap_day),
        token_cap_week=_optional_int(member.token_cap_week),
        token_cap_month=_optional_int(member.token_cap_month),
        allowed_models=deserialize_allowed_models(member.allowed_models),
        notes=member.notes,
        created_at=to_utc_naive(member.created_at),
        updated_at=to_utc_naive(member.updated_at),
        usage=usage,
        gate=compute_gate(member, usage),
        keys=[
            TeamMemberKeyData(
                id=key.id,
                name=key.name,
                key_prefix=key.key_prefix,
                is_active=key.is_active,
                last_used_at=key.last_used_at,
            )
            for key in keys
        ],
    )


def compute_gate(member: TeamMember, usage: dict[str, TeamUsageTotals]) -> str:
    if _status_value(member.status) == TeamMemberStatus.SUSPENDED.value:
        return GATE_SUSPENDED

    near = False
    for window in TEAM_WINDOWS:
        totals = usage.get(window)
        if totals is None:
            continue
        cost_cap = _cap_for(member, window, "cost")
        token_cap = _cap_for(member, window, "token")
        if cost_cap is not None and float(cost_cap) > 0:
            ratio = totals.cost_usd / float(cost_cap)
            if ratio >= 1.0:
                return GATE_OVER_CAP
            if ratio >= _NEAR_CAP_RATIO:
                near = True
        if token_cap is not None and int(token_cap) > 0:
            ratio = totals.tokens / int(token_cap)
            if ratio >= 1.0:
                return GATE_OVER_CAP
            if ratio >= _NEAR_CAP_RATIO:
                near = True
    return GATE_NEAR_CAP if near else GATE_OK


def _optional_float(value: object) -> float | None:
    return None if value is None else float(value)  # type: ignore[arg-type]


def _optional_int(value: object) -> int | None:
    return None if value is None else int(value)  # type: ignore[arg-type]
