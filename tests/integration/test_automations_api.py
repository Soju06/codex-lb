from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from app.core.clients.proxy import ProxyResponseError
from app.core.crypto import TokenEncryptor
from app.core.errors import openai_error
from app.core.openai.model_registry import ReasoningLevel, UpstreamModel, get_model_registry
from app.core.types import JsonValue
from app.core.utils.time import naive_utc_to_epoch, utcnow
from app.db.models import Account, AccountStatus, AutomationJob, AutomationRun
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.automations.repository import AutomationsRepository
from app.modules.automations.service import AutomationsService, _manual_slot_key, _scheduled_slot_key
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


def _make_upstream_model(slug: str, *, reasoning_efforts: tuple[str, ...]) -> UpstreamModel:
    raw: dict[str, JsonValue] = {}
    return UpstreamModel(
        slug=slug,
        display_name=slug,
        description=f"Test model {slug}",
        context_window=128000,
        input_modalities=("text",),
        supported_reasoning_levels=tuple(
            ReasoningLevel(effort=effort, description=effort) for effort in reasoning_efforts
        ),
        default_reasoning_level=reasoning_efforts[0] if reasoning_efforts else None,
        supports_reasoning_summaries=False,
        support_verbosity=False,
        default_verbosity=None,
        prefer_websockets=False,
        supports_parallel_tool_calls=True,
        supported_in_api=True,
        minimal_client_version=None,
        priority=0,
        available_in_plans=frozenset({"plus", "pro"}),
        raw=raw,
    )


async def _populate_automation_reasoning_models() -> None:
    models = [
        _make_upstream_model("automation-reasoning-xhigh", reasoning_efforts=("low", "medium", "high", "xhigh")),
        _make_upstream_model("automation-reasoning-ultra", reasoning_efforts=("low", "max", "ultra")),
        _make_upstream_model("automation-reasoning-medium", reasoning_efforts=("medium",)),
    ]
    await get_model_registry().update({"plus": models, "pro": models})


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
async def test_automations_write_endpoints_require_dashboard_write_access(app_instance):
    accounts = await _create_accounts("auto-guest-canary")
    async with app_instance.router.lifespan_context(app_instance):
        local_transport = ASGITransport(app=app_instance, client=("127.0.0.1", 50000))
        async with AsyncClient(transport=local_transport, base_url="http://localhost") as local_client:
            current_settings_response = await local_client.get("/api/settings")
            assert current_settings_response.status_code == 200
            current_settings_payload = current_settings_response.json()
            current_settings_payload["guestAccessEnabled"] = True
            updated_settings_response = await local_client.put(
                "/api/settings",
                json=current_settings_payload,
            )
            assert updated_settings_response.status_code == 200

            create_response = await local_client.post(
                "/api/automations",
                json={
                    "name": "Guest read-only canary",
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

        remote_transport = ASGITransport(app=app_instance, client=("203.0.113.20", 50001))
        async with AsyncClient(transport=remote_transport, base_url="http://lb.example") as remote_client:
            session_response = await remote_client.get("/api/dashboard-auth/session")
            assert session_response.status_code == 200
            session_payload = session_response.json()
            assert session_payload["authenticated"] is True
            assert session_payload["role"] == "guest"
            assert session_payload["permissions"] == ["read"]

            list_response = await remote_client.get("/api/automations")
            assert list_response.status_code == 200

            runs_response = await remote_client.get(f"/api/automations/{automation_id}/runs")
            assert runs_response.status_code == 200

            blocked_create = await remote_client.post(
                "/api/automations",
                json={
                    "name": "Blocked write",
                    "enabled": True,
                    "schedule": {
                        "type": "daily",
                        "time": "06:00",
                        "timezone": "UTC",
                        "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                    },
                    "model": "gpt-5.3-codex",
                    "prompt": "nope",
                    "accountIds": [accounts[0].id],
                },
            )
            assert blocked_create.status_code == 403
            assert blocked_create.json()["error"]["code"] == "read_only_access"

            blocked_update = await remote_client.patch(
                f"/api/automations/{automation_id}",
                json={"prompt": "blocked"},
            )
            assert blocked_update.status_code == 403
            assert blocked_update.json()["error"]["code"] == "read_only_access"

            blocked_delete = await remote_client.delete(f"/api/automations/{automation_id}")
            assert blocked_delete.status_code == 403
            assert blocked_delete.json()["error"]["code"] == "read_only_access"

            blocked_run = await remote_client.post(f"/api/automations/{automation_id}/run-now")
            assert blocked_run.status_code == 403
            assert blocked_run.json()["error"]["code"] == "read_only_access"


@pytest.mark.asyncio
async def test_automations_patch_model_rejects_retained_unsupported_reasoning_effort(async_client):
    await _populate_automation_reasoning_models()
    accounts = await _create_accounts("auto-reasoning-model-change")

    create_response = await async_client.post(
        "/api/automations",
        json={
            "name": "Reasoning compatibility",
            "enabled": True,
            "schedule": {
                "type": "daily",
                "time": "05:00",
                "timezone": "UTC",
                "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            },
            "model": "automation-reasoning-xhigh",
            "reasoningEffort": "xhigh",
            "prompt": "ping",
            "accountIds": [accounts[0].id],
        },
    )
    assert create_response.status_code == 200
    create_payload = create_response.json()
    assert create_payload["accountScopeAll"] is False
    automation_id = create_payload["id"]

    invalid_update = await async_client.patch(
        f"/api/automations/{automation_id}",
        json={"model": "automation-reasoning-medium"},
    )
    assert invalid_update.status_code == 400
    assert invalid_update.json()["error"]["code"] == "invalid_reasoning_effort"

    valid_update = await async_client.patch(
        f"/api/automations/{automation_id}",
        json={"model": "automation-reasoning-medium", "reasoningEffort": None},
    )
    assert valid_update.status_code == 200
    payload = valid_update.json()
    assert payload["model"] == "automation-reasoning-medium"
    assert payload["reasoningEffort"] is None


@pytest.mark.asyncio
async def test_automations_api_accepts_extended_reasoning_efforts(async_client):
    await _populate_automation_reasoning_models()
    accounts = await _create_accounts("auto-reasoning-ultra")

    create_response = await async_client.post(
        "/api/automations",
        json={
            "name": "Extended reasoning",
            "enabled": True,
            "schedule": {
                "type": "daily",
                "time": "05:00",
                "timezone": "UTC",
                "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            },
            "model": "automation-reasoning-ultra",
            "reasoningEffort": "ultra",
            "prompt": "ping",
            "accountIds": [accounts[0].id],
        },
    )
    assert create_response.status_code == 200
    assert create_response.json()["reasoningEffort"] == "ultra"

    update_response = await async_client.patch(
        f"/api/automations/{create_response.json()['id']}",
        json={"reasoningEffort": "max"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["reasoningEffort"] == "max"


@pytest.mark.asyncio
async def test_automations_run_history_keeps_claimed_model_snapshot(async_client, monkeypatch):
    await _populate_automation_reasoning_models()
    started_at = utcnow()
    accounts = await _create_accounts("auto-run-model-snapshot")
    compact_requests = []

    async def _fake_compact(request, *_args, **_kwargs):
        compact_requests.append(request)
        return SimpleNamespace(id="resp-model-snapshot")

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    create_response = await async_client.post(
        "/api/automations",
        json={
            "name": "Run model snapshot",
            "enabled": False,
            "schedule": {
                "type": "daily",
                "time": "05:00",
                "timezone": "UTC",
                "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            },
            "model": "automation-reasoning-xhigh",
            "reasoningEffort": "xhigh",
            "prompt": "old ping",
            "accountIds": [accounts[0].id],
        },
    )
    assert create_response.status_code == 200
    automation_id = create_response.json()["id"]

    run_response = await async_client.post(f"/api/automations/{automation_id}/run-now")
    assert run_response.status_code == 202
    run_id = run_response.json()["id"]

    update_response = await async_client.patch(
        f"/api/automations/{automation_id}",
        json={"model": "automation-reasoning-medium", "reasoningEffort": None, "prompt": "new ping"},
    )
    assert update_response.status_code == 200

    runs_response = await async_client.get(f"/api/automations/{automation_id}/runs")
    assert runs_response.status_code == 200
    run_item = runs_response.json()["items"][0]
    assert run_item["id"] == run_id
    assert run_item["model"] == "automation-reasoning-xhigh"
    assert run_item["reasoningEffort"] == "xhigh"

    old_model_response = await async_client.get(
        "/api/automations/runs",
        params={"automationId": automation_id, "model": "automation-reasoning-xhigh", "limit": 25, "offset": 0},
    )
    assert old_model_response.status_code == 200
    assert [item["id"] for item in old_model_response.json()["items"]] == [run_id]

    new_model_response = await async_client.get(
        "/api/automations/runs",
        params={"automationId": automation_id, "model": "automation-reasoning-medium", "limit": 25, "offset": 0},
    )
    assert new_model_response.status_code == 200
    assert new_model_response.json()["items"] == []
    assert len(compact_requests) == 1
    assert compact_requests[0].model == "automation-reasoning-xhigh"
    assert compact_requests[0].reasoning is not None
    assert compact_requests[0].reasoning.effort == "xhigh"
    assert compact_requests[0].input == [{"role": "user", "content": [{"type": "input_text", "text": "old ping"}]}]

    async with SessionLocal() as session:
        request_logs_repository = RequestLogsRepository(session)
        recent_logs = (await request_logs_repository.list_recent(limit=200, since=started_at)).logs
        matching_logs = [
            log
            for log in recent_logs
            if (
                log.transport == "automation"
                and log.account_id == accounts[0].id
                and log.request_id == "resp-model-snapshot"
            )
        ]
        assert len(matching_logs) == 1
        assert matching_logs[0].model == "automation-reasoning-xhigh"
        assert matching_logs[0].reasoning_effort == "xhigh"


@pytest.mark.asyncio
async def test_automations_run_now_aliases_ultra_reasoning_to_max_on_wire(async_client, monkeypatch):
    started_at = utcnow()
    accounts = await _create_accounts("auto-ultra-wire-alias")
    compact_requests = []

    async def _fake_compact(request, *_args, **_kwargs):
        compact_requests.append(request)
        return SimpleNamespace(id="resp-ultra-wire-alias")

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    # ``gpt-5.6-sol`` comes from the bootstrap catalog (registry snapshot is
    # reset per test) and advertises the client-plane ``ultra`` effort.
    create_response = await async_client.post(
        "/api/automations",
        json={
            "name": "Ultra wire alias",
            "enabled": False,
            "schedule": {
                "type": "daily",
                "time": "05:00",
                "timezone": "UTC",
                "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            },
            "model": "gpt-5.6-sol",
            "reasoningEffort": "ultra",
            "prompt": "ultra ping",
            "accountIds": [accounts[0].id],
        },
    )
    assert create_response.status_code == 200
    automation_id = create_response.json()["id"]
    assert create_response.json()["reasoningEffort"] == "ultra"

    run_response = await async_client.post(f"/api/automations/{automation_id}/run-now")
    assert run_response.status_code == 202
    assert run_response.json()["status"] == "success"

    # The configured client-plane effort is preserved in run history...
    runs_response = await async_client.get(f"/api/automations/{automation_id}/runs")
    assert runs_response.status_code == 200
    run_item = runs_response.json()["items"][0]
    assert run_item["reasoningEffort"] == "ultra"

    # ...but the compact request sent upstream carries the wire-safe alias.
    assert len(compact_requests) == 1
    assert compact_requests[0].model == "gpt-5.6-sol"
    assert compact_requests[0].reasoning is not None
    assert compact_requests[0].reasoning.effort == "max"

    async with SessionLocal() as session:
        request_logs_repository = RequestLogsRepository(session)
        recent_logs = (await request_logs_repository.list_recent(limit=200, since=started_at)).logs
        matching_logs = [
            log
            for log in recent_logs
            if (
                log.transport == "automation"
                and log.account_id == accounts[0].id
                and log.request_id == "resp-ultra-wire-alias"
            )
        ]
        assert len(matching_logs) == 1
        assert matching_logs[0].model == "gpt-5.6-sol"
        assert matching_logs[0].reasoning_effort == "max"


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
    assert run_payload["status"] == "success"
    assert run_payload["effectiveStatus"] == "success"
    assert run_payload["accountId"] == accounts[0].id

    executed = await _run_due_jobs(now_utc=utcnow() + timedelta(seconds=5))
    assert executed == 0

    async with SessionLocal() as session:
        request_logs_repository = RequestLogsRepository(session)
        recent_logs = (await request_logs_repository.list_recent(limit=200, since=started_at)).logs
        matching_logs = [
            log
            for log in recent_logs
            if log.transport == "automation" and log.account_id == accounts[0].id and log.model == "gpt-5.3-codex"
        ]
        assert matching_logs
        assert matching_logs[0].status == "success"


@pytest.mark.asyncio
async def test_automations_run_now_times_out_hung_compact_ping(async_client, monkeypatch):
    from app.core.config.settings import get_settings

    accounts = await _create_accounts("auto-hung-compact")
    compact_call_started = asyncio.Event()
    compact_call_cancelled = asyncio.Event()

    monkeypatch.setenv("CODEX_LB_COMPACT_REQUEST_BUDGET_SECONDS", "0.01")
    get_settings.cache_clear()

    async def _hung_compact(*_args, **_kwargs):
        compact_call_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            compact_call_cancelled.set()
            raise

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _hung_compact)

    create_response = await async_client.post(
        "/api/automations",
        json={
            "name": "Hung compact ping",
            "enabled": False,
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

    run_response = await asyncio.wait_for(async_client.post(f"/api/automations/{automation_id}/run-now"), timeout=1.0)
    assert run_response.status_code == 202
    run_payload = run_response.json()
    assert run_payload["status"] == "failed"
    assert run_payload["errorCode"] == "automation_ping_failed"
    assert run_payload["errorMessage"] == "Automation ping failed"
    assert compact_call_started.is_set()
    await asyncio.wait_for(compact_call_cancelled.wait(), timeout=1.0)


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
@pytest.mark.parametrize(
    ("schedule_patch", "expected_code"),
    [
        ({"time": "5:00"}, "invalid_schedule_time"),
        ({"thresholdMinutes": 241}, "invalid_schedule_threshold"),
    ],
)
async def test_automations_api_preserves_dashboard_schedule_error_contract(
    async_client,
    schedule_patch,
    expected_code,
):
    accounts = await _create_accounts(f"auto-invalid-schedule-{expected_code}")
    valid_schedule = {
        "type": "daily",
        "time": "05:00",
        "timezone": "UTC",
        "thresholdMinutes": 0,
        "days": ["mon", "tue", "wed", "thu", "fri"],
    }
    invalid_schedule = {**valid_schedule, **schedule_patch}

    create_response = await async_client.post(
        "/api/automations",
        json={
            "name": "Invalid schedule",
            "enabled": True,
            "schedule": invalid_schedule,
            "model": "gpt-5.3-codex",
            "prompt": "ping",
            "accountIds": [accounts[0].id],
        },
    )
    assert create_response.status_code == 400
    assert create_response.json()["error"]["code"] == expected_code

    valid_create_response = await async_client.post(
        "/api/automations",
        json={
            "name": f"Invalid schedule update {expected_code}",
            "enabled": True,
            "schedule": valid_schedule,
            "model": "gpt-5.3-codex",
            "prompt": "ping",
            "accountIds": [accounts[0].id],
        },
    )
    assert valid_create_response.status_code == 200
    automation_id = valid_create_response.json()["id"]

    update_response = await async_client.patch(
        f"/api/automations/{automation_id}",
        json={"schedule": invalid_schedule},
    )
    assert update_response.status_code == 400
    assert update_response.json()["error"]["code"] == expected_code


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
async def test_automations_run_now_fails_over_for_retryable_forced_account_failure(async_client, monkeypatch):
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
    assert run_payload["cycleKey"]

    executed = await _run_due_jobs(now_utc=utcnow() + timedelta(seconds=5))
    assert executed == 0

    runs_response = await async_client.get(f"/api/automations/{automation_id}/runs")
    assert runs_response.status_code == 200
    runs_payload = runs_response.json()["items"]
    assert len(runs_payload) == 2
    assert sorted(entry["status"] for entry in runs_payload) == ["partial", "success"]
    assert {entry["accountId"] for entry in runs_payload} == {accounts[1].id}

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

    grouped_success_for_account_response = await async_client.get(
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
    assert grouped_success_for_account_response.status_code == 200
    grouped_success_for_account_payload = grouped_success_for_account_response.json()
    assert grouped_success_for_account_payload["total"] == 1
    filtered_grouped_item = grouped_success_for_account_payload["items"][0]
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
    assert options_with_status_payload["accountIds"] == [accounts[1].id]
    assert call_order == [
        f"chatgpt-{accounts[0].id}",
        f"chatgpt-{accounts[1].id}",
        f"chatgpt-{accounts[1].id}",
    ]


@pytest.mark.asyncio
async def test_automations_run_now_omits_synthetic_chatgpt_account_id(async_client, monkeypatch):
    account = (await _create_accounts("auto-synthetic-account"))[0]
    async with SessionLocal() as session:
        await session.execute(
            update(Account)
            .where(Account.id == account.id)
            .values(chatgpt_account_id="email_auto_synthetic_account_example_com")
        )
        await session.commit()

    called_chatgpt_account_ids: list[str | None] = []

    async def _fake_compact(*_args, **kwargs):
        called_chatgpt_account_ids.append(kwargs.get("account_id"))
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    create_response = await async_client.post(
        "/api/automations",
        json={
            "name": "Synthetic account ping",
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

    run_response = await async_client.post(f"/api/automations/{create_response.json()['id']}/run-now")
    assert run_response.status_code == 202
    assert run_response.json()["status"] == "success"
    assert called_chatgpt_account_ids == [None]


@pytest.mark.asyncio
async def test_automations_run_now_records_permanent_account_failure_before_failover(async_client, monkeypatch):
    accounts = await _create_accounts("auto-permanent-failure-a", "auto-permanent-failure-b")
    call_order: list[str | None] = []

    async def _fake_compact(*_args, **kwargs):
        account_id = kwargs.get("account_id")
        call_order.append(account_id)
        if account_id == accounts[0].chatgpt_account_id:
            raise ProxyResponseError(
                403,
                openai_error("account_deactivated", "Account has been deactivated"),
            )
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    create_response = await async_client.post(
        "/api/automations",
        json={
            "name": "Permanent failover ping",
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
    assert call_order == [
        accounts[0].chatgpt_account_id,
        accounts[1].chatgpt_account_id,
        accounts[1].chatgpt_account_id,
    ]

    runs_response = await async_client.get(f"/api/automations/{automation_id}/runs")
    assert runs_response.status_code == 200
    runs_payload = runs_response.json()["items"]
    assert len(runs_payload) == 2
    assert sorted(entry["status"] for entry in runs_payload) == ["partial", "success"]

    async with SessionLocal() as session:
        accounts_repository = AccountsRepository(session)
        failed_account = await accounts_repository.get_by_id(accounts[0].id)
        assert failed_account is not None
        assert failed_account.status == AccountStatus.DEACTIVATED
        assert failed_account.deactivation_reason == "Account has been deactivated"


@pytest.mark.asyncio
async def test_automations_run_now_all_accounts_executes_all_accounts_with_delayed_slots(async_client, monkeypatch):
    accounts = await _create_accounts(
        "auto-manual-all-a",
        "auto-manual-all-b",
        "auto-manual-all-c",
        "auto-manual-unrelated",
    )
    started_at = utcnow()
    unrelated_scheduled_for = started_at - timedelta(seconds=5)

    async def _fake_compact(*_args, **_kwargs):
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        unrelated_job = await automations_repository.create_job(
            name="Unrelated manual due job",
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
            account_ids=[accounts[3].id],
        )
        unrelated_cycle_id = "unrelated"
        unrelated_cycle_key = f"manual:{unrelated_job.id}:{unrelated_cycle_id}"
        unrelated_cycle = await automations_repository.create_run_cycle(
            cycle_key=unrelated_cycle_key,
            job_id=unrelated_job.id,
            trigger="manual",
            cycle_expected_accounts=1,
            cycle_window_end=unrelated_scheduled_for,
            accounts=[(accounts[3].id, unrelated_scheduled_for)],
        )
        unrelated_run = await automations_repository.claim_run(
            job_id=unrelated_job.id,
            trigger="manual",
            slot_key=_manual_slot_key(unrelated_job.id, unrelated_cycle_id, accounts[3].id),
            cycle_key=unrelated_cycle.cycle_key,
            cycle_expected_accounts=unrelated_cycle.cycle_expected_accounts,
            cycle_window_end=unrelated_cycle.cycle_window_end,
            scheduled_for=unrelated_scheduled_for,
            started_at=unrelated_scheduled_for,
            account_id=accounts[3].id,
        )
        assert unrelated_run is not None

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
                "thresholdMinutes": 1,
            },
            "model": "gpt-5.3-codex",
            "prompt": "ping",
            "accountIds": [account.id for account in accounts[:3]],
        },
    )
    assert create_response.status_code == 200
    automation_id = create_response.json()["id"]

    run_response = await async_client.post(f"/api/automations/{automation_id}/run-now")
    assert run_response.status_code == 202
    run_payload = run_response.json()
    assert run_payload["trigger"] == "manual"
    assert run_payload["totalAccounts"] == 3
    assert run_payload["completedAccounts"] == 1
    assert run_payload["pendingAccounts"] == 2

    async with SessionLocal() as session:
        request_logs_repository = RequestLogsRepository(session)
        recent_logs = (await request_logs_repository.list_recent(limit=200, since=started_at)).logs
        observed_after_run_now = {
            log.account_id for log in recent_logs if log.transport == "automation" and log.model == "gpt-5.3-codex"
        }
        observed_target_accounts_after_run_now = observed_after_run_now & {account.id for account in accounts[:3]}
        assert len(observed_target_accounts_after_run_now) == 1
        assert accounts[3].id not in observed_after_run_now

    await _run_due_jobs(now_utc=utcnow() + timedelta(seconds=5))
    await _run_due_jobs(now_utc=utcnow() + timedelta(minutes=2))

    async with SessionLocal() as session:
        request_logs_repository = RequestLogsRepository(session)
        recent_logs = (await request_logs_repository.list_recent(limit=200, since=started_at)).logs
        observed = {
            log.account_id for log in recent_logs if log.transport == "automation" and log.model == "gpt-5.3-codex"
        }
        expected = {account.id for account in accounts[:3]}
        assert expected.issubset(observed)
        assert accounts[3].id in observed

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
    assert executed == 0

    async with SessionLocal() as session:
        account_repository = AccountsRepository(session)
        refreshed = await account_repository.get_by_id(account.id)
        assert refreshed is not None
        assert refreshed.status == AccountStatus.ACTIVE
        assert refreshed.reset_at is None
        assert refreshed.blocked_at is None

        request_logs_repository = RequestLogsRepository(session)
        recent_logs = (await request_logs_repository.list_recent(limit=200, since=started_at)).logs
        observed = {
            log.account_id for log in recent_logs if log.transport == "automation" and log.model == "gpt-5.3-codex"
        }
        assert account.id in observed


@pytest.mark.asyncio
async def test_grouped_manual_runs_hide_ineligible_unclaimed_placeholder_from_status_filters(async_client):
    accounts = await _create_accounts("auto-manual-hidden-a", "auto-manual-hidden-b")
    now = utcnow().replace(second=0, microsecond=0)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        job = await automations_repository.create_job(
            name="Hidden manual placeholder",
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
            account_ids=[accounts[0].id, accounts[1].id],
        )
        cycle_id = "hidden-placeholder"
        cycle = await automations_repository.create_run_cycle(
            cycle_key=f"manual:{job.id}:{cycle_id}",
            job_id=job.id,
            trigger="manual",
            cycle_expected_accounts=2,
            cycle_window_end=now + timedelta(minutes=5),
            accounts=[
                (accounts[0].id, now),
                (accounts[1].id, now),
            ],
        )
        success_run = await automations_repository.claim_run(
            job_id=job.id,
            trigger="manual",
            slot_key=_manual_slot_key(job.id, cycle_id, accounts[0].id),
            cycle_key=cycle.cycle_key,
            cycle_expected_accounts=cycle.cycle_expected_accounts,
            cycle_window_end=cycle.cycle_window_end,
            scheduled_for=now,
            started_at=now,
            account_id=accounts[0].id,
        )
        hidden_placeholder = await automations_repository.claim_run(
            job_id=job.id,
            trigger="manual",
            slot_key=_manual_slot_key(job.id, cycle_id, accounts[1].id),
            cycle_key=cycle.cycle_key,
            cycle_expected_accounts=cycle.cycle_expected_accounts,
            cycle_window_end=cycle.cycle_window_end,
            scheduled_for=now,
            started_at=now,
            account_id=accounts[1].id,
        )
        assert success_run is not None
        assert hidden_placeholder is not None
        await automations_repository.complete_run(
            success_run.id,
            status="success",
            finished_at=now + timedelta(seconds=5),
            account_id=accounts[0].id,
            error_code=None,
            error_message=None,
            attempt_count=1,
        )

    await _set_account_status(accounts[1].id, AccountStatus.RATE_LIMITED)

    grouped_response = await async_client.get(
        "/api/automations/runs",
        params={"automationId": job.id, "trigger": "manual", "limit": 25, "offset": 0},
    )
    assert grouped_response.status_code == 200
    grouped_payload = grouped_response.json()
    assert grouped_payload["total"] == 1
    grouped_item = grouped_payload["items"][0]
    assert grouped_item["effectiveStatus"] == "success"
    assert grouped_item["totalAccounts"] == 1
    assert grouped_item["completedAccounts"] == 1
    assert grouped_item["pendingAccounts"] == 0

    running_response = await async_client.get(
        "/api/automations/runs",
        params={
            "automationId": job.id,
            "trigger": "manual",
            "status": "running",
            "limit": 25,
            "offset": 0,
        },
    )
    assert running_response.status_code == 200
    assert running_response.json()["total"] == 0

    success_response = await async_client.get(
        "/api/automations/runs",
        params={
            "automationId": job.id,
            "trigger": "manual",
            "status": "success",
            "limit": 25,
            "offset": 0,
        },
    )
    assert success_response.status_code == 200
    success_payload = success_response.json()
    assert success_payload["total"] == 1
    assert success_payload["items"][0]["effectiveStatus"] == "success"


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

    now = utcnow() + timedelta(seconds=5)
    scheduled_for = now - timedelta(seconds=5)
    cycle_key = f"manual:race:{account.id}"
    async with SessionLocal() as setup_session:
        setup_repository = AutomationsRepository(setup_session)
        job = await setup_repository.create_job(
            name="Manual race",
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
        await setup_repository.create_run_cycle(
            cycle_key=cycle_key,
            job_id=job.id,
            trigger="manual",
            cycle_expected_accounts=1,
            cycle_window_end=scheduled_for,
            accounts=[(account.id, scheduled_for)],
        )
        initial_run = await setup_repository.claim_run(
            job_id=job.id,
            trigger="manual",
            slot_key=_manual_slot_key(job.id, "race", account.id),
            cycle_key=cycle_key,
            cycle_expected_accounts=1,
            cycle_window_end=scheduled_for,
            scheduled_for=scheduled_for,
            started_at=scheduled_for,
            account_id=account.id,
        )
        assert initial_run is not None

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

    runs_response = await async_client.get(f"/api/automations/{job.id}/runs")
    assert runs_response.status_code == 200
    runs_payload = runs_response.json()["items"]
    assert len(runs_payload) == 1
    assert runs_payload[0]["status"] == "success"
    assert runs_payload[0]["attemptCount"] == 1
    assert runs_payload[0]["scheduledFor"] == scheduled_for.isoformat() + "Z"


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
async def test_grouped_scheduled_runs_keep_cycle_order_when_later_account_starts_after_next_cycle(async_client):
    accounts = await _create_accounts("auto-scheduled-group-order-a", "auto-scheduled-group-order-b")
    first_now = datetime(2026, 4, 22, 10, 0, 0)
    second_now = first_now + timedelta(minutes=1)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        job = await automations_repository.create_job(
            name="Scheduled grouped order",
            enabled=True,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time="05:00",
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=0,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[account.id for account in accounts],
        )
        first_cycle = await automations_repository.create_run_cycle(
            cycle_key=f"scheduled:{job.id}:{first_now.isoformat()}",
            job_id=job.id,
            trigger="scheduled",
            cycle_expected_accounts=2,
            cycle_window_end=first_now,
            accounts=[
                (accounts[0].id, first_now),
                (accounts[1].id, first_now + timedelta(seconds=10)),
            ],
        )
        first_cycle_first_run = await automations_repository.claim_run(
            job_id=job.id,
            trigger="scheduled",
            slot_key=_scheduled_slot_key(job.id, account_id=accounts[0].id, due_slot=first_now),
            cycle_key=first_cycle.cycle_key,
            cycle_expected_accounts=first_cycle.cycle_expected_accounts,
            cycle_window_end=first_cycle.cycle_window_end,
            scheduled_for=first_now,
            started_at=first_now,
            account_id=accounts[0].id,
        )
        first_cycle_late_run = await automations_repository.claim_run(
            job_id=job.id,
            trigger="scheduled",
            slot_key=_scheduled_slot_key(
                job.id,
                account_id=accounts[1].id,
                due_slot=first_now + timedelta(seconds=10),
            ),
            cycle_key=first_cycle.cycle_key,
            cycle_expected_accounts=first_cycle.cycle_expected_accounts,
            cycle_window_end=first_cycle.cycle_window_end,
            scheduled_for=first_now + timedelta(seconds=10),
            started_at=second_now + timedelta(minutes=5),
            account_id=accounts[1].id,
        )
        second_cycle = await automations_repository.create_run_cycle(
            cycle_key=f"scheduled:{job.id}:{second_now.isoformat()}",
            job_id=job.id,
            trigger="scheduled",
            cycle_expected_accounts=2,
            cycle_window_end=second_now,
            accounts=[
                (accounts[0].id, second_now),
                (accounts[1].id, second_now + timedelta(seconds=10)),
            ],
        )
        second_cycle_run = await automations_repository.claim_run(
            job_id=job.id,
            trigger="scheduled",
            slot_key=_scheduled_slot_key(job.id, account_id=accounts[0].id, due_slot=second_now),
            cycle_key=second_cycle.cycle_key,
            cycle_expected_accounts=second_cycle.cycle_expected_accounts,
            cycle_window_end=second_cycle.cycle_window_end,
            scheduled_for=second_now,
            started_at=second_now,
            account_id=accounts[0].id,
        )
        assert first_cycle_first_run is not None
        assert first_cycle_late_run is not None
        assert second_cycle_run is not None

    grouped_response = await async_client.get(
        "/api/automations/runs",
        params={"automationId": job.id, "trigger": "scheduled", "limit": 25, "offset": 0},
    )
    assert grouped_response.status_code == 200
    grouped_items = grouped_response.json()["items"]
    assert len(grouped_items) == 2
    assert grouped_items[0]["cycleKey"] == second_cycle.cycle_key
    assert grouped_items[0]["startedAt"] == second_now.isoformat() + "Z"
    assert grouped_items[0]["scheduledFor"] == second_now.isoformat() + "Z"
    assert grouped_items[1]["cycleKey"] == first_cycle.cycle_key
    assert grouped_items[1]["startedAt"] == first_now.isoformat() + "Z"
    assert grouped_items[1]["scheduledFor"] == first_now.isoformat() + "Z"


@pytest.mark.asyncio
async def test_automations_list_last_run_uses_latest_scheduled_cycle_not_late_account_dispatch(async_client):
    accounts = await _create_accounts("auto-scheduled-last-run-a", "auto-scheduled-last-run-b")
    first_now = datetime(2026, 4, 22, 10, 0, 0)
    second_now = first_now + timedelta(minutes=1)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        job = await automations_repository.create_job(
            name="Scheduled last run",
            enabled=True,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time="05:00",
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=0,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[account.id for account in accounts],
        )
        first_cycle = await automations_repository.create_run_cycle(
            cycle_key=f"scheduled:{job.id}:{first_now.isoformat()}",
            job_id=job.id,
            trigger="scheduled",
            cycle_expected_accounts=2,
            cycle_window_end=first_now,
            accounts=[
                (accounts[0].id, first_now),
                (accounts[1].id, first_now + timedelta(seconds=10)),
            ],
        )
        first_cycle_first_run = await automations_repository.claim_run(
            job_id=job.id,
            trigger="scheduled",
            slot_key=_scheduled_slot_key(job.id, account_id=accounts[0].id, due_slot=first_now),
            cycle_key=first_cycle.cycle_key,
            cycle_expected_accounts=first_cycle.cycle_expected_accounts,
            cycle_window_end=first_cycle.cycle_window_end,
            scheduled_for=first_now,
            started_at=first_now,
            account_id=accounts[0].id,
        )
        first_cycle_late_run = await automations_repository.claim_run(
            job_id=job.id,
            trigger="scheduled",
            slot_key=_scheduled_slot_key(
                job.id,
                account_id=accounts[1].id,
                due_slot=first_now + timedelta(seconds=10),
            ),
            cycle_key=first_cycle.cycle_key,
            cycle_expected_accounts=first_cycle.cycle_expected_accounts,
            cycle_window_end=first_cycle.cycle_window_end,
            scheduled_for=first_now + timedelta(seconds=10),
            started_at=second_now + timedelta(minutes=5),
            account_id=accounts[1].id,
        )
        second_cycle = await automations_repository.create_run_cycle(
            cycle_key=f"scheduled:{job.id}:{second_now.isoformat()}",
            job_id=job.id,
            trigger="scheduled",
            cycle_expected_accounts=2,
            cycle_window_end=second_now,
            accounts=[
                (accounts[0].id, second_now),
                (accounts[1].id, second_now + timedelta(seconds=10)),
            ],
        )
        second_cycle_run = await automations_repository.claim_run(
            job_id=job.id,
            trigger="scheduled",
            slot_key=_scheduled_slot_key(job.id, account_id=accounts[0].id, due_slot=second_now),
            cycle_key=second_cycle.cycle_key,
            cycle_expected_accounts=second_cycle.cycle_expected_accounts,
            cycle_window_end=second_cycle.cycle_window_end,
            scheduled_for=second_now,
            started_at=second_now,
            account_id=accounts[0].id,
        )
        assert first_cycle_first_run is not None
        assert first_cycle_late_run is not None
        assert second_cycle_run is not None

    jobs_response = await async_client.get("/api/automations", params={"limit": 25, "offset": 0})
    assert jobs_response.status_code == 200
    jobs_items = jobs_response.json()["items"]
    matching = [item for item in jobs_items if item["id"] == job.id]
    assert len(matching) == 1
    assert matching[0]["lastRun"]["cycleKey"] == second_cycle.cycle_key


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
        cycle = await automations_repository.get_run_cycle(cycle_key=f"scheduled:{job.id}:{due_slot.isoformat()}")
        assert cycle is not None
        assert {entry.slot_key for entry in cycle.accounts} == {
            _scheduled_slot_key(job.id, account_id=entry.account_id, due_slot=due_slot) for entry in cycle.accounts
        }


@pytest.mark.asyncio
async def test_automations_due_run_uses_scheduled_slot_owner_before_fallback_account(db_setup, monkeypatch):
    del db_setup
    accounts = await _create_accounts("auto-slot-owner-a", "auto-slot-owner-b")
    now = utcnow().replace(second=0, microsecond=0)
    schedule_time = now.strftime("%H:%M")
    due_slot = datetime(now.year, now.month, now.day, now.hour, now.minute)
    second_slot = due_slot + timedelta(minutes=1)

    async def _fake_compact(*_args, **_kwargs):
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)

        job = await automations_repository.create_job(
            name="Scheduled fallback slot owner",
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
        cycle_key = f"scheduled:{job.id}:{due_slot.isoformat()}"
        cycle_window_end = second_slot
        await automations_repository.create_run_cycle(
            cycle_key=cycle_key,
            job_id=job.id,
            trigger="scheduled",
            cycle_expected_accounts=2,
            cycle_window_end=cycle_window_end,
            accounts=[(accounts[0].id, due_slot), (accounts[1].id, second_slot)],
        )
        fallback_run = await automations_repository.claim_run(
            job_id=job.id,
            trigger="scheduled",
            slot_key=_scheduled_slot_key(job.id, account_id=accounts[0].id, due_slot=due_slot),
            cycle_key=cycle_key,
            cycle_expected_accounts=2,
            cycle_window_end=cycle_window_end,
            scheduled_for=due_slot,
            started_at=due_slot,
            account_id=accounts[1].id,
        )
        assert fallback_run is not None
        await automations_repository.complete_run(
            fallback_run.id,
            status="success",
            finished_at=due_slot + timedelta(seconds=5),
            account_id=accounts[1].id,
            error_code=None,
            error_message=None,
            attempt_count=1,
        )

        executed = await service.run_due_jobs(now_utc=second_slot + timedelta(seconds=1))

        assert executed == 1
        runs = await automations_repository.list_runs(job.id, limit=20)
        assert len(runs) == 2
        assert {(run.slot_key, run.account_id) for run in runs} == {
            (_scheduled_slot_key(job.id, account_id=accounts[0].id, due_slot=due_slot), accounts[1].id),
            (_scheduled_slot_key(job.id, account_id=accounts[1].id, due_slot=due_slot), accounts[1].id),
        }


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
        assert executed_first == 1

        cycle_key = f"scheduled:{job.id}:{now.isoformat()}"
        cycle = await automations_repository.get_run_cycle(cycle_key=cycle_key)
        assert cycle is not None
        assert cycle.cycle_expected_accounts == 0
        assert cycle.accounts == []
        first_runs = await automations_repository.list_runs(job.id, limit=10)
        assert len(first_runs) == 1
        assert first_runs[0].status == "failed"
        assert first_runs[0].error_code == "no_available_accounts"

        await accounts_repository.update_status(account.id, AccountStatus.ACTIVE)
        executed_second = await service.run_due_jobs(now_utc=now + timedelta(minutes=4))

        assert executed_second == 0
        second_runs = await automations_repository.list_runs(job.id, limit=10)
        assert [run.id for run in second_runs] == [first_runs[0].id]


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
async def test_automations_due_run_executes_latest_missed_slot_before_todays_slot(db_setup, monkeypatch):
    del db_setup
    account = (await _create_accounts("auto-missed-before-today-slot"))[0]
    now = datetime(2026, 4, 21, 4, 0, 0)
    missed_slot = datetime(2026, 4, 20, 5, 0, 0)

    async def _fake_compact(*_args, **_kwargs):
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)

        job = await automations_repository.create_job(
            name="Missed slot before today's slot",
            enabled=True,
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
        await _set_job_updated_at(job.id, missed_slot - timedelta(hours=1))

        executed = await service.run_due_jobs(now_utc=now)
        runs = await automations_repository.list_runs(job.id, limit=10)

    assert executed == 1
    assert len(runs) == 1
    assert runs[0].trigger == "scheduled"
    assert runs[0].scheduled_for == missed_slot


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
async def test_automations_due_run_does_not_execute_same_day_when_account_targets_are_updated_after_slot(
    db_setup,
    monkeypatch,
):
    del db_setup
    accounts = await _create_accounts("auto-target-edit-after-slot-a", "auto-target-edit-after-slot-b")
    now = utcnow().replace(second=0, microsecond=0)
    due_slot = now - timedelta(hours=1)

    async def _fake_compact(*_args, **_kwargs):
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)

        job = await automations_repository.create_job(
            name="Target edit after slot",
            enabled=True,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time=due_slot.strftime("%H:%M"),
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=0,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[accounts[0].id],
        )
        await _set_job_updated_at(job.id, due_slot - timedelta(hours=1))

        updated = await automations_repository.update_job(job.id, account_ids=[accounts[1].id])
        assert updated is not None

        executed_after_update = await service.run_due_jobs(now_utc=now + timedelta(minutes=1))
        assert executed_after_update == 0

        executed_next_day = await service.run_due_jobs(now_utc=now + timedelta(days=1, minutes=1))
        assert executed_next_day == 1

        runs = await automations_repository.list_runs(job.id, limit=10)
        assert len(runs) == 1
        assert runs[0].trigger == "scheduled"
        assert runs[0].scheduled_for == due_slot + timedelta(days=1)
        assert runs[0].account_id == accounts[1].id


@pytest.mark.asyncio
async def test_automations_empty_patch_does_not_skip_due_same_day_slot(db_setup, async_client, monkeypatch):
    del db_setup
    account = (await _create_accounts("auto-empty-patch-after-slot"))[0]
    now = utcnow().replace(second=0, microsecond=0)
    due_slot = now - timedelta(hours=1)

    async def _fake_compact(*_args, **_kwargs):
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    create_response = await async_client.post(
        "/api/automations",
        json={
            "name": "No-op edit after slot",
            "enabled": True,
            "schedule": {
                "type": "daily",
                "time": due_slot.strftime("%H:%M"),
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
    await _set_job_updated_at(automation_id, due_slot - timedelta(hours=1))

    update_response = await async_client.patch(f"/api/automations/{automation_id}", json={})
    assert update_response.status_code == 200

    executed = await _run_due_jobs(now_utc=now + timedelta(minutes=1))
    assert executed == 1

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        runs = await automations_repository.list_runs(automation_id, limit=10)

    assert len(runs) == 1
    assert runs[0].scheduled_for == due_slot
    assert runs[0].account_id == account.id


@pytest.mark.asyncio
async def test_automations_noop_full_patch_does_not_skip_due_same_day_slot(db_setup, async_client, monkeypatch):
    del db_setup
    account = (await _create_accounts("auto-full-noop-patch-after-slot"))[0]
    now = utcnow().replace(second=0, microsecond=0)
    due_slot = now - timedelta(hours=1)
    payload = {
        "name": "Full no-op edit after slot",
        "enabled": True,
        "schedule": {
            "type": "daily",
            "time": due_slot.strftime("%H:%M"),
            "timezone": "UTC",
            "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        },
        "model": "gpt-5.3-codex",
        "prompt": "ping",
        "accountIds": [account.id],
    }

    async def _fake_compact(*_args, **_kwargs):
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    create_response = await async_client.post("/api/automations", json=payload)
    assert create_response.status_code == 200
    automation_id = create_response.json()["id"]
    await _set_job_updated_at(automation_id, due_slot - timedelta(hours=1))

    update_response = await async_client.patch(f"/api/automations/{automation_id}", json=payload)
    assert update_response.status_code == 200

    executed = await _run_due_jobs(now_utc=now + timedelta(minutes=1))
    assert executed == 1

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        runs = await automations_repository.list_runs(automation_id, limit=10)

    assert len(runs) == 1
    assert runs[0].scheduled_for == due_slot
    assert runs[0].account_id == account.id


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
async def test_automations_due_run_continues_existing_cycle_after_job_is_disabled(db_setup, monkeypatch):
    del db_setup
    accounts = await _create_accounts("auto-cycle-disable-a", "auto-cycle-disable-b")
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
            name="Existing cycle after disable",
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

        updated = await automations_repository.update_job(job.id, enabled=False)
        assert updated is not None
        await _set_job_updated_at(job.id, now + timedelta(minutes=1))

        executed_second = await service.run_due_jobs(now_utc=now + timedelta(minutes=3))
        assert executed_second == 1

        runs = await automations_repository.list_runs(job.id, limit=10)

    assert len(runs) == 2
    assert {run.account_id for run in runs} == {accounts[0].id, accounts[1].id}


@pytest.mark.asyncio
async def test_automations_due_run_continues_existing_cycle_after_schedule_update(db_setup, monkeypatch):
    del db_setup
    accounts = await _create_accounts("auto-cycle-schedule-update-a", "auto-cycle-schedule-update-b")
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
            name="Existing cycle after schedule update",
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

        updated = await automations_repository.update_job(job.id, schedule_time="07:00")
        assert updated is not None
        await _set_job_updated_at(job.id, now + timedelta(minutes=1))

        executed_second = await service.run_due_jobs(now_utc=now + timedelta(minutes=3))
        assert executed_second == 1

        runs = await automations_repository.list_runs(job.id, limit=10)

    assert len(runs) == 2
    assert {run.account_id for run in runs} == {accounts[0].id, accounts[1].id}
    assert {run.scheduled_for for run in runs} == {now, now + timedelta(minutes=2)}


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
async def test_automations_scheduler_does_not_reclaim_fresh_scheduled_claim(db_setup, monkeypatch):
    del db_setup
    account = (await _create_accounts("auto-scheduled-no-reclaim"))[0]
    now = utcnow().replace(second=0, microsecond=0)
    first_call_started = asyncio.Event()
    release_first_call = asyncio.Event()
    called_chatgpt_account_ids: list[str | None] = []

    async def _fake_compact(*_args, **kwargs):
        called_chatgpt_account_ids.append(kwargs.get("account_id"))
        if len(called_chatgpt_account_ids) == 1:
            first_call_started.set()
            await release_first_call.wait()
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        job = await automations_repository.create_job(
            name="No duplicate scheduled claim",
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
            account_ids=[account.id],
        )
        await _set_job_updated_at(job.id, now)

    first_run_task = asyncio.create_task(_run_due_jobs(now_utc=now))
    await asyncio.wait_for(first_call_started.wait(), timeout=1.0)
    second_executed = await _run_due_jobs(now_utc=now)
    release_first_call.set()
    first_executed = await first_run_task

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        runs = await automations_repository.list_runs(job.id, limit=10)

    assert first_executed == 1
    assert second_executed == 0
    assert called_chatgpt_account_ids == [account.chatgpt_account_id]
    assert len(runs) == 1
    assert runs[0].started_at > runs[0].scheduled_for


@pytest.mark.asyncio
async def test_automations_scheduler_uses_cycle_include_paused_snapshot(db_setup, monkeypatch):
    del db_setup
    account = (await _create_accounts("auto-cycle-paused-snapshot"))[0]
    await _set_account_status(account.id, AccountStatus.PAUSED)
    now = utcnow().replace(second=0, microsecond=0)
    called_chatgpt_account_ids: list[str | None] = []

    async def _fake_compact(*_args, **kwargs):
        called_chatgpt_account_ids.append(kwargs.get("account_id"))
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        job = await automations_repository.create_job(
            name="Frozen paused snapshot",
            enabled=True,
            include_paused_accounts=True,
            schedule_type="daily",
            schedule_time=now.strftime("%H:%M"),
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=0,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[account.id],
        )
        await _set_job_updated_at(job.id, now)
        cycle_key = f"scheduled:{job.id}:{now.isoformat()}"
        await automations_repository.create_run_cycle(
            cycle_key=cycle_key,
            job_id=job.id,
            trigger="scheduled",
            cycle_expected_accounts=1,
            cycle_window_end=now,
            accounts=[(account.id, now)],
            include_paused_accounts=True,
        )
        await session.execute(
            update(AutomationJob).where(AutomationJob.id == job.id).values(include_paused_accounts=False)
        )
        await session.commit()

    executed = await _run_due_jobs(now_utc=now)

    assert executed == 1
    assert called_chatgpt_account_ids == [account.chatgpt_account_id]


@pytest.mark.asyncio
async def test_automations_manual_delayed_run_uses_cycle_include_paused_snapshot(db_setup, monkeypatch):
    del db_setup
    account = (await _create_accounts("auto-manual-paused-snapshot"))[0]
    await _set_account_status(account.id, AccountStatus.PAUSED)
    now = utcnow().replace(second=0, microsecond=0)
    called_chatgpt_account_ids: list[str | None] = []

    async def _fake_compact(*_args, **kwargs):
        called_chatgpt_account_ids.append(kwargs.get("account_id"))
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)
    monkeypatch.setattr(
        "app.modules.automations.service._pick_dispatch_offsets_seconds",
        lambda **_kwargs: [60],
    )

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)
        job = await automations_repository.create_job(
            name="Frozen manual paused snapshot",
            enabled=False,
            include_paused_accounts=True,
            schedule_type="daily",
            schedule_time=now.strftime("%H:%M"),
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=1,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[account.id],
        )

        run = await service.run_now(job.id, now_utc=now)
        assert run.status == "running"
        updated_job = await automations_repository.update_job(job.id, include_paused_accounts=False)
        assert updated_job is not None

        executed = await service.run_due_jobs(now_utc=now + timedelta(seconds=61))
        stored_runs = await automations_repository.list_runs(job.id, limit=10)

    assert executed == 1
    assert called_chatgpt_account_ids == [account.chatgpt_account_id]
    assert len(stored_runs) == 1
    assert stored_runs[0].status == "success"


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

    filtered_pending_account_response = await async_client.get(
        "/api/automations/runs",
        params={
            "automationId": run["jobId"],
            "accountId": accounts[1].id,
            "status": "running",
            "limit": 10,
            "offset": 0,
        },
    )
    assert filtered_pending_account_response.status_code == 200
    filtered_pending_account_payload = filtered_pending_account_response.json()
    assert filtered_pending_account_payload["total"] == 1
    filtered_pending_account_item = filtered_pending_account_payload["items"][0]
    assert filtered_pending_account_item["id"] == run["id"]
    assert filtered_pending_account_item["effectiveStatus"] == "running"
    assert filtered_pending_account_item["pendingAccounts"] == 1

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
async def test_automations_run_details_keep_completed_deleted_account_from_reverting_to_pending(async_client):
    accounts = await _create_accounts("auto-deleted-summary-a", "auto-deleted-summary-b")
    now = utcnow().replace(second=0, microsecond=0)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)
        job = await automations_repository.create_job(
            name="Deleted account summary",
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
        success_run = await automations_repository.claim_run(
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
        assert success_run is not None
        await automations_repository.complete_run(
            success_run.id,
            status="success",
            finished_at=now + timedelta(seconds=5),
            account_id=accounts[0].id,
            error_code=None,
            error_message=None,
            attempt_count=1,
        )
        edited = await automations_repository.update_job(
            job.id,
            schedule_time=(now + timedelta(hours=1)).strftime("%H:%M"),
        )
        assert edited is not None
        deleted = await accounts_repository.delete(accounts[0].id)
        assert deleted is True

        details = await service.get_run_details(success_run.id)

    assert details.completed_accounts == 1
    assert details.pending_accounts == 1
    statuses_by_account = {entry.account_id: entry.status for entry in details.accounts}
    assert statuses_by_account[accounts[0].id] == "success"
    assert statuses_by_account[accounts[1].id] == "pending"


@pytest.mark.asyncio
async def test_automations_scheduler_keeps_completed_deleted_account_in_cycle_snapshot(async_client, monkeypatch):
    accounts = await _create_accounts("auto-deleted-scheduler-a", "auto-deleted-scheduler-b")
    now = utcnow().replace(second=0, microsecond=0)

    async def _fake_compact(*_args, **_kwargs):
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)
        job = await automations_repository.create_job(
            name="Deleted account scheduler summary",
            enabled=True,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time=now.strftime("%H:%M"),
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=1,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[accounts[0].id, accounts[1].id],
        )
        await _set_job_updated_at(job.id, now)
        cycle_key = f"scheduled:{job.id}:{now.isoformat()}"
        cycle = await automations_repository.create_run_cycle(
            cycle_key=cycle_key,
            job_id=job.id,
            trigger="scheduled",
            cycle_expected_accounts=2,
            cycle_window_end=now + timedelta(minutes=1),
            accounts=[
                (accounts[0].id, now),
                (accounts[1].id, now + timedelta(minutes=1)),
            ],
        )
        success_run = await automations_repository.claim_run(
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
        assert success_run is not None
        await automations_repository.complete_run(
            success_run.id,
            status="success",
            finished_at=now + timedelta(seconds=5),
            account_id=accounts[0].id,
            error_code=None,
            error_message=None,
            attempt_count=1,
        )
        deleted = await accounts_repository.delete(accounts[0].id)
        assert deleted is True

        executed = await service.run_due_jobs(now_utc=now + timedelta(minutes=1, seconds=5))
        details = await service.get_run_details(success_run.id)
        run_page = await service.list_runs_page(limit=10, offset=0, job_ids=[job.id])
        success_run_page = await service.list_runs_page(limit=10, offset=0, statuses=["success"], job_ids=[job.id])
        stored_cycle = await automations_repository.get_run_cycle(cycle_key=cycle_key)

    assert executed == 1
    assert details.total_accounts == 2
    assert details.completed_accounts == 2
    assert details.pending_accounts == 0
    assert {entry.account_id: entry.status for entry in details.accounts} == {
        accounts[0].id: "success",
        accounts[1].id: "success",
    }
    assert run_page.total == 1
    assert run_page.items[0].effective_status == "success"
    assert run_page.items[0].total_accounts == 2
    assert run_page.items[0].completed_accounts == 2
    assert success_run_page.total == 1
    assert success_run_page.items[0].id == run_page.items[0].id
    assert stored_cycle is not None
    assert [entry.account_id for entry in stored_cycle.accounts] == [accounts[0].id, accounts[1].id]


@pytest.mark.asyncio
async def test_due_scheduled_cycle_query_ignores_completed_deleted_account(async_client):
    account = (await _create_accounts("auto-due-query-deleted"))[0]
    now = utcnow().replace(second=0, microsecond=0)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        job = await automations_repository.create_job(
            name="Deleted account due query",
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
            account_ids=[account.id],
        )
        cycle_key = f"scheduled:{job.id}:{now.isoformat()}"
        cycle = await automations_repository.create_run_cycle(
            cycle_key=cycle_key,
            job_id=job.id,
            trigger="scheduled",
            cycle_expected_accounts=1,
            cycle_window_end=now,
            accounts=[(account.id, now)],
        )
        run = await automations_repository.claim_run(
            job_id=job.id,
            trigger="scheduled",
            slot_key=_scheduled_slot_key(job.id, account_id=account.id, due_slot=now),
            cycle_key=cycle.cycle_key,
            cycle_expected_accounts=cycle.cycle_expected_accounts,
            cycle_window_end=cycle.cycle_window_end,
            scheduled_for=now,
            started_at=now,
            account_id=account.id,
        )
        assert run is not None
        await automations_repository.complete_run(
            run.id,
            status="success",
            finished_at=now + timedelta(seconds=5),
            account_id=account.id,
            error_code=None,
            error_message=None,
            attempt_count=1,
        )
        deleted = await accounts_repository.delete(account.id)
        assert deleted is True

        due_cycles = await automations_repository.list_due_scheduled_run_cycles(
            job_id=job.id,
            now_utc=now + timedelta(seconds=30),
            limit=1,
        )

    assert due_cycles == []


@pytest.mark.asyncio
async def test_due_scheduled_cycle_query_ignores_completed_run_with_fallback_account_id(
    async_client,
):
    snapshot_account, fallback_account = await _create_accounts(
        "auto-due-query-fallback-a",
        "auto-due-query-fallback-b",
    )
    now = utcnow().replace(second=0, microsecond=0)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        job = await automations_repository.create_job(
            name="Fallback account due query",
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
            account_ids=[snapshot_account.id],
        )
        cycle_key = f"scheduled:{job.id}:{now.isoformat()}"
        cycle = await automations_repository.create_run_cycle(
            cycle_key=cycle_key,
            job_id=job.id,
            trigger="scheduled",
            cycle_expected_accounts=1,
            cycle_window_end=now,
            accounts=[(snapshot_account.id, now)],
        )
        run = await automations_repository.claim_run(
            job_id=job.id,
            trigger="scheduled",
            slot_key=_scheduled_slot_key(job.id, account_id=snapshot_account.id, due_slot=now),
            cycle_key=cycle.cycle_key,
            cycle_expected_accounts=cycle.cycle_expected_accounts,
            cycle_window_end=cycle.cycle_window_end,
            scheduled_for=now,
            started_at=now,
            account_id=snapshot_account.id,
        )
        assert run is not None
        await automations_repository.complete_run(
            run.id,
            status="success",
            finished_at=now + timedelta(seconds=5),
            account_id=fallback_account.id,
            error_code=None,
            error_message=None,
            attempt_count=1,
        )

        due_job_ids = await automations_repository.list_due_scheduled_run_cycle_job_ids(
            now_utc=now + timedelta(minutes=1, seconds=5),
        )
        due_cycles = await automations_repository.list_due_scheduled_run_cycles(
            job_id=job.id,
            now_utc=now + timedelta(minutes=1, seconds=5),
        )

    assert due_job_ids == []
    assert due_cycles == []


@pytest.mark.asyncio
async def test_due_scheduled_cycle_query_keeps_fallback_account_own_slot_due(
    async_client,
):
    snapshot_account, fallback_account = await _create_accounts(
        "auto-due-query-fallback-slot-a",
        "auto-due-query-fallback-slot-b",
    )
    now = utcnow().replace(second=0, microsecond=0)
    fallback_scheduled_for = now + timedelta(minutes=1)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        job = await automations_repository.create_job(
            name="Fallback account own slot due query",
            enabled=True,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time=now.strftime("%H:%M"),
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=1,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[snapshot_account.id, fallback_account.id],
        )
        cycle_key = f"scheduled:{job.id}:{now.isoformat()}"
        cycle = await automations_repository.create_run_cycle(
            cycle_key=cycle_key,
            job_id=job.id,
            trigger="scheduled",
            cycle_expected_accounts=2,
            cycle_window_end=fallback_scheduled_for,
            accounts=[(snapshot_account.id, now), (fallback_account.id, fallback_scheduled_for)],
        )
        run = await automations_repository.claim_run(
            job_id=job.id,
            trigger="scheduled",
            slot_key=_scheduled_slot_key(job.id, account_id=snapshot_account.id, due_slot=now),
            cycle_key=cycle.cycle_key,
            cycle_expected_accounts=cycle.cycle_expected_accounts,
            cycle_window_end=cycle.cycle_window_end,
            scheduled_for=now,
            started_at=now,
            account_id=snapshot_account.id,
        )
        assert run is not None
        await automations_repository.complete_run(
            run.id,
            status="success",
            finished_at=now + timedelta(seconds=5),
            account_id=fallback_account.id,
            error_code=None,
            error_message=None,
            attempt_count=1,
        )

        due_job_ids = await automations_repository.list_due_scheduled_run_cycle_job_ids(
            now_utc=fallback_scheduled_for + timedelta(seconds=5),
        )
        due_cycles = await automations_repository.list_due_scheduled_run_cycles(
            job_id=job.id,
            now_utc=fallback_scheduled_for + timedelta(seconds=5),
        )

    assert due_job_ids == [job.id]
    assert [cycle.cycle_key for cycle in due_cycles] == [cycle_key]


@pytest.mark.asyncio
async def test_automations_run_details_omit_ineligible_accounts_that_are_still_pending(async_client):
    accounts = await _create_accounts(
        "auto-ineligible-summary-a",
        "auto-ineligible-summary-b",
        "auto-ineligible-summary-c",
    )
    now = utcnow().replace(second=0, microsecond=0)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)
        job = await automations_repository.create_job(
            name="Ineligible pending summary",
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
            account_ids=[accounts[0].id, accounts[1].id, accounts[2].id],
        )
        cycle_key = f"scheduled:{job.id}:{now.isoformat()}"
        cycle = await automations_repository.create_run_cycle(
            cycle_key=cycle_key,
            job_id=job.id,
            trigger="scheduled",
            cycle_expected_accounts=3,
            cycle_window_end=now + timedelta(minutes=2),
            accounts=[
                (accounts[0].id, now),
                (accounts[1].id, now + timedelta(minutes=1)),
                (accounts[2].id, now + timedelta(minutes=2)),
            ],
        )
        success_run = await automations_repository.claim_run(
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
        assert success_run is not None
        await automations_repository.complete_run(
            success_run.id,
            status="success",
            finished_at=now + timedelta(seconds=5),
            account_id=accounts[0].id,
            error_code=None,
            error_message=None,
            attempt_count=1,
        )
        quota_updated = await accounts_repository.update_status(accounts[1].id, AccountStatus.QUOTA_EXCEEDED)
        assert quota_updated is True

        details = await service.get_run_details(success_run.id)

    assert details.total_accounts == 2
    assert details.completed_accounts == 1
    assert details.pending_accounts == 1
    statuses_by_account = {entry.account_id: entry.status for entry in details.accounts}
    assert statuses_by_account == {
        accounts[0].id: "success",
        accounts[2].id: "pending",
    }


@pytest.mark.asyncio
async def test_automations_scheduler_skips_ineligible_snapshot_accounts_without_failed_run(async_client, monkeypatch):
    accounts = await _create_accounts(
        "auto-ineligible-dispatch-a",
        "auto-ineligible-dispatch-b",
        "auto-ineligible-dispatch-c",
    )
    now = utcnow().replace(second=0, microsecond=0)

    async def _fake_compact(*_args, **_kwargs):
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)
        job = await automations_repository.create_job(
            name="Ineligible dispatch summary",
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
            account_ids=[accounts[0].id, accounts[1].id, accounts[2].id],
        )
        await _set_job_updated_at(job.id, now)
        cycle_key = f"scheduled:{job.id}:{now.isoformat()}"
        cycle = await automations_repository.create_run_cycle(
            cycle_key=cycle_key,
            job_id=job.id,
            trigger="scheduled",
            cycle_expected_accounts=3,
            cycle_window_end=now + timedelta(minutes=2),
            accounts=[
                (accounts[0].id, now),
                (accounts[1].id, now + timedelta(minutes=1)),
                (accounts[2].id, now + timedelta(minutes=2)),
            ],
        )
        success_run = await automations_repository.claim_run(
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
        assert success_run is not None
        await automations_repository.complete_run(
            success_run.id,
            status="success",
            finished_at=now + timedelta(seconds=5),
            account_id=accounts[0].id,
            error_code=None,
            error_message=None,
            attempt_count=1,
        )
        quota_updated = await accounts_repository.update_status(accounts[1].id, AccountStatus.QUOTA_EXCEEDED)
        assert quota_updated is True

        executed = await service.run_due_jobs(now_utc=now + timedelta(minutes=1, seconds=5))
        details = await service.get_run_details(success_run.id)
        active_updated = await accounts_repository.update_status(accounts[1].id, AccountStatus.ACTIVE)
        assert active_updated is True
        executed_after_reactivation = await service.run_due_jobs(now_utc=now + timedelta(minutes=1, seconds=30))
        details_after_reactivation = await service.get_run_details(success_run.id)
        executed_third = await service.run_due_jobs(now_utc=now + timedelta(minutes=2, seconds=5))
        run_page_after_third = await service.list_runs_page(limit=10, offset=0, job_ids=[job.id])
        cycle_runs = await automations_repository.list_runs_for_cycle_key(cycle_key=cycle.cycle_key)

    assert executed == 0
    assert executed_after_reactivation == 0
    assert executed_third == 1
    assert [run.account_id for run in cycle_runs] == [accounts[2].id, accounts[0].id]
    assert details.total_accounts == 2
    assert details.completed_accounts == 1
    assert details.pending_accounts == 1
    assert details_after_reactivation.total_accounts == 2
    assert details_after_reactivation.completed_accounts == 1
    assert details_after_reactivation.pending_accounts == 1
    statuses_by_account = {entry.account_id: entry.status for entry in details.accounts}
    assert statuses_by_account == {
        accounts[0].id: "success",
        accounts[2].id: "pending",
    }
    statuses_after_reactivation_by_account = {
        entry.account_id: entry.status for entry in details_after_reactivation.accounts
    }
    assert statuses_after_reactivation_by_account == {
        accounts[0].id: "success",
        accounts[2].id: "pending",
    }
    assert run_page_after_third.total == 1
    assert run_page_after_third.items[0].effective_status == "success"
    assert run_page_after_third.items[0].total_accounts == 2
    assert run_page_after_third.items[0].completed_accounts == 2
    assert run_page_after_third.items[0].pending_accounts == 0


@pytest.mark.asyncio
async def test_automations_scheduler_fails_over_for_retryable_forced_cycle_slot_failure(
    async_client,
    monkeypatch,
):
    accounts = await _create_accounts(
        "auto-pinned-slot-a",
        "auto-pinned-slot-b",
    )
    now = utcnow().replace(second=0, microsecond=0)
    called_chatgpt_account_ids: list[str | None] = []

    async def _fake_compact(*_args, **kwargs):
        account_id = kwargs.get("account_id")
        called_chatgpt_account_ids.append(account_id)
        if account_id == accounts[0].chatgpt_account_id:
            raise ProxyResponseError(
                429,
                openai_error("usage_limit_reached", "The usage limit has been reached"),
            )
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)
        job = await automations_repository.create_job(
            name="Pinned scheduled slot",
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
            account_ids=[accounts[0].id, accounts[1].id],
        )
        await _set_job_updated_at(job.id, now)

        executed = await service.run_due_jobs(now_utc=now + timedelta(seconds=5))
        cycle_runs = await automations_repository.list_runs_for_cycle_key(
            cycle_key=f"scheduled:{job.id}:{now.isoformat()}"
        )
        details = await service.get_run_details(cycle_runs[0].id)

    assert executed == 2
    assert called_chatgpt_account_ids == [
        accounts[0].chatgpt_account_id,
        accounts[1].chatgpt_account_id,
        accounts[1].chatgpt_account_id,
    ]
    assert sorted(run.status for run in cycle_runs) == ["partial", "success"]
    assert {run.account_id for run in cycle_runs} == {accounts[1].id}
    assert sorted(run.attempt_count for run in cycle_runs) == [1, 2]
    assert details.total_accounts == 2
    assert details.completed_accounts == 2
    assert details.pending_accounts == 0


@pytest.mark.asyncio
async def test_automations_scheduler_retries_with_persisted_cycle_accounts_after_job_account_edit(
    async_client,
    monkeypatch,
):
    accounts = await _create_accounts(
        "auto-snapshot-retry-a",
        "auto-snapshot-retry-b",
        "auto-snapshot-retry-c",
    )
    now = utcnow().replace(second=0, microsecond=0)
    called_chatgpt_account_ids: list[str | None] = []

    async def _fake_compact(*_args, **kwargs):
        account_id = kwargs.get("account_id")
        called_chatgpt_account_ids.append(account_id)
        if account_id == accounts[0].chatgpt_account_id:
            raise ProxyResponseError(
                429,
                openai_error("usage_limit_reached", "The usage limit has been reached"),
            )
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)
        job = await automations_repository.create_job(
            name="Snapshot retry scheduled slot",
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
            account_ids=[accounts[0].id, accounts[1].id],
        )
        await _set_job_updated_at(job.id, now)
        cycle_key = f"scheduled:{job.id}:{now.isoformat()}"
        cycle = await automations_repository.create_run_cycle(
            cycle_key=cycle_key,
            job_id=job.id,
            trigger="scheduled",
            cycle_expected_accounts=2,
            cycle_window_end=now,
            accounts=[
                (accounts[0].id, now),
                (accounts[1].id, now),
            ],
        )
        updated = await automations_repository.update_job(job.id, account_ids=[accounts[2].id])
        assert updated is not None

        executed = await service.run_due_jobs(now_utc=now + timedelta(seconds=5))
        cycle_runs = await automations_repository.list_runs_for_cycle_key(cycle_key=cycle.cycle_key)

    assert executed == 2
    assert called_chatgpt_account_ids == [
        accounts[0].chatgpt_account_id,
        accounts[1].chatgpt_account_id,
        accounts[1].chatgpt_account_id,
    ]
    assert accounts[2].chatgpt_account_id not in called_chatgpt_account_ids
    assert sorted(run.status for run in cycle_runs) == ["partial", "success"]
    assert {run.account_id for run in cycle_runs} == {accounts[1].id}


@pytest.mark.asyncio
async def test_automations_scheduler_reactivates_elapsed_reset_before_delayed_dispatch(
    async_client,
    monkeypatch,
):
    accounts = await _create_accounts(
        "auto-delayed-reset-a",
        "auto-delayed-reset-b",
    )
    now = utcnow().replace(second=0, microsecond=0)
    called_chatgpt_account_ids: list[str | None] = []

    async def _fake_compact(*_args, **kwargs):
        called_chatgpt_account_ids.append(kwargs.get("account_id"))
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)
        job = await automations_repository.create_job(
            name="Delayed reset recovery",
            enabled=True,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time=now.strftime("%H:%M"),
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=1,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[accounts[0].id, accounts[1].id],
        )
        await _set_job_updated_at(job.id, now)
        cycle_key = f"scheduled:{job.id}:{now.isoformat()}"
        cycle = await automations_repository.create_run_cycle(
            cycle_key=cycle_key,
            job_id=job.id,
            trigger="scheduled",
            cycle_expected_accounts=2,
            cycle_window_end=now + timedelta(minutes=1),
            accounts=[
                (accounts[0].id, now),
                (accounts[1].id, now + timedelta(minutes=1)),
            ],
        )
        success_run = await automations_repository.claim_run(
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
        assert success_run is not None
        await automations_repository.complete_run(
            success_run.id,
            status="success",
            finished_at=now + timedelta(seconds=5),
            account_id=accounts[0].id,
            error_code=None,
            error_message=None,
            attempt_count=1,
        )
        await accounts_repository.update_status(
            accounts[1].id,
            AccountStatus.RATE_LIMITED,
            reset_at=naive_utc_to_epoch(now + timedelta(seconds=30)),
            blocked_at=naive_utc_to_epoch(now),
        )

        executed = await service.run_due_jobs(now_utc=now + timedelta(minutes=1, seconds=5))
        details = await service.get_run_details(success_run.id)
        refreshed = await accounts_repository.get_by_id(accounts[1].id)

    assert executed == 1
    assert called_chatgpt_account_ids == [accounts[1].chatgpt_account_id]
    assert refreshed is not None
    assert refreshed.status == AccountStatus.ACTIVE
    assert refreshed.reset_at is None
    assert details.total_accounts == 2
    assert details.completed_accounts == 2
    assert details.pending_accounts == 0


@pytest.mark.asyncio
async def test_automations_scheduler_reduces_expected_count_when_stale_skip_was_already_deleted(
    async_client,
    monkeypatch,
):
    accounts = await _create_accounts(
        "auto-stale-skip-a",
        "auto-stale-skip-b",
        "auto-stale-skip-c",
    )
    now = utcnow().replace(second=0, microsecond=0)

    async def _fake_compact(*_args, **_kwargs):
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)
        job = await automations_repository.create_job(
            name="Stale skip expected count",
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
            account_ids=[accounts[0].id, accounts[1].id, accounts[2].id],
        )
        await _set_job_updated_at(job.id, now)
        cycle_key = f"scheduled:{job.id}:{now.isoformat()}"
        cycle = await automations_repository.create_run_cycle(
            cycle_key=cycle_key,
            job_id=job.id,
            trigger="scheduled",
            cycle_expected_accounts=3,
            cycle_window_end=now + timedelta(minutes=2),
            accounts=[
                (accounts[0].id, now),
                (accounts[1].id, now + timedelta(minutes=1)),
                (accounts[2].id, now + timedelta(minutes=2)),
            ],
        )
        success_run = await automations_repository.claim_run(
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
        assert success_run is not None
        await automations_repository.complete_run(
            success_run.id,
            status="success",
            finished_at=now + timedelta(seconds=5),
            account_id=accounts[0].id,
            error_code=None,
            error_message=None,
            attempt_count=1,
        )
        quota_updated = await accounts_repository.update_status(accounts[1].id, AccountStatus.QUOTA_EXCEEDED)
        assert quota_updated is True

        stale_cycle = await automations_repository.get_run_cycle(cycle_key=cycle.cycle_key)
        assert stale_cycle is not None
        deleted_by_other_worker = await automations_repository.delete_run_cycle_account(
            cycle_key=cycle.cycle_key,
            account_id=accounts[1].id,
        )
        assert deleted_by_other_worker is True
        real_get_run_cycle = automations_repository.get_run_cycle

        async def _fake_get_run_cycle(*, cycle_key: str):
            if cycle_key == stale_cycle.cycle_key:
                return stale_cycle
            return await real_get_run_cycle(cycle_key=cycle_key)

        async def _fake_delete_run_cycle_account(*, cycle_key: str, account_id: str) -> bool:
            assert cycle_key == stale_cycle.cycle_key
            assert account_id == accounts[1].id
            return False

        monkeypatch.setattr(automations_repository, "get_run_cycle", _fake_get_run_cycle)
        monkeypatch.setattr(automations_repository, "delete_run_cycle_account", _fake_delete_run_cycle_account)

        executed = await service.run_due_jobs(now_utc=now + timedelta(minutes=2, seconds=5))
        run_page = await service.list_runs_page(limit=10, offset=0, job_ids=[job.id])
        cycle_runs = await automations_repository.list_runs_for_cycle_key(cycle_key=cycle.cycle_key)

    account_two_run = next(run for run in cycle_runs if run.account_id == accounts[2].id)
    assert executed == 1
    assert account_two_run.cycle_expected_accounts == 2
    assert run_page.total == 1
    assert run_page.items[0].effective_status == "success"
    assert run_page.items[0].total_accounts == 2
    assert run_page.items[0].completed_accounts == 2
    assert run_page.items[0].pending_accounts == 0


@pytest.mark.asyncio
async def test_automations_scheduler_does_not_claim_reactivated_account_removed_from_stale_snapshot(
    async_client,
    monkeypatch,
):
    accounts = await _create_accounts(
        "auto-stale-reactivated-a",
        "auto-stale-reactivated-b",
        "auto-stale-reactivated-c",
    )
    now = utcnow().replace(second=0, microsecond=0)
    called_chatgpt_account_ids: list[str | None] = []

    async def _fake_compact(*_args, **kwargs):
        called_chatgpt_account_ids.append(kwargs.get("account_id"))
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)
        job = await automations_repository.create_job(
            name="Stale reactivated snapshot",
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
            account_ids=[accounts[0].id, accounts[1].id, accounts[2].id],
        )
        await _set_job_updated_at(job.id, now)
        cycle_key = f"scheduled:{job.id}:{now.isoformat()}"
        cycle = await automations_repository.create_run_cycle(
            cycle_key=cycle_key,
            job_id=job.id,
            trigger="scheduled",
            cycle_expected_accounts=3,
            cycle_window_end=now + timedelta(minutes=2),
            accounts=[
                (accounts[0].id, now),
                (accounts[1].id, now + timedelta(minutes=1)),
                (accounts[2].id, now + timedelta(minutes=2)),
            ],
        )
        success_run = await automations_repository.claim_run(
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
        assert success_run is not None
        await automations_repository.complete_run(
            success_run.id,
            status="success",
            finished_at=now + timedelta(seconds=5),
            account_id=accounts[0].id,
            error_code=None,
            error_message=None,
            attempt_count=1,
        )
        quota_updated = await accounts_repository.update_status(accounts[1].id, AccountStatus.QUOTA_EXCEEDED)
        assert quota_updated is True

        stale_cycle = await automations_repository.get_run_cycle(cycle_key=cycle.cycle_key)
        assert stale_cycle is not None
        deleted_by_other_worker = await automations_repository.delete_run_cycle_account(
            cycle_key=cycle.cycle_key,
            account_id=accounts[1].id,
        )
        assert deleted_by_other_worker is True
        active_updated = await accounts_repository.update_status(accounts[1].id, AccountStatus.ACTIVE)
        assert active_updated is True
        real_get_run_cycle = automations_repository.get_run_cycle

        async def _fake_get_run_cycle(*, cycle_key: str):
            if cycle_key == stale_cycle.cycle_key:
                return stale_cycle
            return await real_get_run_cycle(cycle_key=cycle_key)

        monkeypatch.setattr(automations_repository, "get_run_cycle", _fake_get_run_cycle)

        executed_reactivated = await service.run_due_jobs(now_utc=now + timedelta(minutes=1, seconds=30))
        executed_third = await service.run_due_jobs(now_utc=now + timedelta(minutes=2, seconds=5))
        monkeypatch.setattr(automations_repository, "get_run_cycle", real_get_run_cycle)
        cycle_runs = await automations_repository.list_runs_for_cycle_key(cycle_key=cycle.cycle_key)
        run_page = await service.list_runs_page(limit=10, offset=0, job_ids=[job.id])

    assert executed_reactivated == 0
    assert executed_third == 1
    assert called_chatgpt_account_ids == [accounts[2].chatgpt_account_id]
    assert [run.account_id for run in cycle_runs] == [accounts[2].id, accounts[0].id]
    assert run_page.total == 1
    assert run_page.items[0].effective_status == "success"
    assert run_page.items[0].total_accounts == 2
    assert run_page.items[0].completed_accounts == 2
    assert run_page.items[0].pending_accounts == 0


@pytest.mark.asyncio
async def test_automations_manual_cycle_omits_ineligible_pending_account_without_failed_run(
    async_client,
    monkeypatch,
):
    accounts = await _create_accounts(
        "auto-manual-ineligible-a",
        "auto-manual-ineligible-b",
    )
    now = utcnow().replace(second=0, microsecond=0)
    called_chatgpt_account_ids: list[str | None] = []

    async def _fake_compact(*_args, **kwargs):
        called_chatgpt_account_ids.append(kwargs.get("account_id"))
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)
    monkeypatch.setattr(
        "app.modules.automations.service._pick_dispatch_offsets_seconds",
        lambda **_kwargs: [0, 60],
    )

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)
        job = await automations_repository.create_job(
            name="Manual ineligible pending summary",
            enabled=False,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time=now.strftime("%H:%M"),
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=1,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[accounts[0].id, accounts[1].id],
        )
        representative_run = await service.run_now(job.id, now_utc=now)

        executed_first = await service.run_due_jobs(now_utc=now + timedelta(seconds=5))
        assert executed_first == 0
        quota_updated = await accounts_repository.update_status(accounts[1].id, AccountStatus.QUOTA_EXCEEDED)
        assert quota_updated is True

        details_before_due = await service.get_run_details(representative_run.id)
        executed_second = await service.run_due_jobs(now_utc=now + timedelta(minutes=1, seconds=5))
        details_after_due = await service.get_run_details(representative_run.id)
        active_updated = await accounts_repository.update_status(accounts[1].id, AccountStatus.ACTIVE)
        assert active_updated is True
        executed_after_reactivation = await service.run_due_jobs(now_utc=now + timedelta(minutes=2, seconds=5))
        details_after_reactivation = await service.get_run_details(representative_run.id)
        cycle_runs = await automations_repository.list_runs_for_cycle_key(cycle_key=representative_run.cycle_key or "")

    assert executed_second == 0
    assert executed_after_reactivation == 0
    assert called_chatgpt_account_ids == [accounts[0].chatgpt_account_id]
    assert details_before_due.total_accounts == 1
    assert details_before_due.completed_accounts == 1
    assert details_before_due.pending_accounts == 0
    assert details_after_due.total_accounts == 1
    assert details_after_due.completed_accounts == 1
    assert details_after_due.pending_accounts == 0
    assert details_after_due.run.effective_status == "success"
    assert details_after_reactivation.total_accounts == 1
    assert details_after_reactivation.completed_accounts == 1
    assert details_after_reactivation.pending_accounts == 0
    assert details_after_reactivation.run.effective_status == "success"
    assert {entry.account_id: entry.status for entry in details_after_due.accounts} == {
        accounts[0].id: "success",
    }
    assert {run.account_id: run.status for run in cycle_runs if run.account_id is not None} == {
        accounts[0].id: "success",
    }
    assert accounts[1].id not in {run.account_id for run in cycle_runs}


@pytest.mark.asyncio
async def test_automations_manual_cycle_reactivates_elapsed_reset_before_delayed_dispatch(
    async_client,
    monkeypatch,
):
    accounts = await _create_accounts(
        "auto-manual-delayed-reset-a",
        "auto-manual-delayed-reset-b",
    )
    now = utcnow().replace(second=0, microsecond=0)
    called_chatgpt_account_ids: list[str | None] = []

    async def _fake_compact(*_args, **kwargs):
        called_chatgpt_account_ids.append(kwargs.get("account_id"))
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)
    monkeypatch.setattr(
        "app.modules.automations.service._pick_dispatch_offsets_seconds",
        lambda **_kwargs: [0, 60],
    )

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)
        job = await automations_repository.create_job(
            name="Manual delayed reset recovery",
            enabled=False,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time=now.strftime("%H:%M"),
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=1,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[accounts[0].id, accounts[1].id],
        )
        representative_run = await service.run_now(job.id, now_utc=now)

        executed_first = await service.run_due_jobs(now_utc=now + timedelta(seconds=5))
        assert executed_first == 0
        rate_limited = await accounts_repository.update_status(
            accounts[1].id,
            AccountStatus.RATE_LIMITED,
            reset_at=naive_utc_to_epoch(now + timedelta(seconds=30)),
            blocked_at=naive_utc_to_epoch(now),
        )
        assert rate_limited is True

        executed_second = await service.run_due_jobs(now_utc=now + timedelta(minutes=1, seconds=5))
        details_after_due = await service.get_run_details(representative_run.id)
        refreshed = await accounts_repository.get_by_id(accounts[1].id)

    assert executed_second == 1
    assert called_chatgpt_account_ids == [accounts[0].chatgpt_account_id, accounts[1].chatgpt_account_id]
    assert refreshed is not None
    assert refreshed.status == AccountStatus.ACTIVE
    assert refreshed.reset_at is None
    assert details_after_due.total_accounts == 2
    assert details_after_due.completed_accounts == 2
    assert details_after_due.pending_accounts == 0


@pytest.mark.asyncio
async def test_automations_manual_cycle_skips_deleted_pending_placeholder(async_client, monkeypatch):
    accounts = await _create_accounts(
        "auto-manual-deleted-a",
        "auto-manual-deleted-b",
    )
    now = utcnow().replace(second=0, microsecond=0)
    called_chatgpt_account_ids: list[str | None] = []

    async def _fake_compact(*_args, **kwargs):
        called_chatgpt_account_ids.append(kwargs.get("account_id"))
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)
    monkeypatch.setattr(
        "app.modules.automations.service._pick_dispatch_offsets_seconds",
        lambda **_kwargs: [0, 60],
    )

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)
        job = await automations_repository.create_job(
            name="Manual deleted pending summary",
            enabled=False,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time=now.strftime("%H:%M"),
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=1,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[accounts[0].id, accounts[1].id],
        )
        representative_run = await service.run_now(job.id, now_utc=now)

        executed_first = await service.run_due_jobs(now_utc=now + timedelta(seconds=5))
        assert executed_first == 0
        deleted = await accounts_repository.delete(accounts[1].id)
        assert deleted is True

        executed_second = await service.run_due_jobs(now_utc=now + timedelta(minutes=1, seconds=5))
        details_after_due = await service.get_run_details(representative_run.id)
        stored_cycle = await automations_repository.get_run_cycle(cycle_key=representative_run.cycle_key or "")

    assert executed_second == 0
    assert called_chatgpt_account_ids == [accounts[0].chatgpt_account_id]
    assert details_after_due.total_accounts == 1
    assert details_after_due.completed_accounts == 1
    assert details_after_due.pending_accounts == 0
    assert details_after_due.run.effective_status == "success"
    assert stored_cycle is not None
    assert [entry.account_id for entry in stored_cycle.accounts] == [accounts[0].id]


@pytest.mark.asyncio
async def test_automations_manual_cycle_does_not_skip_stale_claimed_run_when_account_becomes_ineligible(
    async_client,
    monkeypatch,
):
    accounts = await _create_accounts("auto-manual-stale-claimed-a")
    now = utcnow().replace(second=0, microsecond=0)
    scheduled_for = now - timedelta(hours=3)
    claimed_started_at = now - timedelta(hours=2)
    called_chatgpt_account_ids: list[str | None] = []

    async def _fake_compact(*_args, **kwargs):
        called_chatgpt_account_ids.append(kwargs.get("account_id"))
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)
        job = await automations_repository.create_job(
            name="Manual stale claimed skip guard",
            enabled=False,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time=now.strftime("%H:%M"),
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=1,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[accounts[0].id],
        )
        cycle_id = "staleclaimed"
        cycle_key = f"manual:{job.id}:{cycle_id}"
        cycle = await automations_repository.create_run_cycle(
            cycle_key=cycle_key,
            job_id=job.id,
            trigger="manual",
            cycle_expected_accounts=1,
            cycle_window_end=now - timedelta(hours=1),
            accounts=[(accounts[0].id, scheduled_for)],
        )
        run = await automations_repository.claim_run(
            job_id=job.id,
            trigger="manual",
            slot_key=_manual_slot_key(job.id, cycle_id, accounts[0].id),
            cycle_key=cycle.cycle_key,
            cycle_expected_accounts=cycle.cycle_expected_accounts,
            cycle_window_end=cycle.cycle_window_end,
            scheduled_for=scheduled_for,
            started_at=claimed_started_at,
            account_id=accounts[0].id,
        )
        assert run is not None
        quota_updated = await accounts_repository.update_status(accounts[0].id, AccountStatus.QUOTA_EXCEEDED)
        assert quota_updated is True

        executed = await service.run_due_jobs(now_utc=now)
        stored_run = await automations_repository.get_run(run.id)
        stored_cycle = await automations_repository.get_run_cycle(cycle_key=cycle_key)

    assert executed == 0
    assert called_chatgpt_account_ids == []
    assert stored_run is not None
    assert stored_run.status == "running"
    assert stored_run.account_id == accounts[0].id
    assert stored_run.started_at == claimed_started_at
    assert stored_cycle is not None
    assert [entry.account_id for entry in stored_cycle.accounts] == [accounts[0].id]


@pytest.mark.asyncio
async def test_automations_manual_cycle_reclaims_timed_out_claimed_run(async_client, monkeypatch):
    accounts = await _create_accounts("auto-manual-stale-active-a")
    now = utcnow().replace(second=0, microsecond=0)
    scheduled_for = now - timedelta(hours=3)
    claimed_started_at = now - timedelta(hours=2)
    called_chatgpt_account_ids: list[str | None] = []

    async def _fake_compact(*_args, **kwargs):
        called_chatgpt_account_ids.append(kwargs.get("account_id"))
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)
        job = await automations_repository.create_job(
            name="Manual stale claimed reclaim",
            enabled=False,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time=now.strftime("%H:%M"),
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=1,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[accounts[0].id],
        )
        cycle_id = "stalenoexec"
        cycle_key = f"manual:{job.id}:{cycle_id}"
        cycle = await automations_repository.create_run_cycle(
            cycle_key=cycle_key,
            job_id=job.id,
            trigger="manual",
            cycle_expected_accounts=1,
            cycle_window_end=now - timedelta(hours=1),
            accounts=[(accounts[0].id, scheduled_for)],
        )
        run = await automations_repository.claim_run(
            job_id=job.id,
            trigger="manual",
            slot_key=_manual_slot_key(job.id, cycle_id, accounts[0].id),
            cycle_key=cycle.cycle_key,
            cycle_expected_accounts=cycle.cycle_expected_accounts,
            cycle_window_end=cycle.cycle_window_end,
            scheduled_for=scheduled_for,
            started_at=claimed_started_at,
            account_id=accounts[0].id,
        )
        assert run is not None

        executed = await service.run_due_jobs(now_utc=now)
        stored_run = await automations_repository.get_run(run.id)

    assert executed == 1
    assert called_chatgpt_account_ids == [accounts[0].chatgpt_account_id]
    assert stored_run is not None
    assert stored_run.status == "success"
    assert stored_run.account_id == accounts[0].id
    assert stored_run.started_at > claimed_started_at


@pytest.mark.asyncio
async def test_automations_scheduled_cycle_does_not_skip_claimed_run_when_account_becomes_ineligible(
    async_client,
    monkeypatch,
):
    accounts = await _create_accounts("auto-scheduled-claimed-ineligible-a")
    now = utcnow().replace(second=0, microsecond=0)
    scheduled_for = now - timedelta(hours=3)
    claimed_started_at = now - timedelta(seconds=5)
    called_chatgpt_account_ids: list[str | None] = []

    async def _fake_compact(*_args, **kwargs):
        called_chatgpt_account_ids.append(kwargs.get("account_id"))
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)
        job = await automations_repository.create_job(
            name="Scheduled claimed ineligible guard",
            enabled=True,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time=now.strftime("%H:%M"),
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=1,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[accounts[0].id],
        )
        cycle_key = f"scheduled:{job.id}:{now.isoformat()}"
        cycle = await automations_repository.create_run_cycle(
            cycle_key=cycle_key,
            job_id=job.id,
            trigger="scheduled",
            cycle_expected_accounts=1,
            cycle_window_end=now,
            accounts=[(accounts[0].id, scheduled_for)],
        )
        run = await automations_repository.claim_run(
            job_id=job.id,
            trigger="scheduled",
            slot_key=_scheduled_slot_key(job.id, account_id=accounts[0].id, due_slot=now),
            cycle_key=cycle.cycle_key,
            cycle_expected_accounts=cycle.cycle_expected_accounts,
            cycle_window_end=cycle.cycle_window_end,
            scheduled_for=scheduled_for,
            started_at=claimed_started_at,
            account_id=accounts[0].id,
        )
        assert run is not None
        quota_updated = await accounts_repository.update_status(accounts[0].id, AccountStatus.QUOTA_EXCEEDED)
        assert quota_updated is True

        executed = await service.run_due_jobs(now_utc=now)
        stored_run = await automations_repository.get_run(run.id)
        stored_cycle = await automations_repository.get_run_cycle(cycle_key=cycle_key)

    assert executed == 0
    assert called_chatgpt_account_ids == []
    assert stored_run is not None
    assert stored_run.status == "running"
    assert stored_run.account_id == accounts[0].id
    assert stored_run.started_at == claimed_started_at
    assert stored_cycle is not None
    assert [entry.account_id for entry in stored_cycle.accounts] == [accounts[0].id]


@pytest.mark.asyncio
async def test_automations_scheduled_cycle_reclaims_claimed_ineligible_run(async_client, monkeypatch):
    accounts = await _create_accounts("auto-scheduled-stale-ineligible-a", "auto-scheduled-stale-fallback-a")
    now = utcnow().replace(second=0, microsecond=0)
    scheduled_for = now - timedelta(hours=3)
    claimed_started_at = now - timedelta(hours=2)
    called_chatgpt_account_ids: list[str | None] = []

    async def _fake_compact(*_args, **kwargs):
        called_chatgpt_account_ids.append(kwargs.get("account_id"))
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)
        job = await automations_repository.create_job(
            name="Scheduled stale claimed ineligible reclaim",
            enabled=True,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time=now.strftime("%H:%M"),
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=1,
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
            cycle_expected_accounts=1,
            cycle_window_end=now,
            accounts=[(accounts[0].id, scheduled_for)],
        )
        run = await automations_repository.claim_run(
            job_id=job.id,
            trigger="scheduled",
            slot_key=_scheduled_slot_key(job.id, account_id=accounts[0].id, due_slot=now),
            cycle_key=cycle.cycle_key,
            cycle_expected_accounts=cycle.cycle_expected_accounts,
            cycle_window_end=cycle.cycle_window_end,
            scheduled_for=scheduled_for,
            started_at=claimed_started_at,
            account_id=accounts[0].id,
        )
        assert run is not None
        quota_updated = await accounts_repository.update_status(accounts[0].id, AccountStatus.QUOTA_EXCEEDED)
        assert quota_updated is True

        executed = await service.run_due_jobs(now_utc=now)
        stored_run = await automations_repository.get_run(run.id)
        stored_cycle = await automations_repository.get_run_cycle(cycle_key=cycle_key)

    assert executed == 1
    assert called_chatgpt_account_ids == []
    assert stored_run is not None
    assert stored_run.status == "failed"
    assert stored_run.account_id == accounts[0].id
    assert stored_run.started_at > claimed_started_at
    assert stored_run.error_code == "no_available_accounts"
    assert stored_cycle is not None
    assert [entry.account_id for entry in stored_cycle.accounts] == [accounts[0].id]


@pytest.mark.asyncio
async def test_automations_scheduled_cycle_reclaims_timed_out_claimed_run(async_client, monkeypatch):
    accounts = await _create_accounts("auto-scheduled-stale-active-a")
    now = utcnow().replace(second=0, microsecond=0)
    scheduled_for = now - timedelta(hours=3)
    claimed_started_at = now - timedelta(hours=2)
    called_chatgpt_account_ids: list[str | None] = []

    async def _fake_compact(*_args, **kwargs):
        called_chatgpt_account_ids.append(kwargs.get("account_id"))
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)
        job = await automations_repository.create_job(
            name="Scheduled stale claimed reclaim",
            enabled=True,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time=now.strftime("%H:%M"),
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=1,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[accounts[0].id],
        )
        cycle_key = f"scheduled:{job.id}:{now.isoformat()}"
        cycle = await automations_repository.create_run_cycle(
            cycle_key=cycle_key,
            job_id=job.id,
            trigger="scheduled",
            cycle_expected_accounts=1,
            cycle_window_end=now,
            accounts=[(accounts[0].id, scheduled_for)],
        )
        run = await automations_repository.claim_run(
            job_id=job.id,
            trigger="scheduled",
            slot_key=_scheduled_slot_key(job.id, account_id=accounts[0].id, due_slot=now),
            cycle_key=cycle.cycle_key,
            cycle_expected_accounts=cycle.cycle_expected_accounts,
            cycle_window_end=cycle.cycle_window_end,
            scheduled_for=scheduled_for,
            started_at=claimed_started_at,
            account_id=accounts[0].id,
        )
        assert run is not None

        executed = await service.run_due_jobs(now_utc=now)
        stored_run = await automations_repository.get_run(run.id)

    assert executed == 1
    assert called_chatgpt_account_ids == [accounts[0].chatgpt_account_id]
    assert stored_run is not None
    assert stored_run.status == "success"
    assert stored_run.account_id == accounts[0].id
    assert stored_run.started_at > claimed_started_at


@pytest.mark.asyncio
async def test_automations_scheduled_cycle_reclaim_keeps_all_account_failover_inside_cycle_snapshot(
    async_client,
    monkeypatch,
):
    accounts = await _create_accounts(
        "auto-scheduled-snapshot-stale-a",
        "auto-scheduled-snapshot-late-b",
    )
    now = utcnow().replace(second=0, microsecond=0)
    scheduled_for = now - timedelta(hours=3)
    claimed_started_at = now - timedelta(hours=2)
    called_chatgpt_account_ids: list[str | None] = []

    async def _fake_compact(*_args, **kwargs):
        account_id = kwargs.get("account_id")
        called_chatgpt_account_ids.append(account_id)
        if account_id == accounts[0].chatgpt_account_id:
            raise ProxyResponseError(
                429,
                openai_error("usage_limit_reached", "The usage limit has been reached"),
            )
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)
        job = await automations_repository.create_job(
            name="Scheduled stale claimed snapshot reclaim",
            enabled=True,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time=now.strftime("%H:%M"),
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=1,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[],
        )
        cycle_key = f"scheduled:{job.id}:{now.isoformat()}"
        cycle = await automations_repository.create_run_cycle(
            cycle_key=cycle_key,
            job_id=job.id,
            trigger="scheduled",
            cycle_expected_accounts=1,
            cycle_window_end=now,
            accounts=[(accounts[0].id, scheduled_for)],
        )
        run = await automations_repository.claim_run(
            job_id=job.id,
            trigger="scheduled",
            slot_key=_scheduled_slot_key(job.id, account_id=accounts[0].id, due_slot=now),
            cycle_key=cycle.cycle_key,
            cycle_expected_accounts=cycle.cycle_expected_accounts,
            cycle_window_end=cycle.cycle_window_end,
            scheduled_for=scheduled_for,
            started_at=claimed_started_at,
            account_id=accounts[0].id,
        )
        assert run is not None

        executed = await service.run_due_jobs(now_utc=now)
        stored_run = await automations_repository.get_run(run.id)
        stored_cycle = await automations_repository.get_run_cycle(cycle_key=cycle_key)

    assert executed == 1
    assert called_chatgpt_account_ids == [accounts[0].chatgpt_account_id]
    assert stored_run is not None
    assert stored_run.status == "failed"
    assert stored_run.account_id == accounts[0].id
    assert stored_run.started_at > claimed_started_at
    assert stored_run.error_code == "usage_limit_reached"
    assert stored_cycle is not None
    assert [entry.account_id for entry in stored_cycle.accounts] == [accounts[0].id]


@pytest.mark.asyncio
async def test_automations_scheduler_finds_old_cycle_with_only_stale_running_rows(async_client, monkeypatch):
    accounts = await _create_accounts("auto-scheduled-old-stale-a")
    now = utcnow().replace(second=0, microsecond=0)
    due_slot = now - timedelta(days=1)
    scheduled_for = due_slot
    claimed_started_at = now - timedelta(hours=2)
    called_chatgpt_account_ids: list[str | None] = []

    async def _fake_compact(*_args, **kwargs):
        called_chatgpt_account_ids.append(kwargs.get("account_id"))
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)
        job = await automations_repository.create_job(
            name="Old scheduled stale claimed reclaim",
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
            account_ids=[accounts[0].id],
        )
        await _set_job_updated_at(job.id, due_slot - timedelta(hours=1))
        cycle_key = f"scheduled:{job.id}:{due_slot.isoformat()}"
        cycle = await automations_repository.create_run_cycle(
            cycle_key=cycle_key,
            job_id=job.id,
            trigger="scheduled",
            cycle_expected_accounts=1,
            cycle_window_end=due_slot,
            accounts=[(accounts[0].id, scheduled_for)],
        )
        disabled_job = await automations_repository.update_job(job.id, enabled=False)
        assert disabled_job is not None
        run = await automations_repository.claim_run(
            job_id=job.id,
            trigger="scheduled",
            slot_key=_scheduled_slot_key(job.id, account_id=accounts[0].id, due_slot=due_slot),
            cycle_key=cycle.cycle_key,
            cycle_expected_accounts=cycle.cycle_expected_accounts,
            cycle_window_end=cycle.cycle_window_end,
            scheduled_for=scheduled_for,
            started_at=claimed_started_at,
            account_id=accounts[0].id,
        )
        assert run is not None

        due_job_ids = await automations_repository.list_due_scheduled_run_cycle_job_ids(now_utc=now)
        due_cycles = await automations_repository.list_due_scheduled_run_cycles(job_id=job.id, now_utc=now)
        executed = await service.run_due_jobs(now_utc=now)
        stored_run = await automations_repository.get_run(run.id)

    assert job.id in due_job_ids
    assert [cycle.cycle_key for cycle in due_cycles] == [cycle_key]
    assert executed == 1
    assert called_chatgpt_account_ids == [accounts[0].chatgpt_account_id]
    assert stored_run is not None
    assert stored_run.status == "success"
    assert stored_run.account_id == accounts[0].id
    assert stored_run.started_at > claimed_started_at


@pytest.mark.asyncio
async def test_automations_scheduled_cycle_execution_uses_upstream_route(async_client, monkeypatch):
    accounts = await _create_accounts("auto-scheduled-route-a")
    now = utcnow().replace(second=0, microsecond=0)
    compact_calls: list[dict[str, object]] = []
    resolved_accounts: list[str] = []
    route = SimpleNamespace(mode="account", pool_id="pool", endpoint_id="endpoint")

    async def _fake_compact(*_args, **kwargs):
        compact_calls.append(kwargs)
        return SimpleNamespace()

    async def _fake_resolve_route(
        _account: Account,
        *,
        encryptor: object,
    ) -> object:
        resolved_accounts.append(_account.id)
        return route

    monkeypatch.setattr("app.modules.automations.service._resolve_upstream_route_for_account", _fake_resolve_route)
    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)
        job = await automations_repository.create_job(
            name="Scheduled route ping",
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
            account_ids=[accounts[0].id],
        )
        _ = await automations_repository.create_run_cycle(
            cycle_key=f"scheduled:{job.id}:{now.isoformat()}",
            job_id=job.id,
            trigger="scheduled",
            cycle_expected_accounts=1,
            cycle_window_end=now,
            accounts=[(accounts[0].id, now)],
        )

        executed = await service.run_due_jobs(now_utc=now)

    assert executed == 1
    assert len(compact_calls) == 1
    assert resolved_accounts == [accounts[0].id]
    assert compact_calls[0]["route"] is route
    assert compact_calls[0]["allow_direct_egress"] is False


@pytest.mark.asyncio
async def test_automations_manual_cycle_all_skipped_placeholders_are_not_reported_success(
    async_client,
    monkeypatch,
):
    accounts = await _create_accounts(
        "auto-manual-all-skipped-a",
        "auto-manual-all-skipped-b",
    )
    now = utcnow().replace(second=0, microsecond=0)
    called_chatgpt_account_ids: list[str | None] = []

    async def _fake_compact(*_args, **kwargs):
        called_chatgpt_account_ids.append(kwargs.get("account_id"))
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)
    monkeypatch.setattr(
        "app.modules.automations.service._pick_dispatch_offsets_seconds",
        lambda **_kwargs: [60, 120],
    )

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        accounts_repository = AccountsRepository(session)
        service = AutomationsService(automations_repository, accounts_repository)
        job = await automations_repository.create_job(
            name="Manual all skipped summary",
            enabled=False,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time=now.strftime("%H:%M"),
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=1,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[accounts[0].id, accounts[1].id],
        )
        await service.run_now(job.id, now_utc=now)
        for account in accounts:
            quota_updated = await accounts_repository.update_status(account.id, AccountStatus.QUOTA_EXCEEDED)
            assert quota_updated is True

        executed = await service.run_due_jobs(now_utc=now + timedelta(minutes=2, seconds=5))
        run_page = await service.list_runs_page(limit=10, offset=0, job_ids=[job.id])

    assert executed == 0
    assert called_chatgpt_account_ids == []
    assert run_page.total == 1
    assert run_page.items[0].effective_status == "partial"
    assert run_page.items[0].total_accounts == 0
    assert run_page.items[0].completed_accounts == 0
    assert run_page.items[0].pending_accounts == 0


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
                "thresholdMinutes": 1,
                "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            },
            "model": "gpt-5.3-codex",
            "prompt": "ping",
            "accountIds": [accounts[0].id, accounts[1].id],
        },
    )
    assert create_response.status_code == 200
    automation_id = create_response.json()["id"]

    monkeypatch.setattr(
        "app.modules.automations.service._pick_dispatch_offsets_seconds",
        lambda **_kwargs: [0, 60],
    )

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
async def test_scheduled_cycle_status_filter_uses_current_account_eligibility(async_client):
    accounts = await _create_accounts("auto-cycle-filter-eligible-a", "auto-cycle-filter-eligible-b")
    now = utcnow().replace(second=0, microsecond=0) - timedelta(hours=1)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        job = await automations_repository.create_job(
            name="Cycle filter eligibility",
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
            cycle_window_end=now + timedelta(minutes=5),
            accounts=[
                (accounts[0].id, now),
                (accounts[1].id, now + timedelta(minutes=1)),
            ],
        )
        success_run = await automations_repository.claim_run(
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
        assert success_run is not None
        await automations_repository.complete_run(
            success_run.id,
            status="success",
            finished_at=now + timedelta(seconds=5),
            account_id=accounts[0].id,
            error_code=None,
            error_message=None,
            attempt_count=1,
        )

    await _set_account_status(accounts[1].id, AccountStatus.RATE_LIMITED)

    grouped_response = await async_client.get(
        "/api/automations/runs",
        params={"automationId": job.id, "trigger": "scheduled", "limit": 25, "offset": 0},
    )
    assert grouped_response.status_code == 200
    grouped_payload = grouped_response.json()
    assert grouped_payload["total"] == 1
    assert grouped_payload["items"][0]["effectiveStatus"] == "success"
    assert grouped_payload["items"][0]["totalAccounts"] == 1
    assert grouped_payload["items"][0]["pendingAccounts"] == 0

    success_response = await async_client.get(
        "/api/automations/runs",
        params={
            "automationId": job.id,
            "trigger": "scheduled",
            "status": "success",
            "limit": 25,
            "offset": 0,
        },
    )
    assert success_response.status_code == 200
    success_payload = success_response.json()
    assert success_payload["total"] == 1
    assert success_payload["items"][0]["effectiveStatus"] == "success"

    running_response = await async_client.get(
        "/api/automations/runs",
        params={
            "automationId": job.id,
            "trigger": "scheduled",
            "status": "running",
            "limit": 25,
            "offset": 0,
        },
    )
    assert running_response.status_code == 200
    assert running_response.json()["total"] == 0


@pytest.mark.asyncio
async def test_scheduled_cycle_status_filter_matches_completed_slots_by_slot_key(async_client):
    accounts = await _create_accounts("auto-cycle-slot-filter-a", "auto-cycle-slot-filter-b")
    now = utcnow().replace(second=0, microsecond=0) - timedelta(hours=1)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        job = await automations_repository.create_job(
            name="Cycle slot-key filter",
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
            cycle_window_end=now + timedelta(minutes=5),
            accounts=[
                (accounts[0].id, now),
                (accounts[1].id, now + timedelta(minutes=1)),
            ],
        )
        first_slot_run = await automations_repository.claim_run(
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
        assert first_slot_run is not None
        await automations_repository.complete_run(
            first_slot_run.id,
            status="success",
            finished_at=now + timedelta(seconds=5),
            account_id=accounts[1].id,
            error_code=None,
            error_message=None,
            attempt_count=1,
        )

    grouped_response = await async_client.get(
        "/api/automations/runs",
        params={"automationId": job.id, "trigger": "scheduled", "limit": 25, "offset": 0},
    )
    assert grouped_response.status_code == 200
    grouped_payload = grouped_response.json()
    assert grouped_payload["total"] == 1
    assert grouped_payload["items"][0]["effectiveStatus"] == "partial"
    assert grouped_payload["items"][0]["totalAccounts"] == 2
    assert grouped_payload["items"][0]["completedAccounts"] == 1
    assert grouped_payload["items"][0]["pendingAccounts"] == 1

    success_response = await async_client.get(
        "/api/automations/runs",
        params={
            "automationId": job.id,
            "trigger": "scheduled",
            "status": "success",
            "limit": 25,
            "offset": 0,
        },
    )
    assert success_response.status_code == 200
    assert success_response.json()["total"] == 0


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
async def test_automations_manual_cycle_totals_omit_ineligible_placeholder_without_dropping_completed(
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
    assert executed == 0

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
    assert run_payload["errorCode"] == "no_available_accounts"
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
    assert run_item["errorCode"] == "no_available_accounts"
    assert run_item["errorMessage"] == "No available accounts configured for automation job"

    details_response = await async_client.get(f"/api/automations/runs/{run_id}/details")
    assert details_response.status_code == 200
    details_payload = details_response.json()
    assert details_payload["run"]["id"] == run_id
    assert details_payload["run"]["errorCode"] == "no_available_accounts"
    assert details_payload["run"]["errorMessage"] == "No available accounts configured for automation job"
    assert details_payload["totalAccounts"] == 0
    assert details_payload["completedAccounts"] == 0
    assert details_payload["pendingAccounts"] == 0
    assert details_payload["accounts"] == []


@pytest.mark.asyncio
async def test_automations_deleted_explicit_account_does_not_broaden_to_all_accounts(async_client, monkeypatch):
    accounts = await _create_accounts("auto-explicit-deleted-a", "auto-explicit-deleted-b")
    called_chatgpt_account_ids: list[str | None] = []

    async def _fake_compact(*_args, **kwargs):
        called_chatgpt_account_ids.append(kwargs.get("account_id"))
        return SimpleNamespace()

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    create_response = await async_client.post(
        "/api/automations",
        json={
            "name": "Deleted explicit account scope",
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

    async with SessionLocal() as session:
        accounts_repository = AccountsRepository(session)
        deleted = await accounts_repository.delete(accounts[0].id)
        assert deleted is True

    run_response = await async_client.post(f"/api/automations/{automation_id}/run-now")
    assert run_response.status_code == 202
    run_payload = run_response.json()
    assert run_payload["status"] == "failed"
    assert run_payload["errorCode"] == "no_available_accounts"
    assert called_chatgpt_account_ids == []


@pytest.mark.asyncio
async def test_automations_scheduled_cycle_records_failed_run_without_available_accounts(async_client, monkeypatch):
    accounts = await _create_accounts("auto-scheduled-empty-a")
    now = utcnow().replace(second=0, microsecond=0)

    async def _fake_compact(*_args, **_kwargs):
        raise AssertionError("scheduled zero-account cycle must not call upstream")

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    create_response = await async_client.post(
        "/api/automations",
        json={
            "name": "Scheduled cycle without eligible accounts",
            "enabled": True,
            "schedule": {
                "type": "daily",
                "time": now.strftime("%H:%M"),
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
    await _set_job_updated_at(automation_id, now - timedelta(hours=1))

    future_reset_at = naive_utc_to_epoch(now + timedelta(hours=1))
    await _set_account_status_with_reset(
        accounts[0].id,
        AccountStatus.RATE_LIMITED,
        reset_at=future_reset_at,
    )

    executed = await _run_due_jobs(now_utc=now)
    assert executed == 1

    runs_response = await async_client.get(
        "/api/automations/runs",
        params={"automationId": automation_id, "trigger": "scheduled", "limit": 25, "offset": 0},
    )
    assert runs_response.status_code == 200
    runs_payload = runs_response.json()
    assert runs_payload["total"] == 1
    run_item = runs_payload["items"][0]
    assert run_item["status"] == "failed"
    assert run_item["totalAccounts"] == 0
    assert run_item["completedAccounts"] == 0
    assert run_item["pendingAccounts"] == 0
    assert run_item["errorCode"] == "no_available_accounts"
    assert run_item["errorMessage"] == "No available accounts configured for automation job"


@pytest.mark.asyncio
async def test_automations_scheduler_finishes_persisted_empty_scheduled_cycle_after_restart(db_setup, monkeypatch):
    del db_setup
    now = utcnow().replace(second=0, microsecond=0)
    due_slot = now - timedelta(minutes=5)

    async def _fake_compact(*_args, **_kwargs):
        raise AssertionError("scheduled zero-account cycle must not call upstream")

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        job = await automations_repository.create_job(
            name="Restarted empty scheduled cycle",
            enabled=False,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time=due_slot.strftime("%H:%M"),
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=0,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[],
        )
        cycle_key = f"scheduled:{job.id}:{due_slot.isoformat()}"
        await automations_repository.create_run_cycle(
            cycle_key=cycle_key,
            job_id=job.id,
            trigger="scheduled",
            cycle_expected_accounts=0,
            cycle_window_end=due_slot,
            accounts=[],
        )

    async with SessionLocal() as session:
        await session.execute(
            update(AutomationJob)
            .where(AutomationJob.id == job.id)
            .values(enabled=True, updated_at=now + timedelta(hours=1))
        )
        await session.commit()

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        enabled_job_ids = [enabled_job.id for enabled_job in await automations_repository.list_enabled_jobs()]
        assert job.id in enabled_job_ids
        due_cycles = await automations_repository.list_due_scheduled_run_cycles(job_id=job.id, now_utc=now)
        assert [cycle.cycle_key for cycle in due_cycles] == [cycle_key]
        assert await automations_repository.list_runs_for_cycle_key(cycle_key=cycle_key) == []

    executed = await _run_due_jobs(now_utc=now)
    assert executed == 1

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        runs = await automations_repository.list_runs_for_cycle_key(cycle_key=cycle_key)
        assert len(runs) == 1
        assert runs[0].cycle_key == cycle_key
        assert runs[0].status == "failed"
        assert runs[0].error_code == "no_available_accounts"
        assert runs[0].model == "gpt-5.3-codex"


@pytest.mark.asyncio
async def test_automations_scheduler_empty_cycle_does_not_fallback_to_reactivated_accounts(
    db_setup,
    monkeypatch,
):
    del db_setup
    account = (await _create_accounts("auto-empty-cycle-reactivated"))[0]
    now = utcnow().replace(second=0, microsecond=0)
    due_slot = now - timedelta(minutes=5)
    compact_calls = 0

    async def _fake_compact(*_args, **_kwargs):
        nonlocal compact_calls
        compact_calls += 1
        raise AssertionError("empty scheduled cycle must not call upstream")

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        job = await automations_repository.create_job(
            name="Empty scheduled cycle stays empty",
            enabled=True,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time=due_slot.strftime("%H:%M"),
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=0,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[account.id],
        )
        cycle_key = f"scheduled:{job.id}:{due_slot.isoformat()}"
        await automations_repository.create_run_cycle(
            cycle_key=cycle_key,
            job_id=job.id,
            trigger="scheduled",
            cycle_expected_accounts=0,
            cycle_window_end=due_slot,
            accounts=[],
        )

    executed = await _run_due_jobs(now_utc=now)
    assert executed == 1
    assert compact_calls == 0

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        runs = await automations_repository.list_runs_for_cycle_key(cycle_key=cycle_key)
        assert len(runs) == 1
        assert runs[0].status == "failed"
        assert runs[0].account_id is None
        assert runs[0].attempt_count == 0
        assert runs[0].error_code == "no_available_accounts"


@pytest.mark.asyncio
async def test_automations_scheduler_reclaims_stale_empty_cycle_run_after_restart(db_setup, monkeypatch):
    del db_setup
    now = utcnow().replace(second=0, microsecond=0)
    due_slot = now - timedelta(minutes=5)
    stale_started_at = now - timedelta(hours=2)

    async def _fake_compact(*_args, **_kwargs):
        raise AssertionError("stale empty scheduled cycle must not call upstream")

    monkeypatch.setattr("app.modules.automations.service.core_compact_responses", _fake_compact)

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        job = await automations_repository.create_job(
            name="Stale empty scheduled cycle",
            enabled=True,
            include_paused_accounts=False,
            schedule_type="daily",
            schedule_time=due_slot.strftime("%H:%M"),
            schedule_timezone="UTC",
            schedule_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            schedule_threshold_minutes=0,
            model="gpt-5.3-codex",
            reasoning_effort=None,
            prompt="ping",
            account_ids=[],
        )
        cycle_key = f"scheduled:{job.id}:{due_slot.isoformat()}"
        await automations_repository.create_run_cycle(
            cycle_key=cycle_key,
            job_id=job.id,
            trigger="scheduled",
            cycle_expected_accounts=0,
            cycle_window_end=due_slot,
            accounts=[],
        )
        stale_run = await automations_repository.claim_run(
            job_id=job.id,
            trigger="scheduled",
            slot_key=f"scheduled:{job.id}:{due_slot.isoformat()}:none",
            cycle_key=cycle_key,
            cycle_expected_accounts=0,
            cycle_window_end=due_slot,
            scheduled_for=due_slot,
            started_at=stale_started_at,
        )
        assert stale_run is not None

    executed = await _run_due_jobs(now_utc=now)
    assert executed == 1

    async with SessionLocal() as session:
        automations_repository = AutomationsRepository(session)
        runs = await automations_repository.list_runs_for_cycle_key(cycle_key=cycle_key)
        assert len(runs) == 1
        assert runs[0].id == stale_run.id
        assert runs[0].status == "failed"
        assert runs[0].account_id is None
        assert runs[0].error_code == "no_available_accounts"
        assert runs[0].started_at == now


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
