"""Tests for the WebSocket model-source guard.

The guard makes source-owned models fail the WebSocket session so Codex
clients fall back to the HTTP transport, which is the only path that routes to
an OpenAI-compatible model source.
"""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock

import anyio
import pytest

import app.modules.proxy._service.websocket.mixin as ws_mixin
from app.modules.api_keys.service import ApiKeyData
from app.modules.model_sources.selection import effective_model_for_api_key

pytestmark = pytest.mark.unit


def _api_key(*, enforced_model: str | None = None) -> ApiKeyData:
    from datetime import datetime

    return ApiKeyData(
        id="key_ws_guard",
        name="ws guard",
        key_prefix="sk-test-ws-guard",
        allowed_models=[],
        enforced_model=enforced_model,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=datetime(2026, 1, 1),
        last_used_at=None,
    )


def test_effective_model_prefers_enforced_model() -> None:
    assert effective_model_for_api_key(None, "gpt-5.6-sol") == "gpt-5.6-sol"
    assert effective_model_for_api_key(_api_key(), "gpt-5.6-sol") == "gpt-5.6-sol"
    assert effective_model_for_api_key(_api_key(enforced_model="qwen3.8-max"), "gpt-5.6-sol") == "qwen3.8-max"


async def _run_guard(monkeypatch: pytest.MonkeyPatch, *, is_source_owned: bool):
    """Drive ``_select_websocket_connect_account`` far enough to observe the guard.

    ``_select_account_with_budget_compatible`` stands in for the selection loop
    the guard is supposed to short-circuit, so calling it means the guard did
    not fire.
    """
    emitted: dict[str, object] = {}
    selection_called = False

    async def fake_is_source_owned(model, api_key):  # noqa: ANN001
        return is_source_owned

    async def fake_emit(self, websocket, **kwargs):  # noqa: ANN001
        emitted.update(kwargs)

    async def fake_select(*args, **kwargs):  # noqa: ANN002, ANN003
        nonlocal selection_called
        selection_called = True
        raise AssertionError("account selection must not run for a source-owned model")

    monkeypatch.setattr(ws_mixin, "responses_model_is_source_owned", fake_is_source_owned)
    monkeypatch.setattr(ws_mixin._WebSocketMixin, "_emit_websocket_connect_failure", fake_emit, raising=False)
    monkeypatch.setattr(
        ws_mixin._WebSocketMixin,
        "_select_account_with_budget_compatible",
        fake_select,
        raising=False,
    )

    service = ws_mixin._WebSocketMixin()
    request_state = cast(
        ws_mixin._WebSocketRequestState,
        type("S", (), {"request_log_id": "log-1", "request_id": "req-1"})(),
    )

    account = await service._select_websocket_connect_account(  # type: ignore[attr-defined]
        anyio.current_time() + 30,
        sticky_key=None,
        sticky_kind=None,
        prefer_earlier_reset=False,
        routing_strategy="capacity_weighted",
        model="qwen3.8-max",
        request_state=request_state,
        api_key=None,
        client_send_lock=anyio.Lock(),
        websocket=AsyncMock(),
        reallocate_sticky=False,
        sticky_max_age_seconds=None,
        exclude_account_ids=set(),
        preferred_account_id=None,
    )
    return account, emitted, selection_called


@pytest.mark.asyncio
async def test_guard_fails_session_for_source_owned_model(monkeypatch: pytest.MonkeyPatch) -> None:
    account, emitted, selection_called = await _run_guard(monkeypatch, is_source_owned=True)

    assert account is None
    assert selection_called is False
    assert emitted["error_code"] == "model_source_requires_http_transport"
    assert emitted["status_code"] == 503
    assert emitted["account_id"] is None
