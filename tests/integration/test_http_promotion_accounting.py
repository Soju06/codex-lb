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


def _image_history(image_url, *, position=-1):
    history = _promotion_history()
    history[position] = {"role": "user", "content": [{"type": "input_image", "image_url": image_url}]}
    return history


@pytest.mark.asyncio
async def test_promoted_oversized_payload_bypass_is_counted_and_pins_http(
    async_client, promotion_transport, monkeypatch
):
    upstreams, raw_calls, _ = promotion_transport
    routing_counter = Mock()
    monkeypatch.setattr(observability, "http_bridge_routing_total", routing_counter)
    # The payload bypass compares against the fixed upstream frame budget
    # (MAX_SSE_EVENT_BYTES - 2 MiB headroom, floored at 1 MiB): shrink it so
    # a 1 MiB + 1 history trips the bypass.
    monkeypatch.setattr(core_proxy_module, "MAX_SSE_EVENT_BYTES", 3 * 1024 * 1024)
    history = _promotion_history("x" * (1024 * 1024 + 1))
    response = await async_client.post("/v1/responses", json={"model": "gpt-5.4", "input": history})
    assert response.status_code == 200, response.text
    assert not upstreams
    assert raw_calls[-1]["upstream_transport"] == "http"
    routing_counter.labels.assert_any_call(stage="admission", reason="smart_history")
    routing_counter.labels.assert_any_call(stage="bypass", reason="payload_size")


@pytest.mark.asyncio
async def test_promoted_image_bypass_is_counted_without_pinning_http(async_client, promotion_transport, monkeypatch):
    # Regression for #2363: the image bypass frees bridge pending slots, and it
    # must keep doing that, but an inline ``data:`` image below the frame budget
    # must no longer drag the request onto the upstream HTTP transport.
    upstreams, raw_calls, _ = promotion_transport
    routing_counter = Mock()
    monkeypatch.setattr(observability, "http_bridge_routing_total", routing_counter)
    history = _image_history("data:image/png;base64,aGVsbG8=")
    response = await async_client.post("/v1/responses", json={"model": "gpt-5.4", "input": history})
    assert response.status_code == 200, response.text
    assert not upstreams
    assert raw_calls[-1]["upstream_transport"] == "auto"
    routing_counter.labels.assert_any_call(stage="admission", reason="smart_history")
    routing_counter.labels.assert_any_call(stage="bypass", reason="image")


@pytest.mark.asyncio
async def test_historical_image_does_not_pin_later_turns_to_http(async_client, promotion_transport):
    # The reported production shape: Codex keeps earlier screenshots in the
    # input, so before #2363 one historical image pinned every later turn of the
    # thread to the degraded upstream HTTP path.
    upstreams, raw_calls, _ = promotion_transport
    history = _image_history("data:image/png;base64,aGVsbG8=", position=0)
    response = await async_client.post("/v1/responses", json={"model": "gpt-5.4", "input": history})
    assert response.status_code == 200, response.text
    assert not upstreams
    assert raw_calls[-1]["upstream_transport"] == "auto"


@pytest.mark.asyncio
async def test_oversized_image_request_still_pins_http(async_client, promotion_transport, monkeypatch):
    upstreams, raw_calls, dashboard = promotion_transport
    routing_counter = Mock()
    monkeypatch.setattr(observability, "http_bridge_routing_total", routing_counter)
    monkeypatch.setattr(core_proxy_module, "MAX_SSE_EVENT_BYTES", 3 * 1024 * 1024)
    # Pin the websocket explicitly so the size clause of the narrowed predicate
    # is the only thing that can still produce "http" here: an explicit pin
    # short-circuits _resolve_stream_transport before its own frame-budget gate,
    # which would otherwise satisfy this assertion on its own. This is also the
    # configuration the residual pin exists for, since that short-circuit is what
    # would turn an oversized image payload into a local 400 payload_too_large.
    dashboard.upstream_stream_transport = "websocket"
    history = _image_history("data:image/png;base64," + "A" * (1024 * 1024 + 1))
    response = await async_client.post("/v1/responses", json={"model": "gpt-5.4", "input": history})
    assert response.status_code == 200, response.text
    assert not upstreams
    assert raw_calls[-1]["upstream_transport"] == "http"
    # The size gate runs first and already disables the bridge, so only
    # ``payload_size`` is counted; the image gate is skipped, as it is today for
    # every oversized image request.
    bypass_reasons = [
        call.kwargs["reason"] for call in routing_counter.labels.call_args_list if call.kwargs["stage"] == "bypass"
    ]
    assert bypass_reasons == ["payload_size"]


@pytest.mark.asyncio
async def test_external_image_url_request_still_pins_http(async_client, promotion_transport):
    # An external URL survives when ``_inline_content_images`` cannot fetch it,
    # and the upstream websocket does not accept one, so this shape keeps the pin.
    upstreams, raw_calls, _ = promotion_transport
    history = _image_history("https://example.com/shot.png")
    response = await async_client.post("/v1/responses", json={"model": "gpt-5.4", "input": history})
    assert response.status_code == 200, response.text
    assert not upstreams
    assert raw_calls[-1]["upstream_transport"] == "http"


@pytest.mark.asyncio
async def test_external_image_url_inside_a_tool_output_still_pins_http(async_client, promotion_transport):
    # A ``function_call_output`` output array is a routine Codex tool-result
    # shape, and the URL inliner never walks into it, so an external URL there
    # is still external at the upstream. It has to keep the pin even though it
    # is deeper than the shapes ``_count_external_image_urls`` visits.
    upstreams, raw_calls, _ = promotion_transport
    history = _promotion_history()
    history[-1] = {
        "type": "function_call_output",
        "call_id": "call_shot",
        "output": [{"type": "input_image", "image_url": "https://example.com/shot.png"}],
    }
    response = await async_client.post("/v1/responses", json={"model": "gpt-5.4", "input": history})
    assert response.status_code == 200, response.text
    assert not upstreams
    assert raw_calls[-1]["upstream_transport"] == "http"


@pytest.mark.asyncio
async def test_external_image_url_pins_http_under_an_explicit_websocket_override(async_client, promotion_transport):
    # The residual pin is deliberately evaluated ahead of an explicit websocket
    # override: the override short-circuits the transport resolver, so without
    # the pin the upstream WebSocket would be handed a URL it does not accept.
    upstreams, raw_calls, dashboard = promotion_transport
    dashboard.upstream_stream_transport = "websocket"
    history = _image_history("https://example.com/shot.png")
    response = await async_client.post("/v1/responses", json={"model": "gpt-5.4", "input": history})
    assert response.status_code == 200, response.text
    assert not upstreams
    assert raw_calls[-1]["upstream_transport"] == "http"


@pytest.mark.asyncio
@pytest.mark.parametrize("scheme", ["https", "HTTPS", "HtTp"])
async def test_external_image_url_scheme_match_is_case_insensitive(async_client, promotion_transport, scheme):
    # URL schemes are case-insensitive, and this is the fail-safe direction:
    # missing one sends a raw external URL to a websocket that accepts only
    # ``data:``.
    upstreams, raw_calls, _ = promotion_transport
    history = _image_history(f"{scheme}://example.com/shot.png")
    response = await async_client.post("/v1/responses", json={"model": "gpt-5.4", "input": history})
    assert response.status_code == 200, response.text
    assert not upstreams
    assert raw_calls[-1]["upstream_transport"] == "http"


@pytest.mark.asyncio
async def test_inline_image_inside_a_tool_output_does_not_pin_http(async_client, promotion_transport):
    # The counterpart that makes the clause above narrow rather than a
    # reinstatement of the old blanket pin: an inline image nested just as
    # deeply is carried by the websocket unchanged, so it must not pin.
    upstreams, raw_calls, _ = promotion_transport
    history = _promotion_history()
    history[-1] = {
        "type": "function_call_output",
        "call_id": "call_shot",
        "output": [{"type": "input_image", "image_url": "data:image/png;base64,aGVsbG8="}],
    }
    response = await async_client.post("/v1/responses", json={"model": "gpt-5.4", "input": history})
    assert response.status_code == 200, response.text
    assert not upstreams
    assert raw_calls[-1]["upstream_transport"] == "auto"


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
