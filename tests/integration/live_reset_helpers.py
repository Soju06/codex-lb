from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta

import pytest

from app.core.crypto import TokenEncryptor
from app.core.usage import refresh_scheduler
from app.core.usage.live_snapshots import LiveRateLimitSnapshot, LiveUsageWindow
from app.core.usage.models import RateLimitPayload, UsagePayload, UsageWindow
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.limit_warmup.repository import LimitWarmupRepository
from app.modules.limit_warmup.service import LimitWarmupSendResult
from app.modules.settings.repository import SettingsRepository
from app.modules.usage.live_ingest import LiveUsageIngestor
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository


async def run_scheduler_ticks(monkeypatch: pytest.MonkeyPatch, *, workers: int = 1) -> None:
    finished: asyncio.Queue[None] = asyncio.Queue()

    class Leader:
        async def run_if_leader(self, fn: Callable[[], Awaitable[float]]) -> float:
            try:
                return await fn()
            finally:
                finished.put_nowait(None)

    monkeypatch.setattr(refresh_scheduler, "_get_leader_election", Leader)
    schedulers = [refresh_scheduler.UsageRefreshScheduler(interval_seconds=60, enabled=True) for _ in range(workers)]
    for scheduler in schedulers:
        await scheduler.start()
    try:
        async with asyncio.timeout(5):
            for _ in schedulers:
                await finished.get()
    finally:
        await asyncio.gather(*(scheduler.stop() for scheduler in schedulers))


async def publish_and_wait(
    ingestor: LiveUsageIngestor, snapshot: LiveRateLimitSnapshot, account_id: str, window: str
) -> None:
    async with SessionLocal() as session:
        previous = (await UsageRepository(session).latest_by_account(window)).get(account_id)
    ingestor.publish(snapshot, account_id=account_id)
    async with asyncio.timeout(5):
        while True:
            async with SessionLocal() as session:
                latest = (await UsageRepository(session).latest_by_account(window)).get(account_id)
            if latest is not None and (previous is None or latest.id != previous.id):
                return
            await asyncio.sleep(0.001)


async def prepare_live_reset(
    window: str, *, prior_status: str | None = None, baseline: str = "reset"
) -> tuple[Account, LiveRateLimitSnapshot, int]:
    encryptor = TokenEncryptor()
    account = Account(
        id="live-reset",
        chatgpt_account_id="workspace-live-reset",
        email="live-reset@example.com",
        plan_type="free" if window == "monthly" else "plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        limit_warmup_enabled=True,
    )
    now = int(time.time())
    window_minutes = {"primary": 300, "secondary": 10_080, "monthly": 43_200}[window]
    reset_at = now + window_minutes * 60
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(account)
        if baseline != "missing":
            await UsageRepository(session).add_entry(
                account.id,
                51.0,
                window=window,
                recorded_at=utcnow() - timedelta(minutes=2),
                reset_at=reset_at - 5 if baseline == "jitter" else now - 1,
                window_minutes=window_minutes,
            )
        await AdditionalUsageRepository(session).add_entry(
            account.id,
            "codex",
            "codex",
            "secondary",
            0.0,
            reset_at=now + 604_800,
            window_minutes=10_080,
            recorded_at=utcnow(),
            quota_key="codex",
        )
        await SettingsRepository(session).update(
            limit_warmup_enabled=True,
            limit_warmup_windows="primary" if window == "primary" else "secondary",
            limit_warmup_model="gpt-5.1-codex-mini",
            limit_warmup_staggered_idle_enabled=False,
        )
        if prior_status is not None:
            await LimitWarmupRepository(session).try_create_attempt(
                account_id=account.id,
                window=window,
                reset_at=reset_at,
                model="gpt-5.1-codex-mini",
                attempted_at=utcnow(),
                status=prior_status,
            )

    snapshot = LiveRateLimitSnapshot(
        primary=LiveUsageWindow(
            0.0, window_minutes if window == "monthly" else 300, reset_at if window != "secondary" else now + 18_000
        ),
        secondary=None
        if window == "monthly"
        else LiveUsageWindow(0.0, 10_080, reset_at if window == "secondary" else now + 604_800),
        credits_has=None,
        credits_unlimited=None,
        credits_balance=None,
    )
    return account, snapshot, reset_at


def usage_payload(snapshot: LiveRateLimitSnapshot, plan_type: str) -> UsagePayload:
    primary = snapshot.primary
    secondary = snapshot.secondary
    return UsagePayload(
        plan_type=plan_type,
        rate_limit=RateLimitPayload(
            primary_window=UsageWindow(
                used_percent=primary.used_percent,
                reset_at=primary.reset_at,
                limit_window_seconds=(primary.window_minutes or 300) * 60,
            )
            if primary
            else None,
            secondary_window=UsageWindow(
                used_percent=secondary.used_percent,
                reset_at=secondary.reset_at,
                limit_window_seconds=(secondary.window_minutes or 10_080) * 60,
            )
            if secondary
            else None,
        ),
    )


def record_warmup_sends(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    sends: list[str] = []

    class Sender:
        async def send(self, target: Account, *, model: str, prompt: str) -> LimitWarmupSendResult:
            sends.append(target.id)
            return LimitWarmupSendResult(request_id="reset-probe", success=True, latency_ms=1)

    monkeypatch.setattr(refresh_scheduler, "StreamingLimitWarmupSender", lambda *args, **kwargs: Sender())
    return sends
