from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.clients.proxy import UpstreamProxyRouteTrace
from app.db.models import Base, RequestLog
from app.modules.proxy.service import (
    _record_websocket_route_metadata,
    _websocket_route_log_kwargs,
    _WebSocketRequestState,
)
from app.modules.request_logs.repository import RequestLogsRepository

pytestmark = pytest.mark.unit


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_add_log_persists_upstream_proxy_route_metadata(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        repo = RequestLogsRepository(session)
        await repo.add_log(
            account_id=None,
            request_id="req_1",
            model="gpt-5.2",
            input_tokens=1,
            output_tokens=2,
            latency_ms=3,
            status="success",
            error_code=None,
            upstream_proxy_route_mode="account_bound",
            upstream_proxy_pool_id="pool_1",
            upstream_proxy_endpoint_id="ep_1",
            upstream_proxy_fallback_used=True,
            upstream_proxy_fail_closed_reason=None,
        )
        row = (await session.execute(select(RequestLog))).scalar_one()

    assert row.upstream_proxy_route_mode == "account_bound"
    assert row.upstream_proxy_pool_id == "pool_1"
    assert row.upstream_proxy_endpoint_id == "ep_1"
    assert row.upstream_proxy_fallback_used is True
    assert row.upstream_proxy_fail_closed_reason is None


def test_websocket_route_metadata_flows_into_request_log_kwargs() -> None:
    request_state = _WebSocketRequestState(
        request_id="ws_req_1",
        model="gpt-5.2",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
    )
    upstream = SimpleNamespace(
        upstream_proxy_route_mode="account_bound",
        upstream_proxy_pool_id="pool_1",
        upstream_proxy_endpoint_id="ep_1",
        upstream_proxy_fallback_used=True,
    )

    _record_websocket_route_metadata(request_state, upstream=cast(Any, upstream))

    assert _websocket_route_log_kwargs(request_state) == {
        "upstream_proxy_route_mode": "account_bound",
        "upstream_proxy_pool_id": "pool_1",
        "upstream_proxy_endpoint_id": "ep_1",
        "upstream_proxy_fallback_used": True,
        "upstream_proxy_fail_closed_reason": None,
    }


def test_route_trace_records_explicit_direct_egress_for_auditability() -> None:
    trace = UpstreamProxyRouteTrace()

    trace.record_direct()

    assert trace.mode == "direct"
    assert trace.pool_id is None
    assert trace.endpoint_id is None
    assert trace.fallback_used is None
