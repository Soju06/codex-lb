from __future__ import annotations

import asyncio
import copy
import json
from collections import deque
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import anyio
import pytest

from app.db.models import Account
from app.modules.proxy import api as proxy_api
from app.modules.proxy import rate_limit_events
from app.modules.proxy._service.support import _DownstreamWebSocketActivity, _WebSocketUpstreamControl
from app.modules.proxy._service.websocket.mixin import _process_and_forward_upstream_websocket_text
from app.modules.proxy.rate_limit_events import project_codex_rate_limit_event

pytestmark = pytest.mark.unit


def test_rate_limit_event_replaces_all_account_metadata_with_pool_snapshot():
    upstream = {
        "type": "codex.rate_limits",
        "plan_type": "pro",
        "rate_limits": {
            "primary": {"used_percent": 97, "window_minutes": 10080, "reset_at": 1900000000},
            "allowed": False,
        },
        "credits": {"has_credits": False, "unlimited": False, "balance": "0"},
        "account_id": "upstream-account",
    }
    original = copy.deepcopy(upstream)
    event = project_codex_rate_limit_event(
        upstream,
        {
            "x-codex-secondary-used-percent": "66.5",
            "x-codex-secondary-window-minutes": "10080",
            "x-codex-secondary-reset-at": "1800000000",
            "x-codex-credits-has-credits": "true",
            "x-codex-credits-unlimited": "false",
            "x-codex-credits-balance": "15.25",
        },
    )
    assert event == {
        "type": "codex.rate_limits",
        "rate_limits": {
            "primary": None,
            "secondary": {"used_percent": 66.5, "window_minutes": 10080, "reset_at": 1800000000},
        },
        "credits": {"has_credits": True, "unlimited": False, "balance": "15.25"},
    }
    assert upstream == original


@pytest.mark.parametrize("headers", [{}, {"x-codex-primary-used-percent": "NaN"}])
def test_unknown_pool_does_not_leak_account_usage(headers):
    assert project_codex_rate_limit_event({"type": "codex.rate_limits"}, headers) is None


@pytest.mark.parametrize("discriminator", ["limit_id", "metered_limit_name", "limit_name"])
def test_model_specific_quota_is_not_relabelled_as_global_usage(discriminator):
    assert (
        project_codex_rate_limit_event(
            {"type": "codex.rate_limits", discriminator: "codex_other"},
            {"x-codex-primary-used-percent": "66.5", "x-codex-primary-window-minutes": "300"},
        )
        is None
    )


def test_reset_windows_do_not_reuse_upstream_reset_or_credits():
    event = project_codex_rate_limit_event(
        {"type": "codex.rate_limits", "credits": {"balance": "999"}},
        {"x-codex-primary-used-percent": "0", "x-codex-primary-window-minutes": "300"},
    )
    assert event == {
        "type": "codex.rate_limits",
        "rate_limits": {
            "primary": {"used_percent": 0.0, "window_minutes": 300, "reset_at": None},
            "secondary": None,
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("visible", [False, True])
async def test_source_sse_projects_split_quota_events_and_preserves_terminal(visible):
    raw = (
        b'event: codex.rate_limits\r\ndata: {"type":"codex.rate_limits","plan_type":"pro",'
        b'"rate_limits":{"primary":{"used_percent":97,"window_minutes":10080}}}\r\n\r\n'
        b'data: {"type":"response.completed","response":{"id":"resp_source","output":[]}}\n\n'
    )
    closed = False

    async def source():
        nonlocal closed
        try:
            yield raw[:61]
            yield raw[61:]
        finally:
            closed = True

    chunks = [
        chunk
        async for chunk in proxy_api._wrap_source_responses_public_stream(
            source(),
            enforce_openai_sdk_contract=False,
            codex_rate_limit_headers={
                "x-codex-secondary-used-percent": "66.5",
                "x-codex-secondary-window-minutes": "10080",
            }
            if visible
            else {},
        )
    ]
    events = [proxy_api._parse_sse_payload(chunk) for chunk in chunks]
    events = [event for event in events if event and event["type"] != "codex.keepalive"]
    assert events[-1]["type"] == "response.completed"
    assert "plan_type" not in "".join(chunks)
    assert "97" not in "".join(chunks)
    assert closed
    if visible:
        assert len(events) == 2
        assert events[0]["rate_limits"] == {
            "primary": None,
            "secondary": {"used_percent": 66.5, "window_minutes": 10080, "reset_at": None},
        }
    else:
        assert len(events) == 1


@pytest.mark.asyncio
async def test_websocket_quota_projection_does_not_swallow_cancellation():
    text = json.dumps({"type": "codex.rate_limits", "rate_limits": {}})
    proxy = SimpleNamespace(
        _process_upstream_websocket_text=AsyncMock(return_value=text),
        rate_limit_headers=AsyncMock(side_effect=asyncio.CancelledError),
    )
    with pytest.raises(asyncio.CancelledError):
        await _process_and_forward_upstream_websocket_text(
            proxy,
            cast(Any, SimpleNamespace()),
            cast(Any, SimpleNamespace()),
            message=SimpleNamespace(kind="text", text=text),
            text=text,
            account=Account(id="quota-account"),
            account_id_value="quota-account",
            pending_requests=deque(),
            pending_lock=anyio.Lock(),
            client_send_lock=anyio.Lock(),
            api_key=None,
            upstream_control=_WebSocketUpstreamControl(),
            response_create_gate=asyncio.Semaphore(1),
            downstream_activity=_DownstreamWebSocketActivity(),
            continuity_state=None,
            codex_session_affinity=False,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(("hidden", "is_api_key"), [(True, True), (True, False), (False, True)])
async def test_quota_visibility_hides_key_metadata_but_preserves_owner_access(monkeypatch, hidden, is_api_key):
    settings = AsyncMock(return_value=SimpleNamespace(hide_upstream_quota_from_api_keys=hidden))
    monkeypatch.setattr(rate_limit_events, "get_settings_cache", lambda: SimpleNamespace(get=settings))
    pooled = {"x-codex-secondary-used-percent": "66.5"}
    load = AsyncMock(return_value=pooled)
    headers = await rate_limit_events.rate_limit_headers_for_client(cast(Any, object()) if is_api_key else None, load)
    if hidden and is_api_key:
        assert headers == {}
        load.assert_not_awaited()
    else:
        assert headers == pooled
        load.assert_awaited_once()
    if not is_api_key:
        settings.assert_not_awaited()
