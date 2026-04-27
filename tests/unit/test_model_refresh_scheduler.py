from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.core.openai.model_refresh_scheduler as scheduler_module

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def clear_model_refresh_cooldowns() -> None:
    scheduler_module._model_refresh_auth_cooldowns.clear()


def test_model_refresh_auth_cooldown_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        scheduler_module,
        "get_settings",
        lambda: SimpleNamespace(model_registry_refresh_auth_failure_cooldown_seconds=30.0),
    )
    monkeypatch.setattr(scheduler_module.time, "monotonic", lambda: 100.0)

    scheduler_module._mark_model_refresh_auth_cooldown("acc_1", 403)
    assert scheduler_module._model_refresh_auth_cooldown_active("acc_1") is True

    monkeypatch.setattr(scheduler_module.time, "monotonic", lambda: 131.0)
    assert scheduler_module._model_refresh_auth_cooldown_active("acc_1") is False


@pytest.mark.asyncio
async def test_fetch_with_failover_skips_all_accounts_in_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        scheduler_module,
        "get_settings",
        lambda: SimpleNamespace(model_registry_refresh_auth_failure_cooldown_seconds=30.0),
    )
    monkeypatch.setattr(scheduler_module.time, "monotonic", lambda: 100.0)
    scheduler_module._model_refresh_auth_cooldowns["acc_cooldown"] = 120.0

    account = SimpleNamespace(
        id="acc_cooldown",
        plan_type="pro",
        access_token_encrypted=b"encrypted",
        chatgpt_account_id="workspace-1",
    )
    encryptor = SimpleNamespace(decrypt=lambda _: "token")

    result = await scheduler_module._fetch_with_failover([account], encryptor, object())

    assert result.models is None
    assert result.attempted is False
    assert result.cooled_down_only is True


@pytest.mark.asyncio
async def test_fetch_with_failover_skips_cooled_down_account_and_uses_next_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _AuthManager:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def ensure_fresh(self, account, force: bool = False):
            return account

    async def fake_fetch_models_for_plan(access_token: str, account_id: str | None):
        assert access_token == "token"
        assert account_id == "workspace-b"
        return [SimpleNamespace(slug="gpt-5.4")]

    monkeypatch.setattr(
        scheduler_module,
        "get_settings",
        lambda: SimpleNamespace(model_registry_refresh_auth_failure_cooldown_seconds=30.0),
    )
    monkeypatch.setattr(scheduler_module.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(scheduler_module, "AuthManager", _AuthManager)
    monkeypatch.setattr(scheduler_module, "fetch_models_for_plan", fake_fetch_models_for_plan)
    scheduler_module._model_refresh_auth_cooldowns["acc_a"] = 140.0

    account_a = SimpleNamespace(
        id="acc_a",
        plan_type="pro",
        access_token_encrypted=b"a",
        chatgpt_account_id="workspace-a",
    )
    account_b = SimpleNamespace(
        id="acc_b",
        plan_type="pro",
        access_token_encrypted=b"b",
        chatgpt_account_id="workspace-b",
    )
    encryptor = SimpleNamespace(decrypt=lambda _: "token")

    result = await scheduler_module._fetch_with_failover([account_a, account_b], encryptor, object())

    assert result.attempted is True
    assert result.cooled_down_only is False
    assert result.models is not None
    assert result.models[0].slug == "gpt-5.4"
