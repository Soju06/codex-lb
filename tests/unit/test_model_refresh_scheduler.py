from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

import app.core.clients.model_fetcher as model_fetcher_module
import app.core.openai.model_refresh_scheduler as scheduler_module
from app.core.openai.model_registry import ReasoningLevel, UpstreamModel

pytestmark = pytest.mark.unit


def _account() -> SimpleNamespace:
    return SimpleNamespace(
        id="account-1",
        plan_type="team",
        chatgpt_account_id="chatgpt-account-1",
        access_token_encrypted=b"encrypted-access-token",
    )


def _model(slug: str) -> UpstreamModel:
    return UpstreamModel(
        slug=slug,
        display_name=slug,
        description=f"Model {slug}",
        context_window=128000,
        input_modalities=("text",),
        supported_reasoning_levels=(ReasoningLevel(effort="medium", description="balanced"),),
        default_reasoning_level="medium",
        supports_reasoning_summaries=False,
        support_verbosity=False,
        default_verbosity=None,
        prefer_websockets=False,
        supports_parallel_tool_calls=True,
        supported_in_api=True,
        minimal_client_version=None,
        priority=0,
        available_in_plans=frozenset(),
        raw={},
    )


class _StubAuthManager:
    def __init__(self, _repo: object) -> None:
        pass

    async def ensure_fresh(self, account: SimpleNamespace, *, force: bool = False) -> SimpleNamespace:
        return account


@pytest.mark.asyncio
async def test_fetch_models_for_plan_marks_transport_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    session = MagicMock()
    session.get.side_effect = aiohttp.ClientError("dns failed")

    monkeypatch.setattr(
        model_fetcher_module,
        "get_codex_version_cache",
        lambda: SimpleNamespace(get_version=AsyncMock(return_value="1.2.3")),
    )
    monkeypatch.setattr(
        model_fetcher_module,
        "get_http_client",
        lambda: SimpleNamespace(session=session),
    )
    monkeypatch.setattr(
        model_fetcher_module,
        "get_settings",
        lambda: SimpleNamespace(upstream_base_url="https://example.test/backend-api"),
    )

    with pytest.raises(model_fetcher_module.ModelFetchError) as excinfo:
        await model_fetcher_module.fetch_models_for_plan("access-token", "account-1")

    exc = excinfo.value
    assert exc.status_code == 0
    assert exc.transport_error is True
    assert "dns failed" in exc.message


@pytest.mark.asyncio
async def test_fetch_with_failover_refreshes_http_client_after_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = _account()
    encryptor = MagicMock()
    encryptor.decrypt.return_value = "access-token"
    expected_models = [_model("gpt-5.4")]

    fetch_models_for_plan = AsyncMock(
        side_effect=[
            scheduler_module.ModelFetchError(0, "temporary dns failure", transport_error=True),
            expected_models,
        ]
    )
    refresh_http_client = AsyncMock()

    monkeypatch.setattr(scheduler_module, "AuthManager", _StubAuthManager)
    monkeypatch.setattr(scheduler_module, "fetch_models_for_plan", fetch_models_for_plan)
    monkeypatch.setattr(scheduler_module, "refresh_http_client", refresh_http_client)

    result = await scheduler_module._fetch_with_failover([account], encryptor, MagicMock())

    assert result == expected_models
    refresh_http_client.assert_awaited_once()
    assert fetch_models_for_plan.await_count == 2
    assert encryptor.decrypt.call_count == 2
