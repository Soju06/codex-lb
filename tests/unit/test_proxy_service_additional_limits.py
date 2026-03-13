from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import pytest

from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus, AdditionalUsageHistory
from app.modules.proxy.repo_bundle import ProxyRepositories
from app.modules.proxy.service import ProxyService

pytestmark = pytest.mark.unit


def _make_account(account_id: str, email: str = "a@example.com") -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        chatgpt_account_id=f"workspace-{account_id}",
        email=email,
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=datetime.now(tz=timezone.utc),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


def _entry(
    entry_id: int,
    *,
    account_id: str,
    limit_name: str,
    window: str,
    used_percent: float,
    reset_at: int,
    window_minutes: int,
    metered_feature: str = "o_pro",
) -> AdditionalUsageHistory:
    return AdditionalUsageHistory(
        id=entry_id,
        account_id=account_id,
        quota_key=limit_name,
        limit_name=limit_name,
        metered_feature=metered_feature,
        window=window,
        used_percent=used_percent,
        reset_at=reset_at,
        window_minutes=window_minutes,
        recorded_at=datetime.now(tz=timezone.utc),
    )


class StubAdditionalUsageRepository:
    def __init__(
        self,
        *,
        limit_names: list[str],
        primary: dict[str, dict[str, AdditionalUsageHistory]],
        secondary: dict[str, dict[str, AdditionalUsageHistory]],
    ) -> None:
        self._limit_names = limit_names
        self._primary = primary
        self._secondary = secondary

    async def list_limit_names(self, *, account_ids: list[str] | None = None) -> list[str]:
        return self._limit_names

    async def list_quota_keys(self, *, account_ids: list[str] | None = None) -> list[str]:
        return self._limit_names

    async def latest_by_account(
        self,
        limit_name: str | None = None,
        window: str | None = None,
        *,
        quota_key: str | None = None,
        account_ids: list[str] | None = None,
        since: datetime | None = None,
    ) -> dict[str, AdditionalUsageHistory]:
        effective_key = quota_key or limit_name
        assert effective_key is not None
        assert window is not None
        source = self._secondary if window == "secondary" else self._primary
        rows = dict(source.get(effective_key, {}))
        if account_ids is not None:
            allowed = set(account_ids)
            rows = {account_id: entry for account_id, entry in rows.items() if account_id in allowed}
        if since is not None:
            rows = {account_id: entry for account_id, entry in rows.items() if entry.recorded_at >= since}
        return rows


@pytest.mark.asyncio
async def test_build_additional_rate_limits_aggregates_reset_metadata_deterministically() -> None:
    accounts = {
        "acc-a": _make_account("acc-a", email="a@example.com"),
        "acc-b": _make_account("acc-b", email="b@example.com"),
    }
    additional_usage = StubAdditionalUsageRepository(
        limit_names=["o-pro"],
        primary={
            "o-pro": {
                "acc-a": _entry(
                    1,
                    account_id="acc-a",
                    limit_name="o-pro",
                    window="primary",
                    used_percent=40.0,
                    reset_at=1100,
                    window_minutes=5,
                ),
                "acc-b": _entry(
                    2,
                    account_id="acc-b",
                    limit_name="o-pro",
                    window="primary",
                    used_percent=80.0,
                    reset_at=1300,
                    window_minutes=15,
                ),
            }
        },
        secondary={
            "o-pro": {
                "acc-a": _entry(
                    3,
                    account_id="acc-a",
                    limit_name="o-pro",
                    window="secondary",
                    used_percent=20.0,
                    reset_at=1400,
                    window_minutes=30,
                ),
                "acc-b": _entry(
                    4,
                    account_id="acc-b",
                    limit_name="o-pro",
                    window="secondary",
                    used_percent=60.0,
                    reset_at=1700,
                    window_minutes=45,
                ),
            }
        },
    )

    @asynccontextmanager
    async def repo_factory() -> Any:
        yield ProxyRepositories(
            accounts=object(),  # type: ignore[arg-type]
            usage=object(),  # type: ignore[arg-type]
            request_logs=object(),  # type: ignore[arg-type]
            sticky_sessions=object(),  # type: ignore[arg-type]
            api_keys=object(),  # type: ignore[arg-type]
            additional_usage=additional_usage,  # type: ignore[arg-type]
        )

    service = ProxyService(repo_factory)

    results = await service._build_additional_rate_limits(
        ProxyRepositories(
            accounts=object(),  # type: ignore[arg-type]
            usage=object(),  # type: ignore[arg-type]
            request_logs=object(),  # type: ignore[arg-type]
            sticky_sessions=object(),  # type: ignore[arg-type]
            api_keys=object(),  # type: ignore[arg-type]
            additional_usage=additional_usage,  # type: ignore[arg-type]
        ),
        accounts,
        now_epoch=1000,
    )

    assert len(results) == 1
    limit = results[0]
    assert limit.quota_key == "o-pro"
    assert limit.limit_name == "o-pro"
    assert limit.display_label == "o-pro"
    assert limit.metered_feature == "o_pro"
    assert limit.rate_limit is not None
    assert limit.rate_limit.primary_window is not None
    assert limit.rate_limit.primary_window.limit_window_seconds == 900
    assert limit.rate_limit.primary_window.reset_at == 1100  # min (earliest reset)
    assert limit.rate_limit.primary_window.reset_after_seconds == 100
    assert limit.rate_limit.secondary_window is not None
    assert limit.rate_limit.secondary_window.limit_window_seconds == 2700
    assert limit.rate_limit.secondary_window.reset_at == 1400  # min (earliest reset)
    assert limit.rate_limit.secondary_window.reset_after_seconds == 400
