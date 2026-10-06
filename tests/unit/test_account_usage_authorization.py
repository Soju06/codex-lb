from __future__ import annotations

import asyncio
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.http_bridge import request_submit
from app.modules.proxy.load_balancer import LoadBalancer
from app.modules.usage.authorization import OwnerAuthorizationKind

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("cancel", [False, True], ids=["deadline", "caller-cancellation"])
async def test_usage_authorization_bounds_and_cancels_its_read(cancel: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def stalled_read(_account_id: str) -> None:
        started.set()
        try:
            await asyncio.Future()
        finally:
            stopped.set()

    balancer = AsyncMock(spec=LoadBalancer)
    balancer.authorize_account_fresh.side_effect = stalled_read
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    service._load_balancer = balancer
    monkeypatch.setattr(request_submit, "_HTTP_BRIDGE_OWNER_AUTHORIZATION_TIMEOUT_SECONDS", 60.0 if cancel else 0.02)
    session = cast(Any, SimpleNamespace(account=SimpleNamespace(id="account")))
    authorization = asyncio.create_task(service._fresh_http_bridge_owner_authorization(session))
    await asyncio.wait_for(started.wait(), timeout=1.0)
    if cancel:
        authorization.cancel()
        with pytest.raises(asyncio.CancelledError):
            await authorization
    else:
        decision = await authorization
        assert decision.kind is OwnerAuthorizationKind.AUTHORIZATION_FAILED
    assert stopped.is_set()
