"""Inline-image bridge admission (allow-bounded-inline-images-on-bridge).

Covers the default-on admission contract: the three-way verdict (unsupported /
image_too_large / admitted) with the 5,000,000-byte decoded per-image budget
and its pre-decode fast path, mixed-input precedence, the runtime config
mapping of `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INLINE_IMAGES_ENABLED`
(default true, explicit false rollback), the `_stream_http_bridge_or_retry`
routing gate, and the 64 MiB bridge receive-envelope plumbing.
"""

from __future__ import annotations

import base64
import inspect
import os
from types import SimpleNamespace
from typing import Any
from unittest import mock
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

import app.core.clients.proxy as core_proxy_module
import app.core.clients.proxy_websocket as proxy_websocket_module
import app.modules.proxy._service.support as proxy_support
import app.modules.proxy.service as proxy_service
from app.core.clients.native_egress import _REQUIRED_NATIVE_CAPABILITIES, NativeWebSocketRequest
from app.core.clients.proxy import ProxyResponseError
from app.core.clients.proxy_websocket import connect_responses_websocket
from app.core.config.settings import Settings
from app.core.openai.requests import ResponsesRequest
from app.core.upstream_proxy.types import ResolvedProxyEndpoint, ResolvedUpstreamRoute
from app.modules.proxy._service.http_bridge import helpers as http_bridge_helpers
from tests.unit.test_proxy_utils import (  # noqa: F401
    _make_proxy_settings,
    _repo_factory,
    _RequestLogsRecorder,
    _SettingsCache,
)

pytestmark = pytest.mark.unit

_PNG_DATA_URL = "data:image/png;base64,iVBORw0KGgo="
# A ~4.9 MB decoded image (the operator-observed working shape) and the exact
# inclusive boundary, both as real base64 so the strict decode runs.
_UNDER_BUDGET_IMAGE_URL = "data:image/jpeg;base64," + base64.b64encode(b"J" * 4_900_000).decode("ascii")
_AT_BUDGET_IMAGE_URL = "data:image/jpeg;base64," + base64.b64encode(b"J" * 5_000_000).decode("ascii")
_OVER_BUDGET_IMAGE_URL = "data:image/jpeg;base64," + base64.b64encode(b"J" * 5_000_001).decode("ascii")
# Clearly over budget on the pre-decode fast path alone.
_FAST_PATH_OVER_IMAGE_URL = "data:image/png;base64," + (
    "A" * (http_bridge_helpers._HTTP_BRIDGE_INLINE_IMAGE_MAX_ENCODED_FAST_PATH_BYTES + 4)
)
# Small legal image used to cross a deliberately shrunk frame budget.
_SMALL_LEGAL_IMAGE_URL = "data:image/png;base64," + base64.b64encode(b"P" * 2048).decode("ascii")


def _image_payload(*image_urls: str, extra_input: list[dict[str, Any]] | None = None) -> ResponsesRequest:
    content: list[dict[str, Any]] = [{"type": "input_text", "text": "describe"}]
    content.extend({"type": "input_image", "image_url": url} for url in image_urls)
    input_items: list[dict[str, Any]] = [{"role": "user", "content": content}]
    input_items.extend(extra_input or [])
    return ResponsesRequest.model_validate(
        {"model": "gpt-5.5", "instructions": "hi", "input": input_items, "prompt_cache_key": "inline-bounded"}
    )


@pytest.mark.parametrize(
    ("image_url", "verdict"),
    [
        pytest.param(_PNG_DATA_URL, "admitted", id="png"),
        pytest.param(_UNDER_BUDGET_IMAGE_URL, "admitted", id="jpeg_4_9mb_decoded"),
        pytest.param(_AT_BUDGET_IMAGE_URL, "admitted", id="jpeg_exactly_5mb_decoded_inclusive"),
        pytest.param(_OVER_BUDGET_IMAGE_URL, "image_too_large", id="jpeg_5mb_plus_one_decoded"),
        pytest.param(_FAST_PATH_OVER_IMAGE_URL, "image_too_large", id="encoded_fast_path_over"),
        pytest.param("data:IMAGE/PNG;BASE64,iVBORw0KGgo=", "admitted", id="case_insensitive_prefix"),
        pytest.param("A" * 1024, "unsupported", id="not_a_data_url"),
        pytest.param("data:image/png;base64,", "unsupported", id="empty_segment"),
        pytest.param("https://example.com/shot.png", "unsupported", id="external_https"),
        pytest.param("data:image/webp;base64,iVBORw0KGgo=", "unsupported", id="unsupported_media_type"),
        pytest.param("data:image/png,iVBORw0KGgo=", "unsupported", id="missing_base64_marker"),
        pytest.param("data:image/png;base64,iVBO rw0KGgo=", "unsupported", id="whitespace_rejected"),
        pytest.param("data:image/png;base64,iVBORw0KGgo", "unsupported", id="bad_padding"),
        pytest.param("data:image/png;base64,iVBORw0KGg_", "unsupported", id="urlsafe_alphabet_rejected"),
        pytest.param(None, "unsupported", id="missing_image_url"),
        pytest.param(42, "unsupported", id="non_string_image_url"),
    ],
)
def test_inline_data_image_url_verdicts(image_url: object, verdict: str) -> None:
    classified, decoded_bytes = http_bridge_helpers._inline_data_image_url_verdict(image_url)
    assert classified == verdict
    if verdict == "admitted":
        assert http_bridge_helpers._inline_data_image_url_is_bridge_admissible(image_url) is True
        assert 0 < decoded_bytes <= http_bridge_helpers._HTTP_BRIDGE_INLINE_IMAGE_MAX_DECODED_BYTES
    elif verdict == "image_too_large":
        assert decoded_bytes > http_bridge_helpers._HTTP_BRIDGE_INLINE_IMAGE_MAX_DECODED_BYTES


def test_fast_path_bound_arithmetic() -> None:
    # ceil(5,000,000 / 3) * 4 = 6,666,668: a segment at the bound decodes to
    # at most 5,000,001 bytes (rejected by the exact check), a segment one
    # character longer is provably over budget before decoding.
    assert http_bridge_helpers._HTTP_BRIDGE_INLINE_IMAGE_MAX_ENCODED_FAST_PATH_BYTES == 6_666_668
    at_bound = base64.b64encode(b"J" * 5_000_001).decode("ascii")
    assert len(at_bound) == 6_666_668
    assert http_bridge_helpers._inline_data_image_url_verdict("data:image/png;base64," + at_bound)[0] == (
        "image_too_large"
    )
    # 5,000,000 bytes encode to the same 6,666,668 characters with one pad
    # character and stay admitted.
    assert len(base64.b64encode(b"J" * 5_000_000)) == 6_666_668


def test_over_budget_admission_names_the_worst_image() -> None:
    admission = http_bridge_helpers._inline_input_image_request_admission(_image_payload(_OVER_BUDGET_IMAGE_URL))
    assert admission.verdict == "image_too_large"
    assert admission.max_decoded_image_bytes == 5_000_001


def test_request_admission_walks_the_whole_input() -> None:
    nested_admissible = _image_payload(
        _PNG_DATA_URL,
        extra_input=[
            {
                "type": "function_call_output",
                "call_id": "call_shot",
                "output": [{"type": "input_image", "image_url": _UNDER_BUDGET_IMAGE_URL}],
            }
        ],
    )
    admission = http_bridge_helpers._inline_input_image_request_admission(nested_admissible)
    assert admission.verdict == "admitted"
    assert admission.max_decoded_image_bytes == 4_900_000


def test_valid_image_part_with_unsafe_image_in_a_sibling_field_is_unsupported() -> None:
    # Regression for a walker short-circuit: a parent ``input_image`` with an
    # admissible URL must not hide an unsafe ``input_image`` sitting in
    # another field of the very same part — every ``input_image`` anywhere in
    # the input is checked.
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.5",
            "instructions": "hi",
            "input": [
                {
                    "type": "input_image",
                    "image_url": _PNG_DATA_URL,
                    "metadata": [{"type": "input_image", "image_url": "https://example.com/hidden.png"}],
                }
            ],
        }
    )
    assert http_bridge_helpers._inline_input_image_request_admission(payload).verdict == "unsupported"


def test_request_admission_requires_images() -> None:
    text_only = ResponsesRequest.model_validate({"model": "gpt-5.5", "instructions": "hi", "input": "hello"})
    assert http_bridge_helpers._inline_input_image_request_admission(text_only).verdict == "unsupported"


def test_runtime_config_maps_the_flag_with_default_true() -> None:
    dashboard_settings = SimpleNamespace(
        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
        http_responses_session_bridge_gateway_safe_mode=False,
    )
    default_config = http_bridge_helpers._http_bridge_runtime_config(dashboard_settings, Settings(_env_file=None))
    assert default_config.inline_images_enabled is True
    rollback_config = http_bridge_helpers._http_bridge_runtime_config(
        dashboard_settings,
        Settings(_env_file=None, http_responses_session_bridge_inline_images_enabled=False),
    )
    assert rollback_config.inline_images_enabled is False


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        pytest.param(None, True, id="unset_defaults_on"),
        pytest.param("true", True, id="true"),
        pytest.param("1", True, id="one"),
        pytest.param("false", False, id="false_rollback"),
        pytest.param("0", False, id="zero_rollback"),
        pytest.param("FALSE", False, id="upper_false_rollback"),
        pytest.param("banana", None, id="invalid_fails_at_settings_load"),
    ],
)
def test_inline_images_env_parsing(env_value: str | None, expected: bool | None) -> None:
    name = "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INLINE_IMAGES_ENABLED"
    clean = {k: v for k, v in os.environ.items() if k != name}
    if env_value is not None:
        clean[name] = env_value
    with mock.patch.dict(os.environ, clean, clear=True):
        if expected is None:
            with pytest.raises(ValidationError):
                # Pydantic rejects non-boolean coercions at startup rather
                # than silently falling back to the default.
                Settings(_env_file=None)
        else:
            settings = Settings(_env_file=None)
            assert settings.http_responses_session_bridge_inline_images_enabled is expected


def _runtime_config(*, enabled: bool, inline_images_enabled: bool) -> Any:
    return proxy_service._HTTPBridgeRuntimeConfig(
        enabled=enabled,
        idle_ttl_seconds=30.0,
        codex_idle_ttl_seconds=30.0,
        max_sessions=8,
        queue_limit=16,
        prompt_cache_idle_ttl_seconds=30.0,
        gateway_safe_mode=False,
        inline_images_enabled=inline_images_enabled,
    )


def _make_service(monkeypatch: pytest.MonkeyPatch, *, runtime_config: Any) -> tuple[Any, list[tuple[str, Any]]]:
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    settings = _make_proxy_settings()
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        proxy_service,
        "_http_bridge_runtime_config",
        lambda _dashboard_settings, _app_settings: runtime_config,
    )
    monkeypatch.setattr(service, "_resolve_file_account_for_responses", AsyncMock(return_value=None))
    calls: list[tuple[str, Any]] = []

    async def fake_stream_with_retry(payload, headers, **kwargs):
        del payload, headers
        calls.append(("retry", kwargs.get("upstream_stream_transport_override")))
        yield "data: retry\n\n"

    async def fake_stream_via_http_bridge(payload, headers, **kwargs):
        del payload, headers
        calls.append(("bridge", kwargs.get("inline_image_request")))
        yield "data: bridge\n\n"

    monkeypatch.setattr(service, "_stream_with_retry", fake_stream_with_retry)
    monkeypatch.setattr(service, "_stream_via_http_bridge", fake_stream_via_http_bridge)
    return service, calls


async def _run_bridge_or_retry(service: Any, payload: ResponsesRequest) -> list[str]:
    return [
        line
        async for line in service._stream_http_bridge_or_retry(
            payload=payload,
            headers={},
            codex_session_affinity=False,
            propagate_http_errors=False,
            openai_cache_affinity=False,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("image_urls", "extra_input", "expected"),
    [
        pytest.param([_PNG_DATA_URL, "https://example.com/shot.png"], None, ("retry", "http"), id="mixed_top_level"),
        pytest.param(
            [_FAST_PATH_OVER_IMAGE_URL, "https://example.com/shot.png"],
            None,
            ("retry", "http"),
            id="oversize_plus_external_keeps_bypass",
        ),
        pytest.param(
            [_PNG_DATA_URL],
            [
                {
                    "type": "function_call_output",
                    "call_id": "call_shot",
                    "output": [{"type": "input_image", "image_url": _OVER_BUDGET_IMAGE_URL}],
                }
            ],
            ("reject", None),
            id="oversize_nested_in_tool_output",
        ),
    ],
)
async def test_gate_routing_verdicts(monkeypatch, image_urls, extra_input, expected):
    proxy_support.clear_upstream_websocket_transport_failure()
    service, calls = _make_service(
        monkeypatch, runtime_config=_runtime_config(enabled=True, inline_images_enabled=True)
    )
    payload = _image_payload(*image_urls, extra_input=extra_input)
    if expected[0] == "reject":
        with pytest.raises(ProxyResponseError) as exc_info:
            await _run_bridge_or_retry(service, payload)
        assert exc_info.value.status_code == 400
        error = exc_info.value.payload["error"]
        assert error["code"] == "payload_too_large"
        assert error["type"] == "invalid_request_error"
        assert error["param"] == "input"
        assert "5000001" in error["message"]
        # Neither the raw path nor the bridge served the request.
        assert calls == []
        return
    output = await _run_bridge_or_retry(service, payload)
    assert output == ["data: retry\n\n"]
    assert calls == [expected]


@pytest.mark.asyncio
async def test_gate_default_on_admits_bounded_inline_image_onto_the_bridge(monkeypatch):
    proxy_support.clear_upstream_websocket_transport_failure()
    service, calls = _make_service(
        monkeypatch, runtime_config=_runtime_config(enabled=True, inline_images_enabled=True)
    )
    output = await _run_bridge_or_retry(service, _image_payload(_UNDER_BUDGET_IMAGE_URL, _PNG_DATA_URL))
    assert output == ["data: bridge\n\n"]
    assert calls == [("bridge", True)]


@pytest.mark.asyncio
async def test_gate_explicit_false_restores_the_blanket_image_bypass(monkeypatch):
    proxy_support.clear_upstream_websocket_transport_failure()
    service, calls = _make_service(
        monkeypatch, runtime_config=_runtime_config(enabled=True, inline_images_enabled=False)
    )
    output = await _run_bridge_or_retry(service, _image_payload(_PNG_DATA_URL))
    assert output == ["data: retry\n\n"]
    assert calls == [("retry", None)]


@pytest.mark.asyncio
async def test_gate_still_bypasses_for_image_generation(monkeypatch):
    proxy_support.clear_upstream_websocket_transport_failure()
    service, calls = _make_service(
        monkeypatch, runtime_config=_runtime_config(enabled=True, inline_images_enabled=True)
    )
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.5",
            "instructions": "draw",
            "input": [{"role": "user", "content": "draw"}, {"type": "input_image", "image_url": _PNG_DATA_URL}],
            "tools": [{"type": "image_generation"}],
        }
    )
    output = await _run_bridge_or_retry(service, payload)
    assert output == ["data: retry\n\n"]
    assert calls == [("retry", None)]


@pytest.mark.asyncio
async def test_gate_over_frame_budget_is_explicitly_rejected(monkeypatch):
    proxy_support.clear_upstream_websocket_transport_failure()
    service, _calls = _make_service(
        monkeypatch, runtime_config=_runtime_config(enabled=True, inline_images_enabled=True)
    )
    # Shrink the frame budget so an admitted-shape payload crosses it: the
    # explicit frame-budget 400, never a size-driven bypass.
    monkeypatch.setattr(http_bridge_helpers, "_HTTP_BRIDGE_IMAGE_REQUEST_MAX_FRAME_BYTES", 512)
    with pytest.raises(ProxyResponseError) as exc_info:
        await _run_bridge_or_retry(service, _image_payload(_SMALL_LEGAL_IMAGE_URL, _SMALL_LEGAL_IMAGE_URL))
    assert exc_info.value.status_code == 400
    error = exc_info.value.payload["error"]
    assert error["code"] == "payload_too_large"
    assert error["param"] == "input"


# --- 64 MiB bridge receive envelope ---------------------------------------


def _resolved_route() -> ResolvedUpstreamRoute:
    endpoint = ResolvedProxyEndpoint(id="ep-1", scheme="http", host="127.0.0.1", port=1080)
    return ResolvedUpstreamRoute(mode="proxy", pool_id="pool-1", endpoint=endpoint)


_STOCK_CAP = core_proxy_module.MAX_SSE_EVENT_BYTES
_FRAME_CAP = 64 * 1024 * 1024


def test_connector_signature_preserves_optional_per_connection_cap() -> None:
    parameters = inspect.signature(connect_responses_websocket).parameters
    assert parameters["max_message_bytes"].default is None
    assert "max_message_bytes" in inspect.signature(proxy_websocket_module._connect_upstream_websocket).parameters


@pytest.mark.parametrize("inline_enabled", [False, True])
def test_bridge_connection_cap_follows_inline_mode(monkeypatch: pytest.MonkeyPatch, inline_enabled: bool) -> None:
    monkeypatch.setattr(
        http_bridge_helpers,
        "_service_get_settings",
        lambda: SimpleNamespace(http_responses_session_bridge_inline_images_enabled=inline_enabled),
    )
    assert http_bridge_helpers._http_bridge_upstream_max_message_bytes() == (_FRAME_CAP if inline_enabled else None)


@pytest.mark.asyncio
@pytest.mark.parametrize("cap", [None, _FRAME_CAP])
async def test_websockets_client_uses_selected_cap(monkeypatch: pytest.MonkeyPatch, cap: int | None) -> None:
    captured: dict[str, Any] = {}

    async def fake_websocket_connect(url: str, **kwargs: Any) -> SimpleNamespace:
        del url
        captured.update(kwargs)
        return SimpleNamespace(connection_lost_waiter=None)

    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect)
    monkeypatch.setattr(proxy_websocket_module, "discover_native_egress_client", lambda: None)
    await connect_responses_websocket({}, "token", None, allow_direct_egress=True, max_message_bytes=cap)
    assert captured["max_size"] == (_STOCK_CAP if cap is None else cap)


@pytest.mark.asyncio
@pytest.mark.parametrize("cap", [None, _FRAME_CAP])
async def test_native_egress_request_uses_selected_cap(monkeypatch: pytest.MonkeyPatch, cap: int | None) -> None:
    captured: dict[str, Any] = {}

    class _FakeNativeEgressClient:
        async def websocket(self, request: NativeWebSocketRequest) -> SimpleNamespace:
            captured["request"] = request
            return SimpleNamespace()

    monkeypatch.setattr(
        proxy_websocket_module,
        "discover_native_egress_client",
        lambda: _FakeNativeEgressClient(),
    )
    await connect_responses_websocket({}, "token", None, allow_direct_egress=True, max_message_bytes=cap)
    assert captured["request"].max_message_bytes == (_STOCK_CAP if cap is None else cap)


@pytest.mark.asyncio
@pytest.mark.parametrize("cap", [None, _FRAME_CAP])
async def test_routed_aiohttp_opener_uses_selected_cap(monkeypatch: pytest.MonkeyPatch, cap: int | None) -> None:
    captured: dict[str, Any] = {}
    route = _resolved_route()

    class _FakeCodexClient:
        def __init__(self, session: Any) -> None:
            del session

        async def open_ws_with_route_metadata(self, url: str, **kwargs: Any) -> SimpleNamespace:
            del url
            captured.update(kwargs)
            return SimpleNamespace(
                context=None,
                websocket=SimpleNamespace(),
                route=route,
                fallback_used=False,
                native=False,
            )

        async def close(self) -> None:
            return None

    monkeypatch.setattr(proxy_websocket_module, "CodexClient", _FakeCodexClient)
    monkeypatch.setattr(proxy_websocket_module, "create_codex_session", lambda: object())
    await connect_responses_websocket(
        {},
        "token",
        "acc-1",
        route=route,
        max_message_bytes=cap,
    )
    assert captured["max_msg_size"] == (_STOCK_CAP if cap is None else cap)


def test_native_chunking_capability_pair_is_required() -> None:
    # The parent must refuse a helper that cannot chunk large messages:
    # otherwise an oversize upstream message would arrive as one IPC line
    # that trips the parent's readline limit.
    assert "websocket_text_chunking_v1" in _REQUIRED_NATIVE_CAPABILITIES
