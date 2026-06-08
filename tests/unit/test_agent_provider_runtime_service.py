from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast

import pytest

import app.modules.agent_provider_runtime.service as runtime_service_module
from app.db.models import AgentProviderAccount
from app.modules.agent_provider_routing.settlement import AgentProviderUsageSettlementData
from app.modules.agent_provider_runtime.antigravity import (
    AntigravityHarnessRequest,
    AntigravityProcessResult,
    antigravity_harness_env,
    build_antigravity_command,
    command_preview,
)
from app.modules.agent_provider_runtime.service import (
    AntigravityHarnessService,
    AntigravityManagedAgentService,
    AntigravityRuntimeRequestContext,
    GeminiRuntimeRequestContext,
    GeminiRuntimeService,
    GeminiRuntimeValidationError,
    parse_chat_completion_request,
)
from app.modules.api_keys.service import ApiKeyData, ApiKeyUsageReservationData


@dataclass(slots=True)
class _Selected:
    account: AgentProviderAccount


class _RoutingService:
    def __init__(self, account: AgentProviderAccount) -> None:
        self.account = account
        self.provider_ids: list[str] = []
        self.auth_modes: list[str | None] = []
        self.settlements: list[tuple[str, str, AgentProviderUsageSettlementData]] = []

    async def select_account(self, provider_id: str, *, auth_mode: str | None = None) -> _Selected:
        self.provider_ids.append(provider_id)
        self.auth_modes.append(auth_mode)
        return _Selected(account=self.account)

    async def settle_usage(
        self,
        provider_id: str,
        account_id: str,
        usage: AgentProviderUsageSettlementData,
    ) -> None:
        self.settlements.append((provider_id, account_id, usage))


class _FailingSettlementRoutingService(_RoutingService):
    async def settle_usage(
        self,
        provider_id: str,
        account_id: str,
        usage: AgentProviderUsageSettlementData,
    ) -> None:
        del provider_id, account_id, usage
        raise RuntimeError("database unavailable")


class _Decryptor:
    def decrypt(self, encrypted: bytes) -> str:
        assert encrypted == b"encrypted-key"
        return "AIza-test-key"


class _Response:
    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self._payload = payload or {}
        self.content = _Content(chunks or [])

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def json(self) -> dict[str, Any]:
        return self._payload

    async def text(self) -> str:
        return "upstream-error"


class _Content:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def iter_any(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


class _CancelledContent:
    async def iter_any(self) -> AsyncIterator[bytes]:
        raise asyncio.CancelledError
        yield b""


class _FailingAfterChunkContent:
    async def iter_any(self) -> AsyncIterator[bytes]:
        yield (
            b'data: {"responseId":"resp_partial","candidates":[{"content":{"parts":[{"text":"Hi"}]}}],'
            b'"usageMetadata":{"promptTokenCount":2,"candidatesTokenCount":1,"totalTokenCount":3}}\n\n'
        )
        raise RuntimeError("stream failed")


class _CancelledResponse(_Response):
    def __init__(self) -> None:
        super().__init__(chunks=[])
        self.content = _CancelledContent()


class _FailingAfterChunkResponse(_Response):
    def __init__(self) -> None:
        super().__init__(chunks=[])
        self.content = _FailingAfterChunkContent()


class _Session:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append({"url": url, **kwargs})
        return self.response


class _ApiKeyUsageService:
    def __init__(self) -> None:
        self.released: list[str] = []
        self.finalized: list[tuple[str, int, int]] = []

    async def enforce_limits_for_request(
        self,
        key_id: str,
        *,
        request_model: str | None,
        request_service_tier: str | None = None,
        request_usage_budget: object | None = None,
    ) -> ApiKeyUsageReservationData:
        del request_service_tier, request_usage_budget
        return ApiKeyUsageReservationData(
            reservation_id=f"reservation-{request_model}",
            key_id=key_id,
            model=request_model or "",
        )

    async def finalize_usage_reservation(
        self,
        reservation_id: str,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
        service_tier: str | None = None,
    ) -> None:
        del model, cached_input_tokens, service_tier
        self.finalized.append((reservation_id, input_tokens, output_tokens))

    async def release_usage_reservation(self, reservation_id: str) -> None:
        self.released.append(reservation_id)


def _account(
    *,
    account_id: str = "gemini-account",
    provider_id: str = "gemini",
    display_name: str = "Gemini",
    auth_mode: str = "api_key",
    api_key_encrypted: bytes | None = b"encrypted-key",
    external_account_id: str | None = None,
) -> AgentProviderAccount:
    return AgentProviderAccount(
        id=account_id,
        provider_id=provider_id,
        external_account_id=external_account_id,
        display_name=display_name,
        auth_mode=auth_mode,
        api_key_encrypted=api_key_encrypted,
        status="active",
    )


class _AntigravityRunner:
    def __init__(self, result: AntigravityProcessResult | None = None) -> None:
        self.result = result or AntigravityProcessResult(
            exit_code=0,
            stdout="agy-result",
            stderr="",
            duration_ms=12,
        )
        self.commands = []
        self.envs: list[dict[str, str]] = []

    async def run(self, command, *, env):
        self.commands.append(command)
        self.envs.append(dict(env))
        return self.result


def test_build_antigravity_command_redacts_prompt_and_disables_dangerous_flags(tmp_path) -> None:
    extra_dir = tmp_path / "extra"
    extra_dir.mkdir()

    command = build_antigravity_command(
        AntigravityHarnessRequest(
            prompt="Inspect this",
            workspace_path=str(tmp_path),
            timeout_seconds=30,
            add_dirs=("extra",),
            sandbox="read-only",
        )
    )

    assert command.cwd == tmp_path.resolve()
    assert command.args[:5] == ("--print", "--print-timeout", "30s", "--prompt", "Inspect this")
    assert "--dangerously-skip-permissions" not in command.args
    assert command_preview(command)[:5] == ("agy", "--print", "--print-timeout", "30s", "--prompt")
    assert command_preview(command)[5] == "<redacted>"
    assert antigravity_harness_env({})["AGY_CLI_DISABLE_AUTO_UPDATE"] == "true"


@pytest.mark.asyncio
async def test_antigravity_harness_selects_profile_and_settles_request(tmp_path) -> None:
    account = _account(
        account_id="agy-account",
        provider_id="antigravity",
        display_name="Antigravity",
        auth_mode="cli_keyring",
        api_key_encrypted=None,
        external_account_id="default",
    )
    routing = _RoutingService(account)
    runner = _AntigravityRunner()
    service = AntigravityHarnessService(routing, runner=runner)

    result = await service.print_prompt(
        AntigravityHarnessRequest(prompt="Say hi", workspace_path=str(tmp_path), timeout_seconds=5)
    )

    assert result.account.id == "agy-account"
    assert routing.provider_ids == ["antigravity"]
    assert routing.settlements == [("antigravity", "agy-account", AgentProviderUsageSettlementData(requests=1))]
    assert runner.commands[0].args[:5] == ("--print", "--print-timeout", "5s", "--prompt", "Say hi")
    assert "--dangerously-skip-permissions" not in runner.commands[0].args
    assert runner.envs[0]["AGY_CLI_DISABLE_AUTO_UPDATE"] == "true"
    assert runner.envs[0]["AGY_CLI_PROFILE"] == "default"
    assert runner.envs[0]["ANTIGRAVITY_CLI_PROFILE"] == "default"


@pytest.mark.asyncio
async def test_antigravity_harness_does_not_settle_failed_cli_run(tmp_path) -> None:
    account = _account(
        account_id="agy-account",
        provider_id="antigravity",
        display_name="Antigravity",
        auth_mode="cli_keyring",
        api_key_encrypted=None,
    )
    routing = _RoutingService(account)
    runner = _AntigravityRunner(AntigravityProcessResult(exit_code=2, stdout="", stderr="failed", duration_ms=7))
    service = AntigravityHarnessService(routing, runner=runner)

    result = await service.print_prompt(
        AntigravityHarnessRequest(prompt="Say hi", workspace_path=str(tmp_path), timeout_seconds=5)
    )

    assert result.process.exit_code == 2
    assert routing.settlements == []


@pytest.mark.asyncio
async def test_antigravity_interaction_maps_steps_text_and_usage(monkeypatch) -> None:
    upstream = _Response(
        {
            "id": "interaction_1",
            "agent": "antigravity-preview-05-2026",
            "steps": [
                {
                    "type": "model_output",
                    "content": [
                        {"type": "text", "text": "Official "},
                        {"type": "text", "text": "shape"},
                    ],
                }
            ],
            "usage": {"total_input_tokens": 4, "total_output_tokens": 2, "total_tokens": 6},
        }
    )
    session = _Session(upstream)

    @asynccontextmanager
    async def _lease() -> AsyncIterator[_Session]:
        yield session

    monkeypatch.setattr(runtime_service_module, "lease_http_session", _lease)
    routing = _RoutingService(_account(account_id="agy-account", provider_id="antigravity"))
    service = AntigravityManagedAgentService(routing, decryptor=_Decryptor())

    response = await service.complete_chat(
        {"model": "antigravity-preview-05-2026", "messages": [{"role": "user", "content": "Do work"}]}
    )

    choices = cast(list[dict[str, Any]], response["choices"])
    message = cast(dict[str, Any], choices[0]["message"])
    assert message["content"] == "Official shape"
    assert session.calls[0]["headers"]["Api-Revision"] == "2026-05-20"
    assert routing.settlements == [
        (
            "antigravity",
            "agy-account",
            AgentProviderUsageSettlementData(requests=1, prompt_tokens=4, completion_tokens=2, total_tokens=6),
        )
    ]


@pytest.mark.asyncio
async def test_antigravity_interaction_finalizes_api_key_when_provider_settlement_fails(monkeypatch) -> None:
    upstream = _Response(
        {
            "id": "interaction_1",
            "output_text": "done",
            "usage": {"total_input_tokens": 4, "total_output_tokens": 2, "total_tokens": 6},
        }
    )
    session = _Session(upstream)

    @asynccontextmanager
    async def _lease() -> AsyncIterator[_Session]:
        yield session

    monkeypatch.setattr(runtime_service_module, "lease_http_session", _lease)
    api_key_usage = _ApiKeyUsageService()
    service = AntigravityManagedAgentService(
        _FailingSettlementRoutingService(_account(account_id="agy-account", provider_id="antigravity")),
        decryptor=_Decryptor(),
        api_key_service=api_key_usage,
    )
    context = AntigravityRuntimeRequestContext(
        api_key=ApiKeyData(
            id="key-1",
            name="Key",
            key_prefix="sk",
            allowed_models=None,
            enforced_model=None,
            enforced_reasoning_effort=None,
            enforced_service_tier=None,
            expires_at=None,
            is_active=True,
            created_at=datetime.now(timezone.utc),
            last_used_at=None,
        )
    )

    response = await service.create_interaction(
        {"agent": "antigravity-preview-05-2026", "input": "Do work", "environment": "remote"},
        context,
    )

    assert response["output_text"] == "done"
    assert api_key_usage.finalized == [("reservation-antigravity-preview-05-2026", 4, 2)]
    assert api_key_usage.released == []


def test_parse_chat_completion_request_validates_shape() -> None:
    with pytest.raises(GeminiRuntimeValidationError, match="model is required"):
        parse_chat_completion_request({"messages": [{"role": "user", "content": "Hi"}]})

    request = parse_chat_completion_request(
        {
            "model": "gemini-2.5-flash",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": True,
            "temperature": 0.2,
            "max_tokens": 8,
        }
    )

    assert request.model == "gemini-2.5-flash"
    assert request.stream is True
    assert request.temperature == 0.2
    assert request.max_tokens == 8


def test_parse_chat_completion_request_uses_max_completion_tokens_fallback() -> None:
    request = parse_chat_completion_request(
        {
            "model": "gemini-2.5-flash",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_completion_tokens": 32,
        }
    )

    assert request.max_tokens == 32


def test_parse_chat_completion_request_prefers_max_tokens_over_max_completion_tokens() -> None:
    request = parse_chat_completion_request(
        {
            "model": "gemini-2.5-flash",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 8,
            "max_completion_tokens": 32,
        }
    )

    assert request.max_tokens == 8


@pytest.mark.asyncio
async def test_complete_chat_selects_gemini_account_and_calls_native_endpoint(monkeypatch) -> None:
    upstream = _Response(
        {
            "responseId": "resp_1",
            "candidates": [{"content": {"parts": [{"text": "Hello"}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1, "totalTokenCount": 3},
        }
    )
    session = _Session(upstream)

    @asynccontextmanager
    async def _lease() -> AsyncIterator[_Session]:
        yield session

    monkeypatch.setattr(runtime_service_module, "lease_http_session", _lease)
    routing = _RoutingService(_account())
    service = GeminiRuntimeService(routing, decryptor=_Decryptor())

    response = await service.complete_chat(
        {"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Hi"}]}
    )

    assert routing.provider_ids == ["gemini"]
    assert routing.settlements == [
        (
            "gemini",
            "gemini-account",
            AgentProviderUsageSettlementData(requests=1, prompt_tokens=2, completion_tokens=1, total_tokens=3),
        )
    ]
    choices = cast(list[dict[str, Any]], response["choices"])
    message = cast(dict[str, Any], choices[0]["message"])
    assert message["content"] == "Hello"
    assert session.calls[0]["url"].endswith("/models/gemini-2.5-flash:generateContent")
    assert session.calls[0]["headers"]["x-goog-api-key"] == "AIza-test-key"
    assert session.calls[0]["json"] == {"contents": [{"role": "user", "parts": [{"text": "Hi"}]}]}


@pytest.mark.asyncio
async def test_complete_chat_finalizes_api_key_when_provider_settlement_fails(monkeypatch) -> None:
    upstream = _Response(
        {
            "responseId": "resp_1",
            "candidates": [{"content": {"parts": [{"text": "Hello"}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1, "totalTokenCount": 3},
        }
    )
    session = _Session(upstream)

    @asynccontextmanager
    async def _lease() -> AsyncIterator[_Session]:
        yield session

    monkeypatch.setattr(runtime_service_module, "lease_http_session", _lease)
    api_key_usage = _ApiKeyUsageService()
    service = GeminiRuntimeService(
        _FailingSettlementRoutingService(_account()),
        decryptor=_Decryptor(),
        api_key_service=api_key_usage,
    )
    context = GeminiRuntimeRequestContext(
        api_key=ApiKeyData(
            id="key-1",
            name="Key",
            key_prefix="sk",
            allowed_models=None,
            enforced_model=None,
            enforced_reasoning_effort=None,
            enforced_service_tier=None,
            expires_at=None,
            is_active=True,
            created_at=datetime.now(timezone.utc),
            last_used_at=None,
        )
    )

    response = await service.complete_chat(
        {"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Hi"}]},
        context,
    )

    choices = cast(list[dict[str, Any]], response["choices"])
    message = cast(dict[str, Any], choices[0]["message"])
    assert message["content"] == "Hello"
    assert api_key_usage.finalized == [("reservation-gemini-2.5-flash", 2, 1)]
    assert api_key_usage.released == []


@pytest.mark.asyncio
async def test_stream_chat_translates_gemini_sse_to_openai_sse(monkeypatch) -> None:
    upstream = _Response(
        chunks=[
            b'data: {"responseId":"resp_2","candidates":[{"content":{"parts":[{"text":"Hel"}]}}]}\r\n\r\n',
            (
                b'data: {"responseId":"resp_2","candidates":'
                b'[{"content":{"parts":[{"text":"lo"}]},"finishReason":"STOP"}]}\n\n'
            ),
        ]
    )
    session = _Session(upstream)

    @asynccontextmanager
    async def _lease() -> AsyncIterator[_Session]:
        yield session

    monkeypatch.setattr(runtime_service_module, "lease_http_session", _lease)
    routing = _RoutingService(_account())
    service = GeminiRuntimeService(routing, decryptor=_Decryptor())

    body = await service.stream_chat(
        {"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Hi"}], "stream": True}
    )
    chunks = [chunk async for chunk in body]

    assert session.calls[0]["url"].endswith("/models/gemini-2.5-flash:streamGenerateContent?alt=sse")
    assert '"object":"chat.completion.chunk"' in chunks[0]
    assert '"content":"Hel"' in chunks[0]
    assert '"content":"lo"' in chunks[1]
    assert chunks[-1] == "data: [DONE]\n\n"
    assert routing.settlements == [
        ("gemini", "gemini-account", AgentProviderUsageSettlementData(requests=1)),
    ]


@pytest.mark.asyncio
async def test_stream_chat_decodes_utf8_split_across_chunks(monkeypatch) -> None:
    text = "H" + chr(233)
    event = (
        'data: {"responseId":"resp_utf8","candidates":[{"content":{"parts":[{"text":"'
        + text
        + '"}]},"finishReason":"STOP"}]}\n\n'
    ).encode("utf-8")
    split_at = event.index(chr(233).encode("utf-8")) + 1
    upstream = _Response(chunks=[event[:split_at], event[split_at:]])
    session = _Session(upstream)

    @asynccontextmanager
    async def _lease() -> AsyncIterator[_Session]:
        yield session

    monkeypatch.setattr(runtime_service_module, "lease_http_session", _lease)
    service = GeminiRuntimeService(_RoutingService(_account()), decryptor=_Decryptor())

    body = await service.stream_chat(
        {"model": "gemini-3.5-flash", "messages": [{"role": "user", "content": "Hi"}], "stream": True}
    )
    chunks = [chunk async for chunk in body]

    assert "H\\u00e9" in chunks[0]


@pytest.mark.asyncio
async def test_stream_chat_settles_request_when_cancelled_after_account_selection(monkeypatch) -> None:
    session = _Session(_CancelledResponse())

    @asynccontextmanager
    async def _lease() -> AsyncIterator[_Session]:
        yield session

    monkeypatch.setattr(runtime_service_module, "lease_http_session", _lease)
    api_key_usage = _ApiKeyUsageService()
    routing = _RoutingService(_account())
    service = GeminiRuntimeService(routing, decryptor=_Decryptor(), api_key_service=api_key_usage)
    context = GeminiRuntimeRequestContext(
        api_key=ApiKeyData(
            id="key-1",
            name="Key",
            key_prefix="sk",
            allowed_models=None,
            enforced_model=None,
            enforced_reasoning_effort=None,
            enforced_service_tier=None,
            expires_at=None,
            is_active=True,
            created_at=datetime.now(timezone.utc),
            last_used_at=None,
        )
    )

    body = await service.stream_chat(
        {"model": "gemini-3.5-flash", "messages": [{"role": "user", "content": "Hi"}], "stream": True},
        context,
    )

    with pytest.raises(asyncio.CancelledError):
        async for _chunk in body:
            pass

    assert api_key_usage.released == ["reservation-gemini-3.5-flash"]
    assert api_key_usage.finalized == []
    assert routing.settlements == []


@pytest.mark.asyncio
async def test_stream_chat_settles_partial_usage_when_stream_fails_after_chunks(monkeypatch) -> None:
    session = _Session(_FailingAfterChunkResponse())

    @asynccontextmanager
    async def _lease() -> AsyncIterator[_Session]:
        yield session

    monkeypatch.setattr(runtime_service_module, "lease_http_session", _lease)
    api_key_usage = _ApiKeyUsageService()
    routing = _RoutingService(_account())
    service = GeminiRuntimeService(routing, decryptor=_Decryptor(), api_key_service=api_key_usage)
    context = GeminiRuntimeRequestContext(
        api_key=ApiKeyData(
            id="key-1",
            name="Key",
            key_prefix="sk",
            allowed_models=None,
            enforced_model=None,
            enforced_reasoning_effort=None,
            enforced_service_tier=None,
            expires_at=None,
            is_active=True,
            created_at=datetime.now(timezone.utc),
            last_used_at=None,
        )
    )
    body = await service.stream_chat(
        {"model": "gemini-3.5-flash", "messages": [{"role": "user", "content": "Hi"}], "stream": True},
        context,
    )

    chunks: list[str] = []
    with pytest.raises(RuntimeError, match="stream failed"):
        async for chunk in body:
            chunks.append(chunk)

    assert chunks
    assert api_key_usage.released == []
    assert api_key_usage.finalized == [("reservation-gemini-3.5-flash", 2, 1)]
    assert routing.settlements == [
        (
            "gemini",
            "gemini-account",
            AgentProviderUsageSettlementData(requests=1, prompt_tokens=2, completion_tokens=1, total_tokens=3),
        )
    ]
