from __future__ import annotations

from typing import NoReturn

import aiohttp
import pytest

from app.core.clients.proxy import ProxyResponseError
from app.core.config.dashboard_overrides import dashboard_overrides_bound
from app.core.config.settings import Settings
from app.core.openai.requests import ResponsesRequest
from app.db.models import DashboardSettings
from app.modules.proxy import http_bridge_forwarding as forwarding


class _ReachedOwnerSession(Exception):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("capability_proven", [False, True])
@pytest.mark.parametrize("dashboard_timeout", [None, 3.25])
async def test_dashboard_timeouts_preserve_owner_classifier_gate(
    monkeypatch: pytest.MonkeyPatch,
    capability_proven: bool,
    dashboard_timeout: float | None,
) -> None:
    base = Settings().model_copy(update={"upstream_connect_timeout_seconds": 10.0, "stream_idle_timeout_seconds": 60.0})
    monkeypatch.setattr(forwarding, "get_settings", lambda: base)
    row = DashboardSettings(
        upstream_connect_timeout_seconds=dashboard_timeout,
        stream_idle_timeout_seconds=dashboard_timeout,
    )
    captured: list[aiohttp.ClientTimeout] = []

    def capture_session(*, timeout: aiohttp.ClientTimeout, trust_env: bool) -> NoReturn:
        assert trust_env is False
        captured.append(timeout)
        raise _ReachedOwnerSession

    monkeypatch.setattr(forwarding.aiohttp, "ClientSession", capture_session)
    payload = ResponsesRequest.model_validate({"model": "gpt-5.4", "instructions": "hi", "input": "x" * 4095})
    context = forwarding.HTTPBridgeForwardContext(
        origin_instance="origin",
        target_instance="owner",
        codex_session_affinity=False,
        downstream_turn_state=None,
        expected_owner_process_epoch="proven-process",
    )
    with dashboard_overrides_bound(row):
        stream = forwarding.HTTPBridgeOwnerClient().stream_responses(
            owner_endpoint="http://owner.invalid",
            payload=payload,
            headers={},
            context=context,
            request_started_at=0.0,
            owner_supports_input_shape_classifier=capability_proven,
        )
        if capability_proven:
            with pytest.raises(_ReachedOwnerSession):
                _ = [event async for event in stream]
        else:
            with pytest.raises(ProxyResponseError) as exc_info:
                _ = [event async for event in stream]
            assert exc_info.value.failure_phase == "owner_forward"
            assert exc_info.value.failure_detail == "owner_input_shape_upgrade_required"

    if capability_proven:
        assert len(captured) == 1
        assert captured[0].sock_connect == (dashboard_timeout if dashboard_timeout is not None else 10.0)
        assert captured[0].sock_read == (dashboard_timeout if dashboard_timeout is not None else 60.0)
    else:
        assert captured == []
    assert base.upstream_connect_timeout_seconds == 10.0
    assert base.stream_idle_timeout_seconds == 60.0
