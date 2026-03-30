from __future__ import annotations

from contextlib import nullcontext
from typing import cast

import pytest

from app.core import shutdown as shutdown_module
from app.core.clients.proxy import ProxyResponseError
from app.modules.proxy.repo_bundle import ProxyRepoFactory
from app.modules.proxy.service import ProxyService, _AffinityPolicy, _HTTPBridgeSessionKey

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_new_sessions_rejected_during_drain() -> None:
    assert hasattr(shutdown_module, "is_bridge_drain_active")
    assert hasattr(shutdown_module, "set_bridge_drain_active")
    assert callable(shutdown_module.is_bridge_drain_active)
    assert callable(shutdown_module.set_bridge_drain_active)

    shutdown_module.set_bridge_drain_active(False)
    assert not shutdown_module.is_bridge_drain_active()

    service = ProxyService(repo_factory=cast(ProxyRepoFactory, nullcontext()))
    key = _HTTPBridgeSessionKey("request", "drain-test", None)

    shutdown_module.set_bridge_drain_active(True)
    assert shutdown_module.is_bridge_drain_active()

    with pytest.raises(ProxyResponseError) as exc_info:
        await service._get_or_create_http_bridge_session(
            key,
            headers={},
            affinity=_AffinityPolicy(),
            api_key=None,
            request_model=None,
            idle_ttl_seconds=30.0,
            max_sessions=16,
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.payload["error"].get("code") == "bridge_drain_active"

    shutdown_module.set_bridge_drain_active(False)
