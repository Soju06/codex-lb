from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.core.clients.proxy_websocket import UpstreamWebSocketMessage
from app.core.utils.sse import parse_sse_data_json
from app.db.models import ApiKeyUsageReservation
from app.db.session import SessionLocal
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy import service as proxy_service
from app.modules.proxy.load_balancer import AccountSelection
from tests.integration.test_http_promotion_accounting import _key
from tests.integration.test_http_responses_bridge import (
    _assert_created_text_delta_completed,
    _cleanup_http_bridge_sessions,  # noqa: F401
    _collect_sse_events,
    _PromotionUpstreamWebSocket,
)
from tests.integration.test_http_responses_bridge import (
    promotion_transport as promotion_transport,
)

pytestmark = pytest.mark.integration


class _TooBigUpstream(_PromotionUpstreamWebSocket):
    def __init__(self, after_output: bool) -> None:
        super().__init__("resp_too_big")
        self.after_output = after_output

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)
        if self.after_output:
            for event in (
                {
                    "type": "response.created",
                    "response": {"id": "resp_too_big", "object": "response", "status": "in_progress"},
                },
                {
                    "type": "response.output_text.delta",
                    "response_id": "resp_too_big",
                    "delta": "partial",
                    "output_index": 0,
                    "content_index": 0,
                },
            ):
                await self._messages.put(UpstreamWebSocketMessage("text", text=json.dumps(event)))
        await self._messages.put(UpstreamWebSocketMessage("close", close_code=1009))


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/responses", "/v1/responses/", "/backend-api/codex/responses"])
@pytest.mark.parametrize("after_output", [False, True])
async def test_close_1009_is_terminal_and_same_account_can_recover(
    async_client, app_instance, promotion_transport, monkeypatch, path: str, after_output: bool
) -> None:
    upstreams, raw_calls, _ = promotion_transport
    rejected = _TooBigUpstream(after_output)
    healthy = _PromotionUpstreamWebSocket("resp_recovered")
    connect = AsyncMock(side_effect=[rejected, healthy])
    monkeypatch.setattr(proxy_service, "connect_responses_websocket", connect)
    service = get_proxy_service_for_app(app_instance)
    original_select = service._select_account_with_budget
    exclusions: list[set[str]] = []

    async def select_only_account(*args, **kwargs):
        excluded: set[str] = set(kwargs.get("exclude_account_ids") or ())
        exclusions.append(excluded)
        if excluded:
            return AccountSelection(account=None, error_code="no_accounts", error_message="No eligible account")
        return await original_select(*args, **kwargs)

    monkeypatch.setattr(service, "_select_account_with_budget", select_only_account)
    health = AsyncMock()
    monkeypatch.setattr(service, "_handle_stream_error", health)
    key = await _key(async_client, "close-1009")
    headers = {"Authorization": f"Bearer {key['key']}"}
    body = {
        "model": "gpt-5.4",
        "instructions": "test",
        "input": "hello",
        "stream": True,
        "prompt_cache_key": "close-1009-series",
    }

    response = await async_client.post(path, json=body, headers=headers)

    if not after_output and path.startswith("/v1/"):
        assert response.status_code == 400
    if response.status_code == 400:
        assert not after_output
        error = response.json()["error"]
    else:
        assert response.status_code == 200
        events = [e for block in response.text.split("\n\n") if (e := parse_sse_data_json(block)) is not None]
        failures = [e for e in events if e["type"] in {"error", "response.failed"}]
        assert len(failures) == 1
        failure = failures[0]
        detail = failure["response"] if failure["type"] == "response.failed" else failure
        assert isinstance(detail, dict)
        error = detail["error"]
        if after_output:
            assert [e["delta"] for e in events if e["type"] == "response.output_text.delta"] == ["partial"]
    assert isinstance(error, dict)
    assert error["code"] == "payload_too_large"
    assert error["type"] == "invalid_request_error"
    assert error["param"] == "input"
    assert len(rejected.sent_text) == 1
    connect.assert_awaited_once()
    health.assert_not_awaited()
    assert exclusions and not any(exclusions)
    assert not raw_calls and not upstreams

    events = await _collect_sse_events(async_client, path, json_body=body, headers=headers)
    _assert_created_text_delta_completed(events)
    assert connect.await_count == 2 and len(healthy.sent_text) == 1
    assert connect.await_args_list[0].args[2] == connect.await_args_list[1].args[2]
    health.assert_not_awaited()
    assert not any(exclusions)
    await service.drain_persistence_tasks(timeout_seconds=5)
    async with SessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(ApiKeyUsageReservation).where(ApiKeyUsageReservation.api_key_id == key["id"])
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 2
    assert sum(row.status == "released" for row in rows) == 1
    assert all(row.status not in {"reserved", "settling"} for row in rows)
    for session in service._http_bridge_sessions.values():
        assert not session.pending_requests and session.queued_request_count == 0
        assert not session.response_create_gate.locked()
