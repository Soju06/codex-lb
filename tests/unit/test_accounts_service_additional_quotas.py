from __future__ import annotations

import time
from typing import cast
from unittest.mock import AsyncMock

import pytest

from app.db.models import AdditionalUsageHistory
from app.modules.accounts.service import AccountsService, clear_account_caches

pytestmark = pytest.mark.unit

_ACCOUNT_ID = "acc_fable"
_FABLE_QUOTA_KEY = "anthropic_fable_scoped_weekly"


def _fable_entry(used_percent: float) -> AdditionalUsageHistory:
    return AdditionalUsageHistory(
        account_id=_ACCOUNT_ID,
        quota_key=_FABLE_QUOTA_KEY,
        limit_name=_FABLE_QUOTA_KEY,
        metered_feature=_FABLE_QUOTA_KEY,
        window="primary",
        used_percent=used_percent,
        reset_at=int(time.time()) + 3600,
        window_minutes=10080,
    )


def _build_service(entry: AdditionalUsageHistory) -> AccountsService:
    repo = AsyncMock()
    repo.additional_quota_routing_policy_overrides.return_value = {}

    additional_usage_repo = AsyncMock()

    async def _latest_by_account(
        quota_key: str, window: str, *, account_ids: list[str] | None = None
    ) -> dict[str, AdditionalUsageHistory]:
        if quota_key == _FABLE_QUOTA_KEY and window == "primary":
            return {_ACCOUNT_ID: entry}
        return {}

    additional_usage_repo.latest_by_account.side_effect = _latest_by_account
    return AccountsService(repo=repo, additional_usage_repo=additional_usage_repo)


@pytest.mark.asyncio
async def test_additional_quotas_include_fable_scoped_weekly():
    """Regression: the Fable scoped-weekly marker is stored outside the
    additional-quota registry, so registry-driven enumeration must still
    surface it — clients read the Fable remaining percent from this row."""
    clear_account_caches()
    service = _build_service(_fable_entry(used_percent=57.0))

    result = await service._additional_quotas_by_account([_ACCOUNT_ID], {_ACCOUNT_ID})

    quotas = result.get(_ACCOUNT_ID) or []
    fable = next((quota for quota in quotas if quota.quota_key == _FABLE_QUOTA_KEY), None)
    assert fable is not None
    assert fable.primary_window is not None
    assert fable.primary_window.used_percent == 57.0


@pytest.mark.asyncio
async def test_fable_scoped_weekly_lookup_feeds_the_account_summary():
    """The same row also reaches `fableScopedWeekly` and the pools endpoint,
    through one batched lookup keyed on (quota key, window) — not an N+1."""
    clear_account_caches()
    entry = _fable_entry(used_percent=87.0)
    service = _build_service(entry)

    by_account = await service._fable_scoped_weekly_by_account([_ACCOUNT_ID])

    assert by_account == {_ACCOUNT_ID: entry}
    additional_usage_repo = cast(AsyncMock, service._additional_usage_repo)
    additional_usage_repo.latest_by_account.assert_awaited_once_with(
        _FABLE_QUOTA_KEY,
        "primary",
        account_ids=[_ACCOUNT_ID],
    )
