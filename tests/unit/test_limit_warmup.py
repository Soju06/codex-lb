from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.utils.time import utcnow
from app.db.models import Account, AccountLimitWarmup, AccountStatus, DashboardSettings, UsageHistory
from app.modules.limit_warmup import service as limit_warmup_service
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


class FailingCompletionWarmupRepo(FakeWarmupRepo):
    def __init__(self, *, fail_attempt_id: int) -> None:
        super().__init__()
        self.fail_attempt_id = fail_attempt_id
        self.failed_once = False

    async def complete_attempt(
        self,
        attempt_id: int,
        *,
        status: str,
        completed_at,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> AccountLimitWarmup | None:
        if attempt_id == self.fail_attempt_id and not self.failed_once:
            self.failed_once = True
            raise RuntimeError("completion failed")
        return await super().complete_attempt(
            attempt_id,
            status=status,
            completed_at=completed_at,
            error_code=error_code,
            error_message=error_message,
        )


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


class TrackingSender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.active = 0
        self.max_active = 0
        self.release = asyncio.Event()

    async def send(self, account: Account, *, model: str, prompt: str) -> LimitWarmupSendResult:
        self.calls.append((account.id, model))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        if self.max_active >= 4:
            self.release.set()
        await self.release.wait()
        await asyncio.sleep(0)
        self.active -= 1
        return LimitWarmupSendResult(
            request_id=f"warmup-{len(self.calls)}",
            success=True,
            latency_ms=12,
        )


class BlockingSecondSender:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.block = asyncio.Event()

    async def send(self, account: Account, *, model: str, prompt: str) -> LimitWarmupSendResult:
        self.calls.append(account.id)
        if account.id == "acc_2":
            await self.block.wait()
        return LimitWarmupSendResult(
            request_id=f"warmup-{account.id}",
            success=True,
            latency_ms=12,
        )


class CoordinatedSender:
    def __init__(self, expected: int) -> None:
        self.expected = expected
        self.started = 0
        self.release = asyncio.Event()

    async def send(self, account: Account, *, model: str, prompt: str) -> LimitWarmupSendResult:
        self.started += 1
        if self.started >= self.expected:
            self.release.set()
        await self.release.wait()
        return LimitWarmupSendResult(
            request_id=f"warmup-{account.id}",
            success=True,
            latency_ms=12,
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
async def test_warmup_sends_use_bounded_concurrency() -> None:
    repo = FakeWarmupRepo()
    logs = FakeRequestLogsRepo()
    sender = TrackingSender()
    service = LimitWarmupService(repo, logs, sender=sender)
    accounts = [_account(f"acc_{index}") for index in range(6)]

    await service.run_after_usage_refresh(
        accounts=accounts,
        settings=_settings(),
        before_primary={account.id: _usage(account.id, used_percent=100, reset_at=1000) for account in accounts},
        before_secondary={},
        after_primary={account.id: _usage(account.id, used_percent=0, reset_at=2000) for account in accounts},
        after_secondary={},
    )

    assert len(sender.calls) == 6
    assert sender.max_active == 4
    assert len(logs.logs) == 6
    assert [row.status for row in repo.rows] == ["succeeded"] * 6


@pytest.mark.asyncio
async def test_warmup_completion_failure_cancels_pending_sends() -> None:
    repo = FailingCompletionWarmupRepo(fail_attempt_id=1)
    sender = BlockingSecondSender()
    service = LimitWarmupService(repo, FakeRequestLogsRepo(), sender=sender)
    accounts = [_account("acc_1"), _account("acc_2")]

    with pytest.raises(RuntimeError, match="completion failed"):
        await service.run_after_usage_refresh(
            accounts=accounts,
            settings=_settings(),
            before_primary={account.id: _usage(account.id, used_percent=100, reset_at=1000) for account in accounts},
            before_secondary={},
            after_primary={account.id: _usage(account.id, used_percent=0, reset_at=2000) for account in accounts},
            after_secondary={},
        )

    assert [row.status for row in repo.rows] == ["failed", "failed"]
    assert repo.rows[0].error_code == "warmup_completion_failed"
    assert repo.rows[1].error_code == "warmup_cancelled"


@pytest.mark.asyncio
async def test_warmup_completion_failure_finalizes_same_batch_sends() -> None:
    repo = FailingCompletionWarmupRepo(fail_attempt_id=1)
    sender = CoordinatedSender(expected=2)
    service = LimitWarmupService(repo, FakeRequestLogsRepo(), sender=sender)
    accounts = [_account("acc_1"), _account("acc_2")]

    with pytest.raises(RuntimeError, match="completion failed"):
        await service.run_after_usage_refresh(
            accounts=accounts,
            settings=_settings(),
            before_primary={account.id: _usage(account.id, used_percent=100, reset_at=1000) for account in accounts},
            before_secondary={},
            after_primary={account.id: _usage(account.id, used_percent=0, reset_at=2000) for account in accounts},
            after_secondary={},
        )

    statuses_by_account = {row.account_id: row.status for row in repo.rows}
    assert statuses_by_account == {"acc_1": "failed", "acc_2": "succeeded"}
    failed = next(row for row in repo.rows if row.account_id == "acc_1")
    assert failed.error_code == "warmup_completion_failed"


@pytest.mark.asyncio
async def test_warmup_completion_failure_drains_finished_pending_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = FailingCompletionWarmupRepo(fail_attempt_id=1)
    sender = FakeSender()
    service = LimitWarmupService(repo, FakeRequestLogsRepo(), sender=sender)
    accounts = [_account("acc_1"), _account("acc_2")]

    async def wait_with_finished_pending(
        tasks,
        *,
        return_when,
    ):
        await asyncio.sleep(0)
        failed_task = {task for task in tasks if task.get_name() == "limit-warmup:1"}
        assert failed_task
        pending = set(tasks) - failed_task
        assert pending
        assert all(task.done() for task in pending)
        return failed_task, pending

    monkeypatch.setattr(limit_warmup_service.asyncio, "wait", wait_with_finished_pending)

    with pytest.raises(RuntimeError, match="completion failed"):
        await service.run_after_usage_refresh(
            accounts=accounts,
            settings=_settings(),
            before_primary={account.id: _usage(account.id, used_percent=100, reset_at=1000) for account in accounts},
            before_secondary={},
            after_primary={account.id: _usage(account.id, used_percent=0, reset_at=2000) for account in accounts},
            after_secondary={},
        )

    statuses_by_account = {row.account_id: row.status for row in repo.rows}
    assert statuses_by_account == {"acc_1": "failed", "acc_2": "succeeded"}


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
async def test_default_available_threshold_accepts_nonzero_reset_usage() -> None:
    repo = FakeWarmupRepo()
    sender = FakeSender()
    service = LimitWarmupService(repo, FakeRequestLogsRepo(), sender=sender)
    account = _account()

    await service.run_after_usage_refresh(
        accounts=[account],
        settings=_settings(),
        before_primary={account.id: _usage(account.id, used_percent=100, reset_at=1000)},
        before_secondary={},
        after_primary={account.id: _usage(account.id, used_percent=1, reset_at=2000)},
        after_secondary={},
    )

    assert len(sender.calls) == 1
    assert len(repo.rows) == 1


@pytest.mark.asyncio
async def test_min_available_quota_threshold_uses_remaining_percent() -> None:
    repo = FakeWarmupRepo()
    sender = FakeSender()
    service = LimitWarmupService(repo, FakeRequestLogsRepo(), sender=sender)
    account = _account()

    await service.run_after_usage_refresh(
        accounts=[account],
        settings=_settings(limit_warmup_min_available_percent=99.0),
        before_primary={account.id: _usage(account.id, used_percent=100, reset_at=1000)},
        before_secondary={},
        after_primary={account.id: _usage(account.id, used_percent=98, reset_at=2000)},
        after_secondary={},
    )
    await service.run_after_usage_refresh(
        accounts=[account],
        settings=_settings(limit_warmup_min_available_percent=99.0),
        before_primary={account.id: _usage(account.id, used_percent=100, reset_at=1000)},
        before_secondary={},
        after_primary={account.id: _usage(account.id, used_percent=1, reset_at=2000)},
        after_secondary={},
    )

    assert len(sender.calls) == 1
    assert len(repo.rows) == 1


@pytest.mark.asyncio
async def test_both_selected_windows_warm_primary_and_secondary_resets() -> None:
    repo = FakeWarmupRepo()
    sender = FakeSender()
    service = LimitWarmupService(repo, FakeRequestLogsRepo(), sender=sender)
    account = _account()

    await service.run_after_usage_refresh(
        accounts=[account],
        settings=_settings(limit_warmup_windows="both"),
        before_primary={account.id: _usage(account.id, used_percent=100, reset_at=1000)},
        before_secondary={account.id: _usage(account.id, used_percent=100, reset_at=10_000, window="secondary")},
        after_primary={account.id: _usage(account.id, used_percent=0, reset_at=2000)},
        after_secondary={account.id: _usage(account.id, used_percent=0, reset_at=20_000, window="secondary")},
    )

    assert sender.calls == [(account.id, "gpt-5.1-codex-mini"), (account.id, "gpt-5.1-codex-mini")]
    assert [(row.window, row.reset_at, row.status) for row in repo.rows] == [
        ("primary", 2000, "succeeded"),
        ("secondary", 20_000, "succeeded"),
    ]


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
