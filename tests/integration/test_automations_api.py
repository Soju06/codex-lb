from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import update

from app.core.clients.proxy import ProxyResponseError
from app.core.crypto import TokenEncryptor
from app.core.errors import openai_error
from app.core.utils.time import naive_utc_to_epoch, utcnow
from app.db.models import Account, AccountStatus, AutomationJob, AutomationRun
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.automations.repository import AutomationsRepository
from app.modules.automations.service import AutomationsService, _scheduled_slot_key
from app.modules.request_logs.repository import RequestLogsRepository

pytestmark = pytest.mark.integration


async def _create_accounts(*account_ids: str) -> list[Account]:
    encryptor = TokenEncryptor()
    accounts: list[Account] = []
    async with SessionLocal() as session:
        repository = AccountsRepository(session)
        for account_id in account_ids:
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
            await repository.upsert(account)
            accounts.append(account)
    return accounts


async def _set_account_status(account_id: str, status: AccountStatus) -> None:
    async with SessionLocal() as session:
        repository = AccountsRepository(session)
        updated = await repository.update_status(account_id, status)
        assert updated is True


async def _set_account_status_with_reset(
    account_id: str,
    status: AccountStatus,
    *,
    reset_at: int | None,
    blocked_at: int | None = None,
) -> None:
    async with SessionLocal() as session:
        repository = AccountsRepository(session)
        updated = await repository.update_status(
            account_id,
            status,
            reset_at=reset_at,
            blocked_at=blocked_at,
        )
        assert updated is True


async def _run_due_jobs(*, now_utc: datetime | None = None) -> int:
    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        request_logs_repository = RequestLogsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository, request_logs_repository)
        return await service.run_due_jobs(now_utc=now_utc)


async def _set_job_updated_at(job_id: str, updated_at: datetime) -> None:
    async with SessionLocal() as session:
        await session.execute(update(AutomationJob).where(AutomationJob.id == job_id).values(updated_at=updated_at))
        await session.commit()


@pytest.mark.asyncio
async def test_automations_api_crud(async_client):
    accounts = await _create_accounts("auto-a", "auto-b")

    create_response = await async_client.post(
        "/api/automations",
        json={
            "name": "Daily ping",
            "enabled": True,
            "schedule": {
                "type": "daily",
                "time": "05:00",
                "timezone": "Europe/Warsaw",
                "thresholdMinutes": 11,
                "days": ["mon", "tue", "wed", "thu", "fri"],
            },
            "model": "gpt-5.3-codex",
            "prompt": "ping",
            "accountIds": [accounts[0].id, accounts[1].id],
        },
    )
    assert create_response.status_code == 200
    created = create_response.json()
    assert created["name"] == "Daily ping"
    assert created["schedule"]["time"] == "05:00"
    assert created["schedule"]["timezone"] == "Europe/Warsaw"
    assert created["schedule"]["thresholdMinutes"] == 11
    assert created["schedule"]["days"] == ["mon", "tue", "wed", "thu", "fri"]
    assert created["model"] == "gpt-5.3-codex"
    assert created["includePausedAccounts"] is False
    assert created["accountIds"] == [accounts[0].id, accounts[1].id]
    assert created["nextRunAt"] is not None
    automation_id = created["id"]

    list_response = await async_client.get("/api/automations")
    assert list_response.status_code == 200
    listed = list_response.json()
    assert listed["total"] == 1
    assert listed["hasMore"] is False
    assert len(listed["items"]) == 1
    assert listed["items"][0]["id"] == automation_id

    list_filtered = await async_client.get("/api/automations?search=smoke&status=enabled&limit=10&offset=0")
    assert list_filtered.status_code == 200
    filtered_payload = list_filtered.json()
    assert filtered_payload["total"] == 0
    assert filtered_payload["items"] == []

    options_response = await async_client.get("/api/automations/options")
    assert options_response.status_code == 200
    options_payload = options_response.json()
    assert "enabled" in options_payload["statuses"]
    assert "gpt-5.3-codex" in options_payload["models"]
    enabled_only_options = await async_client.get("/api/automations/options?status=enabled")
    assert enabled_only_options.status_code == 200
    enabled_only_payload = enabled_only_options.json()
    assert enabled_only_payload["statuses"] == ["enabled"]
    disabled_only_options = await async_client.get("/api/automations/options?status=disabled")
    assert disabled_only_options.status_code == 200
    disabled_only_payload = disabled_only_options.json()
    assert disabled_only_payload["statuses"] == []

    update_response = await async_client.patch(
        f"/api/automations/{automation_id}",
        json={
            "enabled": False,
            "prompt": "health-check",
            "accountIds": [accounts[1].id],
        },
    )
    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["enabled"] is False
    assert updated["includePausedAccounts"] is False
    assert updated["prompt"] == "health-check"
    assert updated["accountIds"] == [accounts[1].id]
    assert updated["nextRunAt"] is None

    runs_response = await async_client.get("/api/automations/runs?limit=10&offset=0")
    assert runs_response.status_code == 200
    runs_payload = runs_response.json()
    assert runs_payload["total"] >= 0
    assert runs_payload["hasMore"] in {True, False}

    runs_options_response = await async_client.get("/api/automations/runs/options")
    assert runs_options_response.status_code == 200

    delete_response = await async_client.delete(f"/api/automations/{automation_id}")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"status": "deleted"}

    list_after_delete = await async_client.get("/api/automations")
    assert list_after_delete.status_code == 200
    assert list_after_delete.json()["items"] == []


@pytest.mark.asyncio
async def test_automations_api_accepts_server_default_timezone(async_client, monkeypatch):
    accounts = await _create_accounts("auto-server-default")
    started_at = utcnow()

    async def _fake_compact(*_args, **_kwargs):
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    create_response = await async_client.post(
        "/api/automations",
        json={
            "name": "Server TZ ping",
            "enabled": True,
            "schedule": {
                "type": "daily",
                "time": "05:00",
                "timezone": "server_default",
                "days": ["mon", "tue", "wed", "thu", "fri"],
            },
            "model": "gpt-5.3-codex",
            "prompt": "ping",
            "accountIds": [],
        },
    )
    assert create_response.status_code == 200
    created = create_response.json()
    assert created["schedule"]["timezone"] == "server_default"
    assert created["accountIds"] == []
    assert created["nextRunAt"] is not None

    run_response = await async_client.post(f"/api/automations/{created['id']}/run-now")
    assert run_response.status_code == 202
    run_payload = run_response.json()
    assert run_payload["status"] == "running"
    assert run_payload["effectiveStatus"] == "running"
    assert run_payload["accountId"] == accounts[0].id

    executed = await _run_due_jobs(now_utc=utcnow() + timedelta(seconds=5))
    assert executed >= 1

    async with SessionLocal() as session:
        request_logs_repository = RequestLogsRepository(session)
        recent_logs, _ = await request_logs_repository.list_recent(limit=200, since=started_at)
        matching_logs = [
            log
            for log in recent_logs
            if log.transport == "automation" and log.account_id == accounts[0].id and log.model == "gpt-5.3-codex"
        ]
        assert matching_logs
        assert matching_logs[0].status == "success"


@pytest.mark.asyncio
async def test_automations_api_rejects_all_accounts_mode_without_accounts(async_client):
    create_response = await async_client.post(
        "/api/automations",
        json={
            "name": "All accounts ping",
            "enabled": True,
            "schedule": {
                "type": "daily",
                "time": "05:00",
                "timezone": "UTC",
                "days": ["mon", "tue", "wed", "thu", "fri"],
            },
            "model": "gpt-5.3-codex",
            "prompt": "ping",
            "accountIds": [],
        },
    )
    assert create_response.status_code == 400
    payload = create_response.json()
    assert payload["error"]["code"] == "invalid_account_ids"


@pytest.mark.asyncio
async def test_automations_jobs_accounts_filter_and_options_include_all_accounts_jobs(async_client):
    accounts = await _create_accounts("auto-all-filter-a", "auto-all-filter-b")
    create_response = await async_client.post(
        "/api/automations",
        json={
            "name": "All accounts filtered",
            "enabled": True,
            "schedule": {
                "type": "daily",
                "time": "05:00",
                "timezone": "UTC",
                "days": ["mon", "tue", "wed", "thu", "fri"],
            },
            "model": "gpt-5.3-codex",
            "prompt": "ping",
            "accountIds": [],
        },
    )
    assert create_response.status_code == 200

    options_response = await async_client.get("/api/automations/options")
    assert options_response.status_code == 200
    options_payload = options_response.json()
    assert accounts[0].id in options_payload["accountIds"]
    assert accounts[1].id in options_payload["accountIds"]

    filtered_response = await async_client.get(
        "/api/automations",
        params={"accountId": [accounts[0].id], "limit": 25, "offset": 0},
    )
    assert filtered_response.status_code == 200
    filtered_payload = filtered_response.json()
    assert filtered_payload["total"] == 1
    assert filtered_payload["items"][0]["name"] == "All accounts filtered"


@pytest.mark.asyncio
async def test_automations_runs_options_include_all_accounts(async_client):
    accounts = await _create_accounts("auto-runs-options-a", "auto-runs-options-b")
    options_response = await async_client.get("/api/automations/runs/options")
    assert options_response.status_code == 200
    options_payload = options_response.json()
    assert accounts[0].id in options_payload["accountIds"]
    assert accounts[1].id in options_payload["accountIds"]


@pytest.mark.asyncio
async def test_automations_runs_options_respect_status_filter(async_client):
    accounts = await _create_accounts("auto-runs-failed-a", "auto-runs-failed-b")
    now = utcnow().replace(second=0, microsecond=0)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)
        success_job = await automations_repository.create_job(
            name="Success only",
            enabled=False,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time="05:00",
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=0,
            model="gpt-success",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[accounts[0].id],
        )
        failed_job = await automations_repository.create_job(
            name="Failed only",
            enabled=False,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time="05:00",
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=0,
            model="gpt-failed",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[accounts[1].id],
        )
        success_run = await automations_repository.claim_run(
            job_id=success_job.id,
            trigger="manual",
            slot_key=f"manual:{success_job.id}:cycle-success:digest-a",
            cycle_key=f"manual:{success_job.id}:cycle-success",
            cycle_expected_accounts=1,
            cycle_window_end=now,
            scheduled_for=now,
            started_at=now,
            account_id=accounts[0].id,
        )
        failed_run = await automations_repository.claim_run(
            job_id=failed_job.id,
            trigger="manual",
            slot_key=f"manual:{failed_job.id}:cycle-failed:digest-b",
            cycle_key=f"manual:{failed_job.id}:cycle-failed",
            cycle_expected_accounts=1,
            cycle_window_end=now,
            scheduled_for=now,
            started_at=now,
            account_id=accounts[1].id,
        )
        assert success_run is not None
        assert failed_run is not None
        await automations_repository.complete_run(
            success_run.id,
            status="success",
            finished_at=now + timedelta(seconds=5),
            account_id=accounts[0].id,
            error_code=None,
            error_message=None,
            attempt_count=1,
        )
        await automations_repository.complete_run(
            failed_run.id,
            status="failed",
            finished_at=now + timedelta(seconds=5),
            account_id=accounts[1].id,
            error_code="boom",
            error_message="boom",
            attempt_count=1,
        )

        failed_options = await service.list_run_filter_options(statuses=["failed"])

    assert failed_options.account_ids == [accounts[1].id]
    assert failed_options.models == ["gpt-failed"]

    options_response = await async_client.get("/api/automations/runs/options", params={"status": "failed"})
    assert options_response.status_code == 200
    options_payload = options_response.json()
    assert options_payload["accountIds"] == [accounts[1].id]
    assert options_payload["models"] == ["gpt-failed"]


@pytest.mark.asyncio
async def test_automations_run_now_fails_over_to_next_account(async_client, monkeypatch):
    accounts = await _create_accounts("auto-fallback-a", "auto-fallback-b")
    call_order: list[str | None] = []

    async def _fake_compact(*_args, **kwargs):
        account_id = kwargs.get("account_id")
        call_order.append(account_id)
        if len(call_order) == 1:
            raise ProxyResponseError(
                429,
                openai_error("usage_limit_reached", "The usage limit has been reached"),
            )
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    create_response = await async_client.post(
        "/api/automations",
        json={
            "name": "Failover ping",
            "enabled": False,
            "schedule": {
                "type": "daily",
                "time": "05:00",
                "timezone": "UTC",
                "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            },
            "model": "gpt-5.3-codex",
            "prompt": "ping",
            "accountIds": [accounts[0].id, accounts[1].id],
        },
    )
    assert create_response.status_code == 200
    automation_id = create_response.json()["id"]

    run_response = await async_client.post(f"/api/automations/{automation_id}/run-now")
    assert run_response.status_code == 202
    run_payload = run_response.json()
    assert run_payload["trigger"] == "manual"
    assert run_payload["status"] == "running"
    assert run_payload["effectiveStatus"] == "running"
    assert run_payload["attemptCount"] == 0
    assert run_payload["accountId"] == accounts[1].id
    assert run_payload["errorCode"] is None
    assert run_payload["errorMessage"] is None

    executed = await _run_due_jobs(now_utc=utcnow() + timedelta(seconds=5))
    assert executed >= 2

    runs_response = await async_client.get(f"/api/automations/{automation_id}/runs")
    assert runs_response.status_code == 200
    runs_payload = runs_response.json()["items"]
    assert len(runs_payload) == 2
    statuses = {entry["status"] for entry in runs_payload}
    assert statuses == {"failed", "success"}

    grouped_response = await async_client.get(
        "/api/automations/runs",
        params={"automationId": automation_id, "trigger": "manual", "limit": 25, "offset": 0},
    )
    assert grouped_response.status_code == 200
    grouped_payload = grouped_response.json()
    assert grouped_payload["total"] == 1
    grouped_item = grouped_payload["items"][0]
    assert grouped_item["effectiveStatus"] == "partial"
    assert grouped_item["totalAccounts"] == 2
    assert grouped_item["completedAccounts"] == 2
    assert grouped_item["pendingAccounts"] == 0

    grouped_partial_response = await async_client.get(
        "/api/automations/runs",
        params={
            "automationId": automation_id,
            "trigger": "manual",
            "status": "partial",
            "limit": 25,
            "offset": 0,
        },
    )
    assert grouped_partial_response.status_code == 200
    grouped_partial_payload = grouped_partial_response.json()
    assert grouped_partial_payload["total"] == 1
    assert grouped_partial_payload["items"][0]["effectiveStatus"] == "partial"

    grouped_partial_for_account_response = await async_client.get(
        "/api/automations/runs",
        params={
            "automationId": automation_id,
            "trigger": "manual",
            "accountId": accounts[1].id,
            "status": "partial",
            "limit": 25,
            "offset": 0,
        },
    )
    assert grouped_partial_for_account_response.status_code == 200
    grouped_partial_for_account_payload = grouped_partial_for_account_response.json()
    assert grouped_partial_for_account_payload["total"] == 1
    filtered_grouped_item = grouped_partial_for_account_payload["items"][0]
    assert filtered_grouped_item["effectiveStatus"] == "partial"
    assert filtered_grouped_item["totalAccounts"] == 2
    assert filtered_grouped_item["completedAccounts"] == 2
    assert filtered_grouped_item["pendingAccounts"] == 0

    grouped_success_response = await async_client.get(
        "/api/automations/runs",
        params={
            "automationId": automation_id,
            "trigger": "manual",
            "status": "success",
            "limit": 25,
            "offset": 0,
        },
    )
    assert grouped_success_response.status_code == 200
    grouped_success_payload = grouped_success_response.json()
    assert grouped_success_payload["total"] == 0

    options_with_status_response = await async_client.get(
        "/api/automations/runs/options",
        params={
            "automationId": automation_id,
            "trigger": "manual",
            "status": "partial",
        },
    )
    assert options_with_status_response.status_code == 200
    options_with_status_payload = options_with_status_response.json()
    assert accounts[0].id in options_with_status_payload["accountIds"]
    assert accounts[1].id in options_with_status_payload["accountIds"]
    assert len(call_order) == 2
    assert set(call_order) == {f"chatgpt-{accounts[0].id}", f"chatgpt-{accounts[1].id}"}


@pytest.mark.asyncio
async def test_automations_run_now_all_accounts_executes_all_accounts(async_client, monkeypatch):
    accounts = await _create_accounts("auto-manual-all-a", "auto-manual-all-b", "auto-manual-all-c")
    started_at = utcnow()

    async def _fake_compact(*_args, **_kwargs):
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    create_response = await async_client.post(
        "/api/automations",
        json={
            "name": "Manual all accounts",
            "enabled": False,
            "schedule": {
                "type": "daily",
                "time": "05:00",
                "timezone": "UTC",
                "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            },
            "model": "gpt-5.3-codex",
            "prompt": "ping",
            "accountIds": [],
        },
    )
    assert create_response.status_code == 200
    automation_id = create_response.json()["id"]

    run_response = await async_client.post(f"/api/automations/{automation_id}/run-now")
    assert run_response.status_code == 202
    run_payload = run_response.json()
    assert run_payload["trigger"] == "manual"

    executed = await _run_due_jobs(now_utc=utcnow() + timedelta(seconds=5))
    assert executed == 3

    async with SessionLocal() as session:
        request_logs_repository = RequestLogsRepository(session)
        recent_logs, _ = await request_logs_repository.list_recent(limit=200, since=started_at)
        observed = {
            log.account_id for log in recent_logs if log.transport == "automation" and log.model == "gpt-5.3-codex"
        }
        expected = {account.id for account in accounts}
        assert expected.issubset(observed)

    runs_response = await async_client.get("/api/automations/runs?trigger=manual&limit=25&offset=0")
    assert runs_response.status_code == 200
    runs_payload = runs_response.json()
    matching_runs = [run for run in runs_payload["items"] if run["jobId"] == automation_id]
    assert matching_runs
    assert matching_runs[0]["totalAccounts"] == 3
    assert matching_runs[0]["completedAccounts"] == 3
    assert matching_runs[0]["pendingAccounts"] == 0


@pytest.mark.asyncio
async def test_automations_run_now_reactivates_elapsed_rate_limited_account(async_client, monkeypatch):
    account = (await _create_accounts("auto-manual-reset-elapsed"))[0]
    now = utcnow()
    reset_at = naive_utc_to_epoch(now - timedelta(minutes=1))
    blocked_at = naive_utc_to_epoch(now - timedelta(minutes=2))
    await _set_account_status_with_reset(
        account.id,
        AccountStatus.RATE_LIMITED,
        reset_at=reset_at,
        blocked_at=blocked_at,
    )
    started_at = utcnow()

    async def _fake_compact(*_args, **_kwargs):
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    create_response = await async_client.post(
        "/api/automations",
        json={
            "name": "Manual reset recovery",
            "enabled": False,
            "schedule": {
                "type": "daily",
                "time": "05:00",
                "timezone": "UTC",
                "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            },
            "model": "gpt-5.3-codex",
            "prompt": "ping",
            "accountIds": [account.id],
        },
    )
    assert create_response.status_code == 200
    automation_id = create_response.json()["id"]

    run_response = await async_client.post(f"/api/automations/{automation_id}/run-now")
    assert run_response.status_code == 202

    executed = await _run_due_jobs(now_utc=utcnow() + timedelta(seconds=5))
    assert executed == 1

    async with SessionLocal() as session:
        account_repository = AccountsRepository(session)
        refreshed = await account_repository.get_by_id(account.id)
        assert refreshed is not None
        assert refreshed.status == AccountStatus.ACTIVE
        assert refreshed.reset_at is None
        assert refreshed.blocked_at is None

        request_logs_repository = RequestLogsRepository(session)
        recent_logs, _ = await request_logs_repository.list_recent(limit=200, since=started_at)
        observed = {
            log.account_id for log in recent_logs if log.transport == "automation" and log.model == "gpt-5.3-codex"
        }
        assert account.id in observed


@pytest.mark.asyncio
async def test_manual_run_due_jobs_claim_each_run_once_under_race(async_client, monkeypatch):
    account = (await _create_accounts("auto-manual-race"))[0]
    first_call_started = asyncio.Event()
    allow_first_call_to_finish = asyncio.Event()
    call_count = 0

    async def _fake_compact(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            first_call_started.set()
            await allow_first_call_to_finish.wait()
        return SimpleNamespace(id=f"resp-{call_count}")

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    create_response = await async_client.post(
        "/api/automations",
        json={
            "name": "Manual race",
            "enabled": False,
            "schedule": {
                "type": "daily",
                "time": "05:00",
                "timezone": "UTC",
                "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            },
            "model": "gpt-5.3-codex",
            "prompt": "ping",
            "accountIds": [account.id],
        },
    )
    assert create_response.status_code == 200
    automation_id = create_response.json()["id"]

    run_response = await async_client.post(f"/api/automations/{automation_id}/run-now")
    assert run_response.status_code == 202
    initial_run_payload = run_response.json()
    initial_cycle_started_at = initial_run_payload["startedAt"]
    initial_cycle_scheduled_for = initial_run_payload["scheduledFor"]

    now = utcnow() + timedelta(seconds=5)
    async with SessionLocal() as session_one, SessionLocal() as session_two:
        service_one = AutomationsService(
            AutomationsRepository(session_one),
            AccountsRepository(session_one),
            RequestLogsRepository(session_one),
        )
        service_two = AutomationsService(
            AutomationsRepository(session_two),
            AccountsRepository(session_two),
            RequestLogsRepository(session_two),
        )

        task_one = asyncio.create_task(service_one.run_due_jobs(now_utc=now))
        await asyncio.wait_for(first_call_started.wait(), timeout=1.0)
        task_two = asyncio.create_task(service_two.run_due_jobs(now_utc=now))
        await asyncio.sleep(0.05)
        allow_first_call_to_finish.set()
        executed_one, executed_two = await asyncio.gather(task_one, task_two)

    assert call_count == 1
    assert sorted([executed_one, executed_two]) == [0, 1]

    runs_response = await async_client.get(f"/api/automations/{automation_id}/runs")
    assert runs_response.status_code == 200
    runs_payload = runs_response.json()["items"]
    assert len(runs_payload) == 1
    assert runs_payload[0]["status"] == "success"
    assert runs_payload[0]["attemptCount"] == 1
    assert runs_payload[0]["startedAt"] == initial_cycle_started_at
    assert runs_payload[0]["scheduledFor"] == initial_cycle_scheduled_for


@pytest.mark.asyncio
async def test_grouped_manual_runs_keep_cycle_trigger_order_when_started_at_drifts(async_client):
    account = (await _create_accounts("auto-manual-group-order"))[0]
    first_now = datetime(2026, 4, 22, 10, 0, 0)
    second_now = first_now + timedelta(minutes=1)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)

        job = await automations_repository.create_job(
            name="Manual grouped order",
            enabled=False,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time="05:00",
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=0,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[account.id],
        )
        first_run = await service.run_now(job.id, now_utc=first_now)
        second_run = await service.run_now(job.id, now_utc=second_now)

    assert first_run.cycle_key is not None
    assert second_run.cycle_key is not None

    async with SessionLocal() as session:
        await session.execute(
            update(AutomationRun)
            .where(AutomationRun.cycle_key == first_run.cycle_key)
            .values(started_at=second_now + timedelta(minutes=5))
        )
        await session.commit()

    grouped_response = await async_client.get(
        "/api/automations/runs",
        params={"automationId": job.id, "trigger": "manual", "limit": 25, "offset": 0},
    )
    assert grouped_response.status_code == 200
    grouped_items = grouped_response.json()["items"]
    assert len(grouped_items) == 2
    assert grouped_items[0]["cycleKey"] == second_run.cycle_key
    assert grouped_items[0]["startedAt"] == second_now.isoformat() + "Z"
    assert grouped_items[0]["scheduledFor"] == second_now.isoformat() + "Z"
    assert grouped_items[1]["cycleKey"] == first_run.cycle_key
    assert grouped_items[1]["startedAt"] == first_now.isoformat() + "Z"
    assert grouped_items[1]["scheduledFor"] == first_now.isoformat() + "Z"


@pytest.mark.asyncio
async def test_automations_list_last_run_uses_latest_manual_cycle_not_drifted_started_at(async_client):
    account = (await _create_accounts("auto-manual-last-run"))[0]
    first_now = datetime(2026, 4, 22, 10, 0, 0)
    second_now = first_now + timedelta(minutes=1)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)

        job = await automations_repository.create_job(
            name="Manual last run",
            enabled=False,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time="05:00",
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=0,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[account.id],
        )
        first_run = await service.run_now(job.id, now_utc=first_now)
        second_run = await service.run_now(job.id, now_utc=second_now)

    assert first_run.cycle_key is not None
    assert second_run.cycle_key is not None

    async with SessionLocal() as session:
        await session.execute(
            update(AutomationRun)
            .where(AutomationRun.cycle_key == first_run.cycle_key)
            .values(started_at=second_now + timedelta(minutes=5))
        )
        await session.commit()

    jobs_response = await async_client.get("/api/automations", params={"limit": 25, "offset": 0})
    assert jobs_response.status_code == 200
    jobs_items = jobs_response.json()["items"]
    matching = [item for item in jobs_items if item["id"] == job.id]
    assert len(matching) == 1
    assert matching[0]["lastRun"]["cycleKey"] == second_run.cycle_key
    assert matching[0]["lastRun"]["startedAt"] == second_now.isoformat() + "Z"
    assert matching[0]["lastRun"]["scheduledFor"] == second_now.isoformat() + "Z"


@pytest.mark.asyncio
async def test_automations_due_run_is_claimed_once_per_slot(db_setup, monkeypatch):
    del db_setup
    accounts = await _create_accounts("auto-scheduler-a")
    now = utcnow().replace(second=0, microsecond=0)
    schedule_time = now.strftime("%H:%M")

    async def _fake_compact(*_args, **_kwargs):
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)

        job = await automations_repository.create_job(
            name="Scheduler ping",
            enabled=True,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time=schedule_time,
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=0,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[accounts[0].id],
        )
        await _set_job_updated_at(job.id, now)

        executed_first = await service.run_due_jobs(now_utc=now + timedelta(seconds=10))
        executed_second = await service.run_due_jobs(now_utc=now + timedelta(seconds=20))

        assert executed_first >= 1
        assert executed_second == 0

        runs = await automations_repository.list_runs(job.id, limit=20)
        assert len(runs) == 1
        assert runs[0].trigger == "scheduled"
        assert runs[0].status == "success"


@pytest.mark.asyncio
async def test_automations_due_run_spreads_accounts_with_threshold(db_setup, monkeypatch):
    del db_setup
    accounts = await _create_accounts("auto-threshold-a", "auto-threshold-b", "auto-threshold-c")
    now = utcnow().replace(second=0, microsecond=0)
    schedule_time = now.strftime("%H:%M")

    async def _fake_compact(*_args, **_kwargs):
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)

        job = await automations_repository.create_job(
            name="Threshold ping",
            enabled=True,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time=schedule_time,
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=11,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[account.id for account in accounts],
        )
        await _set_job_updated_at(job.id, now)

        executed = await service.run_due_jobs(now_utc=now + timedelta(minutes=30))

        assert executed == 3

        runs = await automations_repository.list_runs(job.id, limit=20)
        assert len(runs) == 3

        due_slot = datetime(now.year, now.month, now.day, now.hour, now.minute)
        offsets = [int((run.scheduled_for - due_slot).total_seconds()) for run in runs]
        assert all(0 <= offset <= 11 * 60 for offset in offsets)
        assert 0 in offsets
        assert len(set(offsets)) == len(offsets)


@pytest.mark.asyncio
async def test_automations_due_run_freezes_all_accounts_snapshot_for_cycle(db_setup, monkeypatch):
    del db_setup
    accounts = await _create_accounts("auto-freeze-a", "auto-freeze-b", "auto-freeze-c")
    now = utcnow().replace(second=0, microsecond=0)
    schedule_time = now.strftime("%H:%M")

    async def _fake_compact(*_args, **_kwargs):
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)
        await accounts_repository.update_status(accounts[2].id, AccountStatus.RATE_LIMITED)

        job = await automations_repository.create_job(
            name="Freeze snapshot",
            enabled=True,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time=schedule_time,
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=5,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[accounts[0].id, accounts[1].id, accounts[2].id],
        )
        await _set_job_updated_at(job.id, now)

        executed_first = await service.run_due_jobs(now_utc=now)
        assert executed_first >= 1

        await accounts_repository.update_status(accounts[2].id, AccountStatus.ACTIVE)
        executed_second = await service.run_due_jobs(now_utc=now + timedelta(minutes=10))

        runs = await automations_repository.list_runs(job.id, limit=20)
        assert executed_first + executed_second == 2
        assert len(runs) == 2
        assert {run.account_id for run in runs} == {accounts[0].id, accounts[1].id}
        assert {run.cycle_expected_accounts for run in runs} == {2}


@pytest.mark.asyncio
async def test_automations_due_run_freezes_empty_cycle_after_late_account_reactivation(db_setup, monkeypatch):
    del db_setup
    account = (await _create_accounts("auto-empty-cycle-a"))[0]
    now = utcnow().replace(second=0, microsecond=0)
    schedule_time = now.strftime("%H:%M")
    future_reset_at = naive_utc_to_epoch(now + timedelta(hours=1))

    async def _fake_compact(*_args, **_kwargs):
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)
        await accounts_repository.update_status(
            account.id,
            AccountStatus.RATE_LIMITED,
            reset_at=future_reset_at,
        )

        job = await automations_repository.create_job(
            name="Freeze empty cycle",
            enabled=True,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time=schedule_time,
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=5,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[account.id],
        )
        await _set_job_updated_at(job.id, now)

        executed_first = await service.run_due_jobs(now_utc=now + timedelta(seconds=5))
        assert executed_first == 0

        cycle_key = f"scheduled:{job.id}:{now.isoformat()}"
        cycle = await automations_repository.get_run_cycle(cycle_key=cycle_key)
        assert cycle is not None
        assert cycle.cycle_expected_accounts == 0
        assert cycle.accounts == []

        await accounts_repository.update_status(account.id, AccountStatus.ACTIVE)
        executed_second = await service.run_due_jobs(now_utc=now + timedelta(minutes=4))

        assert executed_second == 0
        assert await automations_repository.list_runs(job.id, limit=10) == []


@pytest.mark.asyncio
async def test_automations_due_run_keeps_frozen_dispatch_plan_after_threshold_edit(db_setup, monkeypatch):
    del db_setup
    accounts = await _create_accounts("auto-plan-a", "auto-plan-b", "auto-plan-c")
    now = utcnow().replace(second=0, microsecond=0)
    schedule_time = now.strftime("%H:%M")

    def _fake_offsets(**kwargs):
        threshold_minutes = kwargs["threshold_minutes"]
        account_count = kwargs["account_count"]
        if threshold_minutes >= 5:
            return [0, 120, 240][:account_count]
        return [0, 10, 20][:account_count]

    async def _fake_compact(*_args, **_kwargs):
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service._pick_dispatch_offsets_seconds", _fake_offsets)
    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)

        job = await automations_repository.create_job(
            name="Freeze dispatch plan",
            enabled=True,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time=schedule_time,
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=5,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[account.id for account in accounts],
        )
        await _set_job_updated_at(job.id, now)

        executed_first = await service.run_due_jobs(now_utc=now + timedelta(seconds=5))
        assert executed_first == 1

        updated_job = await automations_repository.update_job(
            job.id,
            schedule_threshold_minutes=1,
        )
        assert updated_job is not None
        await _set_job_updated_at(job.id, now + timedelta(minutes=1))

        representative_run = (await automations_repository.list_runs(job.id, limit=10))[0]
        details = await service.get_run_details(representative_run.id)
        pending_dispatches = sorted(
            entry.scheduled_for
            for entry in details.accounts
            if entry.status == "pending" and entry.scheduled_for is not None
        )
        assert pending_dispatches == [now + timedelta(minutes=2), now + timedelta(minutes=4)]

        executed_second = await service.run_due_jobs(now_utc=now + timedelta(seconds=30))
        assert executed_second == 0

        executed_third = await service.run_due_jobs(now_utc=now + timedelta(minutes=5))
        assert executed_third == 2

        runs = await automations_repository.list_runs(job.id, limit=10)
        assert sorted(run.scheduled_for for run in runs) == [
            now,
            now + timedelta(minutes=2),
            now + timedelta(minutes=4),
        ]


@pytest.mark.asyncio
async def test_automations_due_run_does_not_backfill_previous_day_before_today_schedule_time(
    async_client,
    db_setup,
    monkeypatch,
):
    del db_setup
    accounts = await _create_accounts("auto-no-backfill-a", "auto-no-backfill-b")
    now = utcnow().replace(second=0, microsecond=0)
    schedule_time = (now + timedelta(hours=1)).strftime("%H:%M")

    async def _fake_compact(*_args, **_kwargs):
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)

        job = await automations_repository.create_job(
            name="No backfill before today's slot",
            enabled=True,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time=schedule_time,
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=2,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[account.id for account in accounts],
        )
        await _set_job_updated_at(job.id, now)

        executed_before_slot = await service.run_due_jobs(now_utc=now)
        assert executed_before_slot == 0
        assert await automations_repository.list_runs(job.id, limit=10) == []

        executed_after_slot = await service.run_due_jobs(now_utc=now + timedelta(hours=1, minutes=5))
        assert executed_after_slot == 2


@pytest.mark.asyncio
async def test_automations_due_run_does_not_execute_same_day_when_job_is_created_after_slot(db_setup, monkeypatch):
    del db_setup
    account = (await _create_accounts("auto-created-after-slot"))[0]
    now = datetime(2026, 4, 20, 12, 0, 0)

    async def _fake_compact(*_args, **_kwargs):
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)

        job = await automations_repository.create_job(
            name="Created after slot",
            enabled=True,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time="06:00",
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=0,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[account.id],
        )
        await _set_job_updated_at(job.id, now)

        executed = await service.run_due_jobs(now_utc=now)
        assert executed == 0

        executed_next_day = await service.run_due_jobs(now_utc=now + timedelta(days=1, minutes=1))
        assert executed_next_day == 1

        runs = await automations_repository.list_runs(job.id, limit=10)
        assert len(runs) == 1
        assert runs[0].trigger == "scheduled"
        assert runs[0].scheduled_for == datetime(2026, 4, 21, 6, 0, 0)


@pytest.mark.asyncio
async def test_automations_due_run_does_not_execute_same_day_when_job_is_updated_after_slot(db_setup, monkeypatch):
    del db_setup
    account = (await _create_accounts("auto-updated-after-slot"))[0]
    now = datetime(2026, 4, 20, 12, 0, 0)

    async def _fake_compact(*_args, **_kwargs):
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)

        job = await automations_repository.create_job(
            name="Updated after slot",
            enabled=True,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time="23:00",
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=0,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[account.id],
        )
        await _set_job_updated_at(job.id, now - timedelta(hours=1))

        executed_before_update = await service.run_due_jobs(now_utc=now)
        assert executed_before_update == 0

        updated = await automations_repository.update_job(job.id, schedule_time="06:00")
        assert updated is not None
        await _set_job_updated_at(job.id, now)

        executed_after_update = await service.run_due_jobs(now_utc=now + timedelta(minutes=1))
        assert executed_after_update == 0

        executed_next_day = await service.run_due_jobs(now_utc=now + timedelta(days=1, minutes=1))
        assert executed_next_day == 1

        runs = await automations_repository.list_runs(job.id, limit=10)
        assert len(runs) == 1
        assert runs[0].trigger == "scheduled"
        assert runs[0].scheduled_for == datetime(2026, 4, 21, 6, 0, 0)


@pytest.mark.asyncio
async def test_automations_due_run_continues_existing_cycle_after_job_update(db_setup, monkeypatch):
    del db_setup
    accounts = await _create_accounts("auto-cycle-update-a", "auto-cycle-update-b")
    now = datetime(2026, 4, 20, 6, 0, 0)

    def _fake_offsets(**_kwargs):
        return [0, 120]

    async def _fake_compact(*_args, **_kwargs):
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service._pick_dispatch_offsets_seconds", _fake_offsets)
    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)

        job = await automations_repository.create_job(
            name="Existing cycle after update",
            enabled=True,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time="06:00",
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=5,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[account.id for account in accounts],
        )
        await _set_job_updated_at(job.id, now)

        executed_first = await service.run_due_jobs(now_utc=now)
        assert executed_first == 1

        await automations_repository.update_job(job.id, prompt="updated prompt")
        await _set_job_updated_at(job.id, now + timedelta(minutes=1))

        executed_second = await service.run_due_jobs(now_utc=now + timedelta(minutes=3))
        assert executed_second == 1

        runs = await automations_repository.list_runs(job.id, limit=10)
        assert len(runs) == 2
        assert {run.account_id for run in runs} == {accounts[0].id, accounts[1].id}


@pytest.mark.asyncio
async def test_automations_due_run_does_not_execute_same_day_when_job_is_created_after_slot_in_same_minute(
    db_setup,
    monkeypatch,
):
    del db_setup
    account = (await _create_accounts("auto-created-same-minute-after-slot"))[0]
    slot_time = datetime(2026, 4, 20, 6, 0, 0)

    async def _fake_compact(*_args, **_kwargs):
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)

        job = await automations_repository.create_job(
            name="Created after slot same minute",
            enabled=True,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time="06:00",
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=0,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[account.id],
        )
        await _set_job_updated_at(job.id, slot_time + timedelta(seconds=45))

        executed_same_day = await service.run_due_jobs(now_utc=slot_time + timedelta(minutes=1, seconds=5))
        assert executed_same_day == 0

        executed_next_day = await service.run_due_jobs(now_utc=slot_time + timedelta(days=1, minutes=1))
        assert executed_next_day == 1


@pytest.mark.asyncio
async def test_automations_due_run_does_not_execute_same_day_when_job_is_updated_after_slot_in_same_minute(
    db_setup,
    monkeypatch,
):
    del db_setup
    account = (await _create_accounts("auto-updated-same-minute-after-slot"))[0]
    slot_time = datetime(2026, 4, 20, 6, 0, 0)

    async def _fake_compact(*_args, **_kwargs):
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)

        job = await automations_repository.create_job(
            name="Updated after slot same minute",
            enabled=True,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time="23:00",
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=0,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[account.id],
        )
        await _set_job_updated_at(job.id, slot_time - timedelta(hours=1))

        executed_before_update = await service.run_due_jobs(now_utc=slot_time + timedelta(minutes=1))
        assert executed_before_update == 0

        updated = await automations_repository.update_job(job.id, schedule_time="06:00")
        assert updated is not None
        await _set_job_updated_at(job.id, slot_time + timedelta(seconds=45))

        executed_same_day = await service.run_due_jobs(now_utc=slot_time + timedelta(minutes=1, seconds=5))
        assert executed_same_day == 0

        executed_next_day = await service.run_due_jobs(now_utc=slot_time + timedelta(days=1, minutes=1))
        assert executed_next_day == 1


@pytest.mark.asyncio
async def test_automations_due_run_skips_unavailable_accounts_and_can_include_paused(db_setup, monkeypatch):
    del db_setup
    accounts = await _create_accounts(
        "auto-skip-active",
        "auto-skip-paused",
        "auto-skip-rate-limited",
        "auto-skip-quota",
        "auto-skip-deactivated",
    )
    active = accounts[0]
    paused = accounts[1]
    rate_limited = accounts[2]
    quota = accounts[3]
    deactivated = accounts[4]

    await _set_account_status(paused.id, AccountStatus.PAUSED)
    await _set_account_status(rate_limited.id, AccountStatus.RATE_LIMITED)
    await _set_account_status(quota.id, AccountStatus.QUOTA_EXCEEDED)
    await _set_account_status(deactivated.id, AccountStatus.DEACTIVATED)

    called_chatgpt_account_ids: list[str | None] = []

    async def _fake_compact(*_args, **kwargs):
        called_chatgpt_account_ids.append(kwargs.get("account_id"))
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)
    now = utcnow().replace(second=0, microsecond=0)
    schedule_time = now.strftime("%H:%M")

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)

        await automations_repository.create_job(
            name="Skip unavailable accounts",
            enabled=True,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time=schedule_time,
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=0,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[active.id, paused.id, rate_limited.id, quota.id, deactivated.id],
        )
        include_paused_job = await automations_repository.create_job(
            name="Include paused accounts",
            enabled=True,
            include_paused_accounts=True,
            schedule_type="daily",
            schedule_time=schedule_time,
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=0,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[active.id, paused.id, rate_limited.id, quota.id, deactivated.id],
        )
        await _set_job_updated_at(include_paused_job.id, now)
        jobs = await automations_repository.list_jobs()
        for job in jobs:
            await _set_job_updated_at(job.id, now)

        executed = await service.run_due_jobs(now_utc=now + timedelta(seconds=5))
        assert executed == 3

    assert called_chatgpt_account_ids.count(active.chatgpt_account_id) == 2
    assert called_chatgpt_account_ids.count(paused.chatgpt_account_id) == 1
    assert rate_limited.chatgpt_account_id not in called_chatgpt_account_ids
    assert quota.chatgpt_account_id not in called_chatgpt_account_ids
    assert deactivated.chatgpt_account_id not in called_chatgpt_account_ids


@pytest.mark.asyncio
async def test_automations_runs_page_reports_in_progress_cycle_and_details(async_client, monkeypatch):
    accounts = await _create_accounts("auto-cycle-a", "auto-cycle-b")
    now = utcnow().replace(second=0, microsecond=0)
    schedule_time = now.strftime("%H:%M")

    async def _fake_compact(*_args, **_kwargs):
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)
    monkeypatch.setattr(
        "app.modules.automations.service._pick_dispatch_offsets_seconds",
        lambda **_kwargs: [30, 90],
    )

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)
        job = await automations_repository.create_job(
            name="Cycle progress",
            enabled=True,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time=schedule_time,
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=2,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[accounts[0].id, accounts[1].id],
        )
        await _set_job_updated_at(job.id, now)
        executed = await service.run_due_jobs(now_utc=now + timedelta(minutes=1))
        assert executed == 1

    runs_response = await async_client.get("/api/automations/runs?limit=10&offset=0")
    assert runs_response.status_code == 200
    runs_payload = runs_response.json()
    assert runs_payload["total"] == 1
    run = runs_payload["items"][0]
    assert run["effectiveStatus"] == "running"
    assert run["totalAccounts"] == 2
    assert run["completedAccounts"] == 1
    assert run["pendingAccounts"] == 1

    details_response = await async_client.get(f"/api/automations/runs/{run['id']}/details")
    assert details_response.status_code == 200
    details_payload = details_response.json()
    assert details_payload["run"]["effectiveStatus"] == "running"
    assert details_payload["totalAccounts"] == 2
    assert details_payload["completedAccounts"] == 1
    assert details_payload["pendingAccounts"] == 1
    statuses = sorted(entry["status"] for entry in details_payload["accounts"])
    assert statuses == ["pending", "success"]

    filtered_running_response = await async_client.get(
        "/api/automations/runs",
        params={"automationId": run["jobId"], "status": "running", "limit": 10, "offset": 0},
    )
    assert filtered_running_response.status_code == 200
    filtered_running_payload = filtered_running_response.json()
    assert filtered_running_payload["total"] == 1
    assert filtered_running_payload["items"][0]["effectiveStatus"] == "running"

    filtered_success_response = await async_client.get(
        "/api/automations/runs",
        params={"automationId": run["jobId"], "status": "success", "limit": 10, "offset": 0},
    )
    assert filtered_success_response.status_code == 200
    filtered_success_payload = filtered_success_response.json()
    assert filtered_success_payload["total"] == 0


@pytest.mark.asyncio
async def test_automations_run_details_do_not_count_running_accounts_as_completed(async_client):
    accounts = await _create_accounts("auto-running-summary-a", "auto-running-summary-b")
    now = utcnow().replace(second=0, microsecond=0)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)
        job = await automations_repository.create_job(
            name="Running summary",
            enabled=True,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time=now.strftime("%H:%M"),
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=2,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[accounts[0].id, accounts[1].id],
        )
        cycle_key = f"scheduled:{job.id}:{now.isoformat()}"
        cycle = await automations_repository.create_run_cycle(
            cycle_key=cycle_key,
            job_id=job.id,
            trigger="scheduled",
            cycle_expected_accounts=2,
            cycle_window_end=now + timedelta(minutes=2),
            accounts=[
                (accounts[0].id, now),
                (accounts[1].id, now + timedelta(minutes=1)),
            ],
        )
        run = await automations_repository.claim_run(
            job_id=job.id,
            trigger="scheduled",
            slot_key=_scheduled_slot_key(job.id, account_id=accounts[0].id, due_slot=now),
            cycle_key=cycle.cycle_key,
            cycle_expected_accounts=cycle.cycle_expected_accounts,
            cycle_window_end=cycle.cycle_window_end,
            scheduled_for=now,
            started_at=now,
            account_id=accounts[0].id,
        )
        assert run is not None

        run_page = await service.list_runs_page(limit=10, offset=0, job_ids=[job.id])
        assert len(run_page.items) == 1
        assert run_page.items[0].completed_accounts == 0
        assert run_page.items[0].pending_accounts == 2

    runs_response = await async_client.get("/api/automations/runs?limit=10&offset=0")
    assert runs_response.status_code == 200
    runs_payload = runs_response.json()
    run_item = next(item for item in runs_payload["items"] if item["jobId"] == job.id)
    assert run_item["completedAccounts"] == 0
    assert run_item["pendingAccounts"] == 2

    details_response = await async_client.get(f"/api/automations/runs/{run_item['id']}/details")
    assert details_response.status_code == 200
    details_payload = details_response.json()
    assert details_payload["completedAccounts"] == 0
    assert details_payload["pendingAccounts"] == 2
    assert sorted(entry["status"] for entry in details_payload["accounts"]) == ["pending", "running"]


@pytest.mark.asyncio
async def test_automations_runs_page_keeps_running_when_cycle_window_elapsed_but_accounts_still_running(
    async_client,
    monkeypatch,
):
    accounts = await _create_accounts("auto-cycle-window-a", "auto-cycle-window-b")

    async def _fake_compact(*_args, **_kwargs):
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    create_response = await async_client.post(
        "/api/automations",
        json={
            "name": "Manual window status",
            "enabled": False,
            "schedule": {
                "type": "daily",
                "time": "05:00",
                "timezone": "UTC",
                "thresholdMinutes": 0,
                "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            },
            "model": "gpt-5.3-codex",
            "prompt": "ping",
            "accountIds": [accounts[0].id, accounts[1].id],
        },
    )
    assert create_response.status_code == 200
    automation_id = create_response.json()["id"]

    run_now_response = await async_client.post(f"/api/automations/{automation_id}/run-now")
    assert run_now_response.status_code == 202
    cycle_key = run_now_response.json()["cycleKey"]
    assert cycle_key

    async with SessionLocal() as session:
        await session.execute(
            update(AutomationRun)
            .where(AutomationRun.cycle_key == cycle_key)
            .values(cycle_window_end=utcnow() - timedelta(minutes=5))
        )
        await session.commit()

    running_filtered_response = await async_client.get(
        "/api/automations/runs",
        params={
            "automationId": automation_id,
            "trigger": "manual",
            "status": "running",
            "limit": 10,
            "offset": 0,
        },
    )
    assert running_filtered_response.status_code == 200
    running_filtered_payload = running_filtered_response.json()
    assert running_filtered_payload["total"] == 1
    assert running_filtered_payload["items"][0]["effectiveStatus"] == "running"

    failed_filtered_response = await async_client.get(
        "/api/automations/runs",
        params={
            "automationId": automation_id,
            "trigger": "manual",
            "status": "failed",
            "limit": 10,
            "offset": 0,
        },
    )
    assert failed_filtered_response.status_code == 200
    failed_filtered_payload = failed_filtered_response.json()
    assert failed_filtered_payload["total"] == 0


@pytest.mark.asyncio
async def test_automations_runs_page_groups_scheduled_cycle_after_all_accounts_finish(async_client, monkeypatch):
    accounts = await _create_accounts("auto-cycle-group-a", "auto-cycle-group-b", "auto-cycle-group-c")
    now = utcnow().replace(second=0, microsecond=0)
    schedule_time = now.strftime("%H:%M")

    async def _fake_compact(*_args, **_kwargs):
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)
        job = await automations_repository.create_job(
            name="Cycle grouped listing",
            enabled=True,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time=schedule_time,
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=5,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[account.id for account in accounts],
        )
        await _set_job_updated_at(job.id, now)
        executed = await service.run_due_jobs(now_utc=now + timedelta(minutes=10))
        assert executed == 3

    runs_response = await async_client.get("/api/automations/runs?limit=25&offset=0&trigger=scheduled")
    assert runs_response.status_code == 200
    runs_payload = runs_response.json()
    assert runs_payload["total"] == 1
    assert len(runs_payload["items"]) == 1
    run = runs_payload["items"][0]
    assert run["effectiveStatus"] == "success"
    assert run["totalAccounts"] == 3
    assert run["completedAccounts"] == 3
    assert run["pendingAccounts"] == 0


@pytest.mark.asyncio
async def test_grouped_runs_and_last_run_use_cycle_finished_at_and_error_summary(async_client):
    accounts = await _create_accounts("auto-cycle-summary-a", "auto-cycle-summary-b")
    now = datetime(2026, 4, 22, 12, 0, 0)
    success_finished_at = now + timedelta(seconds=45)
    failed_finished_at = now + timedelta(seconds=30)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)

        job = await automations_repository.create_job(
            name="Cycle terminal summary",
            enabled=True,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time=now.strftime("%H:%M"),
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=0,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[account.id for account in accounts],
        )
        cycle = await automations_repository.create_run_cycle(
            cycle_key=f"scheduled:{job.id}:{now.isoformat()}",
            job_id=job.id,
            trigger="scheduled",
            cycle_expected_accounts=2,
            cycle_window_end=now,
            accounts=[
                (accounts[0].id, now),
                (accounts[1].id, now + timedelta(seconds=10)),
            ],
        )
        failed_run = await automations_repository.claim_run(
            job_id=job.id,
            trigger="scheduled",
            slot_key=_scheduled_slot_key(job.id, account_id=accounts[0].id, due_slot=now),
            cycle_key=cycle.cycle_key,
            cycle_expected_accounts=cycle.cycle_expected_accounts,
            cycle_window_end=cycle.cycle_window_end,
            scheduled_for=now,
            started_at=now,
            account_id=accounts[0].id,
        )
        success_run = await automations_repository.claim_run(
            job_id=job.id,
            trigger="scheduled",
            slot_key=_scheduled_slot_key(job.id, account_id=accounts[1].id, due_slot=now),
            cycle_key=cycle.cycle_key,
            cycle_expected_accounts=cycle.cycle_expected_accounts,
            cycle_window_end=cycle.cycle_window_end,
            scheduled_for=now + timedelta(seconds=10),
            started_at=now + timedelta(seconds=20),
            account_id=accounts[1].id,
        )
        assert failed_run is not None
        assert success_run is not None
        await automations_repository.complete_run(
            failed_run.id,
            status="failed",
            finished_at=failed_finished_at,
            account_id=accounts[0].id,
            error_code="rate_limited",
            error_message="try later",
            attempt_count=1,
        )
        await automations_repository.complete_run(
            success_run.id,
            status="success",
            finished_at=success_finished_at,
            account_id=accounts[1].id,
            error_code=None,
            error_message=None,
            attempt_count=1,
        )

        details = await service.get_run_details(success_run.id)

    assert details.run.effective_status == "partial"
    assert details.run.finished_at == success_finished_at
    assert details.run.error_code == "rate_limited"
    assert details.run.error_message == "try later"

    runs_response = await async_client.get(
        "/api/automations/runs",
        params={"automationId": job.id, "trigger": "scheduled", "limit": 25, "offset": 0},
    )
    assert runs_response.status_code == 200
    payload = runs_response.json()
    assert payload["total"] == 1
    run = payload["items"][0]
    assert run["id"] == success_run.id
    assert run["effectiveStatus"] == "partial"
    assert run["finishedAt"] == success_finished_at.isoformat() + "Z"
    assert run["errorCode"] == "rate_limited"
    assert run["errorMessage"] == "try later"

    jobs_response = await async_client.get("/api/automations", params={"limit": 25, "offset": 0})
    assert jobs_response.status_code == 200
    jobs_items = jobs_response.json()["items"]
    matching = [item for item in jobs_items if item["id"] == job.id]
    assert len(matching) == 1
    last_run = matching[0]["lastRun"]
    assert last_run is not None
    assert last_run["effectiveStatus"] == "partial"
    assert last_run["finishedAt"] == success_finished_at.isoformat() + "Z"
    assert last_run["errorCode"] == "rate_limited"
    assert last_run["errorMessage"] == "try later"

    details_response = await async_client.get(f"/api/automations/runs/{success_run.id}/details")
    assert details_response.status_code == 200
    details_payload = details_response.json()
    assert details_payload["run"]["effectiveStatus"] == "partial"
    assert details_payload["run"]["finishedAt"] == success_finished_at.isoformat() + "Z"
    assert details_payload["run"]["errorCode"] == "rate_limited"
    assert details_payload["run"]["errorMessage"] == "try later"


@pytest.mark.asyncio
async def test_automations_manual_cycle_totals_do_not_drop_below_completed_when_account_becomes_ineligible(
    async_client,
    monkeypatch,
):
    accounts = await _create_accounts("auto-manual-count-a", "auto-manual-count-b")

    async def _fake_compact(*_args, **_kwargs):
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    create_response = await async_client.post(
        "/api/automations",
        json={
            "name": "Manual cycle totals",
            "enabled": True,
            "schedule": {
                "type": "daily",
                "time": "05:00",
                "timezone": "UTC",
                "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            },
            "model": "gpt-5.3-codex",
            "prompt": "ping",
            "accountIds": [accounts[0].id, accounts[1].id],
        },
    )
    assert create_response.status_code == 200
    automation_id = create_response.json()["id"]

    run_response = await async_client.post(f"/api/automations/{automation_id}/run-now")
    assert run_response.status_code == 202

    await _set_account_status(accounts[1].id, AccountStatus.RATE_LIMITED)
    executed = await _run_due_jobs(now_utc=utcnow() + timedelta(seconds=5))
    assert executed >= 2

    runs_response = await async_client.get(
        "/api/automations/runs",
        params={"automationId": automation_id, "trigger": "manual", "limit": 25, "offset": 0},
    )
    assert runs_response.status_code == 200
    payload = runs_response.json()
    assert payload["total"] == 1
    run = payload["items"][0]
    assert run["totalAccounts"] == 2
    assert run["completedAccounts"] == 2
    assert run["pendingAccounts"] == 0


@pytest.mark.asyncio
async def test_automations_manual_cycle_without_eligible_accounts_keeps_zero_totals_after_account_reactivation(
    async_client,
    monkeypatch,
):
    accounts = await _create_accounts("auto-manual-empty-a")

    async def _fake_compact(*_args, **_kwargs):
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    create_response = await async_client.post(
        "/api/automations",
        json={
            "name": "Manual cycle without eligible accounts",
            "enabled": True,
            "schedule": {
                "type": "daily",
                "time": "05:00",
                "timezone": "UTC",
                "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            },
            "model": "gpt-5.3-codex",
            "prompt": "ping",
            "accountIds": [accounts[0].id],
        },
    )
    assert create_response.status_code == 200
    automation_id = create_response.json()["id"]

    future_reset_at = naive_utc_to_epoch(utcnow() + timedelta(hours=1))
    await _set_account_status_with_reset(
        accounts[0].id,
        AccountStatus.RATE_LIMITED,
        reset_at=future_reset_at,
    )

    run_response = await async_client.post(f"/api/automations/{automation_id}/run-now")
    assert run_response.status_code == 202
    run_payload = run_response.json()
    assert run_payload["status"] == "failed"
    run_id = run_payload["id"]

    await _set_account_status(accounts[0].id, AccountStatus.ACTIVE)

    runs_response = await async_client.get(
        "/api/automations/runs",
        params={"automationId": automation_id, "trigger": "manual", "limit": 25, "offset": 0},
    )
    assert runs_response.status_code == 200
    runs_payload = runs_response.json()
    assert runs_payload["total"] == 1
    run_item = runs_payload["items"][0]
    assert run_item["id"] == run_id
    assert run_item["totalAccounts"] == 0
    assert run_item["completedAccounts"] == 0
    assert run_item["pendingAccounts"] == 0

    details_response = await async_client.get(f"/api/automations/runs/{run_id}/details")
    assert details_response.status_code == 200
    details_payload = details_response.json()
    assert details_payload["run"]["id"] == run_id
    assert details_payload["totalAccounts"] == 0
    assert details_payload["completedAccounts"] == 0
    assert details_payload["pendingAccounts"] == 0
    assert details_payload["accounts"] == []


@pytest.mark.asyncio
async def test_automations_run_details_normalize_legacy_manual_cycle_key(async_client):
    accounts = await _create_accounts("auto-legacy-details-a", "auto-legacy-details-b")
    now = utcnow().replace(second=0, microsecond=0)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)
        job = await automations_repository.create_job(
            name="Legacy manual details",
            enabled=False,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time="05:00",
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=5,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[accounts[0].id, accounts[1].id],
        )
        cycle_key = f"manual:{job.id}:legacy-cycle"
        cycle = await automations_repository.create_run_cycle(
            cycle_key=cycle_key,
            job_id=job.id,
            trigger="manual",
            cycle_expected_accounts=2,
            cycle_window_end=now + timedelta(minutes=5),
            accounts=[
                (accounts[0].id, now),
                (accounts[1].id, now + timedelta(minutes=1)),
            ],
        )
        legacy_slot_key = f"{cycle_key}:digest-a"
        first_run = await automations_repository.claim_run(
            job_id=job.id,
            trigger="manual",
            slot_key=legacy_slot_key,
            cycle_key=cycle.cycle_key,
            cycle_expected_accounts=cycle.cycle_expected_accounts,
            cycle_window_end=cycle.cycle_window_end,
            scheduled_for=now,
            started_at=now,
            account_id=accounts[0].id,
        )
        assert first_run is not None
        await automations_repository.complete_run(
            first_run.id,
            status="success",
            finished_at=now + timedelta(seconds=5),
            account_id=accounts[0].id,
            error_code=None,
            error_message=None,
            attempt_count=1,
        )
        await session.execute(
            update(AutomationRun)
            .where(AutomationRun.id == first_run.id)
            .values(
                cycle_key=legacy_slot_key,
                cycle_expected_accounts=99,
            )
        )
        await session.commit()

        details = await service.get_run_details(first_run.id)

    assert details.total_accounts == 2
    assert details.completed_accounts == 1
    assert details.pending_accounts == 1
    assert sorted(account.status for account in details.accounts) == ["pending", "success"]

    details_response = await async_client.get(f"/api/automations/runs/{first_run.id}/details")
    assert details_response.status_code == 200
    payload = details_response.json()
    assert payload["totalAccounts"] == 2
    assert payload["completedAccounts"] == 1
    assert payload["pendingAccounts"] == 1
