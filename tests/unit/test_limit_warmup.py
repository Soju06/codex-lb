from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.utils.time import utcnow
from app.db.models import Account, AccountLimitWarmup, AccountStatus, DashboardSettings, UsageHistory
from app.modules.limit_warmup.service import LimitWarmupSendResult, LimitWarmupService

pytestmark = pytest.mark.unit


def _account(
    account_id: str = "acc_1", *, enabled: bool = True, status: AccountStatus = AccountStatus.ACTIVE
) -> Account:
    return Account(
        id=account_id,
        chatgpt_account_id="chatgpt_1",
        email=f"{account_id}@example.com",
        plan_type="plus",
        access_token_encrypted=b"access",
        refresh_token_encrypted=b"refresh",
        id_token_encrypted=b"id",
        last_refresh=utcnow(),
        status=status,
        deactivation_reason=None,
        limit_warmup_enabled=enabled,
    )


def _usage(account_id: str, *, used_percent: float, reset_at: int, window: str = "primary") -> UsageHistory:
    return UsageHistory(
        account_id=account_id,
        used_percent=used_percent,
        reset_at=reset_at,
        window=window,
        window_minutes=300 if window == "primary" else 10_080,
        recorded_at=utcnow(),
    )


def _settings(**overrides: object) -> DashboardSettings:
    values: dict[str, object] = {
        "id": 1,
        "limit_warmup_enabled": True,
        "limit_warmup_windows": "primary",
        "limit_warmup_model": "gpt-5.1-codex-mini",
        "limit_warmup_prompt": "Say OK.",
        "limit_warmup_cooldown_seconds": 3600,
        "limit_warmup_min_available_percent": 100.0,
    }
    values.update(overrides)
    return DashboardSettings(**values)


class FakeWarmupRepo:
    def __init__(self) -> None:
        self.rows: list[AccountLimitWarmup] = []
        self.next_id = 1

    async def latest_by_account(self, account_ids: list[str]) -> dict[str, AccountLimitWarmup]:
        result: dict[str, AccountLimitWarmup] = {}
        for row in self.rows:
            if row.account_id in account_ids:
                current = result.get(row.account_id)
                if current is None or row.attempted_at > current.attempted_at:
                    result[row.account_id] = row
        return result

    async def try_create_attempt(
        self,
        *,
        account_id: str,
        window: str,
        reset_at: int,
        model: str,
        attempted_at,
        status: str = "pending",
    ) -> AccountLimitWarmup | None:
        if any(row.account_id == account_id and row.window == window and row.reset_at == reset_at for row in self.rows):
            return None
        row = AccountLimitWarmup(
            id=self.next_id,
            account_id=account_id,
            window=window,
            reset_at=reset_at,
            status=status,
            model=model,
            attempted_at=attempted_at,
        )
        self.next_id += 1
        self.rows.append(row)
        return row

    async def complete_attempt(
        self,
        attempt_id: int,
        *,
        status: str,
        completed_at,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> AccountLimitWarmup | None:
        row = next((item for item in self.rows if item.id == attempt_id), None)
        if row is None:
            return None
        row.status = status
        row.completed_at = completed_at
        row.error_code = error_code
        row.error_message = error_message
        return row


class FakeRequestLogsRepo:
    def __init__(self) -> None:
        self.logs: list[dict[str, object]] = []

    async def add_log(
        self,
        account_id: str | None,
        request_id: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        latency_ms: int | None,
        status: str,
        error_code: str | None,
        latency_first_token_ms: int | None = None,
        error_message: str | None = None,
        requested_at: datetime | None = None,
        cached_input_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        reasoning_effort: str | None = None,
        service_tier: str | None = None,
        requested_service_tier: str | None = None,
        actual_service_tier: str | None = None,
        transport: str | None = None,
        api_key_id: str | None = None,
        session_id: str | None = None,
        plan_type: str | None = None,
        source: str | None = None,
    ) -> None:
        self.logs.append(
            {
                "account_id": account_id,
                "request_id": request_id,
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "latency_ms": latency_ms,
                "status": status,
                "error_code": error_code,
                "latency_first_token_ms": latency_first_token_ms,
                "error_message": error_message,
                "requested_at": requested_at,
                "cached_input_tokens": cached_input_tokens,
                "reasoning_tokens": reasoning_tokens,
                "reasoning_effort": reasoning_effort,
                "service_tier": service_tier,
                "requested_service_tier": requested_service_tier,
                "actual_service_tier": actual_service_tier,
                "transport": transport,
                "api_key_id": api_key_id,
                "session_id": session_id,
                "plan_type": plan_type,
                "source": source,
            }
        )


class FakeSender:
    def __init__(self, *, success: bool = True, error_code: str | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.success = success
        self.error_code = error_code

    async def send(self, account: Account, *, model: str, prompt: str) -> LimitWarmupSendResult:
        self.calls.append((account.id, model))
        return LimitWarmupSendResult(
            request_id=f"warmup-{len(self.calls)}",
            success=self.success,
            latency_ms=12,
            error_code=self.error_code,
            error_message="failed" if self.error_code else None,
        )


@pytest.mark.asyncio
async def test_reset_confirmed_candidate_sends_one_warmup() -> None:
    repo = FakeWarmupRepo()
    logs = FakeRequestLogsRepo()
    sender = FakeSender()
    service = LimitWarmupService(repo, logs, sender=sender)
    account = _account()

    await service.run_after_usage_refresh(
        accounts=[account],
        settings=_settings(),
        before_primary={account.id: _usage(account.id, used_percent=100, reset_at=1000)},
        before_secondary={},
        after_primary={account.id: _usage(account.id, used_percent=0, reset_at=2000)},
        after_secondary={},
    )
    await service.run_after_usage_refresh(
        accounts=[account],
        settings=_settings(),
        before_primary={account.id: _usage(account.id, used_percent=100, reset_at=1000)},
        before_secondary={},
        after_primary={account.id: _usage(account.id, used_percent=0, reset_at=2000)},
        after_secondary={},
    )

    assert len(sender.calls) == 1
    assert len(repo.rows) == 1
    assert repo.rows[0].status == "succeeded"
    assert logs.logs[0]["source"] == "limit_warmup"


@pytest.mark.asyncio
async def test_disabled_or_account_opt_out_does_not_send() -> None:
    repo = FakeWarmupRepo()
    sender = FakeSender()
    service = LimitWarmupService(repo, FakeRequestLogsRepo(), sender=sender)
    account = _account(enabled=False)

    await service.run_after_usage_refresh(
        accounts=[account],
        settings=_settings(limit_warmup_enabled=True),
        before_primary={account.id: _usage(account.id, used_percent=100, reset_at=1000)},
        before_secondary={},
        after_primary={account.id: _usage(account.id, used_percent=0, reset_at=2000)},
        after_secondary={},
    )
    account.limit_warmup_enabled = True
    await service.run_after_usage_refresh(
        accounts=[account],
        settings=_settings(limit_warmup_enabled=False),
        before_primary={account.id: _usage(account.id, used_percent=100, reset_at=1000)},
        before_secondary={},
        after_primary={account.id: _usage(account.id, used_percent=0, reset_at=2000)},
        after_secondary={},
    )

    assert sender.calls == []
    assert repo.rows == []


@pytest.mark.asyncio
async def test_min_available_threshold_uses_available_quota_percent() -> None:
    repo = FakeWarmupRepo()
    sender = FakeSender()
    service = LimitWarmupService(repo, FakeRequestLogsRepo(), sender=sender)
    eligible_account = _account("acc_available_enough")
    below_threshold_account = _account("acc_available_low")

    await service.run_after_usage_refresh(
        accounts=[eligible_account, below_threshold_account],
        settings=_settings(limit_warmup_min_available_percent=20.0),
        before_primary={
            eligible_account.id: _usage(eligible_account.id, used_percent=100, reset_at=1000),
            below_threshold_account.id: _usage(below_threshold_account.id, used_percent=100, reset_at=1000),
        },
        before_secondary={},
        after_primary={
            eligible_account.id: _usage(eligible_account.id, used_percent=75, reset_at=2000),
            below_threshold_account.id: _usage(below_threshold_account.id, used_percent=85, reset_at=2000),
        },
        after_secondary={},
    )

    assert sender.calls == [(eligible_account.id, "gpt-5.1-codex-mini")]
    assert [row.account_id for row in repo.rows] == [eligible_account.id]


@pytest.mark.asyncio
async def test_unsafe_account_state_does_not_send() -> None:
    repo = FakeWarmupRepo()
    sender = FakeSender()
    service = LimitWarmupService(repo, FakeRequestLogsRepo(), sender=sender)
    account = _account(status=AccountStatus.PAUSED)

    await service.run_after_usage_refresh(
        accounts=[account],
        settings=_settings(),
        before_primary={account.id: _usage(account.id, used_percent=100, reset_at=1000)},
        before_secondary={},
        after_primary={account.id: _usage(account.id, used_percent=0, reset_at=2000)},
        after_secondary={},
    )

    assert sender.calls == []
    assert repo.rows == []


@pytest.mark.asyncio
async def test_auto_model_unavailable_records_skipped_attempt(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.modules.limit_warmup.service.get_model_registry",
        lambda: SimpleNamespace(get_models_with_fallback=lambda: {}),
    )
    repo = FakeWarmupRepo()
    sender = FakeSender()
    service = LimitWarmupService(repo, FakeRequestLogsRepo(), sender=sender)
    account = _account()

    await service.run_after_usage_refresh(
        accounts=[account],
        settings=_settings(limit_warmup_model="auto"),
        before_primary={account.id: _usage(account.id, used_percent=100, reset_at=1000)},
        before_secondary={},
        after_primary={account.id: _usage(account.id, used_percent=0, reset_at=2000)},
        after_secondary={},
    )

    assert sender.calls == []
    assert len(repo.rows) == 1
    assert repo.rows[0].status == "skipped"
    assert repo.rows[0].error_code == "model_unavailable"


@pytest.mark.asyncio
async def test_recent_attempt_cooldown_blocks_new_reset() -> None:
    repo = FakeWarmupRepo()
    account = _account()
    repo.rows.append(
        AccountLimitWarmup(
            id=1,
            account_id=account.id,
            window="primary",
            reset_at=1000,
            status="failed",
            model="gpt-5.1-codex-mini",
            attempted_at=utcnow() - timedelta(minutes=10),
        )
    )
    sender = FakeSender()
    service = LimitWarmupService(repo, FakeRequestLogsRepo(), sender=sender)

    await service.run_after_usage_refresh(
        accounts=[account],
        settings=_settings(limit_warmup_cooldown_seconds=3600),
        before_primary={account.id: _usage(account.id, used_percent=100, reset_at=2000)},
        before_secondary={},
        after_primary={account.id: _usage(account.id, used_percent=0, reset_at=3000)},
        after_secondary={},
    )

    assert sender.calls == []
    assert len(repo.rows) == 1
