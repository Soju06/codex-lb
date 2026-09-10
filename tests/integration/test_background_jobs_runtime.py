"""Dashboard-managed background job toggles change scheduler behaviour without a restart (M2).

Each scheduler keeps its loop and reads the ``SettingsCache`` snapshot at the
entry of every cycle; the environment layer stays at its default (on) while the
dashboard column flips, so these tests prove a dashboard value that differs
from the environment is what the next tick honours.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

import app.core.usage.reset_credits_refresh_scheduler as reset_credits_module
import app.modules.automations.scheduler as automations_scheduler_module
from app.core.auth.guardian import AuthGuardianScheduler
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.usage.reset_credits_refresh_scheduler import RateLimitResetCreditsRefreshScheduler
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.automations.repository import AutomationsRepository
from app.modules.automations.scheduler import AutomationsScheduler
from app.modules.automations.service import AutomationsService
from app.modules.request_logs.repository import RequestLogsRepository

pytestmark = pytest.mark.integration


class _AlwaysLeader:
    async def run_if_leader(self, fn: Callable[[], Awaitable[object]]) -> object:
        return await fn()


class _RecordingRepo:
    def __init__(self) -> None:
        self.list_calls = 0

    async def list_accounts(self, *, refresh_existing: bool = False) -> list[Account]:
        self.list_calls += 1
        return []

    async def get_by_id(self, account_id: str) -> Account | None:
        return None


async def _put(async_client, **fields) -> dict:
    response = await async_client.put("/api/settings", json=fields)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_auth_guardian_pass_follows_dashboard_toggle_without_restart(async_client):
    assert get_settings().auth_guardian_enabled is True
    repo = _RecordingRepo()

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[_RecordingRepo]:
        yield repo

    scheduler = AuthGuardianScheduler(
        interval_seconds=3600,
        enabled=True,
        max_age_seconds=3600,
        batch_size=10,
        concurrency=1,
        jitter_seconds=0.0,
        leader_election_factory=lambda: _AlwaysLeader(),
        repo_factory=repo_factory,
    )

    await scheduler._refresh_once()
    assert repo.list_calls == 1

    paused = await _put(async_client, authGuardianEnabled=False)
    assert paused["provenance"]["auth_guardian_enabled"]["source"] == "dashboard"
    await scheduler._refresh_once()
    assert repo.list_calls == 1

    await _put(async_client, authGuardianEnabled=None)
    await scheduler._refresh_once()
    assert repo.list_calls == 2


@pytest.mark.asyncio
async def test_automations_tick_follows_dashboard_pause_without_restart(async_client, monkeypatch):
    assert get_settings().automations_scheduler_enabled is True
    body_runs = 0

    async def _counting_body(self: AutomationsScheduler, dashboard_settings) -> None:
        # The tick threads its pre-lock snapshot into the leader-gated body.
        nonlocal body_runs
        body_runs += 1

    monkeypatch.setattr(automations_scheduler_module, "_get_leader_election", lambda: _AlwaysLeader())
    monkeypatch.setattr(AutomationsScheduler, "_run_due_as_leader", _counting_body)
    scheduler = automations_scheduler_module.build_automations_scheduler()

    await scheduler._run_due_once()
    assert body_runs == 1

    await _put(async_client, automationsSchedulerEnabled=False)
    await scheduler._run_due_once()
    assert body_runs == 1

    await _put(async_client, automationsSchedulerEnabled=True)
    await scheduler._run_due_once()
    assert body_runs == 2


async def _create_account(account_id: str) -> Account:
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        account = Account(
            id=account_id,
            chatgpt_account_id=f"chatgpt-{account_id}",
            email=f"{account_id}@example.com",
            plan_type="plus",
            access_token_encrypted=encryptor.encrypt(f"access-{account_id}"),
            refresh_token_encrypted=encryptor.encrypt(f"refresh-{account_id}"),
            id_token_encrypted=encryptor.encrypt(f"id-{account_id}"),
            last_refresh=utcnow(),
            status=AccountStatus.ACTIVE,
            deactivation_reason=None,
        )
        await AccountsRepository(session).upsert(account)
    return account


@pytest.mark.asyncio
async def test_paused_automations_refuse_run_now_and_dispatch_nothing(async_client, monkeypatch):
    """The "paused" label must not be a lie: manual runs are refused and due jobs are not dispatched."""
    account = await _create_account("auto-paused")
    compact_calls = AsyncMock()
    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", compact_calls)

    created = await async_client.post(
        "/api/automations",
        json={
            "name": "Paused ping",
            "enabled": True,
            "schedule": {"type": "daily", "time": "05:00", "timezone": "UTC", "days": ["mon"]},
            "model": "gpt-5.6-sol",
            "prompt": "ping",
            "accountIds": [account.id],
        },
    )
    assert created.status_code == 200, created.text
    automation_id = created.json()["id"]

    await _put(async_client, automationsSchedulerEnabled=False)

    refused = await async_client.post(f"/api/automations/{automation_id}/run-now")
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "automations_paused"

    async with SessionLocal() as session:
        service = AutomationsService(
            AutomationsRepository(session), AccountsRepository(session), RequestLogsRepository(session)
        )
        assert await service.run_due_jobs() == 0
    compact_calls.assert_not_called()

    runs = await async_client.get(f"/api/automations/{automation_id}/runs")
    assert runs.status_code == 200
    assert runs.json()["items"] == []

    await _put(async_client, automationsSchedulerEnabled=None)
    resumed = await async_client.post(f"/api/automations/{automation_id}/run-now")
    assert resumed.status_code == 202, resumed.text


@pytest.mark.asyncio
async def test_reset_credits_cycle_follows_dashboard_toggle_without_restart(async_client, monkeypatch):
    assert get_settings().rate_limit_reset_credits_refresh_enabled is True
    refresh = AsyncMock()
    monkeypatch.setattr(reset_credits_module, "refresh_reset_credits_for_accounts", refresh)
    scheduler: RateLimitResetCreditsRefreshScheduler = reset_credits_module.build_rate_limit_reset_credits_scheduler()

    await scheduler._refresh_once()
    assert refresh.await_count == 1

    await _put(async_client, rateLimitResetCreditsRefreshEnabled=False)
    await scheduler._refresh_once()
    assert refresh.await_count == 1

    await _put(async_client, rateLimitResetCreditsRefreshEnabled=True)
    await scheduler._refresh_once()
    assert refresh.await_count == 2
