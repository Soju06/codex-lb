from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import app.core.auth.dependencies as auth_dependencies
import app.core.middleware.api_firewall as api_firewall_module
from app.core.auth.api_key_cache import get_api_key_cache
from app.core.crypto import TokenEncryptor
from app.core.middleware.api_firewall import add_api_firewall_middleware
from app.core.middleware.firewall_cache import get_firewall_ip_cache
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.proxy.load_balancer import LoadBalancer
from app.modules.proxy.repo_bundle import ProxyRepoFactory

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_hot_path_caches() -> None:
    get_api_key_cache().clear()
    get_firewall_ip_cache().invalidate_all()
    get_account_selection_cache().invalidate()


@pytest.mark.asyncio
async def test_api_key_validation_uses_cache_for_repeated_key(monkeypatch: pytest.MonkeyPatch) -> None:
    api_key_data = ApiKeyData(
        id="key_1",
        name="hot-path",
        key_prefix="sk-clb-test",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        expires_at=None,
        is_active=True,
        created_at=datetime.now(UTC),
        last_used_at=None,
    )
    calls = 0

    class _SettingsCache:
        async def get(self) -> SimpleNamespace:
            return SimpleNamespace(api_key_auth_enabled=True)

    class _Service:
        def __init__(self, _repo: object) -> None:
            pass

        async def validate_key(self, _token: str) -> ApiKeyData:
            nonlocal calls
            calls += 1
            return api_key_data

    @asynccontextmanager
    async def _fake_session() -> AsyncIterator[object]:
        yield object()

    monkeypatch.setattr(auth_dependencies, "get_settings_cache", lambda: _SettingsCache())
    monkeypatch.setattr(auth_dependencies, "get_background_session", _fake_session)
    monkeypatch.setattr(auth_dependencies, "ApiKeysRepository", lambda _session: object())
    monkeypatch.setattr(auth_dependencies, "ApiKeysService", _Service)

    token = "sk-clb-hot-path"
    expected_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    for _ in range(10):
        resolved = await auth_dependencies.validate_proxy_api_key_authorization(f"Bearer {token}")
        assert resolved == api_key_data

    assert calls == 1
    cache = get_api_key_cache()
    assert expected_hash in cache._cache
    assert token not in cache._cache


@pytest.mark.asyncio
async def test_firewall_middleware_uses_cache_for_repeated_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    class _Service:
        def __init__(self, _repo: object) -> None:
            pass

        async def is_ip_allowed(self, _ip: str | None) -> bool:
            nonlocal calls
            calls += 1
            return True

    @asynccontextmanager
    async def _fake_session() -> AsyncIterator[object]:
        yield object()

    monkeypatch.setattr(
        api_firewall_module,
        "get_settings",
        lambda: SimpleNamespace(firewall_trusted_proxy_cidrs=[], firewall_trust_proxy_headers=False),
    )
    monkeypatch.setattr(api_firewall_module, "get_background_session", _fake_session)
    monkeypatch.setattr(api_firewall_module, "FirewallRepository", lambda _session: object())
    monkeypatch.setattr(api_firewall_module, "FirewallService", _Service)

    app = FastAPI()
    add_api_firewall_middleware(app)

    @app.get("/v1/test")
    async def _v1_test() -> dict[str, str]:
        return {"ok": "true"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        for _ in range(10):
            response = await client.get("/v1/test")
            assert response.status_code == 200

    assert calls == 1


@pytest.mark.asyncio
async def test_account_selection_cache_reuses_inputs_and_invalidates_on_refresh() -> None:
    encryptor = TokenEncryptor()
    now = datetime.now(UTC)
    now_epoch = int(now.timestamp())
    account = Account(
        id="acc-hot-path",
        chatgpt_account_id="workspace-acc-hot-path",
        email="hot-path@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=now,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    primary = UsageHistory(
        id=1,
        account_id=account.id,
        recorded_at=now,
        window="primary",
        used_percent=15.0,
        reset_at=now_epoch + 300,
        window_minutes=5,
    )
    secondary = UsageHistory(
        id=2,
        account_id=account.id,
        recorded_at=now,
        window="secondary",
        used_percent=25.0,
        reset_at=now_epoch + 1800,
        window_minutes=30,
    )

    class _AccountsRepo:
        def __init__(self) -> None:
            self.calls = 0

        async def list_accounts(self) -> list[Account]:
            self.calls += 1
            return [account]

    class _UsageRepo:
        def __init__(self) -> None:
            self.primary_calls = 0
            self.secondary_calls = 0

        async def latest_by_account(self, window: str | None = None) -> dict[str, UsageHistory]:
            if window == "secondary":
                self.secondary_calls += 1
                return {account.id: secondary}
            self.primary_calls += 1
            return {account.id: primary}

    class _Repos:
        def __init__(self, accounts_repo: _AccountsRepo, usage_repo: _UsageRepo) -> None:
            self.accounts = accounts_repo
            self.usage = usage_repo

        async def __aenter__(self) -> "_Repos":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    accounts_repo = _AccountsRepo()
    usage_repo = _UsageRepo()

    def _repo_factory() -> _Repos:
        return _Repos(accounts_repo, usage_repo)

    # Create cache with explicit TTL > 0 (not the global singleton which may be disabled in tests)
    from app.modules.proxy.account_cache import AccountSelectionCache

    cache = AccountSelectionCache(ttl_seconds=5)
    balancer = LoadBalancer(cast(ProxyRepoFactory, _repo_factory))
    balancer._selection_inputs_cache = cache  # Override with test-specific cache

    for _ in range(10):
        inputs = await balancer._load_selection_inputs(model=None)
        assert len(inputs.accounts) == 1
        assert inputs.accounts[0].id == account.id

    assert accounts_repo.calls == 1
    assert usage_repo.primary_calls == 1
    assert usage_repo.secondary_calls == 1

    cache.invalidate()
    refreshed = await balancer._load_selection_inputs(model=None)
    assert len(refreshed.accounts) == 1
    assert refreshed.accounts[0].id == account.id
    assert accounts_repo.calls == 2
    assert usage_repo.primary_calls == 2
    assert usage_repo.secondary_calls == 2
