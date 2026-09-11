"""Dispatch-level wiring for account-scoped outbound thread identity.

The load-bearing assertion in this file is
``test_flag_off_outbound_request_is_byte_for_byte_unchanged``: the feature is
default-off, so an operator who never touches it must get exactly the bytes
they get today.
"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from app.core.clients import proxy as proxy_module
from app.core.clients import proxy_websocket as proxy_websocket_module
from app.core.clients.account_scoped_identity import (
    SCOPED_SESSION_HEADER_NAMES,
    scope_thread_value,
)
from app.core.clients.proxy import (
    _build_upstream_headers,
    _build_upstream_websocket_headers,
)
from app.core.clients.proxy_websocket import (
    _build_upstream_websocket_headers as _build_upstream_ws_handshake_headers,
)
from app.core.openai.requests import ResponsesRequest

_INSTALL_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_INSTALL_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

_BUILDERS = [
    pytest.param(_build_upstream_headers, id="http"),
    pytest.param(_build_upstream_websocket_headers, id="per-request-websocket"),
    pytest.param(_build_upstream_ws_handshake_headers, id="persistent-websocket-handshake"),
]

_INBOUND: dict[str, str] = {
    "User-Agent": "codex_cli_rs/0.150.1 (Mac OS 26.5.0; arm64) iTerm.app/3.6.10",
    "originator": "codex_cli_rs",
    "version": "0.150.1",
    "session_id": "0189b4d0-9e1d-7f2a-8c3b-1f2e3d4c5b6a",
    "thread-id": "thread-7",
    "x-codex-conversation-id": "conv-7",
    "x-session-affinity": "aff-7",
    "x-codex-turn-state": "opaque-upstream-turn-state",
}


@pytest.mark.parametrize("builder", _BUILDERS)
def test_flag_off_header_build_is_byte_for_byte_unchanged(builder):
    baseline = builder(dict(_INBOUND), "tok", "acct-1")
    with_kwarg = builder(dict(_INBOUND), "tok", "acct-1", scoped_identity_installation_id=None)
    assert list(with_kwarg.items()) == list(baseline.items())


@pytest.mark.parametrize("builder", _BUILDERS)
def test_scoping_rewrites_the_vocabulary_and_nothing_else(builder):
    baseline = builder(dict(_INBOUND), "tok", "acct-1")
    scoped = builder(dict(_INBOUND), "tok", "acct-1", scoped_identity_installation_id=_INSTALL_A)

    # Same header names, same casing, same order; only values move.
    assert list(scoped) == list(baseline)
    for name in baseline:
        if name.lower() in SCOPED_SESSION_HEADER_NAMES:
            assert scoped[name] != baseline[name], name
            assert scoped[name] == scope_thread_value(baseline[name], _INSTALL_A)
        else:
            assert scoped[name] == baseline[name], name


@pytest.mark.parametrize("builder", _BUILDERS)
def test_turn_state_is_never_rewritten(builder):
    scoped = builder(dict(_INBOUND), "tok", "acct-1", scoped_identity_installation_id=_INSTALL_A)
    assert scoped["x-codex-turn-state"] == "opaque-upstream-turn-state"


@pytest.mark.parametrize("builder", _BUILDERS)
def test_two_accounts_get_different_session_headers(builder):
    a = builder(dict(_INBOUND), "tok", "acct-1", scoped_identity_installation_id=_INSTALL_A)
    b = builder(dict(_INBOUND), "tok", "acct-1", scoped_identity_installation_id=_INSTALL_B)
    for name in ("session_id", "thread-id", "x-codex-conversation-id", "x-session-affinity"):
        assert a[name] != b[name], name


@pytest.mark.parametrize("builder", _BUILDERS)
def test_the_same_account_is_stable_across_turns(builder):
    first = builder(dict(_INBOUND), "tok", "acct-1", scoped_identity_installation_id=_INSTALL_A)
    second = builder(dict(_INBOUND), "tok", "acct-1", scoped_identity_installation_id=_INSTALL_A)
    assert first == second


class _Settings:
    upstream_base_url = "https://chatgpt.com/backend-api"
    upstream_connect_timeout_seconds = 8.0
    stream_idle_timeout_seconds = 45.0
    trace_channels = frozenset()
    proxy_request_budget_seconds = 15.0

    def __init__(self, *, account_scoped_thread_identity_enabled: bool) -> None:
        self.account_scoped_thread_identity_enabled = account_scoped_thread_identity_enabled


class _PostResponse:
    def __init__(self) -> None:
        self.status = 200
        self.headers = {"content-type": "text/event-stream"}
        self.content = _Content()

    async def __aenter__(self) -> "_PostResponse":
        return self

    async def __aexit__(self, *_args: object) -> bool:
        return False


class _Content:
    def __init__(self) -> None:
        self._chunks = [b'data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n']

    def __aiter__(self) -> "_Content":
        return self

    async def __anext__(self) -> bytes:
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


class _CapturingSession:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> _PostResponse:
        self.calls.append({"url": url, **kwargs})
        return _PostResponse()


async def _dispatch(monkeypatch, *, enabled: bool) -> dict[str, Any]:
    monkeypatch.setattr(
        proxy_module,
        "get_settings",
        lambda: _Settings(account_scoped_thread_identity_enabled=enabled),
    )
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)
    monkeypatch.setattr(
        proxy_module.get_codex_version_cache(),
        "cached_version_or_default",
        lambda: "0.150.1",
    )
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [{"role": "user", "content": "hi"}],
            "prompt_cache_key": "0189b4d0-9e1d-7f2a-8c3b-1f2e3d4c5b6a",
        }
    )
    session = _CapturingSession()
    async for _event in proxy_module.stream_responses(
        payload,
        headers=dict(_INBOUND),
        access_token="token",
        account_id="acct-1",
        session=cast(Any, session),
        codex_installation_id=_INSTALL_A,
        upstream_stream_transport_override="http",
    ):
        pass
    # The request model must stay account-neutral: the sticky key is derived
    # from it before selection, so a scoped value written back here would
    # shatter routing into per-account lanes.
    assert payload.prompt_cache_key == "0189b4d0-9e1d-7f2a-8c3b-1f2e3d4c5b6a"
    assert len(session.calls) == 1
    return session.calls[0]


@pytest.mark.asyncio
async def test_flag_off_outbound_request_is_byte_for_byte_unchanged(monkeypatch):
    """The default-off flag must not perturb a single outbound byte.

    Compared against a run in which every scoping entry point raises, so the
    equality below cannot be satisfied by an accidental round trip through the
    mapping that happens to be the identity for this fixture.
    """

    def _explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("scoping ran while the flag was off")

    reference = await _dispatch(monkeypatch, enabled=False)

    monkeypatch.setattr(proxy_module, "scope_session_headers", _explode)
    monkeypatch.setattr(proxy_module, "scope_payload_thread_identity", _explode)
    monkeypatch.setattr(proxy_websocket_module, "scope_session_headers", _explode)
    tripwired = await _dispatch(monkeypatch, enabled=False)

    assert list(cast(dict[str, str], tripwired["headers"]).items()) == list(
        cast(dict[str, str], reference["headers"]).items()
    )
    assert json.dumps(tripwired["json"], sort_keys=True) == json.dumps(reference["json"], sort_keys=True)
    assert cast(dict[str, str], reference["headers"])["session_id"] == _INBOUND["session_id"]
    assert cast(dict[str, Any], reference["json"])["prompt_cache_key"] == _INBOUND["session_id"]


@pytest.mark.asyncio
async def test_flag_on_scopes_the_body_and_headers_together(monkeypatch):
    off = await _dispatch(monkeypatch, enabled=False)
    on = await _dispatch(monkeypatch, enabled=True)

    off_headers = cast(dict[str, str], off["headers"])
    on_headers = cast(dict[str, str], on["headers"])
    assert list(on_headers) == list(off_headers)
    for name in off_headers:
        if name.lower() in SCOPED_SESSION_HEADER_NAMES:
            assert on_headers[name] == scope_thread_value(off_headers[name], _INSTALL_A)
        else:
            assert on_headers[name] == off_headers[name], name

    off_body = cast(dict[str, Any], off["json"])
    on_body = cast(dict[str, Any], on["json"])
    assert set(on_body) == set(off_body)
    for key in off_body:
        if key == "prompt_cache_key":
            assert on_body[key] == scope_thread_value(off_body[key], _INSTALL_A)
        else:
            assert on_body[key] == off_body[key], key
    # Header and body agree: one thread, one scoped identity per account.
    assert on_body["prompt_cache_key"] == on_headers["session_id"]


def _sticky_key(payload: ResponsesRequest, headers: dict[str, str]) -> object:
    from app.modules.proxy.affinity import _sticky_key_for_responses_request

    return _sticky_key_for_responses_request(
        payload,
        headers,
        codex_session_affinity=True,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=1800,
        sticky_threads_enabled=True,
    ).key


@pytest.mark.asyncio
async def test_selection_key_is_identical_with_the_flag_on_and_off(monkeypatch):
    """Routing must stay account-independent.

    ``_resolve_prompt_cache_key`` mutates the request model before account
    selection, so if the scoped value were ever written back there the sticky
    key would become account-dependent and selection would shatter into
    per-account lanes.
    """

    def _fresh() -> ResponsesRequest:
        return ResponsesRequest.model_validate(
            {
                "model": "gpt-5.1",
                "instructions": "hi",
                "input": [{"role": "user", "content": "hi"}],
            }
        )

    baseline_payload = _fresh()
    baseline = _sticky_key(baseline_payload, dict(_INBOUND))

    dispatched = _fresh()
    monkeypatch.setattr(
        proxy_module,
        "get_settings",
        lambda: _Settings(account_scoped_thread_identity_enabled=True),
    )
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)
    monkeypatch.setattr(
        proxy_module.get_codex_version_cache(),
        "cached_version_or_default",
        lambda: "0.150.1",
    )
    # The real order: resolve the sticky key (which stamps the derived
    # account-neutral prompt_cache_key onto the model), select, then dispatch.
    resolved_before = _sticky_key(dispatched, dict(_INBOUND))
    session = _CapturingSession()
    async for _event in proxy_module.stream_responses(
        dispatched,
        headers=dict(_INBOUND),
        access_token="token",
        account_id="acct-1",
        session=cast(Any, session),
        codex_installation_id=_INSTALL_A,
        upstream_stream_transport_override="http",
    ):
        pass

    assert resolved_before == baseline
    # Re-resolving after dispatch — as the next turn would — is unchanged.
    assert _sticky_key(dispatched, dict(_INBOUND)) == baseline
