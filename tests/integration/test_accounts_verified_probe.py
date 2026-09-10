from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, update
from sqlalchemy.engine import make_url

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal, get_background_session
from app.modules.accounts import api as accounts_api
from app.modules.accounts import service as accounts_service
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.repository import AccountsRepository
from app.modules.usage.updater import AccountRefreshResult, UsageUpdater

pytestmark = pytest.mark.integration


@pytest.fixture
async def held_account(async_client, monkeypatch):
    async def fresh(self, account, **kwargs):
        return account

    monkeypatch.setattr(AuthManager, "ensure_fresh", fresh)
    monkeypatch.setattr(
        UsageUpdater,
        "force_refresh_result",
        AsyncMock(return_value=AccountRefreshResult(usage_written=False, fetch_succeeded=False)),
    )
    monkeypatch.setattr(
        accounts_api, "get_proxy_service_for_app", lambda app: SimpleNamespace(record_account_probe_result=AsyncMock())
    )
    encryptor = TokenEncryptor()
    expected_url = os.environ["CODEX_LB_TEST_DATABASE_URL"]
    assert os.environ["CODEX_LB_DATABASE_URL"] == expected_url
    async with SessionLocal() as main_session:
        assert main_session.get_bind().engine.url == make_url(expected_url)
    async with get_background_session() as session:
        assert session.get_bind().engine.url == make_url(expected_url)
        session.add(
            Account(
                id="held-probe",
                chatgpt_account_id="test-provider-account",
                email="held@example.invalid",
                plan_type="pro",
                last_refresh=utcnow(),
                access_token_encrypted=encryptor.encrypt("test-access"),
                refresh_token_encrypted=encryptor.encrypt("test-refresh"),
                id_token_encrypted=encryptor.encrypt("test-id"),
            )
        )
        await session.commit()
        await AccountsRepository(session).update_status(
            "held-probe",
            AccountStatus.RATE_LIMITED,
            reset_at=2000000000,
            blocked_at=1900000000,
            rejected_model="gpt-6-astra",
            rejected_service_tier="default",
        )
    return "held-probe"


def upstream(monkeypatch, *, status=200, kind="response.completed", before=None, malformed=False):
    calls = []

    class Content:
        async def __aiter__(self):
            if before:
                await before()
            if kind == "timeout":
                raise asyncio.TimeoutError()
            if kind == "eof":
                return
            if malformed:
                yield b"data: nope\n\n"
                return
            event = {
                "type": kind,
                "response": {
                    "id": "resp_probe_test",
                    "status": "completed" if kind == "response.completed" else "failed",
                },
            }
            for line in ("data: " + json.dumps(event) + "\n\n").encode().splitlines(keepends=True):
                yield line

    @asynccontextmanager
    async def post(url, *, headers, json, timeout):
        calls.append((headers["chatgpt-account-id"], json))
        yield SimpleNamespace(status=status, content=Content())

    @asynccontextmanager
    async def lease():
        yield SimpleNamespace(post=post)

    monkeypatch.setattr(accounts_service, "lease_http_session", lease)
    return calls


async def account_snapshot(account_id):
    async with get_background_session() as session:
        account = await session.get(Account, account_id)
        if account is None:
            return None
        return (
            account.status,
            account.block_generation,
            account.blocked_at,
            account.reset_at,
            account.probe_claim_token,
        )


@pytest.mark.asyncio
async def test_completed_matching_probe_recovers_persisted_hold(async_client, monkeypatch, held_account):
    calls = upstream(monkeypatch)
    response = await async_client.post(f"/api/accounts/{held_account}/probe")
    assert response.status_code == 200, response.text
    assert response.json()["probeCompleted"] is True
    assert response.json()["holdRecovered"] is True
    assert response.json()["accountStatusAfter"] == "active"
    assert calls[0][0] == "test-provider-account"
    assert calls[0][1]["model"] == "gpt-6-astra"
    assert calls[0][1]["service_tier"] == "default"
    assert await account_snapshot(held_account) == (AccountStatus.ACTIVE, 2, None, None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,status",
    [
        ("response.failed", 200),
        ("response.incomplete", 200),
        ("eof", 200),
        ("timeout", 200),
        ("response.completed", 429),
    ],
)
async def test_uncompleted_probe_preserves_hold(async_client, monkeypatch, held_account, kind, status):
    upstream(monkeypatch, status=status, kind=kind)
    response = await async_client.post(f"/api/accounts/{held_account}/probe")
    assert response.status_code == 200
    assert response.json()["probeStatusCode"] == status
    assert response.json()["probeCompleted"] is False
    assert response.json()["holdRecovered"] is False
    assert await account_snapshot(held_account) == (AccountStatus.RATE_LIMITED, 1, 1900000000, 2000000000, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["unknown", "different-model"])
async def test_unproven_scope_stays_held(async_client, monkeypatch, held_account, scope):
    upstream(monkeypatch)
    if scope == "unknown":
        async with get_background_session() as session:
            await session.execute(update(Account).where(Account.id == held_account).values(rejected_model=None))
            await session.commit()
    response = await async_client.post(f"/api/accounts/{held_account}/probe", json={"model": "gpt-5.5"})
    assert response.json()["probeCompleted"] is True
    assert response.json()["holdRecovered"] is False
    assert (await account_snapshot(held_account))[0] == AccountStatus.RATE_LIMITED


@pytest.mark.asyncio
@pytest.mark.parametrize("race", ["newer", "paused", "deleted", "reauth"])
async def test_changed_account_wins_probe_race(async_client, monkeypatch, held_account, race):
    async def mutate():
        async with get_background_session() as session:
            repo = AccountsRepository(session)
            if race == "newer":
                await repo.update_status(
                    held_account,
                    AccountStatus.RATE_LIMITED,
                    reset_at=2000000000,
                    blocked_at=1900000000,
                    rejected_model="gpt-6-astra",
                    rejected_service_tier="default",
                )
            elif race == "paused":
                await repo.update_status(held_account, AccountStatus.PAUSED)
            elif race == "deleted":
                await session.execute(delete(Account).where(Account.id == held_account))
                await session.commit()
            else:
                await session.execute(
                    update(Account)
                    .where(Account.id == held_account)
                    .values(refresh_token_encrypted=TokenEncryptor().encrypt("new-refresh"))
                )
                await session.commit()

    upstream(monkeypatch, before=mutate)
    response = await async_client.post(f"/api/accounts/{held_account}/probe")
    assert response.status_code == 200, response.text
    assert response.json()["holdRecovered"] is False
    snapshot = await account_snapshot(held_account)
    if race == "deleted":
        assert snapshot is None
    elif race == "paused":
        assert snapshot[0] == AccountStatus.PAUSED
    else:
        assert snapshot[0] == AccountStatus.RATE_LIMITED
        assert snapshot[1] == (2 if race == "newer" else 1)


@pytest.mark.asyncio
async def test_concurrent_probe_has_one_provider_admission(async_client, monkeypatch, held_account):
    entered = asyncio.Event()
    release = asyncio.Event()

    async def wait():
        entered.set()
        await release.wait()

    calls = upstream(monkeypatch, before=wait)
    first = asyncio.create_task(async_client.post(f"/api/accounts/{held_account}/probe"))
    await asyncio.wait_for(entered.wait(), timeout=5)
    try:
        second = await async_client.post(f"/api/accounts/{held_account}/probe")
        assert second.status_code == 409, second.text
        assert len(calls) == 1
    finally:
        release.set()
        result = await first
    assert result.json()["holdRecovered"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("record_error_before_selection", [False, True])
async def test_recovery_survives_restart_and_preserves_owner_bindings(
    async_client, monkeypatch, held_account, record_error_before_selection
):
    from sqlalchemy import select

    from app.db.models import StickySession, StickySessionKind
    from app.modules.api_keys.repository import ApiKeysRepository
    from app.modules.proxy.load_balancer import LoadBalancer, RuntimeState
    from app.modules.proxy.repo_bundle import ProxyRepositories
    from app.modules.proxy.sticky_repository import StickySessionsRepository
    from app.modules.request_logs.repository import RequestLogsRepository
    from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository

    @asynccontextmanager
    async def repos():
        async with get_background_session() as session:
            yield ProxyRepositories(
                AccountsRepository(session),
                UsageRepository(session),
                RequestLogsRepository(session),
                StickySessionsRepository(session),
                ApiKeysRepository(session),
                AdditionalUsageRepository(session),
                session=session,
            )

    async with get_background_session() as session:
        session.add(StickySession(key="preserved-owner", kind=StickySessionKind.CODEX_SESSION, account_id=held_account))
        await session.commit()
    restarted = LoadBalancer(repos)
    assert (await restarted.select_account()).account is None
    stale_runtime = LoadBalancer(repos)
    stale_runtime._runtime[held_account] = RuntimeState(
        blocked_at=1900000000, reset_at=2000000000, cooldown_until=2000000000, block_generation=1
    )
    upstream(monkeypatch)
    response = await async_client.post(f"/api/accounts/{held_account}/probe")
    assert response.json()["holdRecovered"] is True
    if record_error_before_selection:
        async with get_background_session() as session:
            recovered = await AccountsRepository(session).get_by_id(held_account)
            assert recovered is not None
            session.expunge(recovered)
        await stale_runtime.record_error(recovered)
    for balancer in (restarted, stale_runtime, LoadBalancer(repos)):
        result = await balancer.select_account()
        assert result.account is not None
        assert result.account.id == held_account
        fenced = await balancer.select_account(
            required_account_id="absent-owner", required_account_is_ownership_constraint=True
        )
        assert fenced.account is None
    async with get_background_session() as session:
        owner = await session.scalar(select(StickySession.account_id).where(StickySession.key == "preserved-owner"))
        assert owner == held_account


@pytest.mark.asyncio
async def test_cancelled_probe_releases_claim_and_keeps_hold(async_client, monkeypatch, held_account):
    entered = asyncio.Event()

    async def wait():
        entered.set()
        await asyncio.Event().wait()

    upstream(monkeypatch, before=wait)
    request = asyncio.create_task(async_client.post(f"/api/accounts/{held_account}/probe"))
    await asyncio.wait_for(entered.wait(), timeout=5)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert await account_snapshot(held_account) == (AccountStatus.RATE_LIMITED, 1, 1900000000, 2000000000, None)


@pytest.mark.asyncio
async def test_malformed_stream_preserves_hold(async_client, monkeypatch, held_account):
    upstream(monkeypatch, malformed=True)
    response = await async_client.post(f"/api/accounts/{held_account}/probe")
    assert response.json()["probeCompleted"] is False
    assert response.json()["holdRecovered"] is False
    assert (await account_snapshot(held_account))[0] == AccountStatus.RATE_LIMITED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("other_model", "other_tier", "can_recover"),
    [("gpt-5.4", "default", False), ("gpt-6-astra", "priority", False), ("gpt-6-astra", "default", True)],
)
async def test_probe_preserves_ambiguous_shared_websocket_hold(
    async_client, app_instance, monkeypatch, held_account, other_model, other_tier, can_recover
):
    import time
    from collections import deque

    import anyio

    from app.dependencies import get_proxy_service_for_app
    from app.modules.proxy.service import _WebSocketRequestState

    service = get_proxy_service_for_app(app_instance)
    async with get_background_session() as session:
        account = await AccountsRepository(session).get_by_id(held_account)
        assert account is not None
        session.expunge(account)
    pending = deque(
        _WebSocketRequestState(
            request_id=request_id,
            model=model,
            service_tier=tier,
            reasoning_effort=None,
            api_key_reservation=None,
            started_at=time.monotonic(),
        )
        for request_id, model, tier in (
            ("shared-first", "gpt-6-astra", "default"),
            ("shared-other", other_model, other_tier),
        )
    )
    assert await service._fail_pending_websocket_requests(
        account=account,
        account_id_value=held_account,
        pending_requests=pending,
        pending_lock=anyio.Lock(),
        error_code="usage_limit_reached",
        error_message="shared upstream failure",
        api_key=None,
    )
    upstream(monkeypatch)
    response = await async_client.post(f"/api/accounts/{held_account}/probe", json={"model": "gpt-6-astra"})
    assert response.status_code == 200
    assert response.json()["probeCompleted"] is True
    assert response.json()["holdRecovered"] is can_recover
    snapshot = await account_snapshot(held_account)
    assert snapshot[0] == (AccountStatus.ACTIVE if can_recover else AccountStatus.RATE_LIMITED)
