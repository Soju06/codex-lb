from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, cast

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentProviderAccount, AgentProviderRoutingSettings
from app.modules.agent_provider_routing.logic import (
    ProviderAccountRoutingState,
    ProviderQuotaWindow,
    ProviderRoutingSettings,
    select_provider_account,
)
from app.modules.agent_provider_routing.repository import AgentProviderRoutingRepository
from app.modules.agent_provider_routing.service import (
    AgentProviderRoutingService,
    AgentProviderRoutingSettingsUpdateData,
    AgentProviderRoutingValidationError,
)


def _state(
    account_id: str,
    *,
    used: int = 0,
    limit: int | None = 100,
    reset_at: datetime | None = None,
    status: str = "active",
) -> ProviderAccountRoutingState:
    return ProviderAccountRoutingState(
        account_id=account_id,
        status=status,
        quota_windows=(
            ProviderQuotaWindow(
                dimension="requests_per_day",
                used=used,
                limit=limit,
                reset_at=reset_at,
            ),
        ),
    )


def test_capacity_weighted_selects_account_with_remaining_budget() -> None:
    result = select_provider_account(
        [_state("low", used=90), _state("high", used=10)],
        ProviderRoutingSettings(strategy="capacity_weighted", quota_threshold_pct=95.0),
    )

    assert result.account_id == "high"
    assert result.denied_reason is None
    assert result.candidate_account_ids == ("low", "high")


def test_budget_filter_blocks_drain_strategy_before_selection() -> None:
    now = datetime(2026, 6, 9, tzinfo=timezone.utc)
    result = select_provider_account(
        [
            _state("near-reset-over-budget", used=99, reset_at=now + timedelta(minutes=1)),
            _state("later-safe", used=40, reset_at=now + timedelta(days=2)),
        ],
        ProviderRoutingSettings(strategy="reset_drain", quota_threshold_pct=95.0),
        now=now,
    )

    assert result.account_id == "later-safe"
    assert result.candidate_account_ids == ("later-safe",)


def test_preflight_denies_when_all_candidates_exceed_budget() -> None:
    result = select_provider_account(
        [_state("a", used=95), _state("b", used=100)],
        ProviderRoutingSettings(strategy="sequential_drain", quota_threshold_pct=95.0),
    )

    assert result.account_id is None
    assert result.denied_reason == "provider_quota_budget_exhausted"
    assert result.candidate_account_ids == ("a", "b")


def test_expired_quota_window_is_budget_safe_again() -> None:
    now = datetime(2026, 6, 9, tzinfo=timezone.utc)
    result = select_provider_account(
        [_state("expired", used=100, reset_at=now - timedelta(minutes=1))],
        ProviderRoutingSettings(strategy="capacity_weighted", quota_threshold_pct=95.0),
        now=now,
    )

    assert result.account_id == "expired"
    assert result.denied_reason is None
    assert result.candidate_account_ids == ("expired",)


def test_single_account_scope_does_not_fallback_to_other_accounts() -> None:
    result = select_provider_account(
        [_state("chosen", used=100), _state("other", used=0)],
        ProviderRoutingSettings(strategy="single_account", single_account_id="chosen", quota_threshold_pct=95.0),
    )

    assert result.account_id is None
    assert result.denied_reason == "provider_quota_budget_exhausted"
    assert result.candidate_account_ids == ("chosen",)


def test_ordered_fallback_selects_first_budget_safe_ordered_account() -> None:
    result = select_provider_account(
        [_state("first", used=99), _state("second", used=10), _state("third", used=0)],
        ProviderRoutingSettings(
            strategy="ordered_fallback",
            ordered_account_ids=("first", "second", "third"),
            quota_threshold_pct=95.0,
        ),
    )

    assert result.account_id == "second"
    assert result.denied_reason is None
    assert result.candidate_account_ids == ("second", "third")


def test_ordered_fallback_denies_when_ordered_accounts_are_not_budget_safe() -> None:
    result = select_provider_account(
        [_state("first", used=99), _state("unordered", used=10)],
        ProviderRoutingSettings(
            strategy="ordered_fallback",
            ordered_account_ids=("first",),
            quota_threshold_pct=95.0,
        ),
    )

    assert result.account_id is None
    assert result.denied_reason == "ordered_fallback_unavailable"
    assert result.candidate_account_ids == ("unordered",)


class _SettingsInsertRaceSession:
    def __init__(self) -> None:
        self.added: AgentProviderRoutingSettings | None = None
        self.existing: AgentProviderRoutingSettings | None = None
        self.rolled_back = False

    async def get(
        self, _model: type[AgentProviderRoutingSettings], provider_id: str
    ) -> AgentProviderRoutingSettings | None:
        if self.existing is not None:
            return self.existing
        return None

    def add(self, row: AgentProviderRoutingSettings) -> None:
        self.added = row

    async def commit(self) -> None:
        assert self.added is not None
        self.existing = AgentProviderRoutingSettings(provider_id=self.added.provider_id)
        raise IntegrityError("insert", {}, Exception("unique"))

    async def rollback(self) -> None:
        self.rolled_back = True

    async def refresh(self, _row: AgentProviderRoutingSettings) -> None:
        raise AssertionError("race recovery should return the reloaded row")


@pytest.mark.asyncio
async def test_get_or_create_settings_recovers_from_concurrent_insert() -> None:
    session = _SettingsInsertRaceSession()
    repository = AgentProviderRoutingRepository(cast(AsyncSession, session))

    row = await repository.get_or_create_settings("gemini")

    assert row.provider_id == "gemini"
    assert session.rolled_back is True


class _SettingsRepository:
    def __init__(self, row: AgentProviderRoutingSettings) -> None:
        self.row = row
        self.saved = False

    async def get_or_create_settings(self, _provider_id: str) -> AgentProviderRoutingSettings:
        return self.row

    async def save_settings(self, row: AgentProviderRoutingSettings) -> AgentProviderRoutingSettings:
        self.saved = True
        self.row = row
        return row

    async def get_account_for_provider(self, _provider_id: str, _account_id: str) -> Any:
        raise AssertionError("explicitly cleared single-account selection should fail before account lookup")


@pytest.mark.asyncio
async def test_update_settings_rejects_clearing_active_single_account() -> None:
    repository = _SettingsRepository(
        AgentProviderRoutingSettings(
            provider_id="gemini",
            strategy="single_account",
            single_account_id="account-1",
        )
    )
    service = AgentProviderRoutingService(cast(Any, repository))

    with pytest.raises(AgentProviderRoutingValidationError, match="single_account_id is required"):
        await service.update_settings(
            "gemini",
            AgentProviderRoutingSettingsUpdateData(single_account_id=None, single_account_id_set=True),
        )

    assert repository.saved is False


class _RoundRobinRepository:
    def __init__(self) -> None:
        self.settings = AgentProviderRoutingSettings(
            provider_id="gemini",
            strategy="round_robin",
            round_robin_cursor=None,
        )
        self.accounts = [
            AgentProviderAccount(
                id="account-a",
                provider_id="gemini",
                display_name="A",
                auth_mode="api_key",
                status="active",
            ),
            AgentProviderAccount(
                id="account-b",
                provider_id="gemini",
                display_name="B",
                auth_mode="api_key",
                status="active",
            ),
        ]
        self.advance_calls: list[tuple[str | None, str]] = []

    async def get_or_create_settings(self, _provider_id: str) -> AgentProviderRoutingSettings:
        return self.settings

    async def list_accounts_with_quota_windows(self, _provider_id: str) -> list[AgentProviderAccount]:
        return self.accounts

    async def advance_round_robin_cursor(
        self,
        _provider_id: str,
        *,
        expected_cursor: str | None,
        selected_account_id: str,
    ) -> bool:
        self.advance_calls.append((expected_cursor, selected_account_id))
        if len(self.advance_calls) == 1:
            self.settings.round_robin_cursor = selected_account_id
            return False
        if self.settings.round_robin_cursor != expected_cursor:
            return False
        self.settings.round_robin_cursor = selected_account_id
        return True


@pytest.mark.asyncio
async def test_select_account_retries_round_robin_cursor_conflict() -> None:
    repository = _RoundRobinRepository()
    service = AgentProviderRoutingService(cast(Any, repository))

    selected = await service.select_account("gemini")

    assert selected.account.id == "account-b"
    assert repository.advance_calls == [(None, "account-a"), ("account-a", "account-b")]
    assert repository.settings.round_robin_cursor == "account-b"
