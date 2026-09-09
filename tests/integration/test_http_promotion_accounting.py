from __future__ import annotations

from unittest.mock import Mock

import pytest
from sqlalchemy import select

import app.core.clients.proxy as core_proxy_module
from app.core.clients.proxy import ProxyResponseError
from app.db.models import ApiKeyUsageReservation
from app.db.session import SessionLocal
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy import service as proxy_module
from app.modules.proxy._service import observability, support
from app.modules.proxy._service.http_bridge import helpers, streaming
from tests.integration.test_http_responses_bridge import (
    _cleanup_http_bridge_sessions as cleanup_http_bridge_sessions,  # noqa: F401
)
from tests.integration.test_http_responses_bridge import (
    _make_app_settings,
    _promotion_history,
)
from tests.integration.test_http_responses_bridge import (
    promotion_transport as promotion_transport,
)

pytestmark = pytest.mark.integration


async def _key(async_client, name, *, policy=None):
    settings = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert settings.status_code == 200, settings.text
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": name,
            "transportPolicyOverride": policy,
            "limits": [{"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 100_000}],
        },
    )
    assert created.status_code == 200, created.text
    return created.json()


@pytest.mark.asyncio
async def test_promoted_chat_keys_isolate_connections_and_settle_usage(async_client, app_instance, promotion_transport):
    upstreams, _, _ = promotion_transport
    keys = [await _key(async_client, "first"), await _key(async_client, "second")]
    for key in [keys[0], keys[1], keys[0]]:
        response = await async_client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-5.4",
                "messages": _promotion_history(),
            },
            headers={"Authorization": f"Bearer {key['key']}"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["usage"]["total_tokens"] == 26
    assert len(upstreams) == 2
    assert [len(upstream.sent_text) for upstream in upstreams] == [2, 1]
    service = get_proxy_service_for_app(app_instance)
    await service.drain_persistence_tasks(timeout_seconds=5)
    async with SessionLocal() as session:
        rows = (await session.execute(select(ApiKeyUsageReservation))).scalars().all()
        assert len(rows) == 3
        assert all(row.status != "reserved" and row.status != "settling" for row in rows)
        assert {row.api_key_id for row in rows} == {key["id"] for key in keys}


@pytest.mark.asyncio
async def test_per_key_http_override_suppresses_native_history_promotion(async_client, promotion_transport, caplog):
    upstreams, raw_calls, _ = promotion_transport
    key = await _key(async_client, "http-only", policy="always_http")
    response = await async_client.post(
        "/v1/responses",
        json={"model": "gpt-5.4", "input": _promotion_history()},
        headers={"Authorization": f"Bearer {key['key']}", "user-agent": "codex_exec/0.153.4"},
    )
    assert response.status_code == 200, response.text
    assert not upstreams
    assert raw_calls[-1]["upstream_transport"] == "http"


@pytest.mark.asyncio
@pytest.mark.parametrize("bypass", ["image", "payload_size"])
async def test_promoted_http_bypass_reasons_are_counted(async_client, promotion_transport, monkeypatch, bypass):
    upstreams, raw_calls, _ = promotion_transport
    routing_counter = Mock()
    monkeypatch.setattr(observability, "http_bridge_routing_total", routing_counter)
    if bypass == "payload_size":
        # The payload bypass compares against the fixed upstream frame budget
        # (MAX_SSE_EVENT_BYTES - 2 MiB headroom, floored at 1 MiB): shrink it so
        # a 1 MiB + 1 history trips the bypass.
        monkeypatch.setattr(core_proxy_module, "MAX_SSE_EVENT_BYTES", 3 * 1024 * 1024)
        history = _promotion_history("x" * (1024 * 1024 + 1))
    else:
        history = _promotion_history()
        history[-1] = {
            "role": "user",
            "content": [
                {"type": "input_image", "image_url": "data:image/png;base64,aGVsbG8="},
            ],
        }
    response = await async_client.post("/v1/responses", json={"model": "gpt-5.4", "input": history})
    assert response.status_code == 200, response.text
    assert not upstreams
    assert raw_calls[-1]["upstream_transport"] == "http"
    routing_counter.labels.assert_any_call(stage="admission", reason="smart_history")
    routing_counter.labels.assert_any_call(stage="bypass", reason=bypass)


@pytest.mark.asyncio
async def test_promotion_counts_reuse_separately_from_admission(async_client, promotion_transport, monkeypatch):
    routing_counter, connection_counter = Mock(), Mock()
    monkeypatch.setattr(observability, "http_bridge_routing_total", routing_counter)
    monkeypatch.setattr(helpers, "http_bridge_connections_total", connection_counter)
    for _ in range(2):
        response = await async_client.post("/v1/responses", json={"model": "gpt-5.4", "input": _promotion_history()})
        assert response.status_code == 200, response.text
    assert routing_counter.labels.call_count == 2
    connection_counter.labels.assert_any_call(event="create")
    connection_counter.labels.assert_any_call(event="reuse")


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["disabled", "image_generation", "recent_failure"])
async def test_http_bridge_outage_counts_only_the_gate_that_disables_bridge(
    async_client, promotion_transport, monkeypatch, gate
):
    upstreams, raw_calls, _ = promotion_transport
    routing_counter = Mock()
    monkeypatch.setattr(observability, "http_bridge_routing_total", routing_counter)
    # Model an outage observed after API admission, at the service bypass gates.
    monkeypatch.setattr(streaming, "upstream_websocket_transport_recently_failed", lambda: True)
    body = {"model": "gpt-5.4", "input": _promotion_history()}
    if gate == "disabled":
        app_settings = _make_app_settings(enabled=False)
        monkeypatch.setattr(proxy_module, "get_settings", lambda: app_settings)
    elif gate == "image_generation":
        body["tools"] = [{"type": "image_generation"}]

    response = await async_client.post("/v1/responses", json=body)

    assert response.status_code == 200, response.text
    assert not upstreams
    assert raw_calls[-1]["upstream_transport"] == "http"
    bypass_reasons = [
        call.kwargs["reason"] for call in routing_counter.labels.call_args_list if call.kwargs["stage"] == "bypass"
    ]
    expected = {"disabled": [], "image_generation": ["image"], "recent_failure": ["recent_ws_failure"]}
    assert bypass_reasons == expected[gate]


@pytest.mark.asyncio
@pytest.mark.parametrize("cursor", [False, True])
async def test_chat_bridge_predispatch_error_releases_limited_key(
    async_client, app_instance, promotion_transport, monkeypatch, cursor
):
    key = await _key(async_client, "failed")

    async def reject(*args, **kwargs):
        raise ProxyResponseError(
            400,
            {
                "error": {
                    "code": "context_length_exceeded" if cursor else "invalid_request_error",
                    "type": "invalid_request_error",
                    "message": "rejected before dispatch",
                }
            },
        )

    monkeypatch.setattr(proxy_module, "connect_responses_websocket", reject)
    response = await async_client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-5.4",
            "messages": _promotion_history(),
        },
        headers={"Authorization": f"Bearer {key['key']}", "User-Agent": "Cursor" if cursor else "test"},
    )
    assert response.status_code == (200 if cursor else 400), response.text
    if not cursor:
        assert response.json()["error"]["code"] == "invalid_request_error"
    await get_proxy_service_for_app(app_instance).drain_persistence_tasks(timeout_seconds=5)
    async with SessionLocal() as session:
        rows = (await session.execute(select(ApiKeyUsageReservation))).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "released"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handoff", "expected_status"),
    [("dispatched", "reserved"), ("rejected", "released"), ("settlement_owned", "reserved")],
)
async def test_chat_bridge_error_respects_reservation_handoff(
    async_client, app_instance, promotion_transport, monkeypatch, handoff, expected_status
):
    key = await _key(async_client, "forwarded")

    async def forwarded_error(*args, **kwargs):
        support._signal_propagated_responses_owner_forward_dispatched()
        if handoff == "rejected":
            support._signal_propagated_responses_owner_forward_rejected()
        elif handoff == "settlement_owned":
            support._signal_propagated_responses_service_cleanup_ready()
        raise ProxyResponseError(502, {"error": {"code": "upstream_unavailable", "message": "owner connection closed"}})
        yield  # pragma: no cover -- preserve async iterator contract

    monkeypatch.setattr(get_proxy_service_for_app(app_instance), "stream_http_responses", forwarded_error)
    response = await async_client.post(
        "/v1/chat/completions",
        json={"model": "gpt-5.4", "messages": _promotion_history()},
        headers={"Authorization": f"Bearer {key['key']}"},
    )
    assert response.status_code == 502, response.text
    await get_proxy_service_for_app(app_instance).drain_persistence_tasks(timeout_seconds=5)
    async with SessionLocal() as session:
        rows = (await session.execute(select(ApiKeyUsageReservation))).scalars().all()
        assert len(rows) == 1
        # Ambiguous dispatch and accepted ownership remain the remote service's
        # responsibility; only definitive rejection permits origin release.
        assert rows[0].status == expected_status
