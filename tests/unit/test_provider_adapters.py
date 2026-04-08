from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import cast

import pytest

import app.modules.proxy.provider_adapters as provider_adapters_module
from app.core.crypto import TokenEncryptor
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.core.utils.request_id import reset_request_id, set_request_id
from app.db.models import Account, AccountStatus
from app.modules.proxy.provider_adapters import (
    ChatGPTWebProviderAdapter,
    OpenAIPlatformProviderAdapter,
    ProviderSubject,
    RequestCapabilities,
)
from app.modules.proxy.repo_bundle import ProxyRepositories
from app.modules.upstream_identities.types import OPENAI_PLATFORM_PROVIDER_KIND, PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY

pytestmark = pytest.mark.unit


def _compact_request() -> ResponsesCompactRequest:
    return ResponsesCompactRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "summarize",
            "input": "hello",
        }
    )


def _responses_request() -> ResponsesRequest:
    return ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": "hi",
        }
    )


def _account(account_id: str = "acc_test") -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        chatgpt_account_id=f"workspace-{account_id}",
        email=f"{account_id}@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=datetime.now(tz=timezone.utc),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


@pytest.mark.asyncio
async def test_chatgpt_adapter_ensure_ready_delegates_to_auth_manager(monkeypatch) -> None:
    refreshed = _account("acc_refreshed")

    class DummyAuthManager:
        def __init__(self, repo) -> None:
            self.repo = repo

        async def ensure_fresh(self, account: Account, *, force: bool = False) -> Account:
            assert account.id == "acc_test"
            assert force is True
            return refreshed

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[ProxyRepositories]:
        yield cast(ProxyRepositories, SimpleNamespace(accounts=object()))

    monkeypatch.setattr(provider_adapters_module, "AuthManager", DummyAuthManager)

    adapter = ChatGPTWebProviderAdapter(repo_factory)
    result = await adapter.ensure_ready(
        ProviderSubject(
            provider_kind="chatgpt_web",
            routing_subject_id="acc_test",
            account=_account(),
        ),
        force=True,
    )

    assert result.account is refreshed


@pytest.mark.asyncio
async def test_chatgpt_adapter_refresh_usage_delegates_to_usage_updater(monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    class DummyUsageUpdater:
        def __init__(self, usage_repo, accounts_repo, additional_usage_repo) -> None:
            del usage_repo, accounts_repo, additional_usage_repo

        async def refresh_accounts(self, accounts, latest_usage) -> None:
            calls.append(([account.id for account in accounts], latest_usage))

    async def latest_by_account(window: str = "primary"):
        assert window == "primary"
        return {"acc_test": object()}

    repos = cast(
        ProxyRepositories,
        SimpleNamespace(
            usage=SimpleNamespace(latest_by_account=latest_by_account),
            accounts=object(),
            additional_usage=object(),
        ),
    )
    monkeypatch.setattr(provider_adapters_module, "UsageUpdater", DummyUsageUpdater)

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[ProxyRepositories]:
        yield cast(ProxyRepositories, SimpleNamespace(accounts=object()))

    adapter = ChatGPTWebProviderAdapter(repo_factory)
    await adapter.refresh_usage(
        repos,
        [ProviderSubject(provider_kind="chatgpt_web", routing_subject_id="acc_test", account=_account())],
    )

    assert calls
    assert calls[0][0] == ["acc_test"]


@pytest.mark.asyncio
async def test_chatgpt_adapter_compact_response_delegates_to_core_client(monkeypatch) -> None:
    async def fake_compact(payload, headers, access_token, account_id):
        assert payload.model == "gpt-5.1"
        assert headers == {"x-test": "1"}
        assert access_token == "access"
        assert account_id == "workspace-acc_test"
        return "compact-result"

    monkeypatch.setattr(provider_adapters_module, "core_compact_responses", fake_compact)

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[ProxyRepositories]:
        yield cast(ProxyRepositories, SimpleNamespace(accounts=object()))

    adapter = ChatGPTWebProviderAdapter(repo_factory)
    result = await adapter.compact_response(
        ProviderSubject(provider_kind="chatgpt_web", routing_subject_id="acc_test", account=_account()),
        _compact_request(),
        {"x-test": "1"},
    )

    assert result == "compact-result"


@pytest.mark.asyncio
async def test_chatgpt_adapter_stream_response_events_delegates_to_core_client(monkeypatch) -> None:
    async def fake_stream(
        payload,
        headers,
        access_token,
        account_id,
        *,
        raise_for_status,
        upstream_stream_transport_override,
    ):
        assert payload.model == "gpt-5.1"
        assert headers == {"x-test": "1"}
        assert access_token == "access"
        assert account_id == "workspace-acc_test"
        assert raise_for_status is True
        assert upstream_stream_transport_override == "http"
        yield "data: first"
        yield "data: second"

    monkeypatch.setattr(provider_adapters_module, "core_stream_responses", fake_stream)

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[ProxyRepositories]:
        yield cast(ProxyRepositories, SimpleNamespace(accounts=object()))

    adapter = ChatGPTWebProviderAdapter(repo_factory)
    stream = await adapter.stream_response_events(
        ProviderSubject(provider_kind="chatgpt_web", routing_subject_id="acc_test", account=_account()),
        _responses_request(),
        {"x-test": "1"},
        raise_for_status=True,
        upstream_stream_transport="http",
    )

    assert [line async for line in stream] == ["data: first", "data: second"]


@pytest.mark.asyncio
async def test_chatgpt_adapter_transcribe_audio_delegates_to_core_client(monkeypatch) -> None:
    async def fake_transcribe(
        audio_bytes,
        *,
        filename,
        content_type,
        prompt,
        headers,
        access_token,
        account_id,
    ):
        assert audio_bytes == b"audio"
        assert filename == "sample.wav"
        assert content_type == "audio/wav"
        assert prompt == "summarize"
        assert headers == {"x-test": "1"}
        assert access_token == "access"
        assert account_id == "workspace-acc_test"
        return {"text": "done"}

    monkeypatch.setattr(provider_adapters_module, "core_transcribe_audio", fake_transcribe)

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[ProxyRepositories]:
        yield cast(ProxyRepositories, SimpleNamespace(accounts=object()))

    adapter = ChatGPTWebProviderAdapter(repo_factory)
    result = await adapter.transcribe_audio(
        ProviderSubject(provider_kind="chatgpt_web", routing_subject_id="acc_test", account=_account()),
        audio_bytes=b"audio",
        filename="sample.wav",
        content_type="audio/wav",
        prompt="summarize",
        headers={"x-test": "1"},
    )

    assert result == {"text": "done"}


@pytest.mark.asyncio
async def test_chatgpt_adapter_open_responses_websocket_delegates_to_transport(monkeypatch) -> None:
    expected_socket = object()

    async def fake_connect(headers, access_token, account_id):
        assert headers == {"x-test": "1"}
        assert access_token == "access"
        assert account_id == "workspace-acc_test"
        return expected_socket

    monkeypatch.setattr(provider_adapters_module, "connect_responses_websocket", fake_connect)

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[ProxyRepositories]:
        yield cast(ProxyRepositories, SimpleNamespace(accounts=object()))

    adapter = ChatGPTWebProviderAdapter(repo_factory)
    result = await adapter.open_responses_websocket(
        ProviderSubject(provider_kind="chatgpt_web", routing_subject_id="acc_test", account=_account()),
        {"x-test": "1"},
    )

    assert result is expected_socket


def test_platform_adapter_rejects_websocket_capability() -> None:
    adapter = OpenAIPlatformProviderAdapter()
    decision = adapter.check_capabilities(
        ProviderSubject(provider_kind=OPENAI_PLATFORM_PROVIDER_KIND, routing_subject_id="plat_1"),
        RequestCapabilities(
            route_family=PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY,
            route_class="openai_public_ws",
            transport="websocket",
            model="gpt-5.1",
        ),
    )

    assert decision.allowed is False
    assert decision.error_code == "provider_transport_unsupported"
    assert decision.error_param == "transport"


def test_platform_adapter_rejects_continuity_capability() -> None:
    adapter = OpenAIPlatformProviderAdapter()
    decision = adapter.check_capabilities(
        ProviderSubject(provider_kind=OPENAI_PLATFORM_PROVIDER_KIND, routing_subject_id="plat_1"),
        RequestCapabilities(
            route_family=PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY,
            route_class="openai_public_http",
            transport="http",
            model="gpt-5.1",
            continuity_param="previous_response_id",
        ),
    )

    assert decision.allowed is False
    assert decision.error_code == "provider_continuity_unsupported"
    assert decision.error_param == "previous_response_id"


@pytest.mark.asyncio
async def test_platform_adapter_fetch_models_delegates_to_core_client(monkeypatch, caplog) -> None:
    async def fake_fetch_models(*, base_url, api_key, organization=None, project=None):
        assert base_url == "https://api.openai.com"
        assert api_key == "sk-platform"
        assert organization == "org_test"
        assert project == "proj_test"
        return SimpleNamespace(
            payload={"object": "list", "data": [{"id": "gpt-5.1"}]},
            upstream_request_id="up_req_models",
        )

    monkeypatch.setattr(provider_adapters_module, "fetch_platform_models", fake_fetch_models)
    monkeypatch.setattr(
        provider_adapters_module,
        "get_settings",
        lambda: SimpleNamespace(log_upstream_request_summary=True),
    )

    adapter = OpenAIPlatformProviderAdapter()
    token = set_request_id("req_platform_adapter_1")
    try:
        caplog.set_level(logging.INFO)
        result = await adapter.fetch_models(
            ProviderSubject(
                provider_kind=OPENAI_PLATFORM_PROVIDER_KIND,
                routing_subject_id="plat_1",
                api_key_encrypted=TokenEncryptor().encrypt("sk-platform"),
                organization_id="org_test",
                project_id="proj_test",
            )
        )
    finally:
        reset_request_id(token)

    assert result.payload["object"] == "list"
    assert result.upstream_request_id == "up_req_models"
    assert "upstream_request_start request_id=req_platform_adapter_1" in caplog.text
    assert "provider_kind=openai_platform" in caplog.text
    assert "route_class=openai_public_http" in caplog.text
    assert "routing_subject_id=plat_1" in caplog.text
    assert "upstream_request_id=up_req_models" in caplog.text
