from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import select

from app.core.clients.codex import CodexTransportError
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import (
    Account,
    AccountProxyBinding,
    AccountStatus,
    DashboardSettings,
    ProxyEndpoint,
    ProxyPool,
    ProxyPoolMember,
    QuotaPlannerDecision,
    RequestLog,
)
from app.db.session import SessionLocal
from app.modules.accounts import api as accounts_api
from app.modules.accounts import service as accounts_service
from app.modules.accounts.auth_manager import AuthManager
from app.modules.quota_planner import warmup
from app.modules.quota_planner.logic import PlannerSettings
from app.modules.quota_planner.repository import QuotaPlannerRepository
from app.modules.usage.updater import AccountRefreshResult, UsageUpdater

pytestmark = pytest.mark.integration


async def _seed(*, broken: str | None = None) -> None:
    token = TokenEncryptor().encrypt("synthetic-token")
    async with SessionLocal() as session:
        for account_id in ("bound-seat", "direct-seat"):
            session.add(
                Account(
                    id=account_id,
                    email=f"{account_id}@example.test",
                    plan_type="plus",
                    chatgpt_account_id="shared-workspace",
                    chatgpt_user_id=account_id,
                    access_token_encrypted=token,
                    refresh_token_encrypted=token,
                    id_token_encrypted=token,
                    last_refresh=utcnow(),
                    status=AccountStatus.ACTIVE,
                )
            )
        pool = ProxyPool(id="bound-pool", name="Bound pool", is_active=broken != "pool")
        endpoint = ProxyEndpoint(
            id="bound-endpoint",
            name="Bound endpoint",
            scheme="http",
            host="proxy.example.test",
            port=8080,
            is_active=broken != "endpoint",
        )
        session.add_all(
            [
                pool,
                endpoint,
                ProxyPoolMember(id="member", pool=pool, endpoint=endpoint),
                AccountProxyBinding(id="binding", account_id="bound-seat", pool_id="bound-pool"),
            ]
        )
        settings = await session.get(DashboardSettings, 1)
        if settings is None:
            settings = DashboardSettings(id=1)
            session.add(settings)
        settings.upstream_proxy_routing_enabled = False
        settings.upstream_proxy_default_pool_id = None
        await session.commit()
        await QuotaPlannerRepository(session).upsert_settings(
            PlannerSettings(
                mode="auto",
                allow_synthetic_traffic=True,
                dry_run=False,
                max_warmups_per_day=5,
                max_warmup_credits_per_day=1.0,
            )
        )


@pytest.fixture
def transports(monkeypatch):
    calls = SimpleNamespace(requests=[], fail=False, status=200, release=Mock(), close=AsyncMock())

    class Response:
        @property
        def status(self):
            return calls.status

        def release(self):
            calls.release()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            self.release()

    class DirectSession:
        def post(self, url, **kwargs):
            calls.requests.append(("direct", None, kwargs))
            return Response()

    @asynccontextmanager
    async def lease():
        yield DirectSession()

    class RoutedClient:
        def __init__(self, session):
            pass

        async def request(self, method, url, *, route, **kwargs):
            calls.requests.append(("bound", route, kwargs))
            if calls.fail:
                raise CodexTransportError("Proxy connection failed")
            return Response()

        async def close(self):
            await calls.close()

    async def fresh(self, account, **kwargs):
        return account

    monkeypatch.setattr(accounts_service, "lease_http_session", lease)
    monkeypatch.setattr(accounts_service, "CodexClient", RoutedClient, raising=False)
    monkeypatch.setattr(accounts_service, "create_codex_session", lambda: object(), raising=False)
    monkeypatch.setattr(AuthManager, "ensure_fresh", fresh)
    monkeypatch.setattr(
        UsageUpdater,
        "force_refresh_result",
        AsyncMock(return_value=AccountRefreshResult(usage_written=False, fetch_succeeded=True)),
    )
    monkeypatch.setattr(
        accounts_api,
        "get_proxy_service_for_app",
        lambda app: SimpleNamespace(record_account_probe_result=AsyncMock()),
    )
    monkeypatch.setattr(warmup.QuotaWarmupService, "_record_warmup_effect", AsyncMock())
    return calls


@pytest.mark.parametrize("account_id", ["bound-seat", "direct-seat"])
@pytest.mark.parametrize("upstream_status", [200, 429])
async def test_force_probe_respects_local_seat_route(async_client, transports, account_id, upstream_status):
    await _seed()
    transports.status = upstream_status

    response = await async_client.post(f"/api/accounts/{account_id}/probe", json={"model": "gpt-5.4-mini"})

    assert response.status_code == 200, response.text
    assert response.json()["probeStatusCode"] == upstream_status
    assert len(transports.requests) == 1
    kind, route, kwargs = transports.requests[0]
    assert kind == ("bound" if account_id == "bound-seat" else "direct")
    assert kwargs["headers"]["chatgpt-account-id"] == "shared-workspace"
    assert kwargs["json"]["stream"] is True
    assert "max_output_tokens" not in kwargs["json"]
    transports.release.assert_called_once()
    if route is not None:
        assert route.pool_id == "bound-pool"
        assert route.endpoint_id == "bound-endpoint"
        assert kwargs["buffer_response"] is False
        transports.close.assert_awaited_once()


@pytest.mark.parametrize("broken", ["pool", "endpoint"])
async def test_force_probe_unavailable_binding_fails_before_dispatch(async_client, transports, broken):
    await _seed(broken=broken)

    response = await async_client.post("/api/accounts/bound-seat/probe")

    assert response.status_code == 502, response.text
    assert response.json()["error"]["code"] == "upstream_proxy_unavailable"
    assert transports.requests == []


async def test_force_probe_proxy_transport_failure_never_retries_direct(async_client, transports):
    await _seed()
    transports.fail = True

    response = await async_client.post("/api/accounts/bound-seat/probe")

    assert response.status_code == 200, response.text
    assert response.json()["probeStatusCode"] == 0
    assert [kind for kind, _, _ in transports.requests] == ["bound"]
    transports.close.assert_awaited_once()
    transports.release.assert_not_called()


@pytest.mark.parametrize("account_id", ["bound-seat", "direct-seat"])
async def test_quota_warmup_routes_and_logs_local_seat(db_setup, transports, monkeypatch, account_id):
    await _seed()
    seen = []

    async def stream(payload, headers, token, upstream_id, **kwargs):
        seen.append(kwargs)
        assert upstream_id == "shared-workspace"
        assert kwargs["suppress_live_usage"] is True
        yield 'data: {"type":"response.completed","response":{"usage":{"input_tokens":3,"output_tokens":1}}}\n\n'

    monkeypatch.setattr(warmup, "stream_responses", stream)
    async with SessionLocal() as session:
        result = await warmup.QuotaWarmupService(session).warm_now(
            account_id=account_id, model="gpt-5.4-mini", force_probe=True
        )

    assert result.status == "executed", result
    assert len(seen) == 1
    route = seen[0]["route"]
    assert seen[0]["allow_direct_egress"] is (account_id == "direct-seat")
    if account_id == "bound-seat":
        assert route is not None
        assert route.pool_id == "bound-pool"
    else:
        assert route is None
    async with SessionLocal() as session:
        row = await session.scalar(select(RequestLog).where(RequestLog.request_id == result.request_id))
        assert row is not None
        assert row.status == "success"
        assert row.upstream_proxy_route_mode == ("account_bound" if route else "direct")
        assert row.upstream_proxy_pool_id == ("bound-pool" if route else None)
        assert row.upstream_proxy_endpoint_id == ("bound-endpoint" if route else None)


@pytest.mark.parametrize("failure", ["pool", "endpoint", "transport"])
async def test_quota_warmup_failure_is_routed_and_recorded(db_setup, transports, monkeypatch, failure):
    await _seed(broken=failure)
    seen = []

    async def stream(*args, **kwargs):
        seen.append(kwargs)
        raise CodexTransportError("Proxy connection failed")
        yield  # pragma: no cover - async-generator contract

    monkeypatch.setattr(warmup, "stream_responses", stream)
    async with SessionLocal() as session:
        result = await warmup.QuotaWarmupService(session).warm_now(
            account_id="bound-seat", model="gpt-5.4-mini", force_probe=True
        )

    assert result.status == "failed", result
    async with SessionLocal() as session:
        row = await session.scalar(select(RequestLog).where(RequestLog.request_id == result.request_id))
        decision = await session.get(QuotaPlannerDecision, result.decision_id)
        assert row is not None and row.status == "error"
        assert decision is not None and decision.status == "failed"
        if failure == "transport":
            assert len(seen) == 1
            assert seen[0]["route"].pool_id == "bound-pool"
            assert seen[0]["allow_direct_egress"] is False
            assert row.upstream_proxy_endpoint_id == "bound-endpoint"
        else:
            assert not seen
            expected = "pool_unavailable" if failure == "pool" else "pool_has_no_active_endpoints"
            assert row.upstream_proxy_fail_closed_reason == expected
