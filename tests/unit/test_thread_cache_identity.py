"""Two-mode thread cache identity.

The first test in this file is the whole safety argument for shipping the
feature default-on-``shared``: the outbound bytes and the outbound header list
under ``shared`` must be identical to the bytes and headers produced when no
identity is threaded at all, which is exactly the pre-change code path.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from app.core.clients import proxy as proxy_module
from app.core.clients.http import lease_http_session
from app.core.clients.proxy import (
    _build_upstream_headers,
    _build_websocket_response_create_payload,
    compact_responses,
    stream_responses,
)
from app.core.clients.thread_cache_identity import (
    THREAD_CACHE_IDENTITY_MODE_DEFAULT,
    ThreadCacheIdentity,
    apply_thread_cache_identity,
    cache_scope_payload_overhead_bytes,
    effective_thread_cache_identity_mode,
    inject_cache_scope_prefix,
    normalize_thread_cache_identity_mode,
    scope_prompt_cache_key,
    scope_session_headers,
)
from app.core.config.settings import Settings, get_settings
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.core.types import JsonValue
from app.db.models import DashboardSettings
from app.modules.proxy._service.http_bridge import request_submit as request_submit_module
from app.modules.settings.service import _resolve_inheritable_settings

pytestmark = pytest.mark.unit

_ACCOUNT_A = "lb-account-a"
_ACCOUNT_B = "lb-account-b"
_TURN_STATE = "turn_8f1c0f5e-2b17-4a9d-9a0e-1b2c3d4e5f60"
_SESSION_UUID = "550e8400-e29b-41d4-a716-446655440000"


def _mapping(value: JsonValue) -> Mapping[str, JsonValue]:
    assert isinstance(value, Mapping)
    return value


def _sequence(value: JsonValue) -> list[JsonValue]:
    assert isinstance(value, list)
    return value


def _text(value: JsonValue) -> str:
    assert isinstance(value, str)
    return value


def _shared(account_id: str = _ACCOUNT_A) -> ThreadCacheIdentity:
    return ThreadCacheIdentity(mode="shared", account_id=account_id)


def _isolated(account_id: str = _ACCOUNT_A) -> ThreadCacheIdentity:
    return ThreadCacheIdentity(mode="isolated", account_id=account_id)


def _lite_payload_dict() -> dict[str, Any]:
    """The responses-lite wire shape: empty ``instructions``, tools in the input prefix."""
    return {
        "model": "gpt-5.2",
        "instructions": "",
        "input": [
            {"type": "additional_tools", "role": "developer", "tools": [{"type": "shell"}]},
            {"role": "user", "content": [{"type": "input_text", "text": "hello"}]},
        ],
        "prompt_cache_key": "cache-key-1",
        "stream": True,
    }


def _classic_payload_dict() -> dict[str, Any]:
    return {
        "model": "gpt-5.2",
        "instructions": "You are a helpful assistant.",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
        "prompt_cache_key": "cache-key-1",
        "stream": True,
    }


def _string_input_payload_dict() -> dict[str, Any]:
    return {"model": "gpt-5.2", "instructions": "", "input": "hello", "stream": True}


def _no_cache_key_payload_dict() -> dict[str, Any]:
    payload = _classic_payload_dict()
    del payload["prompt_cache_key"]
    return payload


def _inbound_headers() -> dict[str, str]:
    return {
        "user-agent": "codex_cli_rs/0.142.0",
        "session_id": _SESSION_UUID,
        "thread-id": "thr_abc123",
        "x-codex-turn-state": _TURN_STATE,
        "Authorization": "Bearer downstream",
    }


# --------------------------------------------------------------------------
# 1. shared mode is a strict no-op (the property the default rests on)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "builder",
    [_lite_payload_dict, _classic_payload_dict, _string_input_payload_dict, _no_cache_key_payload_dict],
    ids=["lite", "classic", "string-input", "no-cache-key"],
)
@pytest.mark.parametrize("identity", [None, _shared(), ThreadCacheIdentity(mode="isolated", account_id=None)])
def test_shared_mode_payload_helpers_are_byte_identical_no_ops(builder, identity) -> None:
    payload = builder()
    before = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))

    apply_thread_cache_identity(payload, identity)

    assert json.dumps(payload, ensure_ascii=True, separators=(",", ":")) == before


@pytest.mark.parametrize("identity", [None, _shared()])
def test_shared_mode_header_scoping_is_a_no_op(identity) -> None:
    headers = _build_upstream_headers(_inbound_headers(), "upstream-token", "chatgpt-acct")
    before = list(headers.items())

    scope_session_headers(headers, identity, replace=proxy_module._replace_header_preserving_position)

    assert list(headers.items()) == before


def test_shared_mode_websocket_response_create_is_byte_identical() -> None:
    payload = _lite_payload_dict()
    baseline = _build_websocket_response_create_payload(copy.deepcopy(payload))

    apply_thread_cache_identity(payload, _shared())

    assert _build_websocket_response_create_payload(payload) == baseline


# --------------------------------------------------------------------------
# 2. token derivation: deterministic, account-only, turn-state immune
# --------------------------------------------------------------------------


def test_token_is_deterministic_across_calls_and_processes() -> None:
    assert _isolated().token == _isolated().token
    # Pinned literal: changing the derivation must be a deliberate, visible
    # edit, because it flushes every account's warm prefix.
    assert _isolated().token == "20d2b7f022ed18bd"
    assert _isolated().scope_line == "codex-lb-cache-scope: 20d2b7f022ed18bd"


def test_token_diverges_across_accounts() -> None:
    assert _isolated(_ACCOUNT_A).token != _isolated(_ACCOUNT_B).token


def test_token_has_no_session_or_turn_component() -> None:
    """Turn 2 of a session, with a fresh turn state and no session header, matches turn 1."""
    turn_one = _isolated()
    turn_two = _isolated()
    assert turn_one.token == turn_two.token
    assert turn_one.scope_line == turn_two.scope_line


# --------------------------------------------------------------------------
# 3. shape-aware injection
# --------------------------------------------------------------------------


def test_lite_payload_gets_a_leading_input_item_and_keeps_empty_instructions() -> None:
    payload = _lite_payload_dict()
    original_first = payload["input"][0]

    inject_cache_scope_prefix(payload, _isolated())

    assert payload["instructions"] == ""
    assert payload["input"][0] == {
        "type": "message",
        "role": "developer",
        "content": [{"type": "input_text", "text": _isolated().scope_line}],
    }
    assert payload["input"][1] is original_first


def test_classic_payload_prefixes_instructions() -> None:
    payload = _classic_payload_dict()

    inject_cache_scope_prefix(payload, _isolated())

    assert payload["instructions"] == f"{_isolated().scope_line}\n\nYou are a helpful assistant."
    assert payload["input"][0]["role"] == "user"


def test_string_input_payload_prefixes_the_string() -> None:
    payload = _string_input_payload_dict()

    inject_cache_scope_prefix(payload, _isolated())

    assert payload["input"] == f"{_isolated().scope_line}\n\nhello"


def test_injection_does_not_mutate_a_shared_input_list() -> None:
    """The caller shares the list with the HTTP and websocket payload copies."""
    payload = _lite_payload_dict()
    shared_list = payload["input"]

    inject_cache_scope_prefix(payload, _isolated())

    assert len(shared_list) == 2
    assert payload["input"] is not shared_list


def test_injection_is_idempotent() -> None:
    payload = _classic_payload_dict()
    inject_cache_scope_prefix(payload, _isolated())
    once = payload["instructions"]
    inject_cache_scope_prefix(payload, _isolated())
    assert payload["instructions"] == once

    lite = _lite_payload_dict()
    inject_cache_scope_prefix(lite, _isolated())
    length = len(lite["input"])
    inject_cache_scope_prefix(lite, _isolated())
    assert len(lite["input"]) == length


def test_injected_prefix_survives_onto_response_create() -> None:
    payload = _lite_payload_dict()
    apply_thread_cache_identity(payload, _isolated())

    frame = _build_websocket_response_create_payload(payload)

    assert frame["type"] == "response.create"
    first_item = _mapping(_sequence(frame["input"])[0])
    assert _text(_mapping(_sequence(first_item["content"])[0])["text"]) == _isolated().scope_line
    assert _text(frame["prompt_cache_key"]).endswith(_isolated().token)


# --------------------------------------------------------------------------
# 4. key and header scoping
# --------------------------------------------------------------------------


def test_scoped_key_is_suffixed() -> None:
    payload = _classic_payload_dict()
    scope_prompt_cache_key(payload, _isolated())
    assert payload["prompt_cache_key"] == f"cache-key-1-{_isolated().token}"


@pytest.mark.parametrize(
    "client_key",
    [
        # A client key that happens to look already-scoped must still be scoped.
        # Skipping it would send the identical key on two accounts, which is
        # precisely the isolation this exists for.
        f"mykey-{_isolated().token}",
        "clbcs1-deadbeefdeadbeefdeadbeefdeadbeef",
    ],
)
def test_client_key_that_looks_pre_scoped_is_still_scoped_per_account(client_key: str) -> None:
    a: dict[str, JsonValue] = {"prompt_cache_key": client_key}
    b: dict[str, JsonValue] = {"prompt_cache_key": client_key}
    scope_prompt_cache_key(a, _isolated(_ACCOUNT_A))
    scope_prompt_cache_key(b, _isolated(_ACCOUNT_B))
    assert a["prompt_cache_key"] != client_key
    assert a["prompt_cache_key"] != b["prompt_cache_key"]


@pytest.mark.parametrize("length", [1, 20, 46, 47, 48, 64, 200])
def test_scoped_key_never_exceeds_the_upstream_length_bound(length: int) -> None:
    """A client key at the upstream limit must not be pushed past it.

    Appending 17 characters unconditionally would turn a previously valid
    request into an upstream rejection, so past the bound the original folds
    into a digest instead.
    """
    payload: dict[str, JsonValue] = {"prompt_cache_key": "k" * length}
    scope_prompt_cache_key(payload, _isolated())
    assert len(payload["prompt_cache_key"]) <= 64


def test_scoped_key_stays_account_unique_past_the_length_bound() -> None:
    long_key = "k" * 200
    a: dict[str, JsonValue] = {"prompt_cache_key": long_key}
    b: dict[str, JsonValue] = {"prompt_cache_key": long_key}
    scope_prompt_cache_key(a, _isolated(_ACCOUNT_A))
    scope_prompt_cache_key(b, _isolated(_ACCOUNT_B))
    assert a["prompt_cache_key"] != b["prompt_cache_key"]


def test_scoped_key_stays_client_unique_past_the_length_bound() -> None:
    a: dict[str, JsonValue] = {"prompt_cache_key": "a" * 200}
    b: dict[str, JsonValue] = {"prompt_cache_key": "b" * 200}
    scope_prompt_cache_key(a, _isolated())
    scope_prompt_cache_key(b, _isolated())
    assert a["prompt_cache_key"] != b["prompt_cache_key"]


@pytest.mark.parametrize("length", [1, 20, 46, 47, 48, 64, 200])
def test_scoped_key_is_deterministic_for_one_account(length: int) -> None:
    """Same client key + same account == same scoped key, on every request."""
    first: dict[str, JsonValue] = {"prompt_cache_key": "k" * length}
    second: dict[str, JsonValue] = {"prompt_cache_key": "k" * length}
    scope_prompt_cache_key(first, _isolated())
    scope_prompt_cache_key(second, _isolated())
    assert first["prompt_cache_key"] == second["prompt_cache_key"]


def test_declared_payload_overhead_bounds_the_real_injection() -> None:
    """Transport selection adds this constant before the payload exists."""
    payload = _lite_payload_dict()
    before = len(json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode())
    apply_thread_cache_identity(payload, _isolated())
    after = len(json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode())

    assert after - before <= cache_scope_payload_overhead_bytes("isolated")
    assert cache_scope_payload_overhead_bytes("shared") == 0
    assert cache_scope_payload_overhead_bytes(None) == 0


def test_absent_cache_key_is_not_invented() -> None:
    payload = _no_cache_key_payload_dict()
    scope_prompt_cache_key(payload, _isolated())
    assert "prompt_cache_key" not in payload


def test_session_headers_are_scoped_shape_preservingly_and_turn_state_is_untouched() -> None:
    headers = {
        "session_id": _SESSION_UUID,
        "Thread-Id": "thr_abc123",
        "x-codex-turn-state": _TURN_STATE,
        "Authorization": "Bearer upstream",
    }
    order_before = list(headers)

    scope_session_headers(headers, _isolated(), replace=proxy_module._replace_header_preserving_position)

    assert list(headers) == order_before, "header order and spelling must survive"
    assert headers["x-codex-turn-state"] == _TURN_STATE
    assert headers["Authorization"] == "Bearer upstream"
    assert headers["session_id"] != _SESSION_UUID
    # UUID-shaped in, UUID-shaped out.
    assert len(headers["session_id"]) == len(_SESSION_UUID)
    assert headers["session_id"].count("-") == 4
    assert headers["Thread-Id"] == f"thr_abc123-{_isolated().token}"


def test_scoped_session_headers_diverge_across_accounts() -> None:
    a = {"session_id": _SESSION_UUID}
    b = {"session_id": _SESSION_UUID}
    scope_session_headers(a, _isolated(_ACCOUNT_A))
    scope_session_headers(b, _isolated(_ACCOUNT_B))
    assert a["session_id"] != b["session_id"]


# --------------------------------------------------------------------------
# 5. override resolution
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Key:
    thread_cache_identity_override: str | None


@dataclass(frozen=True)
class _Snapshot:
    thread_cache_identity_mode: str | None


@pytest.mark.parametrize(
    ("api_key", "snapshot", "expected", "from_key"),
    [
        (_Key("isolated"), _Snapshot("shared"), "isolated", True),
        (_Key("shared"), _Snapshot("isolated"), "shared", True),
        (_Key(None), _Snapshot("isolated"), "isolated", False),
        (_Key(None), _Snapshot(None), "shared", False),
        (None, _Snapshot("isolated"), "isolated", False),
        (None, _Snapshot(None), "shared", False),
        # A stale or hand-edited value degrades to the next layer, never a 500.
        (_Key("nonsense"), _Snapshot("isolated"), "isolated", False),
        (_Key(None), _Snapshot("nonsense"), "shared", False),
    ],
)
def test_override_precedence(api_key, snapshot, expected, from_key) -> None:
    assert effective_thread_cache_identity_mode(api_key, snapshot) == (expected, from_key)


def test_stub_snapshot_without_the_field_degrades_to_the_default() -> None:
    assert effective_thread_cache_identity_mode(None, object()) == (THREAD_CACHE_IDENTITY_MODE_DEFAULT, False)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("shared", "shared"), ("  ISOLATED ", "isolated"), ("", None), (None, None), ("off", None), (7, None)],
)
def test_mode_normalization(value, expected) -> None:
    assert normalize_thread_cache_identity_mode(value) == expected


@pytest.mark.parametrize(
    ("configured", "expected"),
    [("isolated", "isolated"), ("  ISOLATED ", "isolated"), ("typo", "shared"), ("", "shared"), (None, "shared")],
)
def test_environment_value_never_escapes_the_settings_value_domain(configured, expected) -> None:
    """A typo in the env var must not reach the settings response model.

    ``DashboardSettingsResponse.thread_cache_identity_mode`` is pattern-
    constrained, so an unconstrained passthrough would fail ``GET /api/settings``
    for every setting at once rather than for this one.
    """
    assert Settings(thread_cache_identity_mode=configured).thread_cache_identity_mode == expected


def test_unset_environment_value_is_shared() -> None:
    assert Settings().thread_cache_identity_mode == THREAD_CACHE_IDENTITY_MODE_DEFAULT


@pytest.mark.parametrize(
    ("stored", "expected_value", "expected_source"),
    [
        ("isolated", "isolated", "dashboard"),
        ("shared", "shared", "dashboard"),
        (None, "shared", "default"),
        # A stale or hand-edited column value is not a valid decision. It must
        # not reach the pattern-constrained settings response, which would fail
        # GET /api/settings for every setting at once.
        ("NONSENSE", "shared", "default"),
        ("", "shared", "default"),
    ],
)
def test_stale_dashboard_value_never_escapes_the_settings_value_domain(stored, expected_value, expected_source) -> None:
    row = DashboardSettings()
    row.thread_cache_identity_mode = stored

    resolved = _resolve_inheritable_settings(row)["thread_cache_identity_mode"]

    assert resolved.value == expected_value
    assert resolved.source == expected_source


# --------------------------------------------------------------------------
# 6. end-to-end over the real HTTP egress path
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Wire:
    headers: list[tuple[str, str]]
    body: bytes


@pytest.fixture
async def loopback(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[list[_Wire]]:
    received: list[_Wire] = []

    async def respond(request: web.Request) -> web.StreamResponse:
        received.append(_Wire(list(request.headers.items()), await request.read()))
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(
            b'data: {"type":"response.completed","response":{"id":"resp_loopback","status":"completed"}}\n\n'
        )
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_post("/backend-api/codex/responses", respond)
    server = TestServer(app, host="127.0.0.1")
    async with asyncio.timeout(10):
        await server.start_server()
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10), trust_env=False) as session:

            def local_lease(
                existing: aiohttp.ClientSession | None = None,
            ) -> AbstractAsyncContextManager[aiohttp.ClientSession]:
                return lease_http_session(existing or session)

            monkeypatch.setattr(proxy_module, "lease_http_session", local_lease)
            monkeypatch.setattr(proxy_module, "discover_native_egress_client", lambda: None)
            monkeypatch.setattr(get_settings(), "upstream_base_url", str(server.make_url("/backend-api")))
            yield received
    finally:
        async with asyncio.timeout(10):
            await server.close()


def _responses_request() -> ResponsesRequest:
    return ResponsesRequest.model_validate(_lite_payload_dict())


async def _drive(payload: ResponsesRequest, identity: ThreadCacheIdentity | None, headers: Mapping[str, str]) -> None:
    async with asyncio.timeout(10):
        async for _event in stream_responses(
            payload,
            headers,
            "upstream-token",
            "chatgpt-acct",
            upstream_stream_transport_override="http",
            thread_cache_identity=identity,
        ):
            pass


@pytest.mark.asyncio
async def test_shared_mode_http_request_is_byte_identical_to_no_identity(loopback: list[_Wire]) -> None:
    """Golden snapshot: ``shared`` must reproduce the pre-change wire exactly.

    The ``None`` arm *is* the pre-change code path — before this change the
    parameter did not exist and every helper call site was absent — so equality
    here is equality with the previous release's bytes.
    """
    await _drive(_responses_request(), None, _inbound_headers())
    await _drive(_responses_request(), _shared(), _inbound_headers())
    await _drive(_responses_request(), ThreadCacheIdentity(mode="isolated", account_id=None), _inbound_headers())

    assert len(loopback) == 3
    baseline, shared, isolated_without_account = loopback
    assert shared.body == baseline.body
    assert shared.headers == baseline.headers
    # An isolated mode with no resolved account has nothing to scope by and
    # must also fall back to the shared wire rather than hashing "".
    assert isolated_without_account.body == baseline.body
    assert isolated_without_account.headers == baseline.headers


@pytest.mark.asyncio
async def test_isolated_mode_scopes_all_three_legs_over_http(loopback: list[_Wire]) -> None:
    await _drive(_responses_request(), None, _inbound_headers())
    await _drive(_responses_request(), _isolated(), _inbound_headers())

    baseline, isolated = loopback
    token = _isolated().token
    baseline_body = json.loads(baseline.body)
    isolated_body = json.loads(isolated.body)

    assert isolated_body["prompt_cache_key"] == f"{baseline_body['prompt_cache_key']}-{token}"
    assert isolated_body["input"][0]["content"][0]["text"] == f"codex-lb-cache-scope: {token}"
    assert isolated_body["instructions"] == ""

    baseline_headers = {k.lower(): v for k, v in baseline.headers}
    isolated_headers = {k.lower(): v for k, v in isolated.headers}
    assert isolated_headers["session_id"] != baseline_headers["session_id"]
    assert isolated_headers["thread-id"] != baseline_headers["thread-id"]
    # Hard rule: upstream-issued values round-trip verbatim in either mode.
    assert isolated_headers["x-codex-turn-state"] == baseline_headers["x-codex-turn-state"] == _TURN_STATE


@pytest.mark.asyncio
async def test_isolated_mode_leaves_previous_response_id_untouched(loopback: list[_Wire]) -> None:
    payload_dict = _lite_payload_dict() | {"previous_response_id": "resp_upstream_123"}
    payload = ResponsesRequest.model_validate(payload_dict)

    await _drive(payload, _isolated(), _inbound_headers())

    assert json.loads(loopback[0].body)["previous_response_id"] == "resp_upstream_123"


@pytest.mark.asyncio
async def test_isolated_mode_does_not_mutate_the_request_model_or_inbound_headers(loopback: list[_Wire]) -> None:
    """Egress-only: the affinity key the router uses must stay the client's."""
    payload = _responses_request()
    headers = _inbound_headers()
    headers_before = dict(headers)

    await _drive(payload, _isolated(), headers)

    assert payload.prompt_cache_key == "cache-key-1"
    assert isinstance(payload.input, list)
    assert _mapping(payload.input[0])["type"] == "additional_tools"
    assert headers == headers_before


@pytest.mark.asyncio
async def test_isolated_mode_diverges_across_accounts_for_one_session(loopback: list[_Wire]) -> None:
    await _drive(_responses_request(), _isolated(_ACCOUNT_A), _inbound_headers())
    await _drive(_responses_request(), _isolated(_ACCOUNT_B), _inbound_headers())

    first, second = (json.loads(wire.body) for wire in loopback)
    assert first["prompt_cache_key"] != second["prompt_cache_key"]
    assert first["input"][0]["content"][0]["text"] != second["input"][0]["content"][0]["text"]
    first_headers = {k.lower(): v for k, v in loopback[0].headers}
    second_headers = {k.lower(): v for k, v in loopback[1].headers}
    assert first_headers["session_id"] != second_headers["session_id"]


@pytest.mark.asyncio
async def test_isolated_mode_is_stable_across_turns_of_one_session(loopback: list[_Wire]) -> None:
    """Turn 2 carries no session header and a different turn state; the prefix must not move."""
    turn_one_headers = _inbound_headers()
    turn_two_headers = {
        "user-agent": "codex_cli_rs/0.142.0",
        "x-codex-turn-state": "turn_ffffffff-1111-4222-8333-444444444444",
        "Authorization": "Bearer downstream",
    }

    await _drive(_responses_request(), _isolated(), turn_one_headers)
    await _drive(_responses_request(), _isolated(), turn_two_headers)

    first, second = (json.loads(wire.body) for wire in loopback)
    assert first["input"][0] == second["input"][0]
    assert first["prompt_cache_key"] == second["prompt_cache_key"]


@pytest.mark.asyncio
async def test_non_streaming_http_request_is_covered_by_the_same_chokepoint(loopback: list[_Wire]) -> None:
    payload = ResponsesRequest.model_validate(_lite_payload_dict() | {"stream": False})

    await _drive(payload, _isolated(), _inbound_headers())

    assert json.loads(loopback[0].body)["input"][0]["content"][0]["text"] == _isolated().scope_line


def test_isolated_coverage_boundary_is_the_core_upstream_client() -> None:
    """Pin which egress paths thread the identity, so the gap cannot drift silently.

    ``stream_responses``/``compact_responses`` in the core upstream client are
    the covered chokepoints. The HTTP session bridge and the direct downstream
    WebSocket surface are NOT covered yet: both serialize the request text
    before an account is chosen and treat that text as a dispatch-owner and
    replay key. If a later change wires either one, this test should be updated
    deliberately rather than discovered by an operator.
    """
    import inspect

    from app.core.clients import proxy as core

    for entry in (core.stream_responses, core.compact_responses):
        assert "thread_cache_identity" in inspect.signature(entry).parameters, entry.__name__

    from app.modules.proxy._service.http_bridge import streaming as bridge

    bridge_source = inspect.getsource(bridge)
    assert "thread_cache_identity" not in bridge_source, (
        "The HTTP session bridge now references the thread cache identity. "
        "If that is intentional, extend the spec's coverage statement and this test."
    )


# --------------------------------------------------------------------------
# 7. websocket response.create over the real egress path
# --------------------------------------------------------------------------


class _FakeUpstreamWebSocket:
    def __init__(self, sent: list[Any]) -> None:
        self._sent = sent

    async def send_json(self, payload: Any) -> None:
        self._sent.append(payload)

    def __aiter__(self):
        async def _iter():
            yield aiohttp.WSMessage(
                aiohttp.WSMsgType.TEXT,
                '{"type":"response.completed","response":{"id":"resp_ws","status":"completed"}}',
                None,
            )

        return _iter()

    async def close(self) -> None:
        return None


@pytest.fixture
def websocket_capture(monkeypatch: pytest.MonkeyPatch) -> tuple[list[Any], list[Mapping[str, str]]]:
    frames: list[Any] = []
    headers_seen: list[Mapping[str, str]] = []

    async def fake_open(*, headers: Mapping[str, str], **_kwargs: Any):
        headers_seen.append(dict(headers))
        websocket = _FakeUpstreamWebSocket(frames)

        @contextlib.asynccontextmanager
        async def _cm():
            yield websocket

        manager = _cm()
        await manager.__aenter__()
        return manager, cast(Any, websocket)

    monkeypatch.setattr(proxy_module, "_open_upstream_websocket", fake_open)
    monkeypatch.setattr(proxy_module, "discover_native_egress_client", lambda: None)
    return frames, headers_seen


async def _drive_websocket(identity: ThreadCacheIdentity | None) -> None:
    async with aiohttp.ClientSession() as session, asyncio.timeout(10):
        with contextlib.suppress(Exception):
            async for _event in stream_responses(
                _responses_request(),
                _inbound_headers(),
                "upstream-token",
                "chatgpt-acct",
                session=session,
                upstream_stream_transport_override="websocket",
                thread_cache_identity=identity,
            ):
                pass


@pytest.mark.asyncio
async def test_shared_mode_websocket_frame_and_headers_are_identical(
    websocket_capture: tuple[list[Any], list[Mapping[str, str]]],
) -> None:
    frames, headers_seen = websocket_capture

    await _drive_websocket(None)
    await _drive_websocket(_shared())

    assert len(frames) == 2
    assert frames[1] == frames[0]
    assert list(headers_seen[1].items()) == list(headers_seen[0].items())


@pytest.mark.asyncio
async def test_isolated_mode_websocket_frame_carries_the_scope_prefix(
    websocket_capture: tuple[list[Any], list[Mapping[str, str]]],
) -> None:
    frames, headers_seen = websocket_capture

    await _drive_websocket(None)
    await _drive_websocket(_isolated())

    baseline, isolated = frames
    assert isolated["type"] == "response.create"
    assert isolated["input"][0]["content"][0]["text"] == _isolated().scope_line
    assert isolated["prompt_cache_key"] == f"{baseline['prompt_cache_key']}-{_isolated().token}"
    lowered_baseline = {k.lower(): v for k, v in headers_seen[0].items()}
    lowered_isolated = {k.lower(): v for k, v in headers_seen[1].items()}
    assert lowered_isolated["session_id"] != lowered_baseline["session_id"]
    assert lowered_isolated["x-codex-turn-state"] == _TURN_STATE


# --------------------------------------------------------------------------
# 8. compact
# --------------------------------------------------------------------------


def _compact_request() -> ResponsesCompactRequest:
    return ResponsesCompactRequest.model_validate(
        {
            "model": "gpt-5.2",
            "instructions": "",
            "input": [
                {"type": "additional_tools", "role": "developer", "tools": [{"type": "shell"}]},
                {"role": "user", "content": [{"type": "input_text", "text": "compact me"}]},
            ],
            "prompt_cache_key": "compact-key-1",
        }
    )


@pytest.fixture
def compact_budget_spy(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    def spy(payload_dict: dict[str, Any]) -> None:
        seen.append(copy.deepcopy(payload_dict))

    monkeypatch.setattr(proxy_module, "validate_compact_input_wire_budget", spy)
    return seen


async def _drive_compact(identity: ThreadCacheIdentity | None) -> None:
    session = AsyncMock()
    # The payload and headers are fully built before the POST; failing there
    # keeps the test on the real build path without a live upstream.
    session.post.side_effect = RuntimeError("stop after the payload is built")
    with contextlib.suppress(Exception):
        await compact_responses(
            _compact_request(),
            _inbound_headers(),
            "upstream-token",
            "chatgpt-acct",
            session=cast(Any, session),
            thread_cache_identity=identity,
        )


@pytest.mark.asyncio
async def test_compact_shared_mode_payload_is_identical(compact_budget_spy: list[dict[str, Any]]) -> None:
    await _drive_compact(None)
    await _drive_compact(_shared())

    assert len(compact_budget_spy) == 2
    assert compact_budget_spy[1] == compact_budget_spy[0]


@pytest.mark.asyncio
async def test_compact_wire_budget_sees_the_injected_prefix(compact_budget_spy: list[dict[str, Any]]) -> None:
    """The budget check must run against the size actually sent."""
    await _drive_compact(_isolated())

    validated = compact_budget_spy[0]
    assert validated["input"][0]["content"][0]["text"] == _isolated().scope_line
    assert validated["prompt_cache_key"] == f"compact-key-1-{_isolated().token}"


# --------------------------------------------------------------------------
# The wiring itself, not just the helper
#
# Every one of these asserts on a call site rather than on
# `thread_cache_identity.py`. Deleting the wiring in the service or bridge
# layer used to leave the whole suite green, so the feature could be turned
# into a no-op for real traffic without a single test noticing.
# --------------------------------------------------------------------------


def test_the_websocket_rejection_http_fallback_rescopes_its_headers() -> None:
    """The fallback rebuilds headers from the raw inbound set.

    Without a second scoping call the retry goes out with a scoped body and
    unscoped headers, i.e. one thread presenting two identities on one account.
    """

    source = (Path(proxy_module.__file__)).read_text()
    fallback = source[source.index("async def _stream_via_http_after_websocket_rejection") :]
    fallback = fallback[: fallback.index("\n    async def ", 1)] if "\n    async def " in fallback[1:] else fallback
    assert "scope_session_headers(" in fallback


def test_the_http_session_bridge_scopes_the_frame_it_sends() -> None:
    """The bridge dispatches response.create itself.

    If it does not scope, a thread served partly by the bridge and partly by
    the per-turn bypass alternates identities turn by turn.
    """

    source = (Path(request_submit_module.__file__)).read_text()
    assert "_text_with_thread_cache_identity(" in source
    send = source[source.index("async def _send_http_bridge_request_text_with_archive_id") :]
    assert "_text_with_thread_cache_identity(" in send[: send.index("# Operation metadata")]


def test_the_bridge_never_writes_the_scoped_text_back_into_request_state() -> None:
    """The durable operation fingerprint must stay account-neutral.

    A scoped value in the fingerprint would change the operation identity on
    every account swap and make the spool lookup miss mid-recovery.
    """

    source = (Path(request_submit_module.__file__)).read_text()
    assert "request_state.request_text = _text_with_thread_cache_identity" not in source
    assert "fresh_upstream_request_text = _text_with_thread_cache_identity" not in source
