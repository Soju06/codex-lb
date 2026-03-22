from __future__ import annotations

import asyncio
import base64
import json
import time
from collections import deque
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import cast

import anyio
import pytest
import pytest_asyncio
from sqlalchemy import select

import app.modules.proxy.service as proxy_module
from app.core.utils.request_id import reset_request_id, set_request_id
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy.load_balancer import AccountSelection

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_http_bridge_sessions(app_instance):
    yield
    service = get_proxy_service_for_app(app_instance)
    async with service._http_bridge_lock:
        sessions = list(service._http_bridge_sessions.values())
        inflight_sessions = list(service._http_bridge_inflight_sessions.values())
        service._http_bridge_sessions.clear()
        service._http_bridge_inflight_sessions.clear()
        service._http_bridge_turn_state_index.clear()
    for session in sessions:
        await service._close_http_bridge_session(session)
    for inflight_future in inflight_sessions:
        if not inflight_future.done():
            inflight_future.cancel()


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


def _make_auth_json(account_id: str, email: str) -> dict:
    payload = {
        "email": email,
        "chatgpt_account_id": account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    return {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "accountId": account_id,
        },
    }


async def _collect_sse_events(
    async_client,
    path: str,
    *,
    json_body: dict,
    headers: dict[str, str] | None = None,
) -> list[dict]:
    async with async_client.stream("POST", path, json=json_body, headers=headers) as response:
        assert response.status_code == 200
        lines = [line async for line in response.aiter_lines() if line.startswith("data: ")]
    return [json.loads(line[6:]) for line in lines]


async def _collect_sse_events_with_headers(
    async_client,
    path: str,
    *,
    json_body: dict,
    headers: dict[str, str] | None = None,
) -> tuple[list[dict], dict[str, str]]:
    async with async_client.stream("POST", path, json=json_body, headers=headers) as response:
        assert response.status_code == 200
        response_headers = dict(response.headers)
        lines = [line async for line in response.aiter_lines() if line.startswith("data: ")]
    return [json.loads(line[6:]) for line in lines], response_headers


async def _import_account(async_client, account_id: str, email: str) -> str:
    auth_json = _make_auth_json(account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200
    return response.json()["accountId"]


async def _get_account(account_id: str) -> Account:
    async with SessionLocal() as session:
        result = await session.execute(select(Account).where(Account.id == account_id))
        account = result.scalar_one()
        session.expunge(account)
        return account


class _SettingsCache:
    def __init__(self, settings: object) -> None:
        self._settings = settings

    async def get(self) -> object:
        return self._settings


def _install_bridge_settings(monkeypatch: pytest.MonkeyPatch, *, enabled: bool) -> None:
    _install_bridge_settings_with_limits(monkeypatch, enabled=enabled)


def _install_bridge_settings_with_limits(
    monkeypatch: pytest.MonkeyPatch,
    *,
    enabled: bool,
    max_sessions: int = 128,
    queue_limit: int = 8,
    codex_idle_ttl_seconds: float = 900.0,
    codex_prewarm_enabled: bool = False,
    prefer_earlier_reset_accounts: bool = False,
    instance_id: str = "instance-a",
    instance_ring: list[str] | None = None,
) -> None:
    settings = SimpleNamespace(
        prefer_earlier_reset_accounts=prefer_earlier_reset_accounts,
        sticky_threads_enabled=False,
        openai_cache_affinity_max_age_seconds=300,
        openai_prompt_cache_key_derivation_enabled=True,
        routing_strategy="usage_weighted",
        proxy_request_budget_seconds=75.0,
        compact_request_budget_seconds=75.0,
        transcription_request_budget_seconds=120.0,
        upstream_compact_timeout_seconds=None,
        upstream_stream_transport="auto",
        log_proxy_request_payload=False,
        log_proxy_request_shape=False,
        log_proxy_request_shape_raw_cache_key=False,
        log_proxy_service_tier_trace=False,
        stream_idle_timeout_seconds=300.0,
        http_responses_session_bridge_enabled=enabled,
        http_responses_session_bridge_idle_ttl_seconds=120.0,
        http_responses_session_bridge_codex_idle_ttl_seconds=codex_idle_ttl_seconds,
        http_responses_session_bridge_codex_prewarm_enabled=codex_prewarm_enabled,
        http_responses_session_bridge_max_sessions=max_sessions,
        http_responses_session_bridge_queue_limit=queue_limit,
        http_responses_session_bridge_instance_id=instance_id,
        http_responses_session_bridge_instance_ring=list(instance_ring or []),
    )
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_module, "get_settings", lambda: settings)


class _FakeUpstreamMessage:
    def __init__(
        self,
        kind: str,
        *,
        text: str | None = None,
        close_code: int | None = None,
        error: str | None = None,
    ) -> None:
        self.kind = kind
        self.text = text
        self.close_code = close_code
        self.error = error
        self.data = None


class _FakeBridgeUpstreamWebSocket:
    def __init__(self) -> None:
        self.sent_text: list[str] = []
        self.closed = False
        self._messages: asyncio.Queue[_FakeUpstreamMessage] = asyncio.Queue()

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)
        response_id = f"resp_bridge_{len(self.sent_text)}"
        await self._messages.put(
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.created",
                        "response": {"id": response_id, "object": "response", "status": "in_progress"},
                    },
                    separators=(",", ":"),
                ),
            )
        )
        await self._messages.put(
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": response_id,
                            "object": "response",
                            "status": "completed",
                            "output": [
                                {
                                    "type": "message",
                                    "role": "assistant",
                                    "content": [{"type": "output_text", "text": "OK"}],
                                }
                            ],
                            "usage": {
                                "input_tokens": 24,
                                "output_tokens": 2,
                                "total_tokens": 26,
                                "input_tokens_details": {"cached_tokens": 20},
                                "output_tokens_details": {"reasoning_tokens": 0},
                            },
                        },
                    },
                    separators=(",", ":"),
                ),
            )
        )

    async def send_bytes(self, data: bytes) -> None:
        raise AssertionError(f"Unexpected binary frame: {data!r}")

    async def receive(self) -> _FakeUpstreamMessage:
        return await self._messages.get()

    async def close(self) -> None:
        self.closed = True

    def response_header(self, name: str) -> str | None:
        del name
        return None


class _ClosingBridgeUpstreamWebSocket(_FakeBridgeUpstreamWebSocket):
    async def send_text(self, text: str) -> None:
        await super().send_text(text)
        await self._messages.put(_FakeUpstreamMessage("close", close_code=1000))


class _PrecreatedCloseUpstreamWebSocket(_FakeBridgeUpstreamWebSocket):
    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)
        await self._messages.put(_FakeUpstreamMessage("close", close_code=1011))


class _CreatedOnlyUpstreamWebSocket(_FakeBridgeUpstreamWebSocket):
    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)
        response_id = f"resp_created_only_{len(self.sent_text)}"
        await self._messages.put(
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.created",
                        "response": {"id": response_id, "object": "response", "status": "in_progress"},
                    },
                    separators=(",", ":"),
                ),
            )
        )


class _SilentUpstreamWebSocket(_FakeBridgeUpstreamWebSocket):
    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)


class _CreatedThenCloseUpstreamWebSocket(_FakeBridgeUpstreamWebSocket):
    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)
        response_id = f"resp_created_then_close_{len(self.sent_text)}"
        await self._messages.put(
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.created",
                        "response": {"id": response_id, "object": "response", "status": "in_progress"},
                    },
                    separators=(",", ":"),
                ),
            )
        )
        await self._messages.put(_FakeUpstreamMessage("close", close_code=1011))


class _FailingSendThenCloseUpstreamWebSocket(_FakeBridgeUpstreamWebSocket):
    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)
        await self._messages.put(_FakeUpstreamMessage("close", close_code=1011))
        raise RuntimeError("socket closed during send")


def _make_dummy_bridge_session(session_key: proxy_module._HTTPBridgeSessionKey) -> SimpleNamespace:
    async def _close() -> None:
        return None

    return SimpleNamespace(
        key=session_key,
        closed=False,
        account=SimpleNamespace(id=f"acct_{session_key.affinity_key}", status=AccountStatus.ACTIVE),
        request_model="gpt-5.4",
        pending_lock=anyio.Lock(),
        pending_requests=deque(),
        queued_request_count=0,
        last_used_at=time.monotonic(),
        idle_ttl_seconds=120.0,
        codex_session=False,
        downstream_turn_state_aliases=set(),
        upstream_reader=None,
        upstream=SimpleNamespace(close=_close),
    )


class _PrewarmingBridgeUpstreamWebSocket(_FakeBridgeUpstreamWebSocket):
    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)
        payload = json.loads(text)
        response_id = f"resp_prewarm_{len(self.sent_text)}"
        output = []
        usage = {
            "input_tokens": 12,
            "output_tokens": 0,
            "total_tokens": 12,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        }
        if payload.get("generate") is not False:
            response_id = f"resp_actual_{len(self.sent_text)}"
            output = [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "OK"}],
                }
            ]
            usage = {
                "input_tokens": 24,
                "output_tokens": 2,
                "total_tokens": 26,
                "input_tokens_details": {"cached_tokens": 20},
                "output_tokens_details": {"reasoning_tokens": 0},
            }
        await self._messages.put(
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.created",
                        "response": {"id": response_id, "object": "response", "status": "in_progress"},
                    },
                    separators=(",", ":"),
                ),
            )
        )
        await self._messages.put(
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": response_id,
                            "object": "response",
                            "status": "completed",
                            "output": output,
                            "usage": usage,
                        },
                    },
                    separators=(",", ":"),
                ),
            )
        )


class _TurnStateBridgeUpstreamWebSocket(_FakeBridgeUpstreamWebSocket):
    def __init__(self, turn_state: str) -> None:
        super().__init__()
        self._turn_state = turn_state

    def response_header(self, name: str) -> str | None:
        if name.lower() == "x-codex-turn-state":
            return self._turn_state
        return None


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_codex_session_uses_extended_idle_ttl(async_client, app_instance, monkeypatch):
    _install_bridge_settings_with_limits(monkeypatch, enabled=True, codex_idle_ttl_seconds=600.0)
    account_id = await _import_account(async_client, "acc_http_bridge_codex_ttl", "http-bridge-codex-ttl@example.com")
    account = await _get_account(account_id)
    service = get_proxy_service_for_app(app_instance)
    fake_upstream = _FakeBridgeUpstreamWebSocket()

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        return fake_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    payload = proxy_module.ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )
    affinity = proxy_module._sticky_key_for_responses_request(
        payload,
        {"x-codex-turn-state": "turn_state_1"},
        codex_session_affinity=True,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
        api_key=None,
    )
    key = proxy_module._make_http_bridge_session_key(
        payload,
        headers={"x-codex-turn-state": "turn_state_1"},
        affinity=affinity,
        api_key=None,
        request_id="req_1",
    )

    session = await service._get_or_create_http_bridge_session(
        key,
        headers={"x-codex-turn-state": "turn_state_1"},
        affinity=affinity,
        api_key=None,
        request_model=payload.model,
        idle_ttl_seconds=120.0,
        max_sessions=8,
    )

    session.last_used_at = time.monotonic() - 300.0
    async with service._http_bridge_lock:
        await service._prune_http_bridge_sessions_locked()
        assert key in service._http_bridge_sessions

    session.last_used_at = time.monotonic() - 601.0
    async with service._http_bridge_lock:
        await service._prune_http_bridge_sessions_locked()
        assert key not in service._http_bridge_sessions


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_creation_honors_prefer_earlier_reset(async_client, app_instance, monkeypatch):
    _install_bridge_settings_with_limits(monkeypatch, enabled=True, prefer_earlier_reset_accounts=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_prefer_earlier_reset",
        "http-bridge-prefer-earlier-reset@example.com",
    )
    account = await _get_account(account_id)
    service = get_proxy_service_for_app(app_instance)
    fake_upstream = _FakeBridgeUpstreamWebSocket()
    select_calls: list[bool] = []

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        select_calls.append(prefer_earlier_reset_accounts)
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_open_upstream_websocket_with_budget(self, target, headers, *, timeout_seconds):
        del self, target, headers, timeout_seconds
        return fake_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(
        proxy_module.ProxyService,
        "_open_upstream_websocket_with_budget",
        fake_open_upstream_websocket_with_budget,
    )

    payload = proxy_module.ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "",
            "input": "hello",
            "prompt_cache_key": "bridge_prefer_earlier_reset",
        }
    )
    affinity = proxy_module._sticky_key_for_responses_request(
        payload,
        {},
        codex_session_affinity=False,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
    )
    key = proxy_module._make_http_bridge_session_key(
        payload,
        headers={},
        affinity=affinity,
        api_key=None,
        request_id="req_bridge_prefer_earlier_reset",
    )

    session = await service._get_or_create_http_bridge_session(
        key,
        headers={},
        affinity=affinity,
        api_key=None,
        request_model=payload.model,
        idle_ttl_seconds=120.0,
        max_sessions=8,
    )

    assert select_calls == [True]
    await service._close_http_bridge_session(session)


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_codex_session_prewarms_first_request(async_client, monkeypatch):
    _install_bridge_settings_with_limits(
        monkeypatch,
        enabled=True,
        codex_idle_ttl_seconds=600.0,
        codex_prewarm_enabled=True,
    )
    account_id = await _import_account(async_client, "acc_http_bridge_prewarm", "http-bridge-prewarm@example.com")
    account = await _get_account(account_id)
    fake_upstream = _PrewarmingBridgeUpstreamWebSocket()

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        return fake_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    response = await async_client.post(
        "/v1/responses",
        headers={"x-codex-turn-state": "turn_state_prewarm"},
        json={
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        },
    )

    assert response.status_code == 200
    assert response.json()["id"] == "resp_actual_2"
    assert len(fake_upstream.sent_text) == 2
    assert json.loads(fake_upstream.sent_text[0])["generate"] is False
    assert "generate" not in json.loads(fake_upstream.sent_text[1])


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_codex_session_does_not_prewarm_by_default(async_client, monkeypatch):
    _install_bridge_settings_with_limits(monkeypatch, enabled=True, codex_idle_ttl_seconds=600.0)
    account_id = await _import_account(async_client, "acc_http_bridge_no_prewarm", "http-bridge-no-prewarm@example.com")
    account = await _get_account(account_id)
    fake_upstream = _PrewarmingBridgeUpstreamWebSocket()

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        return fake_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    response = await async_client.post(
        "/v1/responses",
        headers={"x-codex-turn-state": "turn_state_no_prewarm"},
        json={
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        },
    )

    assert response.status_code == 200
    assert response.json()["id"] == "resp_actual_1"
    assert len(fake_upstream.sent_text) == 1
    assert "generate" not in json.loads(fake_upstream.sent_text[0])


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_rejects_request_for_non_owner_instance(async_client, app_instance, monkeypatch):
    _install_bridge_settings_with_limits(
        monkeypatch,
        enabled=True,
        instance_id="instance-b",
        instance_ring=["instance-a", "instance-b"],
    )
    account_id = await _import_account(async_client, "acc_http_bridge_owner", "http-bridge-owner@example.com")
    account = await _get_account(account_id)
    service = get_proxy_service_for_app(app_instance)

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)

    candidate_suffix = 0
    while True:
        payload = proxy_module.ResponsesRequest.model_validate(
            {
                "model": "gpt-5.4",
                "instructions": "hi",
                "input": [{"role": "user", "content": "hi"}],
                "prompt_cache_key": f"owner-check-{candidate_suffix}",
            }
        )
        affinity = proxy_module._sticky_key_for_responses_request(
            payload,
            {},
            codex_session_affinity=False,
            openai_cache_affinity=True,
            openai_cache_affinity_max_age_seconds=300,
            sticky_threads_enabled=False,
            api_key=None,
        )
        key = proxy_module._make_http_bridge_session_key(
            payload,
            headers={},
            affinity=affinity,
            api_key=None,
            request_id="req_owner",
        )
        owner = proxy_module._http_bridge_owner_instance(key, proxy_module.get_settings())
        if owner != "instance-b":
            break
        candidate_suffix += 1

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await service._get_or_create_http_bridge_session(
            key,
            headers={},
            affinity=affinity,
            api_key=None,
            request_model=payload.model,
            idle_ttl_seconds=120.0,
            max_sessions=8,
        )

    exc = exc_info.value
    assert exc.status_code == 409
    assert exc.payload["error"].get("code") == "bridge_instance_mismatch"


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_missing_turn_state_alias_with_previous_response_id_fails_closed(
    app_instance,
    monkeypatch,
):
    _install_bridge_settings_with_limits(monkeypatch, enabled=True)
    service = get_proxy_service_for_app(app_instance)
    service._http_bridge_sessions.clear()
    service._http_bridge_inflight_sessions.clear()
    service._http_bridge_turn_state_index.clear()

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await service._get_or_create_http_bridge_session(
            proxy_module._HTTPBridgeSessionKey("turn_state_header", "http_turn_missing_alias", None),
            headers={"x-codex-turn-state": "http_turn_missing_alias"},
            affinity=proxy_module._AffinityPolicy(
                key="http_turn_missing_alias",
                kind=proxy_module.StickySessionKind.CODEX_SESSION,
            ),
            api_key=None,
            request_model="gpt-5.1",
            idle_ttl_seconds=120.0,
            max_sessions=128,
            previous_response_id="resp_missing_alias",
        )

    exc = exc_info.value
    assert exc.status_code == 400
    assert exc.payload["error"] == {
        "message": (
            "Previous response with id 'resp_missing_alias' not found. "
            "HTTP bridge continuity was lost. Replay x-codex-turn-state or retry with a stable prompt_cache_key."
        ),
        "type": "invalid_request_error",
        "code": "previous_response_not_found",
        "param": "previous_response_id",
    }


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_replayed_turn_state_alias_preserves_owner_and_promotes_session(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings_with_limits(
        monkeypatch,
        enabled=True,
        codex_idle_ttl_seconds=600.0,
        instance_id="instance-a",
        instance_ring=["instance-a", "instance-b"],
    )
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_alias_owner",
        "http-bridge-alias-owner@example.com",
    )
    account = await _get_account(account_id)
    service = get_proxy_service_for_app(app_instance)
    upstreams = [_FakeBridgeUpstreamWebSocket(), _FakeBridgeUpstreamWebSocket()]
    connect_headers_seen: list[dict[str, str]] = []

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del access_token, account_id_header, base_url, session
        connect_headers_seen.append(dict(headers))
        return upstreams.pop(0)

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    candidate_suffix = 0
    while True:
        payload = proxy_module.ResponsesRequest(
            model="gpt-5.1",
            instructions="Return exactly OK.",
            input="hello",
            prompt_cache_key=f"owner-alias-thread-{candidate_suffix}",
        )
        affinity = proxy_module._sticky_key_for_responses_request(
            payload,
            {},
            codex_session_affinity=False,
            openai_cache_affinity=True,
            openai_cache_affinity_max_age_seconds=300,
            sticky_threads_enabled=False,
            api_key=None,
        )
        key = proxy_module._make_http_bridge_session_key(
            payload,
            headers={},
            affinity=affinity,
            api_key=None,
            request_id="req_owner_alias",
        )
        if proxy_module._http_bridge_owner_instance(key, proxy_module.get_settings()) == "instance-a":
            break
        candidate_suffix += 1

    session = await service._get_or_create_http_bridge_session(
        key,
        headers={},
        affinity=affinity,
        api_key=None,
        request_model=payload.model,
        idle_ttl_seconds=120.0,
        max_sessions=128,
    )

    replay_turn_state = next(
        candidate
        for candidate in ("turn_owner_alias_b", "turn_owner_alias_c", "turn_owner_alias_d", "turn_owner_alias_e")
        if proxy_module._http_bridge_owner_instance(
            proxy_module._HTTPBridgeSessionKey("turn_state_header", candidate, None),
            proxy_module.get_settings(),
        )
        == "instance-b"
    )
    await service._register_http_bridge_turn_state(session, replay_turn_state)
    replay_key = proxy_module._HTTPBridgeSessionKey("turn_state_header", replay_turn_state, None)
    assert (
        service._http_bridge_turn_state_index[
            proxy_module._http_bridge_turn_state_alias_key(replay_turn_state, session.key.api_key_id)
        ]
        == key
    )

    replayed = await service._get_or_create_http_bridge_session(
        replay_key,
        headers={"x-codex-turn-state": replay_turn_state},
        affinity=proxy_module._AffinityPolicy(key=replay_turn_state, kind=proxy_module.StickySessionKind.CODEX_SESSION),
        api_key=None,
        request_model=payload.model,
        idle_ttl_seconds=120.0,
        max_sessions=128,
    )

    assert replayed is session
    assert replayed.key == key
    assert service._http_bridge_sessions[key] is session
    assert replay_key not in service._http_bridge_sessions
    assert (
        service._http_bridge_turn_state_index[
            proxy_module._http_bridge_turn_state_alias_key(replay_turn_state, session.key.api_key_id)
        ]
        == key
    )
    assert replayed.codex_session is True
    assert replayed.affinity.kind == proxy_module.StickySessionKind.CODEX_SESSION
    assert replayed.affinity.key == replay_turn_state
    assert replayed.idle_ttl_seconds == 600.0
    request_state = proxy_module._WebSocketRequestState(
        request_id="req_owner_alias_reconnect",
        model=payload.model,
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
    )
    await service._reconnect_http_bridge_session(replayed, request_state=request_state)
    assert connect_headers_seen[-1]["x-codex-turn-state"] == replay_turn_state
    await service._close_http_bridge_session(session)


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_generated_turn_state_fails_closed_without_local_alias(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings_with_limits(
        monkeypatch,
        enabled=True,
        instance_id="instance-a",
        instance_ring=["instance-a", "instance-b"],
    )
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_missing_alias",
        "http-bridge-missing-alias@example.com",
    )
    account = await _get_account(account_id)
    service = get_proxy_service_for_app(app_instance)

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await service._get_or_create_http_bridge_session(
            proxy_module._HTTPBridgeSessionKey("turn_state_header", "http_turn_missing_alias", None),
            headers={"x-codex-turn-state": "http_turn_missing_alias"},
            affinity=proxy_module._AffinityPolicy(
                key="http_turn_missing_alias",
                kind=proxy_module.StickySessionKind.CODEX_SESSION,
            ),
            api_key=None,
            request_model="gpt-5.1",
            idle_ttl_seconds=120.0,
            max_sessions=128,
        )

    exc = exc_info.value
    assert exc.status_code == 409
    assert exc.payload["error"].get("code") == "bridge_instance_mismatch"


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_turn_state_alias_respects_api_key_isolation(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings_with_limits(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_api_key_alias",
        "http-bridge-api-key-alias@example.com",
    )
    account = await _get_account(account_id)
    service = get_proxy_service_for_app(app_instance)
    fake_upstream = _FakeBridgeUpstreamWebSocket()

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        return fake_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    payload = proxy_module.ResponsesRequest(
        model="gpt-5.1",
        instructions="Return exactly OK.",
        input="hello",
        prompt_cache_key="api-key-alias-thread",
    )
    affinity = proxy_module._sticky_key_for_responses_request(
        payload,
        {},
        codex_session_affinity=False,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
        api_key=None,
    )
    api_key_a = cast(proxy_module.ApiKeyData, SimpleNamespace(id="api-key-a"))
    session = await service._get_or_create_http_bridge_session(
        proxy_module._make_http_bridge_session_key(
            payload,
            headers={},
            affinity=affinity,
            api_key=api_key_a,
            request_id="req_api_key_alias",
        ),
        headers={},
        affinity=affinity,
        api_key=api_key_a,
        request_model=payload.model,
        idle_ttl_seconds=120.0,
        max_sessions=128,
    )
    await service._register_http_bridge_turn_state(session, "http_turn_api_key_alias")

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await service._get_or_create_http_bridge_session(
            proxy_module._HTTPBridgeSessionKey("turn_state_header", "http_turn_api_key_alias", "api-key-b"),
            headers={"x-codex-turn-state": "http_turn_api_key_alias"},
            affinity=proxy_module._AffinityPolicy(
                key="http_turn_api_key_alias",
                kind=proxy_module.StickySessionKind.CODEX_SESSION,
            ),
            api_key=cast(proxy_module.ApiKeyData, SimpleNamespace(id="api-key-b")),
            request_model=payload.model,
            idle_ttl_seconds=120.0,
            max_sessions=128,
        )

    assert isinstance(exc_info.value, proxy_module.ProxyResponseError)
    exc = exc_info.value
    assert exc.status_code == 409
    assert exc.payload["error"].get("code") == "bridge_instance_mismatch"
    await service._close_http_bridge_session(session)


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_preserves_prior_turn_state_aliases(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings_with_limits(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_alias_preserve",
        "http-bridge-alias-preserve@example.com",
    )
    account = await _get_account(account_id)
    service = get_proxy_service_for_app(app_instance)
    fake_upstream = _FakeBridgeUpstreamWebSocket()

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        return fake_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    payload = proxy_module.ResponsesRequest(
        model="gpt-5.1",
        instructions="Return exactly OK.",
        input="hello",
        prompt_cache_key="alias-preserve-thread",
    )
    affinity = proxy_module._sticky_key_for_responses_request(
        payload,
        {},
        codex_session_affinity=False,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
        api_key=None,
    )
    session = await service._get_or_create_http_bridge_session(
        proxy_module._make_http_bridge_session_key(
            payload,
            headers={},
            affinity=affinity,
            api_key=None,
            request_id="req_alias_preserve",
        ),
        headers={},
        affinity=affinity,
        api_key=None,
        request_model=payload.model,
        idle_ttl_seconds=120.0,
        max_sessions=128,
    )

    await service._register_http_bridge_turn_state(session, "http_turn_alias_a")
    await service._register_http_bridge_turn_state(session, "http_turn_alias_b")

    replayed = await service._get_or_create_http_bridge_session(
        proxy_module._HTTPBridgeSessionKey("turn_state_header", "http_turn_alias_a", None),
        headers={"x-codex-turn-state": "http_turn_alias_a"},
        affinity=proxy_module._AffinityPolicy(
            key="http_turn_alias_a",
            kind=proxy_module.StickySessionKind.CODEX_SESSION,
        ),
        api_key=None,
        request_model=payload.model,
        idle_ttl_seconds=120.0,
        max_sessions=128,
    )

    assert replayed is session
    assert "http_turn_alias_a" in replayed.downstream_turn_state_aliases
    assert "http_turn_alias_b" in replayed.downstream_turn_state_aliases
    await service._close_http_bridge_session(session)


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_close_waits_for_turn_state_index_lock(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings_with_limits(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_close_lock",
        "http-bridge-close-lock@example.com",
    )
    account = await _get_account(account_id)
    service = get_proxy_service_for_app(app_instance)
    fake_upstream = _FakeBridgeUpstreamWebSocket()

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        return fake_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    payload = proxy_module.ResponsesRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": []})
    affinity = proxy_module._AffinityPolicy(key="turn-close-lock", kind=proxy_module.StickySessionKind.CODEX_SESSION)

    session = await service._get_or_create_http_bridge_session(
        proxy_module._make_http_bridge_session_key(
            payload,
            headers={},
            affinity=affinity,
            api_key=None,
            request_id="req_close_lock",
        ),
        headers={},
        affinity=affinity,
        api_key=None,
        request_model=payload.model,
        idle_ttl_seconds=120.0,
        max_sessions=128,
    )
    await service._register_http_bridge_turn_state(session, "http_turn_close_lock")

    alias_key = proxy_module._http_bridge_turn_state_alias_key("http_turn_close_lock", session.key.api_key_id)

    async with service._http_bridge_lock:
        close_task = asyncio.create_task(service._close_http_bridge_session(session))
        await asyncio.sleep(0)
        assert not close_task.done()
        assert service._http_bridge_turn_state_index[alias_key] == session.key

    await close_task

    assert alias_key not in service._http_bridge_turn_state_index


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_allows_unstable_request_key_even_on_non_owner_instance(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings_with_limits(
        monkeypatch,
        enabled=True,
        instance_id="instance-b",
        instance_ring=["instance-a", "instance-b"],
    )
    account_id = await _import_account(async_client, "acc_http_bridge_unstable", "http-bridge-unstable@example.com")
    account = await _get_account(account_id)
    service = get_proxy_service_for_app(app_instance)
    fake_upstream = _FakeBridgeUpstreamWebSocket()

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        return fake_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    payload = proxy_module.ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )
    affinity = proxy_module._sticky_key_for_responses_request(
        payload,
        {},
        codex_session_affinity=False,
        openai_cache_affinity=False,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
        api_key=None,
    )
    key = proxy_module._make_http_bridge_session_key(
        payload,
        headers={},
        affinity=affinity,
        api_key=None,
        request_id="req_owner_unstable",
    )

    session = await service._get_or_create_http_bridge_session(
        key,
        headers={},
        affinity=affinity,
        api_key=None,
        request_model=payload.model,
        idle_ttl_seconds=120.0,
        max_sessions=8,
    )

    assert session.key.affinity_kind == "request"


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_reconnect_uses_last_upstream_turn_state(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings_with_limits(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_upstream_turn",
        "http-bridge-upstream-turn@example.com",
    )
    account = await _get_account(account_id)
    service = get_proxy_service_for_app(app_instance)
    connect_headers_seen: list[dict[str, str]] = []
    upstreams = [
        _TurnStateBridgeUpstreamWebSocket("upstream_turn_state_1"),
        _TurnStateBridgeUpstreamWebSocket("upstream_turn_state_2"),
    ]

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del access_token, account_id_header, base_url, session
        connect_headers_seen.append(dict(headers))
        return upstreams.pop(0)

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    payload = proxy_module.ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )
    affinity = proxy_module._sticky_key_for_responses_request(
        payload,
        {"x-codex-turn-state": "local_turn_state"},
        codex_session_affinity=True,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
        api_key=None,
    )
    key = proxy_module._make_http_bridge_session_key(
        payload,
        headers={"x-codex-turn-state": "local_turn_state"},
        affinity=affinity,
        api_key=None,
        request_id="req_turn_state",
    )
    bridge_session = await service._get_or_create_http_bridge_session(
        key,
        headers={"x-codex-turn-state": "local_turn_state"},
        affinity=affinity,
        api_key=None,
        request_model=payload.model,
        idle_ttl_seconds=120.0,
        max_sessions=8,
    )

    request_state = proxy_module._WebSocketRequestState(
        request_id="req-turn-state-reconnect",
        model=payload.model,
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        awaiting_response_created=True,
        request_text=json.dumps({"type": "response.create", "model": "gpt-5.4", "input": []}),
    )
    await service._reconnect_http_bridge_session(bridge_session, request_state=request_state)

    assert connect_headers_seen[0]["x-codex-turn-state"] == "local_turn_state"
    assert connect_headers_seen[1]["x-codex-turn-state"] == "upstream_turn_state_1"
    assert bridge_session.upstream_turn_state == "upstream_turn_state_2"


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_session_id_reconnect_keeps_upstream_turn_state(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings_with_limits(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_session_reconnect",
        "http-bridge-session-reconnect@example.com",
    )
    account = await _get_account(account_id)
    service = get_proxy_service_for_app(app_instance)
    connect_headers_seen: list[dict[str, str]] = []
    upstreams = [
        _TurnStateBridgeUpstreamWebSocket("upstream_turn_state_1"),
        _TurnStateBridgeUpstreamWebSocket("upstream_turn_state_2"),
    ]

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del access_token, account_id_header, base_url, session
        connect_headers_seen.append(dict(headers))
        return upstreams.pop(0)

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    payload = proxy_module.ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )
    headers = {"session_id": "session_http_bridge_1"}
    affinity = proxy_module._sticky_key_for_responses_request(
        payload,
        headers,
        codex_session_affinity=True,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
        api_key=None,
    )
    key = proxy_module._make_http_bridge_session_key(
        payload,
        headers=headers,
        affinity=affinity,
        api_key=None,
        request_id="req_session_turn_state",
    )
    bridge_session = await service._get_or_create_http_bridge_session(
        key,
        headers=headers,
        affinity=affinity,
        api_key=None,
        request_model=payload.model,
        idle_ttl_seconds=120.0,
        max_sessions=8,
    )
    await service._register_http_bridge_turn_state(bridge_session, "http_turn_alias_session")

    request_state = proxy_module._WebSocketRequestState(
        request_id="req-session-turn-state-reconnect",
        model=payload.model,
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        awaiting_response_created=True,
        request_text=json.dumps({"type": "response.create", "model": "gpt-5.4", "input": []}),
    )
    await service._reconnect_http_bridge_session(bridge_session, request_state=request_state)

    assert connect_headers_seen[0]["session_id"] == "session_http_bridge_1"
    assert "x-codex-turn-state" not in connect_headers_seen[0]
    assert connect_headers_seen[1]["x-codex-turn-state"] == "upstream_turn_state_1"
    assert bridge_session.downstream_turn_state == "http_turn_alias_session"
    assert bridge_session.upstream_turn_state == "upstream_turn_state_2"


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_prefers_evicting_prompt_cache_session_before_codex_session(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings_with_limits(monkeypatch, enabled=True, max_sessions=2, codex_idle_ttl_seconds=600.0)
    account_id = await _import_account(async_client, "acc_http_bridge_evict_pref", "http-bridge-evict-pref@example.com")
    account = await _get_account(account_id)
    service = get_proxy_service_for_app(app_instance)
    upstreams = [_FakeBridgeUpstreamWebSocket(), _FakeBridgeUpstreamWebSocket(), _FakeBridgeUpstreamWebSocket()]

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        return upstreams.pop(0)

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    payload = proxy_module.ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )

    codex_affinity = proxy_module._sticky_key_for_responses_request(
        payload,
        {"x-codex-turn-state": "turn_state_1"},
        codex_session_affinity=True,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
        api_key=None,
    )
    codex_key = proxy_module._make_http_bridge_session_key(
        payload,
        headers={"x-codex-turn-state": "turn_state_1"},
        affinity=codex_affinity,
        api_key=None,
        request_id="req_codex",
    )
    codex_session = await service._get_or_create_http_bridge_session(
        codex_key,
        headers={"x-codex-turn-state": "turn_state_1"},
        affinity=codex_affinity,
        api_key=None,
        request_model=payload.model,
        idle_ttl_seconds=120.0,
        max_sessions=2,
    )
    codex_session.last_used_at = time.monotonic() - 50.0

    prompt_payload = proxy_module.ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": [{"role": "user", "content": "hi"}],
            "prompt_cache_key": "prompt_cache_1",
        }
    )
    prompt_affinity = proxy_module._sticky_key_for_responses_request(
        prompt_payload,
        {},
        codex_session_affinity=False,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
        api_key=None,
    )
    prompt_key = proxy_module._make_http_bridge_session_key(
        prompt_payload,
        headers={},
        affinity=prompt_affinity,
        api_key=None,
        request_id="req_prompt",
    )
    prompt_session = await service._get_or_create_http_bridge_session(
        prompt_key,
        headers={},
        affinity=prompt_affinity,
        api_key=None,
        request_model=prompt_payload.model,
        idle_ttl_seconds=120.0,
        max_sessions=2,
    )
    prompt_session.last_used_at = time.monotonic() - 5.0

    next_payload = proxy_module.ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "next",
            "input": [{"role": "user", "content": "next"}],
            "prompt_cache_key": "prompt_cache_2",
        }
    )
    next_affinity = proxy_module._sticky_key_for_responses_request(
        next_payload,
        {},
        codex_session_affinity=False,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
        api_key=None,
    )
    next_key = proxy_module._make_http_bridge_session_key(
        next_payload,
        headers={},
        affinity=next_affinity,
        api_key=None,
        request_id="req_prompt_2",
    )

    created = await service._get_or_create_http_bridge_session(
        next_key,
        headers={},
        affinity=next_affinity,
        api_key=None,
        request_model=next_payload.model,
        idle_ttl_seconds=120.0,
        max_sessions=2,
    )

    async with service._http_bridge_lock:
        assert codex_key in service._http_bridge_sessions
        assert prompt_key not in service._http_bridge_sessions
        assert next_key in service._http_bridge_sessions
    assert created.key == next_key


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_reuses_upstream_websocket_and_preserves_previous_response_id(
    async_client,
    monkeypatch,
):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(async_client, "acc_http_bridge_reuse", "http-bridge-reuse@example.com")
    account = await _get_account(account_id)
    fake_upstream = _FakeBridgeUpstreamWebSocket()
    connect_calls: list[tuple[str | None, str | None]] = []

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, base_url, session
        connect_calls.append((account_id, account_id_header))
        return fake_upstream

    async def fail_legacy_stream(*args, **kwargs):
        raise AssertionError("legacy core_stream_responses path must not be used when HTTP bridge is enabled")

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)
    monkeypatch.setattr(proxy_module, "core_stream_responses", fail_legacy_stream)

    payload = {
        "model": "gpt-5.1",
        "instructions": "Return exactly OK.",
        "input": "hello",
        "prompt_cache_key": "http-bridge-thread-1",
    }
    first = await async_client.post("/v1/responses", json=payload)
    assert first.status_code == 200
    first_body = first.json()

    second = await async_client.post(
        "/v1/responses",
        json={**payload, "previous_response_id": first_body["id"]},
    )
    assert second.status_code == 200
    second_body = second.json()

    assert first_body["id"] == "resp_bridge_1"
    assert second_body["id"] == "resp_bridge_2"
    assert connect_calls == [(account_id, account.chatgpt_account_id)]
    assert len(fake_upstream.sent_text) == 2
    assert json.loads(fake_upstream.sent_text[1])["previous_response_id"] == "resp_bridge_1"


@pytest.mark.asyncio
async def test_backend_responses_http_bridge_reuses_upstream_websocket_and_preserves_previous_response_id(
    async_client,
    monkeypatch,
):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_backend_http_bridge_reuse",
        "backend-http-bridge-reuse@example.com",
    )
    account = await _get_account(account_id)
    fake_upstream = _FakeBridgeUpstreamWebSocket()
    connect_calls: list[tuple[str | None, str | None]] = []

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, base_url, session
        connect_calls.append((account_id, account_id_header))
        return fake_upstream

    async def fail_legacy_stream(*args, **kwargs):
        raise AssertionError("legacy core_stream_responses path must not be used when HTTP bridge is enabled")

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)
    monkeypatch.setattr(proxy_module, "core_stream_responses", fail_legacy_stream)

    payload = {
        "model": "gpt-5.1",
        "instructions": "Return exactly OK.",
        "input": "hello",
        "prompt_cache_key": "backend-http-bridge-thread-1",
        "stream": True,
    }
    first_events = await _collect_sse_events(async_client, "/backend-api/codex/responses", json_body=payload)
    first_response = first_events[-1]["response"]

    second_events = await _collect_sse_events(
        async_client,
        "/backend-api/codex/responses",
        json_body={**payload, "previous_response_id": first_response["id"]},
    )
    second_response = second_events[-1]["response"]

    assert [event["type"] for event in first_events] == ["response.created", "response.completed"]
    assert [event["type"] for event in second_events] == ["response.created", "response.completed"]
    assert first_response["id"] == "resp_bridge_1"
    assert second_response["id"] == "resp_bridge_2"
    assert connect_calls == [(account_id, account.chatgpt_account_id)]
    assert len(fake_upstream.sent_text) == 2
    assert json.loads(fake_upstream.sent_text[1])["previous_response_id"] == "resp_bridge_1"


@pytest.mark.asyncio
async def test_backend_responses_http_bridge_prefers_codex_session_header_over_prompt_cache_key(
    async_client,
    monkeypatch,
):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_backend_http_bridge_session_header",
        "backend-http-bridge-session-header@example.com",
    )
    account = await _get_account(account_id)
    fake_upstream = _FakeBridgeUpstreamWebSocket()
    connect_calls: list[tuple[str | None, proxy_module.StickySessionKind | None]] = []

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        connect_calls.append((sticky_key, sticky_kind))
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        return fake_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    headers = {"session_id": "backend-http-session-1"}
    first_events = await _collect_sse_events(
        async_client,
        "/backend-api/codex/responses",
        json_body={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello",
            "prompt_cache_key": "backend-http-prompt-a",
            "stream": True,
        },
        headers=headers,
    )
    first_response = first_events[-1]["response"]

    second_events = await _collect_sse_events(
        async_client,
        "/backend-api/codex/responses",
        json_body={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello-again",
            "prompt_cache_key": "backend-http-prompt-b",
            "previous_response_id": first_response["id"],
            "stream": True,
        },
        headers=headers,
    )

    assert [event["type"] for event in first_events] == ["response.created", "response.completed"]
    assert [event["type"] for event in second_events] == ["response.created", "response.completed"]
    assert len(connect_calls) == 1
    assert connect_calls[0] == ("backend-http-session-1", proxy_module.StickySessionKind.CODEX_SESSION)
    assert len(fake_upstream.sent_text) == 2
    assert json.loads(fake_upstream.sent_text[1])["prompt_cache_key"] == "backend-http-prompt-b"


@pytest.mark.asyncio
async def test_backend_responses_http_emits_turn_state_header_and_reuses_when_replayed(
    async_client,
    monkeypatch,
):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_backend_http_bridge_turn_state",
        "backend-http-bridge-turn-state@example.com",
    )
    account = await _get_account(account_id)
    fake_upstream = _FakeBridgeUpstreamWebSocket()
    connect_calls: list[tuple[str | None, proxy_module.StickySessionKind | None]] = []

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        connect_calls.append((sticky_key, sticky_kind))
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        return fake_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    first_events, first_headers = await _collect_sse_events_with_headers(
        async_client,
        "/backend-api/codex/responses",
        json_body={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello",
            "prompt_cache_key": "backend-http-turn-state-a",
            "stream": True,
        },
    )
    turn_state = first_headers["x-codex-turn-state"]
    first_response = first_events[-1]["response"]

    second_events = await _collect_sse_events(
        async_client,
        "/backend-api/codex/responses",
        json_body={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello-again",
            "prompt_cache_key": "backend-http-turn-state-b",
            "previous_response_id": first_response["id"],
            "stream": True,
        },
        headers={"x-codex-turn-state": turn_state},
    )

    assert [event["type"] for event in first_events] == ["response.created", "response.completed"]
    assert [event["type"] for event in second_events] == ["response.created", "response.completed"]
    assert turn_state.startswith("http_turn_")
    assert connect_calls == [("backend-http-turn-state-a", proxy_module.StickySessionKind.PROMPT_CACHE)]


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_reuses_session_across_model_change_for_previous_response_id(
    async_client,
    monkeypatch,
):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_model_change",
        "http-bridge-model-change@example.com",
    )
    account = await _get_account(account_id)
    fake_upstream = _FakeBridgeUpstreamWebSocket()
    connect_calls: list[tuple[str | None, str | None]] = []

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, base_url, session
        connect_calls.append((account_id, account_id_header))
        return fake_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    first = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello",
            "prompt_cache_key": "http-bridge-model-thread",
        },
    )
    assert first.status_code == 200
    first_body = first.json()

    second = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.4",
            "instructions": "Return exactly OK.",
            "input": "hello again",
            "prompt_cache_key": "http-bridge-model-thread",
            "previous_response_id": first_body["id"],
        },
    )
    assert second.status_code == 200

    assert connect_calls == [(account_id, account.chatgpt_account_id)]
    assert len(fake_upstream.sent_text) == 2
    second_payload = json.loads(fake_upstream.sent_text[1])
    assert second_payload["model"] == "gpt-5.4"
    assert second_payload["previous_response_id"] == first_body["id"]


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_requires_live_session_for_previous_response_id(async_client, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_live_session_required",
        "http-bridge-live-session-required@example.com",
    )
    account = await _get_account(account_id)
    fake_upstream = _FakeBridgeUpstreamWebSocket()
    connect_count = 0

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        nonlocal connect_count
        connect_count += 1
        return fake_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    first = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello",
            "prompt_cache_key": "http-bridge-live-session-a",
        },
    )
    assert first.status_code == 200
    first_body = first.json()

    second = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello-again",
            "prompt_cache_key": "http-bridge-live-session-b",
            "previous_response_id": first_body["id"],
        },
    )

    assert second.status_code == 400
    assert second.json() == {
        "error": {
            "message": (
                f"Previous response with id '{first_body['id']}' not found. "
                "HTTP bridge continuity was lost. Replay x-codex-turn-state or retry with a stable prompt_cache_key."
            ),
            "type": "invalid_request_error",
            "code": "previous_response_not_found",
            "param": "previous_response_id",
        }
    }
    assert connect_count == 1


@pytest.mark.asyncio
async def test_v1_responses_http_emits_turn_state_header_and_reuses_when_replayed(async_client, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_v1_http_bridge_turn_state",
        "v1-http-bridge-turn-state@example.com",
    )
    account = await _get_account(account_id)
    fake_upstream = _FakeBridgeUpstreamWebSocket()
    connect_calls: list[tuple[str | None, proxy_module.StickySessionKind | None]] = []

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        connect_calls.append((sticky_key, sticky_kind))
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        return fake_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    first = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello",
            "prompt_cache_key": "v1-http-turn-state-a",
        },
    )
    assert first.status_code == 200
    turn_state = first.headers["x-codex-turn-state"]
    first_body = first.json()

    second = await async_client.post(
        "/v1/responses",
        headers={"x-codex-turn-state": turn_state},
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello-again",
            "prompt_cache_key": "v1-http-turn-state-b",
            "previous_response_id": first_body["id"],
        },
    )
    assert second.status_code == 200

    assert turn_state.startswith("http_turn_")
    assert connect_calls == [("v1-http-turn-state-a", proxy_module.StickySessionKind.PROMPT_CACHE)]


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_streaming_path_uses_persistent_upstream_websocket(async_client, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(async_client, "acc_http_bridge_sse", "http-bridge-sse@example.com")
    account = await _get_account(account_id)
    fake_upstream = _FakeBridgeUpstreamWebSocket()
    connect_count = 0

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        nonlocal connect_count
        connect_count += 1
        return fake_upstream

    async def fail_legacy_stream(*args, **kwargs):
        raise AssertionError("legacy core_stream_responses path must not be used when HTTP bridge is enabled")

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)
    monkeypatch.setattr(proxy_module, "core_stream_responses", fail_legacy_stream)

    payload = {
        "model": "gpt-5.1",
        "instructions": "Return exactly OK.",
        "input": "hello",
        "prompt_cache_key": "http-bridge-sse-thread-1",
        "stream": True,
    }
    async with async_client.stream("POST", "/v1/responses", json=payload) as response:
        assert response.status_code == 200
        lines = [line async for line in response.aiter_lines() if line.startswith("data: ")]

    events = [json.loads(line[6:]) for line in lines]
    assert [event["type"] for event in events] == ["response.created", "response.completed"]
    assert connect_count == 1


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_kill_switch_falls_back_to_legacy_path(async_client, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=False)
    await _import_account(async_client, "acc_http_bridge_fallback", "http-bridge-fallback@example.com")
    seen = {"legacy": 0}

    async def fake_legacy_stream(
        payload,
        headers,
        access_token,
        account_id,
        base_url=None,
        raise_for_status=False,
        **_kw,
    ):
        del headers, access_token, account_id, base_url, raise_for_status, _kw
        seen["legacy"] += 1
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_legacy",'
            '"object":"response","status":"completed",'
            '"usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2,"input_tokens_details":{"cached_tokens":0},'
            '"output_tokens_details":{"reasoning_tokens":0}}}}\n\n'
        )

    async def fail_connect(*args, **kwargs):
        raise AssertionError("bridge websocket path must not be used when the kill switch disables it")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_legacy_stream)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fail_connect)

    response = await async_client.post("/v1/responses", json={"model": "gpt-5.1", "input": "hi"})
    assert response.status_code == 200
    assert response.json()["id"] == "resp_legacy"
    assert "x-codex-turn-state" not in response.headers
    assert seen["legacy"] == 1


@pytest.mark.asyncio
async def test_backend_responses_http_bridge_kill_switch_falls_back_to_legacy_path(async_client, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=False)
    await _import_account(async_client, "acc_backend_http_bridge_fallback", "backend-http-bridge-fallback@example.com")
    seen = {"legacy": 0}

    async def fake_legacy_stream(
        payload,
        headers,
        access_token,
        account_id,
        base_url=None,
        raise_for_status=False,
        **_kw,
    ):
        del payload, headers, access_token, account_id, base_url, raise_for_status, _kw
        seen["legacy"] += 1
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_backend_legacy",'
            '"object":"response","status":"completed",'
            '"usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2,'
            '"input_tokens_details":{"cached_tokens":0},"output_tokens_details":{"reasoning_tokens":0}}}}\n\n'
        )

    async def fail_connect(*args, **kwargs):
        raise AssertionError("bridge websocket path must not be used when the kill switch disables it")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_legacy_stream)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fail_connect)

    events, response_headers = await _collect_sse_events_with_headers(
        async_client,
        "/backend-api/codex/responses",
        json_body={"model": "gpt-5.1", "instructions": "hi", "input": "hello", "stream": True},
    )

    assert [event["type"] for event in events] == ["response.completed"]
    assert events[0]["response"]["id"] == "resp_backend_legacy"
    assert "x-codex-turn-state" not in response_headers
    assert seen["legacy"] == 1


@pytest.mark.asyncio
async def test_backend_responses_http_bridge_startup_error_omits_turn_state_header(async_client, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=True)

    response = await async_client.post(
        "/backend-api/codex/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello",
            "stream": True,
        },
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "no_accounts"
    assert "x-codex-turn-state" not in response.headers


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_startup_error_omits_turn_state_header(async_client, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=True)

    response = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello",
        },
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "no_accounts"
    assert "x-codex-turn-state" not in response.headers


@pytest.mark.asyncio
async def test_backend_responses_http_bridge_refresh_failure_returns_proxy_error(async_client, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_backend_http_bridge_refresh_failure",
        "backend-http-bridge-refresh-failure@example.com",
    )
    account = await _get_account(account_id)

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fail_refresh(self, target, *, force=False, timeout_seconds):
        del self, target, force, timeout_seconds
        raise proxy_module.RefreshError("refresh_token_expired", "token expired", True)

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fail_refresh)

    response = await async_client.post(
        "/backend-api/codex/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello",
            "stream": True,
        },
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"
    assert "x-codex-turn-state" not in response.headers


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_refresh_failure_returns_proxy_error(async_client, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_v1_http_bridge_refresh_failure",
        "v1-http-bridge-refresh-failure@example.com",
    )
    account = await _get_account(account_id)

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fail_refresh(self, target, *, force=False, timeout_seconds):
        del self, target, force, timeout_seconds
        raise proxy_module.RefreshError("refresh_token_expired", "token expired", True)

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fail_refresh)

    response = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello",
        },
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"
    assert "x-codex-turn-state" not in response.headers


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_transient_refresh_failure_returns_upstream_error(async_client, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_v1_http_bridge_refresh_transient_failure",
        "v1-http-bridge-refresh-transient-failure@example.com",
    )
    account = await _get_account(account_id)

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fail_refresh(self, target, *, force=False, timeout_seconds):
        del self, target, force, timeout_seconds
        raise proxy_module.RefreshError("invalid_response", "temporary refresh failure", False)

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fail_refresh)

    response = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello",
        },
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_unavailable"
    assert "x-codex-turn-state" not in response.headers


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_does_not_register_turn_state_alias_before_request_admission(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_alias_after_admission",
        "http-bridge-alias-after-admission@example.com",
    )
    service = get_proxy_service_for_app(app_instance)
    account = await _get_account(account_id)
    upstream = _SilentUpstreamWebSocket()

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        return upstream

    async def fake_submit_http_bridge_request(
        self,
        session,
        *,
        request_state,
        text_data,
        queue_limit,
    ):
        del self, session, request_state, text_data, queue_limit
        raise proxy_module.ProxyResponseError(
            429,
            proxy_module.openai_error(
                "rate_limit_exceeded",
                "HTTP responses session bridge queue is full",
                error_type="rate_limit_error",
            ),
        )

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)
    monkeypatch.setattr(proxy_module.ProxyService, "_submit_http_bridge_request", fake_submit_http_bridge_request)

    payload = proxy_module.ResponsesRequest(
        model="gpt-5.1",
        instructions="Return exactly OK.",
        input="hello",
        prompt_cache_key="bridge-alias-after-admission",
    )
    stream = service.stream_http_responses(
        payload,
        {},
        openai_cache_affinity=True,
        downstream_turn_state="http_turn_unadmitted",
    )

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await stream.__anext__()

    exc = exc_info.value
    assert exc.status_code == 429
    async with service._http_bridge_lock:
        sessions = list(service._http_bridge_sessions.values())
        assert len(sessions) == 1
        bridge_session = sessions[0]
        assert bridge_session.downstream_turn_state is None
        assert bridge_session.downstream_turn_state_aliases == set()
        assert service._http_bridge_turn_state_index == {}


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_reconnects_after_clean_upstream_close(async_client, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(async_client, "acc_http_bridge_reconnect", "http-bridge-reconnect@example.com")
    account = await _get_account(account_id)
    first_upstream = _ClosingBridgeUpstreamWebSocket()
    second_upstream = _FakeBridgeUpstreamWebSocket()
    upstreams = [first_upstream, second_upstream]
    connect_count = 0

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        nonlocal connect_count
        upstream = upstreams[connect_count]
        connect_count += 1
        return upstream

    async def fail_legacy_stream(*args, **kwargs):
        raise AssertionError("legacy core_stream_responses path must not be used when HTTP bridge is enabled")

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)
    monkeypatch.setattr(proxy_module, "core_stream_responses", fail_legacy_stream)

    payload = {
        "model": "gpt-5.1",
        "instructions": "Return exactly OK.",
        "input": "hello",
        "prompt_cache_key": "http-bridge-reconnect-thread-1",
    }
    first = await async_client.post("/v1/responses", json=payload)
    second = await async_client.post("/v1/responses", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert connect_count == 2


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_does_not_open_fresh_session_for_previous_response_id(async_client, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_previous_response_reconnect",
        "http-bridge-previous-response-reconnect@example.com",
    )
    account = await _get_account(account_id)
    first_upstream = _ClosingBridgeUpstreamWebSocket()
    second_upstream = _FakeBridgeUpstreamWebSocket()
    upstreams = [first_upstream, second_upstream]
    connect_count = 0

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        nonlocal connect_count
        upstream = upstreams[connect_count]
        connect_count += 1
        return upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    first = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello",
            "prompt_cache_key": "http-bridge-previous-response-reconnect",
        },
    )
    assert first.status_code == 200
    first_body = first.json()

    second = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello-again",
            "prompt_cache_key": "http-bridge-previous-response-reconnect",
            "previous_response_id": first_body["id"],
        },
    )

    assert second.status_code == 400
    assert second.json() == {
        "error": {
            "message": (
                f"Previous response with id '{first_body['id']}' not found. "
                "HTTP bridge continuity was lost. Replay x-codex-turn-state or retry with a stable prompt_cache_key."
            ),
            "type": "invalid_request_error",
            "code": "previous_response_not_found",
            "param": "previous_response_id",
        }
    }
    assert connect_count == 1


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_reuses_derived_prompt_cache_key_when_client_omits_it(async_client, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(async_client, "acc_http_bridge_derived", "http-bridge-derived@example.com")
    account = await _get_account(account_id)
    fake_upstream = _FakeBridgeUpstreamWebSocket()
    connect_count = 0

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        nonlocal connect_count
        connect_count += 1
        return fake_upstream

    async def fail_legacy_stream(*args, **kwargs):
        raise AssertionError("legacy core_stream_responses path must not be used when HTTP bridge is enabled")

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)
    monkeypatch.setattr(proxy_module, "core_stream_responses", fail_legacy_stream)

    payload = {
        "model": "gpt-5.1",
        "instructions": "Return exactly OK.",
        "input": "same-first-user-input",
    }
    first = await async_client.post("/v1/responses", json=payload)
    second = await async_client.post("/v1/responses", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert connect_count == 1


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_prefers_session_header_for_isolation(async_client, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_session_key",
        "http-bridge-session-key@example.com",
    )
    account = await _get_account(account_id)
    upstreams = [_FakeBridgeUpstreamWebSocket(), _FakeBridgeUpstreamWebSocket()]
    connect_count = 0

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        nonlocal connect_count
        upstream = upstreams[connect_count]
        connect_count += 1
        return upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    payload = {
        "model": "gpt-5.1",
        "instructions": "Return exactly OK.",
        "input": "same-first-user-input",
    }
    first = await async_client.post("/v1/responses", json=payload, headers={"session_id": "session-a"})
    second = await async_client.post("/v1/responses", json=payload, headers={"session_id": "session-b"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert connect_count == 2


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_retries_once_when_upstream_closes_before_response_created(
    async_client,
    monkeypatch,
):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(async_client, "acc_http_bridge_retry", "http-bridge-retry@example.com")
    account = await _get_account(account_id)
    upstreams = [_PrecreatedCloseUpstreamWebSocket(), _FakeBridgeUpstreamWebSocket()]
    connect_count = 0

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        nonlocal connect_count
        upstream = upstreams[connect_count]
        connect_count += 1
        return upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    response = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "retry-me",
            "prompt_cache_key": "retry-key",
        },
    )

    assert response.status_code == 200
    assert connect_count == 2


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_does_not_evict_active_session_when_pool_is_full(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings_with_limits(monkeypatch, enabled=True, max_sessions=1)
    account_id = await _import_account(async_client, "acc_http_bridge_capacity", "http-bridge-capacity@example.com")
    service = get_proxy_service_for_app(app_instance)
    account = await _get_account(account_id)
    hanging_upstream = _CreatedOnlyUpstreamWebSocket()

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        return hanging_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)
    first_payload = proxy_module.ResponsesRequest(
        model="gpt-5.1",
        instructions="Return exactly OK.",
        input="hold-open",
        prompt_cache_key="active-session-a",
    )
    first_affinity = proxy_module._sticky_key_for_responses_request(
        first_payload,
        {},
        codex_session_affinity=False,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
        api_key=None,
    )
    first_key = proxy_module._make_http_bridge_session_key(
        first_payload,
        headers={},
        affinity=first_affinity,
        api_key=None,
        request_id="req_a",
    )
    first_session = await service._get_or_create_http_bridge_session(
        first_key,
        headers={},
        affinity=first_affinity,
        api_key=None,
        request_model="gpt-5.1",
        idle_ttl_seconds=120.0,
        max_sessions=1,
    )
    async with first_session.pending_lock:
        first_session.pending_requests.append(
            proxy_module._WebSocketRequestState(
                request_id="req-active",
                model="gpt-5.1",
                service_tier=None,
                reasoning_effort=None,
                api_key_reservation=None,
                started_at=time.monotonic(),
                awaiting_response_created=True,
                event_queue=asyncio.Queue(),
                transport="http",
            )
        )
    second_payload = proxy_module.ResponsesRequest(
        model="gpt-5.1",
        instructions="Return exactly OK.",
        input="new-session",
        prompt_cache_key="active-session-b",
    )
    second_affinity = proxy_module._sticky_key_for_responses_request(
        second_payload,
        {},
        codex_session_affinity=False,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
        api_key=None,
    )
    second_key = proxy_module._make_http_bridge_session_key(
        second_payload,
        headers={},
        affinity=second_affinity,
        api_key=None,
        request_id="req_b",
    )
    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await service._get_or_create_http_bridge_session(
            second_key,
            headers={},
            affinity=second_affinity,
            api_key=None,
            request_model="gpt-5.1",
            idle_ttl_seconds=120.0,
            max_sessions=1,
        )
    exc = exc_info.value
    assert exc.status_code == 429
    assert hanging_upstream.closed is False
    await service._close_http_bridge_session(first_session)


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_does_not_evict_queued_session_when_pool_is_full(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings_with_limits(monkeypatch, enabled=True, max_sessions=1)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_queued_capacity",
        "http-bridge-queued@example.com",
    )
    service = get_proxy_service_for_app(app_instance)
    account = await _get_account(account_id)
    hanging_upstream = _SilentUpstreamWebSocket()

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        return hanging_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    first_payload = proxy_module.ResponsesRequest(
        model="gpt-5.1",
        instructions="Return exactly OK.",
        input="queued-session",
        prompt_cache_key="queued-session-a",
    )
    first_affinity = proxy_module._sticky_key_for_responses_request(
        first_payload,
        {},
        codex_session_affinity=False,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
        api_key=None,
    )
    first_key = proxy_module._make_http_bridge_session_key(
        first_payload,
        headers={},
        affinity=first_affinity,
        api_key=None,
        request_id="req_queue_a",
    )
    first_session = await service._get_or_create_http_bridge_session(
        first_key,
        headers={},
        affinity=first_affinity,
        api_key=None,
        request_model="gpt-5.1",
        idle_ttl_seconds=120.0,
        max_sessions=1,
    )

    await first_session.response_create_gate.acquire()
    request_state, text_data = service._prepare_http_bridge_request(
        first_payload,
        {},
        api_key=None,
        api_key_reservation=None,
    )
    request_state.transport = "http"
    submit_task = asyncio.create_task(
        service._submit_http_bridge_request(
            first_session,
            request_state=request_state,
            text_data=text_data,
            queue_limit=8,
        )
    )
    await asyncio.sleep(0)

    assert await service._http_bridge_pending_count(first_session) == 1
    async with first_session.pending_lock:
        assert list(first_session.pending_requests) == []
        assert first_session.queued_request_count == 1

    second_payload = proxy_module.ResponsesRequest(
        model="gpt-5.1",
        instructions="Return exactly OK.",
        input="new-session",
        prompt_cache_key="queued-session-b",
    )
    second_affinity = proxy_module._sticky_key_for_responses_request(
        second_payload,
        {},
        codex_session_affinity=False,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
        api_key=None,
    )
    second_key = proxy_module._make_http_bridge_session_key(
        second_payload,
        headers={},
        affinity=second_affinity,
        api_key=None,
        request_id="req_queue_b",
    )
    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await service._get_or_create_http_bridge_session(
            second_key,
            headers={},
            affinity=second_affinity,
            api_key=None,
            request_model="gpt-5.1",
            idle_ttl_seconds=120.0,
            max_sessions=1,
        )

    exc = exc_info.value
    assert exc.status_code == 429
    assert hanging_upstream.closed is False

    submit_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await submit_task
    first_session.response_create_gate.release()
    await service._close_http_bridge_session(first_session)


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_enforces_queue_limit_atomically_for_same_session(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings_with_limits(monkeypatch, enabled=True, queue_limit=1)
    account_id = await _import_account(async_client, "acc_http_bridge_queue", "http-bridge-queue@example.com")
    service = get_proxy_service_for_app(app_instance)
    account = await _get_account(account_id)
    hanging_upstream = _SilentUpstreamWebSocket()

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        return hanging_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    payload = proxy_module.ResponsesRequest(
        model="gpt-5.1",
        instructions="Return exactly OK.",
        input="same-session",
        prompt_cache_key="same-session-key",
    )
    affinity = proxy_module._sticky_key_for_responses_request(
        payload,
        {},
        codex_session_affinity=False,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
        api_key=None,
    )
    key = proxy_module._make_http_bridge_session_key(
        payload,
        headers={},
        affinity=affinity,
        api_key=None,
        request_id="req_queue",
    )
    session = await service._get_or_create_http_bridge_session(
        key,
        headers={},
        affinity=affinity,
        api_key=None,
        request_model="gpt-5.1",
        idle_ttl_seconds=120.0,
        max_sessions=128,
    )

    first_state, first_text = service._prepare_http_bridge_request(payload, {}, api_key=None, api_key_reservation=None)
    first_state.transport = "http"
    await service._submit_http_bridge_request(session, request_state=first_state, text_data=first_text, queue_limit=1)

    second_state, second_text = service._prepare_http_bridge_request(
        payload, {}, api_key=None, api_key_reservation=None
    )
    second_state.transport = "http"
    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await service._submit_http_bridge_request(
            session,
            request_state=second_state,
            text_data=second_text,
            queue_limit=1,
        )

    exc = exc_info.value
    assert exc.status_code == 429
    assert session.queued_request_count == 1
    await service._close_http_bridge_session(session)


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_creates_different_session_keys_in_parallel(app_instance, monkeypatch):
    service = get_proxy_service_for_app(app_instance)
    service._http_bridge_sessions.clear()
    service._http_bridge_inflight_sessions.clear()
    service._http_bridge_turn_state_index.clear()

    settings = SimpleNamespace(
        http_responses_session_bridge_enabled=True,
        http_responses_session_bridge_idle_ttl_seconds=120.0,
        http_responses_session_bridge_codex_idle_ttl_seconds=120.0,
        http_responses_session_bridge_max_sessions=8,
        http_responses_session_bridge_instance_id="instance-a",
        http_responses_session_bridge_instance_ring=[],
    )
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _SettingsCache(settings))

    create_started: list[str] = []

    async def fake_create_http_bridge_session(
        self,
        key,
        *,
        headers,
        affinity,
        request_model,
        idle_ttl_seconds,
    ):
        del self, headers, affinity, request_model, idle_ttl_seconds
        create_started.append(key.affinity_key)
        await asyncio.sleep(0.2)
        return _make_dummy_bridge_session(key)

    monkeypatch.setattr(proxy_module.ProxyService, "_create_http_bridge_session", fake_create_http_bridge_session)

    key_one = proxy_module._HTTPBridgeSessionKey("request", "bridge-a", None)
    key_two = proxy_module._HTTPBridgeSessionKey("request", "bridge-b", None)
    t0 = time.monotonic()

    try:
        first = asyncio.create_task(
            service._get_or_create_http_bridge_session(
                key_one,
                headers={},
                affinity=proxy_module._AffinityPolicy(),
                api_key=None,
                request_model="gpt-5.4",
                idle_ttl_seconds=120.0,
                max_sessions=8,
            )
        )
        second = asyncio.create_task(
            service._get_or_create_http_bridge_session(
                key_two,
                headers={},
                affinity=proxy_module._AffinityPolicy(),
                api_key=None,
                request_model="gpt-5.4",
                idle_ttl_seconds=120.0,
                max_sessions=8,
            )
        )
        session_one, session_two = await asyncio.gather(first, second)
        elapsed = time.monotonic() - t0

        assert elapsed < 0.35
        assert sorted(create_started) == ["bridge-a", "bridge-b"]
        assert session_one.key == key_one
        assert session_two.key == key_two
        assert service._http_bridge_sessions[key_one] is session_one
        assert service._http_bridge_sessions[key_two] is session_two
    finally:
        service._http_bridge_sessions.clear()
        service._http_bridge_inflight_sessions.clear()
        service._http_bridge_turn_state_index.clear()


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_singleflights_same_session_key_during_creation(app_instance, monkeypatch):
    service = get_proxy_service_for_app(app_instance)
    service._http_bridge_sessions.clear()
    service._http_bridge_inflight_sessions.clear()
    service._http_bridge_turn_state_index.clear()

    settings = SimpleNamespace(
        http_responses_session_bridge_enabled=True,
        http_responses_session_bridge_idle_ttl_seconds=120.0,
        http_responses_session_bridge_codex_idle_ttl_seconds=120.0,
        http_responses_session_bridge_max_sessions=8,
        http_responses_session_bridge_instance_id="instance-a",
        http_responses_session_bridge_instance_ring=[],
    )
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _SettingsCache(settings))

    create_started: list[str] = []

    async def fake_create_http_bridge_session(
        self,
        key,
        *,
        headers,
        affinity,
        request_model,
        idle_ttl_seconds,
    ):
        del self, headers, affinity, request_model, idle_ttl_seconds
        create_started.append(key.affinity_key)
        await asyncio.sleep(0.2)
        return _make_dummy_bridge_session(key)

    monkeypatch.setattr(proxy_module.ProxyService, "_create_http_bridge_session", fake_create_http_bridge_session)

    key = proxy_module._HTTPBridgeSessionKey("request", "bridge-singleflight", None)
    t0 = time.monotonic()

    try:
        first = asyncio.create_task(
            service._get_or_create_http_bridge_session(
                key,
                headers={},
                affinity=proxy_module._AffinityPolicy(),
                api_key=None,
                request_model="gpt-5.4",
                idle_ttl_seconds=120.0,
                max_sessions=8,
            )
        )
        second = asyncio.create_task(
            service._get_or_create_http_bridge_session(
                key,
                headers={},
                affinity=proxy_module._AffinityPolicy(),
                api_key=None,
                request_model="gpt-5.4",
                idle_ttl_seconds=120.0,
                max_sessions=8,
            )
        )
        session_one, session_two = await asyncio.gather(first, second)
        elapsed = time.monotonic() - t0

        assert elapsed < 0.35
        assert create_started == ["bridge-singleflight"]
        assert session_one is session_two
        assert service._http_bridge_sessions[key] is session_one
    finally:
        service._http_bridge_sessions.clear()
        service._http_bridge_inflight_sessions.clear()
        service._http_bridge_turn_state_index.clear()


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_singleflight_follower_refreshes_session_model(app_instance, monkeypatch):
    service = get_proxy_service_for_app(app_instance)
    service._http_bridge_sessions.clear()
    service._http_bridge_inflight_sessions.clear()
    service._http_bridge_turn_state_index.clear()

    settings = SimpleNamespace(
        http_responses_session_bridge_enabled=True,
        http_responses_session_bridge_idle_ttl_seconds=120.0,
        http_responses_session_bridge_codex_idle_ttl_seconds=120.0,
        http_responses_session_bridge_max_sessions=8,
        http_responses_session_bridge_instance_id="instance-a",
        http_responses_session_bridge_instance_ring=[],
    )
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _SettingsCache(settings))

    create_started = asyncio.Event()
    release_create = asyncio.Event()

    async def fake_create_http_bridge_session(
        self,
        key,
        *,
        headers,
        affinity,
        request_model,
        idle_ttl_seconds,
    ):
        del self, headers, affinity, request_model, idle_ttl_seconds
        create_started.set()
        await release_create.wait()
        session = _make_dummy_bridge_session(key)
        session.request_model = "gpt-5.1"
        return session

    monkeypatch.setattr(proxy_module.ProxyService, "_create_http_bridge_session", fake_create_http_bridge_session)

    key = proxy_module._HTTPBridgeSessionKey("session_header", "shared-session", None)

    try:
        creator = asyncio.create_task(
            service._get_or_create_http_bridge_session(
                key,
                headers={"session_id": "shared-session"},
                affinity=proxy_module._AffinityPolicy(
                    key="shared-session",
                    kind=proxy_module.StickySessionKind.CODEX_SESSION,
                ),
                api_key=None,
                request_model="gpt-5.1",
                idle_ttl_seconds=120.0,
                max_sessions=8,
            )
        )
        await create_started.wait()
        follower = asyncio.create_task(
            service._get_or_create_http_bridge_session(
                key,
                headers={"session_id": "shared-session"},
                affinity=proxy_module._AffinityPolicy(
                    key="shared-session",
                    kind=proxy_module.StickySessionKind.CODEX_SESSION,
                ),
                api_key=None,
                request_model="gpt-5.4",
                idle_ttl_seconds=120.0,
                max_sessions=8,
            )
        )
        release_create.set()
        created_session, follower_session = await asyncio.gather(creator, follower)

        assert created_session is follower_session
        assert follower_session.request_model == "gpt-5.4"
    finally:
        service._http_bridge_sessions.clear()
        service._http_bridge_inflight_sessions.clear()
        service._http_bridge_turn_state_index.clear()


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_singleflights_stale_session_replacement(app_instance, monkeypatch):
    service = get_proxy_service_for_app(app_instance)
    service._http_bridge_sessions.clear()
    service._http_bridge_inflight_sessions.clear()
    service._http_bridge_turn_state_index.clear()

    settings = SimpleNamespace(
        http_responses_session_bridge_enabled=True,
        http_responses_session_bridge_idle_ttl_seconds=120.0,
        http_responses_session_bridge_codex_idle_ttl_seconds=120.0,
        http_responses_session_bridge_max_sessions=8,
        http_responses_session_bridge_instance_id="instance-a",
        http_responses_session_bridge_instance_ring=[],
    )
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _SettingsCache(settings))

    create_started: list[str] = []

    async def fake_create_http_bridge_session(
        self,
        key,
        *,
        headers,
        affinity,
        request_model,
        idle_ttl_seconds,
    ):
        del self, headers, affinity, request_model, idle_ttl_seconds
        create_started.append(key.affinity_key)
        await asyncio.sleep(0.2)
        return _make_dummy_bridge_session(key)

    monkeypatch.setattr(proxy_module.ProxyService, "_create_http_bridge_session", fake_create_http_bridge_session)

    key = proxy_module._HTTPBridgeSessionKey("request", "bridge-stale-replace", None)
    stale_session = cast(proxy_module._HTTPBridgeSession, _make_dummy_bridge_session(key))
    stale_session.closed = True
    service._http_bridge_sessions[key] = stale_session

    try:
        first = asyncio.create_task(
            service._get_or_create_http_bridge_session(
                key,
                headers={},
                affinity=proxy_module._AffinityPolicy(),
                api_key=None,
                request_model="gpt-5.4",
                idle_ttl_seconds=120.0,
                max_sessions=8,
            )
        )
        second = asyncio.create_task(
            service._get_or_create_http_bridge_session(
                key,
                headers={},
                affinity=proxy_module._AffinityPolicy(),
                api_key=None,
                request_model="gpt-5.4",
                idle_ttl_seconds=120.0,
                max_sessions=8,
            )
        )
        session_one, session_two = await asyncio.gather(first, second)

        assert create_started == ["bridge-stale-replace"]
        assert session_one is session_two
        assert service._http_bridge_sessions[key] is session_one
    finally:
        service._http_bridge_sessions.clear()
        service._http_bridge_inflight_sessions.clear()
        service._http_bridge_turn_state_index.clear()


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_cleans_up_cancelled_singleflight_creator(app_instance, monkeypatch):
    service = get_proxy_service_for_app(app_instance)
    service._http_bridge_sessions.clear()
    service._http_bridge_inflight_sessions.clear()
    service._http_bridge_turn_state_index.clear()

    settings = SimpleNamespace(
        http_responses_session_bridge_enabled=True,
        http_responses_session_bridge_idle_ttl_seconds=120.0,
        http_responses_session_bridge_codex_idle_ttl_seconds=120.0,
        http_responses_session_bridge_max_sessions=8,
        http_responses_session_bridge_instance_id="instance-a",
        http_responses_session_bridge_instance_ring=[],
    )
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _SettingsCache(settings))

    first_create_started = asyncio.Event()
    create_attempts = 0

    async def fake_create_http_bridge_session(
        self,
        key,
        *,
        headers,
        affinity,
        request_model,
        idle_ttl_seconds,
    ):
        del self, headers, affinity, request_model, idle_ttl_seconds
        nonlocal create_attempts
        create_attempts += 1
        if create_attempts == 1:
            first_create_started.set()
            await asyncio.Event().wait()
        return _make_dummy_bridge_session(key)

    monkeypatch.setattr(proxy_module.ProxyService, "_create_http_bridge_session", fake_create_http_bridge_session)

    key = proxy_module._HTTPBridgeSessionKey("request", "bridge-cancelled-create", None)

    creator = asyncio.create_task(
        service._get_or_create_http_bridge_session(
            key,
            headers={},
            affinity=proxy_module._AffinityPolicy(),
            api_key=None,
            request_model="gpt-5.4",
            idle_ttl_seconds=120.0,
            max_sessions=8,
        )
    )
    await first_create_started.wait()
    creator.cancel()
    with pytest.raises(asyncio.CancelledError):
        await creator

    replacement = await asyncio.wait_for(
        service._get_or_create_http_bridge_session(
            key,
            headers={},
            affinity=proxy_module._AffinityPolicy(),
            api_key=None,
            request_model="gpt-5.4",
            idle_ttl_seconds=120.0,
            max_sessions=8,
        ),
        timeout=1.0,
    )

    assert create_attempts == 2
    assert service._http_bridge_sessions[key] is replacement
    assert key not in service._http_bridge_inflight_sessions


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_cleans_up_cancelled_singleflight_creator_after_create(
    app_instance, monkeypatch
):
    service = get_proxy_service_for_app(app_instance)
    service._http_bridge_sessions.clear()
    service._http_bridge_inflight_sessions.clear()
    service._http_bridge_turn_state_index.clear()

    settings = SimpleNamespace(
        http_responses_session_bridge_enabled=True,
        http_responses_session_bridge_idle_ttl_seconds=120.0,
        http_responses_session_bridge_codex_idle_ttl_seconds=120.0,
        http_responses_session_bridge_max_sessions=8,
        http_responses_session_bridge_instance_id="instance-a",
        http_responses_session_bridge_instance_ring=[],
    )
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _SettingsCache(settings))

    create_finished = asyncio.Event()
    allow_return = asyncio.Event()
    create_attempts = 0

    async def fake_create_http_bridge_session(
        self,
        key,
        *,
        headers,
        affinity,
        request_model,
        idle_ttl_seconds,
    ):
        del self, headers, affinity, request_model, idle_ttl_seconds
        nonlocal create_attempts
        create_attempts += 1
        if create_attempts == 1:
            create_finished.set()
            await allow_return.wait()
        return _make_dummy_bridge_session(key)

    monkeypatch.setattr(proxy_module.ProxyService, "_create_http_bridge_session", fake_create_http_bridge_session)

    key = proxy_module._HTTPBridgeSessionKey("request", "bridge-cancelled-after-create", None)
    creator = asyncio.create_task(
        service._get_or_create_http_bridge_session(
            key,
            headers={},
            affinity=proxy_module._AffinityPolicy(),
            api_key=None,
            request_model="gpt-5.4",
            idle_ttl_seconds=120.0,
            max_sessions=8,
        )
    )
    await create_finished.wait()
    async with service._http_bridge_lock:
        allow_return.set()
        await asyncio.sleep(0)
        creator.cancel()

    with pytest.raises(asyncio.CancelledError):
        await creator

    replacement = await asyncio.wait_for(
        service._get_or_create_http_bridge_session(
            key,
            headers={},
            affinity=proxy_module._AffinityPolicy(),
            api_key=None,
            request_model="gpt-5.4",
            idle_ttl_seconds=120.0,
            max_sessions=8,
        ),
        timeout=1.0,
    )

    assert create_attempts == 2
    assert service._http_bridge_sessions[key] is replacement
    assert key not in service._http_bridge_inflight_sessions


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_waits_for_inflight_session_before_continuity_error(app_instance, monkeypatch):
    service = get_proxy_service_for_app(app_instance)
    service._http_bridge_sessions.clear()
    service._http_bridge_inflight_sessions.clear()
    service._http_bridge_turn_state_index.clear()

    settings = SimpleNamespace(
        http_responses_session_bridge_enabled=True,
        http_responses_session_bridge_idle_ttl_seconds=120.0,
        http_responses_session_bridge_codex_idle_ttl_seconds=120.0,
        http_responses_session_bridge_max_sessions=8,
        http_responses_session_bridge_instance_id="instance-a",
        http_responses_session_bridge_instance_ring=[],
    )
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _SettingsCache(settings))

    create_started = asyncio.Event()
    release_create = asyncio.Event()

    async def fake_create_http_bridge_session(
        self,
        key,
        *,
        headers,
        affinity,
        request_model,
        idle_ttl_seconds,
    ):
        del self, headers, affinity, request_model, idle_ttl_seconds
        create_started.set()
        await release_create.wait()
        return _make_dummy_bridge_session(key)

    monkeypatch.setattr(proxy_module.ProxyService, "_create_http_bridge_session", fake_create_http_bridge_session)

    key = proxy_module._HTTPBridgeSessionKey("request", "bridge-waits-for-inflight", None)

    creator = asyncio.create_task(
        service._get_or_create_http_bridge_session(
            key,
            headers={},
            affinity=proxy_module._AffinityPolicy(),
            api_key=None,
            request_model="gpt-5.4",
            idle_ttl_seconds=120.0,
            max_sessions=8,
        )
    )
    await create_started.wait()

    follower = asyncio.create_task(
        service._get_or_create_http_bridge_session(
            key,
            headers={},
            affinity=proxy_module._AffinityPolicy(),
            api_key=None,
            request_model="gpt-5.4",
            idle_ttl_seconds=120.0,
            max_sessions=8,
            previous_response_id="resp_inflight",
        )
    )
    await asyncio.sleep(0.01)
    assert follower.done()

    release_create.set()
    created_session = await creator
    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await follower

    assert service._http_bridge_sessions[key] is created_session
    exc = exc_info.value
    assert exc.status_code == 400
    assert exc.payload["error"] == {
        "message": (
            "Previous response with id 'resp_inflight' not found. "
            "HTTP bridge continuity was lost. Replay x-codex-turn-state or retry with a stable prompt_cache_key."
        ),
        "type": "invalid_request_error",
        "code": "previous_response_not_found",
        "param": "previous_response_id",
    }


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_prunes_idle_session_before_reuse(app_instance, monkeypatch):
    service = get_proxy_service_for_app(app_instance)
    service._http_bridge_sessions.clear()
    service._http_bridge_inflight_sessions.clear()
    service._http_bridge_turn_state_index.clear()

    settings = SimpleNamespace(
        http_responses_session_bridge_enabled=True,
        http_responses_session_bridge_idle_ttl_seconds=120.0,
        http_responses_session_bridge_codex_idle_ttl_seconds=120.0,
        http_responses_session_bridge_max_sessions=8,
        http_responses_session_bridge_instance_id="instance-a",
        http_responses_session_bridge_instance_ring=[],
    )
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _SettingsCache(settings))

    create_started: list[str] = []

    async def fake_create_http_bridge_session(
        self,
        key,
        *,
        headers,
        affinity,
        request_model,
        idle_ttl_seconds,
    ):
        del self, headers, affinity, request_model, idle_ttl_seconds
        create_started.append(key.affinity_key)
        return _make_dummy_bridge_session(key)

    monkeypatch.setattr(proxy_module.ProxyService, "_create_http_bridge_session", fake_create_http_bridge_session)

    key = proxy_module._HTTPBridgeSessionKey("request", "bridge-idle-prune", None)
    stale_session = cast(proxy_module._HTTPBridgeSession, _make_dummy_bridge_session(key))
    stale_session.last_used_at = time.monotonic() - 300.0
    stale_session.idle_ttl_seconds = 120.0
    service._http_bridge_sessions[key] = stale_session

    try:
        replacement = await service._get_or_create_http_bridge_session(
            key,
            headers={},
            affinity=proxy_module._AffinityPolicy(),
            api_key=None,
            request_model="gpt-5.4",
            idle_ttl_seconds=120.0,
            max_sessions=8,
        )

        assert create_started == ["bridge-idle-prune"]
        assert replacement is not stale_session
        assert service._http_bridge_sessions[key] is replacement
    finally:
        service._http_bridge_sessions.clear()
        service._http_bridge_inflight_sessions.clear()
        service._http_bridge_turn_state_index.clear()


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_stream_failure_remains_valid_sse(async_client, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_sse_failure",
        "http-bridge-sse-failure@example.com",
    )
    account = await _get_account(account_id)
    upstream = _CreatedThenCloseUpstreamWebSocket()

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        return upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    async with async_client.stream(
        "POST",
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "trigger-sse-failure",
            "prompt_cache_key": "sse-failure-key",
            "stream": True,
        },
    ) as response:
        assert response.status_code == 200
        lines = [line async for line in response.aiter_lines() if line.startswith("data: ")]

    events = [json.loads(line[6:]) for line in lines]
    assert [event["type"] for event in events] == ["response.created", "response.failed"]
    assert events[-1]["response"]["error"]["code"] == "stream_incomplete"


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_cancellation_releases_queued_slot(async_client, app_instance, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(async_client, "acc_http_bridge_cancel", "http-bridge-cancel@example.com")
    service = get_proxy_service_for_app(app_instance)
    account = await _get_account(account_id)
    upstream = _SilentUpstreamWebSocket()

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        return upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    payload = proxy_module.ResponsesRequest(
        model="gpt-5.1",
        instructions="Return exactly OK.",
        input="cancel-me",
        prompt_cache_key="cancel-key",
    )
    affinity = proxy_module._sticky_key_for_responses_request(
        payload,
        {},
        codex_session_affinity=False,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
        api_key=None,
    )
    key = proxy_module._make_http_bridge_session_key(
        payload,
        headers={},
        affinity=affinity,
        api_key=None,
        request_id="req_cancel",
    )
    session = await service._get_or_create_http_bridge_session(
        key,
        headers={},
        affinity=affinity,
        api_key=None,
        request_model="gpt-5.1",
        idle_ttl_seconds=120.0,
        max_sessions=128,
    )

    await session.response_create_gate.acquire()
    request_state, text_data = service._prepare_http_bridge_request(payload, {}, api_key=None, api_key_reservation=None)
    request_state.transport = "http"
    task = asyncio.create_task(
        service._submit_http_bridge_request(
            session,
            request_state=request_state,
            text_data=text_data,
            queue_limit=8,
        )
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert session.queued_request_count == 0
    async with session.pending_lock:
        assert list(session.pending_requests) == []
    session.response_create_gate.release()
    await service._close_http_bridge_session(session)


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_send_retry_restarts_reader(async_client, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_send_retry",
        "http-bridge-send-retry@example.com",
    )
    account = await _get_account(account_id)
    upstreams = [_FailingSendThenCloseUpstreamWebSocket(), _FakeBridgeUpstreamWebSocket()]
    connect_count = 0

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        nonlocal connect_count
        upstream = upstreams[connect_count]
        connect_count += 1
        if isinstance(upstream, _FakeBridgeUpstreamWebSocket) and not upstream._messages.qsize():
            await upstream._messages.put(
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.created",
                            "response": {"id": "resp_retry_send", "object": "response", "status": "in_progress"},
                        },
                        separators=(",", ":"),
                    ),
                )
            )
            await upstream._messages.put(
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.completed",
                            "response": {
                                "id": "resp_retry_send",
                                "object": "response",
                                "status": "completed",
                                "usage": {
                                    "input_tokens": 24,
                                    "output_tokens": 2,
                                    "total_tokens": 26,
                                    "input_tokens_details": {"cached_tokens": 20},
                                    "output_tokens_details": {"reasoning_tokens": 0},
                                },
                            },
                        },
                        separators=(",", ":"),
                    ),
                )
            )
        return upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    response = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "retry-send",
            "prompt_cache_key": "retry-send-key",
        },
    )

    assert response.status_code == 200
    assert response.json()["id"] == "resp_retry_send"
    assert connect_count == 2


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_send_failure_returns_previous_response_not_found(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_send_failure_previous_response",
        "http-bridge-send-failure-previous-response@example.com",
    )
    account = await _get_account(account_id)
    fake_upstream = _FakeBridgeUpstreamWebSocket()
    failing_upstream = _FailingSendThenCloseUpstreamWebSocket()
    connect_count = 0

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        nonlocal connect_count
        connect_count += 1
        return fake_upstream if connect_count == 1 else failing_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    first = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello",
            "prompt_cache_key": "send-failure-previous-response",
        },
    )
    assert first.status_code == 200
    first_body = first.json()

    service = get_proxy_service_for_app(app_instance)
    async with service._http_bridge_lock:
        session = next(iter(service._http_bridge_sessions.values()))
        session.upstream = cast(proxy_module.UpstreamResponsesWebSocket, failing_upstream)

    second = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello-again",
            "prompt_cache_key": "send-failure-previous-response",
            "previous_response_id": first_body["id"],
        },
    )

    assert second.status_code == 400
    assert second.json() == {
        "error": {
            "message": (
                f"Previous response with id '{first_body['id']}' not found. "
                "HTTP bridge continuity was lost before the request reached upstream. "
                "Replay x-codex-turn-state or retry with a stable prompt_cache_key."
            ),
            "type": "invalid_request_error",
            "code": "previous_response_not_found",
            "param": "previous_response_id",
        }
    }
    assert connect_count == 1


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_precreated_disconnect_returns_previous_response_not_found(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_precreated_previous_response",
        "http-bridge-precreated-previous-response@example.com",
    )
    account = await _get_account(account_id)
    fake_upstream = _FakeBridgeUpstreamWebSocket()
    precreated_close_upstream = _PrecreatedCloseUpstreamWebSocket()
    connect_count = 0

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        nonlocal connect_count
        connect_count += 1
        return fake_upstream if connect_count == 1 else precreated_close_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    first = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello",
            "prompt_cache_key": "precreated-previous-response",
        },
    )
    assert first.status_code == 200
    first_body = first.json()

    service = get_proxy_service_for_app(app_instance)
    async with service._http_bridge_lock:
        session = next(iter(service._http_bridge_sessions.values()))
        session.upstream = cast(proxy_module.UpstreamResponsesWebSocket, precreated_close_upstream)

    second = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello-again",
            "prompt_cache_key": "precreated-previous-response",
            "previous_response_id": first_body["id"],
        },
    )

    assert second.status_code == 400
    assert second.json() == {
        "error": {
            "message": (
                f"Previous response with id '{first_body['id']}' not found. "
                "HTTP bridge continuity was lost before upstream created the next response. "
                "Replay x-codex-turn-state or retry with a stable prompt_cache_key."
            ),
            "type": "invalid_request_error",
            "code": "previous_response_not_found",
            "param": "previous_response_id",
        }
    }
    assert connect_count == 1


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_send_retry_keeps_session_open_for_followup_request(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_send_retry_followup",
        "http-bridge-send-retry-followup@example.com",
    )
    account = await _get_account(account_id)
    upstreams = [_FailingSendThenCloseUpstreamWebSocket(), _FakeBridgeUpstreamWebSocket()]
    connect_count = 0
    service = get_proxy_service_for_app(app_instance)

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        nonlocal connect_count
        upstream = upstreams[min(connect_count, len(upstreams) - 1)]
        connect_count += 1
        if isinstance(upstream, _FakeBridgeUpstreamWebSocket) and not upstream._messages.qsize():
            await upstream._messages.put(
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.created",
                            "response": {
                                "id": "resp_retry_send_followup",
                                "object": "response",
                                "status": "in_progress",
                            },
                        },
                        separators=(",", ":"),
                    ),
                )
            )
            await upstream._messages.put(
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.completed",
                            "response": {
                                "id": "resp_retry_send_followup",
                                "object": "response",
                                "status": "completed",
                                "usage": {
                                    "input_tokens": 24,
                                    "output_tokens": 2,
                                    "total_tokens": 26,
                                    "input_tokens_details": {"cached_tokens": 20},
                                    "output_tokens_details": {"reasoning_tokens": 0},
                                },
                            },
                        },
                        separators=(",", ":"),
                    ),
                )
            )
        return upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    payload = {
        "model": "gpt-5.1",
        "instructions": "Return exactly OK.",
        "input": "retry-send-followup",
        "prompt_cache_key": "retry-send-followup-key",
    }
    first = await async_client.post("/v1/responses", json=payload)
    second = await async_client.post("/v1/responses", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert connect_count == 2

    session_key = proxy_module._HTTPBridgeSessionKey(
        affinity_kind="prompt_cache",
        affinity_key="retry-send-followup-key",
        api_key_id=None,
    )
    async with service._http_bridge_lock:
        session = service._http_bridge_sessions[session_key]
        assert session.closed is False


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_stream_cancel_detaches_pending_request(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_stream_cancel",
        "http-bridge-stream-cancel@example.com",
    )
    service = get_proxy_service_for_app(app_instance)
    account = await _get_account(account_id)
    fake_upstream = _CreatedOnlyUpstreamWebSocket()

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
    ):
        del (
            self,
            deadline,
            request_id,
            kind,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        return fake_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    payload = proxy_module.ResponsesRequest(
        model="gpt-5.1",
        instructions="Return exactly OK.",
        input="cancel-stream",
        prompt_cache_key="cancel-stream-key",
    )
    stream = service._stream_via_http_bridge(
        payload,
        {},
        codex_session_affinity=False,
        propagate_http_errors=False,
        openai_cache_affinity=True,
        api_key=None,
        api_key_reservation=None,
        suppress_text_done_events=False,
        idle_ttl_seconds=120.0,
        codex_idle_ttl_seconds=900.0,
        max_sessions=128,
        queue_limit=8,
    )
    stream = cast(AsyncGenerator[str, None], stream)

    first_event = await stream.__anext__()
    assert "response.created" in first_event
    await stream.aclose()

    session_key = proxy_module._HTTPBridgeSessionKey(
        affinity_kind="prompt_cache",
        affinity_key="cancel-stream-key",
        api_key_id=None,
    )
    async with service._http_bridge_lock:
        session = service._http_bridge_sessions[session_key]
    async with session.pending_lock:
        assert list(session.pending_requests) == []
        assert session.queued_request_count == 0


@pytest.mark.asyncio
async def test_prepare_http_bridge_request_preserves_existing_client_metadata(app_instance):
    service = get_proxy_service_for_app(app_instance)
    payload = proxy_module.ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
            "client_metadata": {
                "bool_flag": True,
                "count": 2,
                "nested": {"enabled": False},
            },
        }
    )

    token = set_request_id("req_http_bridge_existing")
    try:
        first_request_state, text_data = service._prepare_http_bridge_request(
            payload,
            {"x-codex-turn-metadata": '{"turn_id":"turn_123","sandbox":"workspace-write"}'},
            api_key=None,
            api_key_reservation=None,
        )
        second_request_state, _ = service._prepare_http_bridge_request(
            payload,
            {"x-codex-turn-metadata": '{"turn_id":"turn_123","sandbox":"workspace-write"}'},
            api_key=None,
            api_key_reservation=None,
        )
    finally:
        reset_request_id(token)

    assert json.loads(text_data)["client_metadata"] == {
        "bool_flag": True,
        "count": 2,
        "nested": {"enabled": False},
        "x-codex-turn-metadata": '{"turn_id":"turn_123","sandbox":"workspace-write"}',
    }
    assert first_request_state.request_log_id == "req_http_bridge_existing"
    assert second_request_state.request_log_id == "req_http_bridge_existing"
    assert first_request_state.request_id.startswith("ws_")
    assert second_request_state.request_id.startswith("ws_")
    assert first_request_state.request_id != second_request_state.request_id
