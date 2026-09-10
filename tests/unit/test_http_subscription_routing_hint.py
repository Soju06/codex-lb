from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from app.core.clients import proxy as client
from app.core.clients.http import lease_http_session
from app.core.config.settings import get_settings
from app.core.openai.requests import ResponsesRequest
from app.core.utils.sse import parse_sse_data_json
from app.db.models import Account, ModelSource
from app.modules.model_sources import forwarding
from app.modules.proxy import service as proxy_service

pytestmark = pytest.mark.unit


@dataclass(frozen=True, slots=True)
class _WireRequest:
    headers: Mapping[str, str]
    payload: ResponsesRequest


@pytest.fixture
async def loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[TestServer, list[_WireRequest], asyncio.Event]]:
    received: list[_WireRequest] = []
    completed = asyncio.Event()

    async def respond(request: web.Request) -> web.StreamResponse:
        try:
            received.append(_WireRequest(request.headers.copy(), ResponsesRequest.model_validate(await request.json())))
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(
                b'data: {"type":"response.completed","response":{"id":"resp_loopback","status":"completed"}}\n\n'
            )
            await response.write_eof()
            return response
        finally:
            completed.set()

    app = web.Application()
    app.router.add_post("/backend-api/codex/responses", respond)
    app.router.add_post("/v1/responses", respond)
    server = TestServer(app, host="127.0.0.1")
    async with asyncio.timeout(5):
        await server.start_server()
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5), trust_env=False) as session:

            def local_lease(
                existing: aiohttp.ClientSession | None = None,
            ) -> AbstractAsyncContextManager[aiohttp.ClientSession]:
                return lease_http_session(existing or session)

            monkeypatch.setattr(client, "lease_http_session", local_lease)
            monkeypatch.setattr(forwarding, "lease_model_source_session", local_lease)
            monkeypatch.setattr(client, "discover_native_egress_client", lambda: None)
            monkeypatch.setattr(get_settings(), "upstream_base_url", str(server.make_url("/backend-api")))
            yield server, received, completed
    finally:
        async with asyncio.timeout(5):
            await server.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("account_id", ["subscription-account", None, "email_legacy", "local_legacy"])
@pytest.mark.parametrize("tier", ["priority", "ultrafast", None])
async def test_selected_subscription_synthesizes_hint_without_requiring_account_header(
    monkeypatch: pytest.MonkeyPatch,
    loopback: tuple[TestServer, list[_WireRequest], asyncio.Event],
    account_id: str | None,
    tier: str | None,
) -> None:
    # Given a selected subscription Account and the final normalized request.
    _, received, completed = loopback
    service = proxy_service.ProxyService(MagicMock())
    account = Account(
        id="selected-subscription",
        chatgpt_account_id=account_id,
        access_token_encrypted=service._encryptor.encrypt("fixture-subscription-token"),
    )
    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5", "instructions": "", "input": "hello", "service_tier": tier}
    )
    settlement = proxy_service._StreamSettlement()
    # Only persistence and route lookup are isolated; admission, streaming,
    # optional-kwarg dispatch, header construction and aiohttp egress are real.
    monkeypatch.setattr(service, "_resolve_upstream_route_for_account", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_write_request_log", AsyncMock())

    # When the real service attempt reaches a local HTTP Responses endpoint.
    async with asyncio.timeout(5):
        stream = service._stream_once(
            account,
            payload,
            {"Authorization": "Bearer fixture-downstream-key", "X-Codex-Routing-Hint": "model=spoof;tier=flex"},
            "req_subscription_hint",
            False,
            request_started_at=service._clock.monotonic(),
            api_key=None,
            api_key_reservation=None,
            settlement=settlement,
            suppress_text_done_events=False,
            upstream_stream_transport="http",
            request_transport="http",
        )
        events = [parse_sse_data_json(event) async for event in stream]
        await completed.wait()

    # Then the wire hint follows subscription provenance, not the optional ID.
    assert len(received) == 1
    wire = received[0]
    expected = "model=gpt-5" + (f";tier={tier}" if tier is not None else "")
    assert wire.headers.get("x-codex-routing-hint") == expected
    assert wire.headers.get("ChatGPT-Account-ID") == (account_id if account_id == "subscription-account" else None)
    assert wire.headers["Authorization"] == "Bearer fixture-subscription-token"
    assert wire.payload.model == "gpt-5"
    assert wire.payload.service_tier == tier
    assert events == [{"type": "response.completed", "response": {"id": "resp_loopback", "status": "completed"}}]
    assert settlement.status == "success"


@pytest.mark.asyncio
@pytest.mark.parametrize("account_id", ["subscription-account", None])
async def test_low_level_http_client_keeps_routing_hint_default_off(
    loopback: tuple[TestServer, list[_WireRequest], asyncio.Event], account_id: str | None
) -> None:
    # Given a standalone low-level caller, without subscription opt-in.
    _, received, completed = loopback
    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5", "instructions": "", "input": "hello", "service_tier": "priority"}
    )

    # When the actual HTTP client sends the request without the opt-in kwarg.
    async with asyncio.timeout(5):
        events = [
            event
            async for event in client.stream_responses(
                payload,
                {"X-Codex-Routing-Hint": "model=spoof;tier=flex"},
                "fixture-low-level-token",
                account_id,
                upstream_stream_transport_override="http",
            )
        ]
        await completed.wait()

    # Then neither an account header nor an inbound hint enables synthesis.
    assert len(received) == 1
    assert "x-codex-routing-hint" not in received[0].headers
    assert events


@pytest.mark.asyncio
async def test_external_model_source_does_not_gain_subscription_routing_hint(
    loopback: tuple[TestServer, list[_WireRequest], asyncio.Event],
) -> None:
    # Given an external model source, which bypasses subscription streaming.
    server, received, completed = loopback
    source = ModelSource(
        id="fixture-external", name="Fixture", kind="openai_compatible", base_url=str(server.make_url("/v1"))
    )

    # When its real forwarding client dispatches a priority Responses request.
    async with asyncio.timeout(5):
        response = await forwarding.stream_responses(
            source, {"model": "external-model", "instructions": "", "input": "hello", "service_tier": "priority"}
        )
        events = [chunk async for chunk in response.body]
        await completed.wait()

    # Then external traffic stays outside subscription hint synthesis.
    assert len(received) == 1
    assert "x-codex-routing-hint" not in received[0].headers
    assert received[0].payload.model == "external-model"
    assert events
