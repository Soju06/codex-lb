from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, cast

from app.db.models import AgentProviderAccount, AgentProviderQuotaWindow, AgentProviderRoutingSettings
from app.modules.agent_provider_accounts.service import SUPPORTED_PROVIDER_IDS
from app.modules.agent_provider_routing.logic import (
    ProviderAccountRoutingState,
    ProviderQuotaWindow,
    ProviderRoutingSettings,
    ProviderRoutingStrategy,
    select_provider_account,
)
from app.modules.agent_provider_routing.settlement import AgentProviderUsageSettlementData


class AgentProviderRoutingValidationError(Exception):
    pass


class AgentProviderRoutingNotFoundError(Exception):
    pass


class AgentProviderRoutingRepositoryPort(Protocol):
    async def get_or_create_settings(self, provider_id: str) -> AgentProviderRoutingSettings: ...

    async def save_settings(self, row: AgentProviderRoutingSettings) -> AgentProviderRoutingSettings: ...

    async def advance_round_robin_cursor(
        self,
        provider_id: str,
        *,
        expected_cursor: str | None,
        selected_account_id: str,
    ) -> bool: ...

    async def list_accounts_with_quota_windows(self, provider_id: str) -> list[AgentProviderAccount]: ...

    async def get_account_for_provider(self, provider_id: str, account_id: str) -> AgentProviderAccount | None: ...

    async def upsert_quota_window(
        self,
        *,
        account: AgentProviderAccount,
        dimension: str,
        used: int,
        limit: int | None,
        reset_at: datetime | None,
    ) -> AgentProviderQuotaWindow: ...

    async def increment_quota_windows(
        self,
        account: AgentProviderAccount,
        usage: AgentProviderUsageSettlementData,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class AgentProviderRoutingSettingsUpdateData:
    strategy: ProviderRoutingStrategy | None = None
    single_account_id: str | None = None
    single_account_id_set: bool = False
    ordered_account_ids: tuple[str, ...] | None = None
    quota_threshold_pct: float | None = None
    round_robin_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class AgentProviderQuotaWindowUpsertData:
    dimension: str
    used: int
    limit: int | None = None
    reset_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AgentProviderPreflight:
    provider_id: str
    settings: AgentProviderRoutingSettings
    accounts: list[AgentProviderAccount]
    selected_account_id: str | None
    denied_reason: str | None
    candidate_account_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AgentProviderSelectedAccount:
    provider_id: str
    settings: AgentProviderRoutingSettings
    account: AgentProviderAccount
    candidate_account_ids: tuple[str, ...]


class AgentProviderRoutingService:
    def __init__(self, repository: AgentProviderRoutingRepositoryPort) -> None:
        self._repository = repository

    async def get_settings(self, provider_id: str) -> AgentProviderRoutingSettings:
        provider = _normalize_provider_id(provider_id)
        return await self._repository.get_or_create_settings(provider)

    async def update_settings(
        self,
        provider_id: str,
        payload: AgentProviderRoutingSettingsUpdateData,
    ) -> AgentProviderRoutingSettings:
        provider = _normalize_provider_id(provider_id)
        row = await self._repository.get_or_create_settings(provider)
        strategy = payload.strategy or cast(ProviderRoutingStrategy, row.strategy)
        if payload.quota_threshold_pct is not None and not 0.0 <= payload.quota_threshold_pct <= 100.0:
            raise AgentProviderRoutingValidationError("quota_threshold_pct must be between 0 and 100")
        single_account_id = (
            _clean_optional(payload.single_account_id)
            if payload.single_account_id_set
            else _clean_optional(row.single_account_id)
        )
        if strategy == "single_account" and not single_account_id:
            raise AgentProviderRoutingValidationError("single_account_id is required for single_account routing")
        ordered_account_ids = (
            _normalize_account_id_order(payload.ordered_account_ids)
            if payload.ordered_account_ids is not None
            else _parse_account_id_order(row.ordered_account_ids_json)
        )
        if strategy == "ordered_fallback" and not ordered_account_ids:
            raise AgentProviderRoutingValidationError("ordered_account_ids is required for ordered_fallback routing")
        if single_account_id is not None:
            account = await self._repository.get_account_for_provider(provider, single_account_id)
            if account is None:
                raise AgentProviderRoutingValidationError("single_account_id is not a provider account")
        for account_id in ordered_account_ids:
            account = await self._repository.get_account_for_provider(provider, account_id)
            if account is None:
                raise AgentProviderRoutingValidationError("ordered_account_ids must be provider accounts")
        row.strategy = strategy
        if payload.single_account_id_set or strategy == "single_account":
            row.single_account_id = single_account_id
        if payload.ordered_account_ids is not None:
            row.ordered_account_ids_json = _dump_account_id_order(ordered_account_ids)
        if payload.quota_threshold_pct is not None:
            row.quota_threshold_pct = payload.quota_threshold_pct
        if payload.round_robin_cursor is not None:
            row.round_robin_cursor = _clean_optional(payload.round_robin_cursor)
        return await self._repository.save_settings(row)

    async def upsert_quota_window(
        self,
        provider_id: str,
        account_id: str,
        payload: AgentProviderQuotaWindowUpsertData,
    ) -> AgentProviderQuotaWindow:
        provider = _normalize_provider_id(provider_id)
        dimension = payload.dimension.strip()
        if not dimension:
            raise AgentProviderRoutingValidationError("dimension is required")
        if payload.limit is not None and payload.used > payload.limit:
            raise AgentProviderRoutingValidationError("used cannot exceed limit")
        account = await self._repository.get_account_for_provider(provider, account_id)
        if account is None:
            raise AgentProviderRoutingNotFoundError("provider account not found")
        return await self._repository.upsert_quota_window(
            account=account,
            dimension=dimension,
            used=payload.used,
            limit=payload.limit,
            reset_at=payload.reset_at,
        )

    async def preflight(self, provider_id: str, *, auth_mode: str | None = None) -> AgentProviderPreflight:
        provider = _normalize_provider_id(provider_id)
        settings = await self._repository.get_or_create_settings(provider)
        accounts = await self._repository.list_accounts_with_quota_windows(provider)
        if auth_mode is not None:
            accounts = [account for account in accounts if account.auth_mode == auth_mode]
        result = select_provider_account(
            [_routing_state(account) for account in accounts],
            ProviderRoutingSettings(
                strategy=cast(ProviderRoutingStrategy, settings.strategy),
                single_account_id=settings.single_account_id,
                ordered_account_ids=_parse_account_id_order(settings.ordered_account_ids_json),
                quota_threshold_pct=settings.quota_threshold_pct,
                round_robin_cursor=settings.round_robin_cursor,
            ),
        )
        return AgentProviderPreflight(
            provider_id=provider,
            settings=settings,
            accounts=accounts,
            selected_account_id=result.account_id,
            denied_reason=result.denied_reason,
            candidate_account_ids=result.candidate_account_ids,
        )

    async def select_account(self, provider_id: str, *, auth_mode: str | None = None) -> AgentProviderSelectedAccount:
        for _attempt in range(5):
            preflight = await self.preflight(provider_id, auth_mode=auth_mode)
            if preflight.selected_account_id is None:
                raise AgentProviderRoutingNotFoundError(preflight.denied_reason or "provider account unavailable")
            for account in preflight.accounts:
                if account.id == preflight.selected_account_id:
                    if preflight.settings.strategy == "round_robin":
                        advanced = await self._repository.advance_round_robin_cursor(
                            preflight.provider_id,
                            expected_cursor=preflight.settings.round_robin_cursor,
                            selected_account_id=account.id,
                        )
                        if not advanced:
                            break
                        preflight.settings.round_robin_cursor = account.id
                    return AgentProviderSelectedAccount(
                        provider_id=preflight.provider_id,
                        settings=preflight.settings,
                        account=account,
                        candidate_account_ids=preflight.candidate_account_ids,
                    )
            else:
                raise AgentProviderRoutingNotFoundError("selected provider account not found")
        raise AgentProviderRoutingNotFoundError("round_robin_cursor_conflict")

    async def settle_usage(
        self,
        provider_id: str,
        account_id: str,
        usage: AgentProviderUsageSettlementData,
    ) -> None:
        provider = _normalize_provider_id(provider_id)
        account = await self._repository.get_account_for_provider(provider, account_id)
        if account is None:
            raise AgentProviderRoutingNotFoundError("provider account not found")
        await self._repository.increment_quota_windows(account, usage)


def _normalize_provider_id(provider_id: str) -> str:
    normalized = provider_id.strip().lower()
    if normalized not in SUPPORTED_PROVIDER_IDS:
        raise AgentProviderRoutingValidationError(f"Unsupported provider '{provider_id}'")
    return normalized


def _routing_state(account: AgentProviderAccount) -> ProviderAccountRoutingState:
    return ProviderAccountRoutingState(
        account_id=account.id,
        status=account.status,
        quota_windows=tuple(
            ProviderQuotaWindow(
                dimension=window.dimension,
                used=window.used,
                limit=window.limit,
                reset_at=window.reset_at,
            )
            for window in account.quota_windows
        ),
    )


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _normalize_account_id_order(account_ids: tuple[str, ...] | None) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw_account_id in account_ids or ():
        account_id = raw_account_id.strip()
        if not account_id or account_id in seen:
            continue
        seen.add(account_id)
        ordered.append(account_id)
    return tuple(ordered)


def _parse_account_id_order(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    if not isinstance(parsed, list):
        return ()
    return _normalize_account_id_order(tuple(item for item in parsed if isinstance(item, str)))


def _dump_account_id_order(account_ids: tuple[str, ...]) -> str:
    return json.dumps(list(account_ids), separators=(",", ":"))
