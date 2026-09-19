from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import cast

import pytest

from app.core.auth.refresh import RefreshError, TokenRefreshResult
from app.core.crypto import TokenEncryptor
from app.core.upstream_proxy import UpstreamProxyRouteError
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.modules.accounts import auth_manager as auth_manager_module
from app.modules.accounts.auth_manager import AccountsRepositoryPort, AuthManager

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_refresh_state() -> None:
    auth_manager_module._clear_refresh_singleflight_state()


class _DummyRepo:
    def __init__(self) -> None:
        self.tokens_payload: dict[str, object] | None = None
        self.status_payload: dict[str, object] | None = None
        self.accounts_by_id: dict[str, Account] = {}
        self.taken_workspace_slots: set[tuple[str, str | None, str]] = set()
        self.get_calls = 0
        self.reload_calls = 0
        self.tokens_update_result = True

    async def get_by_id(self, account_id: str) -> Account | None:
        self.get_calls += 1
        return self.accounts_by_id.get(account_id)

    async def reload_by_id(self, account_id: str) -> Account | None:
        self.reload_calls += 1
        return self.accounts_by_id.get(account_id)

    async def update_status(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
        blocked_at: int | None = None,
        *,
        expected_refresh_token_encrypted: bytes | None = None,
    ) -> bool:
        del expected_refresh_token_encrypted
        self.status_payload = {
            "account_id": account_id,
            "status": status,
            "deactivation_reason": deactivation_reason,
        }
        return True

    async def update_tokens(
        self,
        account_id: str,
        access_token_encrypted: bytes,
        refresh_token_encrypted: bytes,
        id_token_encrypted: bytes | None,
        last_refresh: datetime,
        plan_type: str | None = None,
        email: str | None = None,
        chatgpt_account_id: str | None = None,
        workspace_id: str | None = None,
        workspace_label: str | None = None,
        seat_type: str | None = None,
        *,
        expected_refresh_token_encrypted: bytes | None = None,
    ) -> bool:
        self.tokens_payload = {
            "account_id": account_id,
            "access_token_encrypted": access_token_encrypted,
            "refresh_token_encrypted": refresh_token_encrypted,
            "id_token_encrypted": id_token_encrypted,
            "last_refresh": last_refresh,
            "plan_type": plan_type,
            "email": email,
            "chatgpt_account_id": chatgpt_account_id,
            "workspace_id": workspace_id,
            "workspace_label": workspace_label,
            "seat_type": seat_type,
            "expected_refresh_token_encrypted": expected_refresh_token_encrypted,
        }
        return self.tokens_update_result

    async def workspace_slot_taken(
        self,
        *,
        account_id: str,
        email: str,
        chatgpt_account_id: str | None,
        workspace_id: str,
    ) -> bool:
        del account_id
        return (email, chatgpt_account_id, workspace_id) in self.taken_workspace_slots


@pytest.mark.asyncio
async def test_ensure_fresh_detached_refresh_owns_session_on_caller_cancel(monkeypatch):
    """Regression: a client disconnect during a forced token refresh must not
    strand a background-pool connection. The shielded refresh task must write
    via its OWN session (from refresh_repo_factory), never the request-scoped
    repo that the cancelled caller closes. Pre-fix this leaked one pooled
    connection per disconnect-during-refresh (agent-lb pool-exhaustion spiral).
    """
    started = asyncio.Event()
    release = asyncio.Event()

    async def _fake_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        started.set()
        await release.wait()
        return TokenRefreshResult(
            access_token="new-access",
            refresh_token="new-refresh",
            id_token="new-id",
            account_id="acc_disconnect",
            plan_type="plus",
            email=None,
        )

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _fake_refresh)

    request_repo = _DummyRepo()
    owned_repo = _DummyRepo()
    scope_state = {"opened": False, "closed": False}

    @asynccontextmanager
    async def _refresh_scope() -> AsyncIterator[AccountsRepositoryPort]:
        scope_state["opened"] = True
        try:
            yield cast(AccountsRepositoryPort, owned_repo)
        finally:
            scope_state["closed"] = True

    encryptor = TokenEncryptor()
    account = Account(
        id="acc_disconnect",
        email="user@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-old"),
        refresh_token_encrypted=encryptor.encrypt("refresh-old"),
        id_token_encrypted=encryptor.encrypt("id-old"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    manager = AuthManager(
        cast(AccountsRepositoryPort, request_repo),
        refresh_repo_factory=_refresh_scope,
    )

    caller = asyncio.create_task(manager.ensure_fresh(account, force=True))
    await started.wait()  # refresh is in-flight
    caller.cancel()  # simulate the client disconnecting mid-refresh
    with pytest.raises(asyncio.CancelledError):
        await caller

    # The shielded refresh task survives the caller's cancellation; let it finish.
    release.set()
    for _ in range(200):
        if owned_repo.tokens_payload is not None and scope_state["closed"]:
            break
        await asyncio.sleep(0.005)

    # The refresh wrote through its OWN session and never the request-scoped one.
    assert owned_repo.tokens_payload is not None
    assert owned_repo.tokens_payload["account_id"] == "acc_disconnect"
    assert request_repo.tokens_payload is None
    # The owned session was opened and deterministically closed (connection returned).
    assert scope_state["opened"] is True
    assert scope_state["closed"] is True


@pytest.mark.asyncio
async def test_refresh_account_preserves_plan_type_when_missing(monkeypatch):
    async def _fake_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        return TokenRefreshResult(
            access_token="new-access",
            refresh_token="new-refresh",
            id_token="new-id",
            account_id="acc_1",
            plan_type=None,
            email=None,
        )

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _fake_refresh)

    encryptor = TokenEncryptor()
    account = Account(
        id="acc_1",
        email="user@example.com",
        plan_type="pro",
        access_token_encrypted=encryptor.encrypt("access-old"),
        refresh_token_encrypted=encryptor.encrypt("refresh-old"),
        id_token_encrypted=encryptor.encrypt("id-old"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    repo = _DummyRepo()
    manager = AuthManager(cast(AccountsRepositoryPort, repo))

    updated = await manager.refresh_account(account)

    assert updated.plan_type == "pro"
    assert repo.tokens_payload is not None
    assert repo.tokens_payload["plan_type"] == "pro"


@pytest.mark.asyncio
async def test_refresh_account_does_not_overwrite_workspace_fields_when_already_set(monkeypatch):
    async def _fake_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        return TokenRefreshResult(
            access_token="new-access",
            refresh_token="new-refresh",
            id_token="new-id",
            account_id="acc_1",
            plan_type="pro",
            email="refreshed@example.com",
            workspace_id="ws_new",
            workspace_label="New Workspace",
            seat_type="pro",
        )

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _fake_refresh)

    encryptor = TokenEncryptor()
    account = Account(
        id="acc_1",
        email="user@example.com",
        plan_type="pro",
        workspace_id="ws_old",
        workspace_label="Old Workspace",
        seat_type="legacy",
        access_token_encrypted=encryptor.encrypt("access-old"),
        refresh_token_encrypted=encryptor.encrypt("refresh-old"),
        id_token_encrypted=encryptor.encrypt("id-old"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    repo = _DummyRepo()
    manager = AuthManager(cast(AccountsRepositoryPort, repo))

    updated = await manager.refresh_account(account)

    assert updated.workspace_id == "ws_old"
    assert updated.workspace_label == "Old Workspace"
    assert updated.seat_type == "legacy"
    assert repo.tokens_payload is not None
    assert repo.tokens_payload["workspace_id"] == "ws_old"
    assert repo.tokens_payload["workspace_label"] == "Old Workspace"
    assert repo.tokens_payload["seat_type"] == "legacy"


@pytest.mark.asyncio
async def test_refresh_account_updates_same_workspace_display_metadata(monkeypatch):
    async def _fake_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        return TokenRefreshResult(
            access_token="new-access",
            refresh_token="new-refresh",
            id_token="new-id",
            account_id="acc_1",
            plan_type="pro",
            email="refreshed@example.com",
            workspace_id="ws_same",
            workspace_label="Renamed Workspace",
            seat_type="business",
        )

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _fake_refresh)

    encryptor = TokenEncryptor()
    account = Account(
        id="acc_1",
        email="user@example.com",
        plan_type="pro",
        workspace_id="ws_same",
        workspace_label="Old Workspace",
        seat_type="legacy",
        access_token_encrypted=encryptor.encrypt("access-old"),
        refresh_token_encrypted=encryptor.encrypt("refresh-old"),
        id_token_encrypted=encryptor.encrypt("id-old"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    repo = _DummyRepo()
    manager = AuthManager(cast(AccountsRepositoryPort, repo))

    updated = await manager.refresh_account(account)

    assert updated.workspace_id == "ws_same"
    assert updated.workspace_label == "Renamed Workspace"
    assert updated.seat_type == "business"
    assert repo.tokens_payload is not None
    assert repo.tokens_payload["workspace_id"] == "ws_same"
    assert repo.tokens_payload["workspace_label"] == "Renamed Workspace"
    assert repo.tokens_payload["seat_type"] == "business"


@pytest.mark.asyncio
async def test_refresh_account_populates_workspace_when_missing(monkeypatch):
    async def _fake_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        return TokenRefreshResult(
            access_token="new-access",
            refresh_token="new-refresh",
            id_token="new-id",
            account_id="acc_2",
            plan_type="pro",
            email="refreshed@example.com",
            workspace_id="ws_new",
            workspace_label="New Workspace",
            seat_type="pro",
        )

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _fake_refresh)

    encryptor = TokenEncryptor()
    account = Account(
        id="acc_2",
        email="user@example.com",
        plan_type="pro",
        workspace_id=None,
        workspace_label=None,
        seat_type=None,
        access_token_encrypted=encryptor.encrypt("access-old"),
        refresh_token_encrypted=encryptor.encrypt("refresh-old"),
        id_token_encrypted=encryptor.encrypt("id-old"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    repo = _DummyRepo()
    manager = AuthManager(cast(AccountsRepositoryPort, repo))

    updated = await manager.refresh_account(account)

    assert updated.workspace_id == "ws_new"
    assert updated.workspace_label == "New Workspace"
    assert updated.seat_type == "pro"
    assert repo.tokens_payload is not None
    assert repo.tokens_payload["workspace_id"] == "ws_new"
    assert repo.tokens_payload["workspace_label"] == "New Workspace"
    assert repo.tokens_payload["seat_type"] == "pro"


@pytest.mark.asyncio
async def test_refresh_account_does_not_promote_unknown_workspace_into_taken_slot(monkeypatch):
    async def _fake_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        return TokenRefreshResult(
            access_token="new-access",
            refresh_token="new-refresh",
            id_token="new-id",
            account_id="chatgpt_shared",
            plan_type="team",
            email="shared@example.com",
            workspace_id="ws_taken",
            workspace_label="Taken Workspace",
            seat_type="business",
        )

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _fake_refresh)

    encryptor = TokenEncryptor()
    account = Account(
        id="acc_unknown_slot",
        email="shared@example.com",
        chatgpt_account_id="chatgpt_shared",
        plan_type="plus",
        workspace_id=None,
        workspace_label=None,
        seat_type=None,
        access_token_encrypted=encryptor.encrypt("access-old"),
        refresh_token_encrypted=encryptor.encrypt("refresh-old"),
        id_token_encrypted=encryptor.encrypt("id-old"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    repo = _DummyRepo()
    repo.taken_workspace_slots.add(("shared@example.com", "chatgpt_shared", "ws_taken"))
    manager = AuthManager(cast(AccountsRepositoryPort, repo))

    updated = await manager.refresh_account(account)

    assert updated.workspace_id is None
    assert updated.workspace_label is None
    assert updated.seat_type is None
    assert repo.tokens_payload is not None
    assert repo.tokens_payload["workspace_id"] is None
    assert repo.tokens_payload["workspace_label"] is None
    assert repo.tokens_payload["seat_type"] is None


@pytest.mark.asyncio
async def test_refresh_account_converts_upstream_route_failure_to_refresh_error(monkeypatch):
    @asynccontextmanager
    async def fake_background_session() -> AsyncIterator[object]:
        yield object()

    async def fail_resolve_route(*_args: object, **_kwargs: object) -> None:
        raise UpstreamProxyRouteError("pool_unavailable", account_id="acc_route")

    async def unexpected_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        raise AssertionError("refresh_access_token should not run when route resolution fails")

    monkeypatch.setattr(auth_manager_module, "get_background_session", fake_background_session)
    monkeypatch.setattr(auth_manager_module, "resolve_upstream_route", fail_resolve_route)
    monkeypatch.setattr(auth_manager_module, "refresh_access_token", unexpected_refresh)

    encryptor = TokenEncryptor()
    account = Account(
        id="acc_route",
        email="user@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-old"),
        refresh_token_encrypted=encryptor.encrypt("refresh-old"),
        id_token_encrypted=encryptor.encrypt("id-old"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    repo = _DummyRepo()
    manager = AuthManager(cast(AccountsRepositoryPort, repo))

    with pytest.raises(RefreshError) as exc_info:
        await manager.refresh_account(account)

    assert exc_info.value.code == "upstream_proxy_unavailable"
    assert exc_info.value.message == "Upstream proxy route unavailable: pool_unavailable"
    assert exc_info.value.is_permanent is False
    assert exc_info.value.transport_error is True
    assert exc_info.value.upstream_proxy_fail_closed_reason == "pool_unavailable"
    assert repo.status_payload is None
    assert repo.tokens_payload is None


@pytest.mark.asyncio
async def test_refresh_account_uses_non_openai_provider_without_id_token(monkeypatch):
    refresh_calls = 0

    async def _fake_refresh(_: str) -> TokenRefreshResult:
        nonlocal refresh_calls
        refresh_calls += 1
        return TokenRefreshResult(
            access_token="new-anthropic-access",
            refresh_token="new-anthropic-refresh",
            id_token=None,
            account_id="anthropic_acc",
            plan_type="max",
            email=None,
        )

    fake_provider = SimpleNamespace(
        name="anthropic",
        requires_id_token=False,
        refresh_access_token=_fake_refresh,
    )
    monkeypatch.setattr(auth_manager_module, "get_provider", lambda _: fake_provider)

    encryptor = TokenEncryptor()
    account = Account(
        id="anthropic_acc",
        provider="anthropic",
        email="claude@example.com",
        plan_type="max",
        access_token_encrypted=encryptor.encrypt("opaque-access"),
        refresh_token_encrypted=encryptor.encrypt("opaque-refresh"),
        id_token_encrypted=None,
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    repo = _DummyRepo()
    manager = AuthManager(cast(AccountsRepositoryPort, repo))

    refreshed = await manager.refresh_account(account)

    assert encryptor.decrypt(refreshed.access_token_encrypted) == "new-anthropic-access"
    assert refresh_calls == 1
    assert repo.tokens_payload is not None
    assert repo.tokens_payload["id_token_encrypted"] is None


@pytest.mark.asyncio
async def test_refresh_account_anthropic_provider_invalid_grant_requires_reauth(monkeypatch):
    async def _fake_anthropic_refresh(
        refresh_token: str,
        **_kwargs: object,
    ) -> TokenRefreshResult:
        assert refresh_token == "anthropic-refresh-old"
        raise RefreshError("invalid_grant", "Refresh token not found or invalid", False)

    monkeypatch.setattr(
        "app.core.providers.anthropic.refresh_anthropic_access_token",
        _fake_anthropic_refresh,
    )

    encryptor = TokenEncryptor()
    stale_refresh = utcnow().replace(year=utcnow().year - 1)
    account = Account(
        id="anthropic_invalid_grant",
        provider="anthropic",
        email="claude@example.com",
        plan_type="max",
        access_token_encrypted=encryptor.encrypt("anthropic-access-old"),
        refresh_token_encrypted=encryptor.encrypt("anthropic-refresh-old"),
        id_token_encrypted=None,
        last_refresh=stale_refresh,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    repo = _DummyRepo()
    latest_account = Account(**{column.name: getattr(account, column.name) for column in Account.__table__.columns})
    repo.accounts_by_id[account.id] = latest_account
    manager = AuthManager(cast(AccountsRepositoryPort, repo))

    with pytest.raises(RefreshError) as exc_info:
        await manager.refresh_account(account)

    assert exc_info.value.code == "invalid_grant"
    assert exc_info.value.is_permanent is True
    assert repo.status_payload is not None
    assert repo.status_payload["status"] == AccountStatus.REAUTH_REQUIRED
    assert repo.status_payload["deactivation_reason"] == "Refresh token grant invalid - re-login required"


@pytest.mark.asyncio
async def test_ensure_fresh_singleflights_concurrent_refreshes(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()
    refresh_calls = 0

    async def _fake_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        nonlocal refresh_calls
        refresh_calls += 1
        started.set()
        await release.wait()
        return TokenRefreshResult(
            access_token="new-access",
            refresh_token="new-refresh",
            id_token="new-id",
            account_id="acc_sf",
            plan_type="plus",
            email=None,
        )

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _fake_refresh)

    encryptor = TokenEncryptor()
    stale_refresh = utcnow().replace(year=utcnow().year - 1)
    account_a = Account(
        id="acc_sf",
        email="user@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-old"),
        refresh_token_encrypted=encryptor.encrypt("refresh-old"),
        id_token_encrypted=encryptor.encrypt("id-old"),
        last_refresh=stale_refresh,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    account_b = Account(**{column.name: getattr(account_a, column.name) for column in Account.__table__.columns})
    repo = _DummyRepo()
    manager = AuthManager(cast(AccountsRepositoryPort, repo))

    first = asyncio.create_task(manager.ensure_fresh(account_a, force=True))
    await started.wait()
    second = asyncio.create_task(manager.ensure_fresh(account_b, force=True))
    await asyncio.sleep(0.01)
    assert not second.done()

    release.set()
    await asyncio.gather(first, second)

    assert refresh_calls == 1


@pytest.mark.asyncio
async def test_ensure_fresh_singleflights_refresh_admission_for_same_account(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()
    refresh_calls = 0
    admission_calls = 0

    async def _fake_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        nonlocal refresh_calls
        refresh_calls += 1
        started.set()
        await release.wait()
        return TokenRefreshResult(
            access_token="new-access",
            refresh_token="new-refresh",
            id_token="new-id",
            account_id="acc_sf_admission",
            plan_type="plus",
            email=None,
        )

    async def _acquire_refresh_admission():
        nonlocal admission_calls
        admission_calls += 1
        return SimpleNamespace(release=lambda: None)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _fake_refresh)

    encryptor = TokenEncryptor()
    stale_refresh = utcnow().replace(year=utcnow().year - 1)
    account_a = Account(
        id="acc_sf_admission",
        email="user@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-old"),
        refresh_token_encrypted=encryptor.encrypt("refresh-old"),
        id_token_encrypted=encryptor.encrypt("id-old"),
        last_refresh=stale_refresh,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    account_b = Account(**{column.name: getattr(account_a, column.name) for column in Account.__table__.columns})
    repo = _DummyRepo()
    manager = AuthManager(
        cast(AccountsRepositoryPort, repo),
        acquire_refresh_admission=_acquire_refresh_admission,
    )

    first = asyncio.create_task(manager.ensure_fresh(account_a, force=True))
    await started.wait()
    second = asyncio.create_task(manager.ensure_fresh(account_b, force=True))
    await asyncio.sleep(0.01)
    assert not second.done()

    release.set()
    await asyncio.gather(first, second)

    assert refresh_calls == 1
    assert admission_calls == 1


@pytest.mark.asyncio
async def test_ensure_fresh_reuses_recent_failure_without_reissuing_refresh(monkeypatch):
    refresh_calls = 0

    async def _fake_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        nonlocal refresh_calls
        refresh_calls += 1
        raise RefreshError("invalid_grant", "refresh failed", False)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _fake_refresh)
    monkeypatch.setattr(
        auth_manager_module,
        "get_settings",
        lambda: SimpleNamespace(proxy_refresh_failure_cooldown_seconds=30.0),
    )

    encryptor = TokenEncryptor()
    stale_refresh = utcnow().replace(year=utcnow().year - 1)
    account = Account(
        id="acc_fail_cache",
        email="user@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-old"),
        refresh_token_encrypted=encryptor.encrypt("refresh-old"),
        id_token_encrypted=encryptor.encrypt("id-old"),
        last_refresh=stale_refresh,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    repo = _DummyRepo()
    manager = AuthManager(cast(AccountsRepositoryPort, repo))

    with pytest.raises(RefreshError):
        await manager.ensure_fresh(account, force=True)
    with pytest.raises(RefreshError):
        await manager.ensure_fresh(account, force=True)

    assert refresh_calls == 1


@pytest.mark.asyncio
async def test_ensure_fresh_does_not_reuse_recent_transport_failure(monkeypatch):
    refresh_calls = 0

    async def _fake_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        nonlocal refresh_calls
        refresh_calls += 1
        raise RefreshError("transport_error", "temporary dns failure", False, transport_error=True)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _fake_refresh)
    monkeypatch.setattr(
        auth_manager_module,
        "get_settings",
        lambda: SimpleNamespace(proxy_refresh_failure_cooldown_seconds=30.0),
    )

    encryptor = TokenEncryptor()
    stale_refresh = utcnow().replace(year=utcnow().year - 1)
    account = Account(
        id="acc_transport_fail_cache",
        email="user@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-old"),
        refresh_token_encrypted=encryptor.encrypt("refresh-old"),
        id_token_encrypted=encryptor.encrypt("id-old"),
        last_refresh=stale_refresh,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    repo = _DummyRepo()
    manager = AuthManager(cast(AccountsRepositoryPort, repo))

    with pytest.raises(RefreshError):
        await manager.ensure_fresh(account, force=True)
    await asyncio.sleep(0)
    with pytest.raises(RefreshError):
        await manager.ensure_fresh(account, force=True)

    assert refresh_calls == 2


@pytest.mark.asyncio
async def test_ensure_fresh_does_not_reuse_failure_after_refresh_token_changes(monkeypatch):
    refresh_calls = 0

    async def _fake_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        nonlocal refresh_calls
        refresh_calls += 1
        raise RefreshError("invalid_grant", f"refresh failed for {refresh_token}", False)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _fake_refresh)
    monkeypatch.setattr(
        auth_manager_module,
        "get_settings",
        lambda: SimpleNamespace(proxy_refresh_failure_cooldown_seconds=30.0),
    )

    encryptor = TokenEncryptor()
    stale_refresh = utcnow().replace(year=utcnow().year - 1)
    account = Account(
        id="acc_fail_cache_versioned",
        email="user@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-old"),
        refresh_token_encrypted=encryptor.encrypt("refresh-old"),
        id_token_encrypted=encryptor.encrypt("id-old"),
        last_refresh=stale_refresh,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    repo = _DummyRepo()
    manager = AuthManager(cast(AccountsRepositoryPort, repo))

    with pytest.raises(RefreshError):
        await manager.ensure_fresh(account, force=True)

    account.refresh_token_encrypted = encryptor.encrypt("refresh-new")

    with pytest.raises(RefreshError) as exc_info:
        await manager.ensure_fresh(account, force=True)

    assert exc_info.value.message == "refresh failed for refresh-new"
    assert refresh_calls == 2


@pytest.mark.asyncio
async def test_refresh_account_does_not_deactivate_when_repo_has_newer_refresh_token(monkeypatch):
    async def _fake_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        pytest.fail("must not exchange an already rotated refresh token")

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _fake_refresh)

    encryptor = TokenEncryptor()
    stale_refresh = utcnow().replace(year=utcnow().year - 1)
    stale_account = Account(
        id="acc_stale_snapshot",
        email="user@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-old"),
        refresh_token_encrypted=encryptor.encrypt("refresh-old"),
        id_token_encrypted=encryptor.encrypt("id-old"),
        last_refresh=stale_refresh,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    repo = _DummyRepo()
    latest_account = Account(
        **{column.name: getattr(stale_account, column.name) for column in Account.__table__.columns}
    )
    latest_account.refresh_token_encrypted = encryptor.encrypt("refresh-new")
    repo.accounts_by_id[stale_account.id] = latest_account
    manager = AuthManager(cast(AccountsRepositoryPort, repo))

    result = await manager.refresh_account(stale_account)

    assert result is latest_account
    assert repo.status_payload is None
    assert stale_account.status == AccountStatus.ACTIVE
    assert repo.reload_calls == 1
    assert repo.get_calls == 0


@pytest.mark.asyncio
async def test_refresh_account_preserves_token_rotated_after_permanent_failure_reload(monkeypatch):
    async def _fake_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        raise RefreshError("invalid_grant", "refresh failed", True)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _fake_refresh)

    encryptor = TokenEncryptor()
    stale_account = Account(
        id="acc_status_cas_loser",
        email="user@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-old"),
        refresh_token_encrypted=encryptor.encrypt("refresh-old"),
        id_token_encrypted=encryptor.encrypt("id-old"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    same_version = Account(
        **{column.name: getattr(stale_account, column.name) for column in Account.__table__.columns}
    )
    winning_account = Account(
        **{column.name: getattr(stale_account, column.name) for column in Account.__table__.columns}
    )
    winning_account.refresh_token_encrypted = encryptor.encrypt("refresh-winning")

    class _StatusRaceRepo(_DummyRepo):
        async def update_status(
            self,
            account_id: str,
            status: AccountStatus,
            deactivation_reason: str | None = None,
            reset_at: int | None = None,
            blocked_at: int | None = None,
            *,
            expected_refresh_token_encrypted: bytes | None = None,
        ) -> bool:
            del status, deactivation_reason, reset_at, blocked_at, expected_refresh_token_encrypted
            self.accounts_by_id[account_id] = winning_account
            return False

    repo = _StatusRaceRepo()
    repo.accounts_by_id[stale_account.id] = same_version
    manager = AuthManager(cast(AccountsRepositoryPort, repo))

    result = await manager.refresh_account(stale_account)

    assert result is winning_account
    assert result.status == AccountStatus.ACTIVE
    assert encryptor.decrypt(result.refresh_token_encrypted) == "refresh-winning"
    assert repo.reload_calls == 3


@pytest.mark.asyncio
async def test_refresh_account_preserves_newer_tokens_when_conditional_write_loses(monkeypatch):
    async def _fake_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        repo.accounts_by_id[account.id] = latest_account
        return TokenRefreshResult(
            access_token="losing-access",
            refresh_token="losing-refresh",
            id_token="losing-id",
            account_id="acc_cas_loser",
            plan_type="plus",
            email=None,
        )

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _fake_refresh)

    encryptor = TokenEncryptor()
    account = Account(
        id="acc_cas_loser",
        email="user@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-old"),
        refresh_token_encrypted=encryptor.encrypt("refresh-old"),
        id_token_encrypted=encryptor.encrypt("id-old"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    expected_refresh_token_encrypted = account.refresh_token_encrypted
    latest_account = Account(
        **{column.name: getattr(account, column.name) for column in Account.__table__.columns}
    )
    latest_account.access_token_encrypted = encryptor.encrypt("winning-access")
    latest_account.refresh_token_encrypted = encryptor.encrypt("winning-refresh")

    repo = _DummyRepo()
    repo.tokens_update_result = False
    manager = AuthManager(cast(AccountsRepositoryPort, repo))

    result = await manager.refresh_account(account)

    assert result is latest_account
    assert encryptor.decrypt(result.refresh_token_encrypted) == "winning-refresh"
    assert repo.tokens_payload is not None
    assert repo.tokens_payload["expected_refresh_token_encrypted"] == expected_refresh_token_encrypted
    assert repo.reload_calls == 2


@pytest.mark.asyncio
async def test_refresh_account_deactivates_when_repo_only_reencrypted_same_refresh_token(monkeypatch):
    async def _fake_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        raise RefreshError("invalid_grant", "refresh failed", True)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _fake_refresh)

    encryptor = TokenEncryptor()
    stale_refresh = utcnow().replace(year=utcnow().year - 1)
    stale_account = Account(
        id="acc_same_token_reencrypted",
        email="user@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-old"),
        refresh_token_encrypted=encryptor.encrypt("refresh-same"),
        id_token_encrypted=encryptor.encrypt("id-old"),
        last_refresh=stale_refresh,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    repo = _DummyRepo()
    latest_account = Account(
        **{column.name: getattr(stale_account, column.name) for column in Account.__table__.columns}
    )
    latest_account.refresh_token_encrypted = encryptor.encrypt("refresh-same")
    repo.accounts_by_id[stale_account.id] = latest_account
    manager = AuthManager(cast(AccountsRepositoryPort, repo))

    with pytest.raises(RefreshError) as exc_info:
        await manager.refresh_account(stale_account)

    assert exc_info.value.is_permanent is True
    assert repo.status_payload is not None
    assert repo.status_payload["status"] == AccountStatus.REAUTH_REQUIRED


@pytest.mark.asyncio
async def test_refresh_account_requires_reauth_when_upstream_returns_token_expired(monkeypatch):
    """Regression for #383: a ``token_expired`` code from the OAuth refresh
    endpoint must classify as a permanent failure and block the account,
    not loop retries forever while the account stays ``ACTIVE``.
    """

    async def _fake_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        # Real upstream-observed shape: HTTP 4xx body whose error code is
        # ``token_expired`` and message is the user-facing "Provided
        # authentication token is expired" wording. classify_refresh_error
        # must surface this as ``is_permanent=True``.
        from app.core.auth.refresh import classify_refresh_error

        assert classify_refresh_error("token_expired") is True
        raise RefreshError(
            "token_expired",
            "Provided authentication token is expired. Please try signing in again.",
            classify_refresh_error("token_expired"),
        )

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _fake_refresh)

    encryptor = TokenEncryptor()
    stale_refresh = utcnow().replace(year=utcnow().year - 1)
    expired_account = Account(
        id="acc_token_expired",
        email="user@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-old"),
        refresh_token_encrypted=encryptor.encrypt("refresh-old"),
        id_token_encrypted=encryptor.encrypt("id-old"),
        last_refresh=stale_refresh,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    repo = _DummyRepo()
    latest_account = Account(
        **{column.name: getattr(expired_account, column.name) for column in Account.__table__.columns}
    )
    repo.accounts_by_id[expired_account.id] = latest_account
    manager = AuthManager(cast(AccountsRepositoryPort, repo))

    with pytest.raises(RefreshError) as exc_info:
        await manager.refresh_account(expired_account)

    assert exc_info.value.code == "token_expired"
    assert exc_info.value.is_permanent is True
    assert repo.status_payload is not None
    assert repo.status_payload["status"] == AccountStatus.REAUTH_REQUIRED
    reason = repo.status_payload["deactivation_reason"]
    assert isinstance(reason, str)
    assert "re-login" in reason.lower() or "expired" in reason.lower()


@pytest.mark.asyncio
async def test_refresh_account_uses_canonical_reason_for_prefixed_invalid_grant(monkeypatch):
    async def _fake_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        raise RefreshError(
            "auth_refresh_invalid_grant",
            "Refresh token not found or invalid",
            False,
        )

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _fake_refresh)

    encryptor = TokenEncryptor()
    stale_refresh = utcnow().replace(year=utcnow().year - 1)
    expired_account = Account(
        id="acc_prefixed_invalid_grant",
        email="user@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-old"),
        refresh_token_encrypted=encryptor.encrypt("refresh-old"),
        id_token_encrypted=encryptor.encrypt("id-old"),
        last_refresh=stale_refresh,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    repo = _DummyRepo()
    latest_account = Account(
        **{column.name: getattr(expired_account, column.name) for column in Account.__table__.columns}
    )
    repo.accounts_by_id[expired_account.id] = latest_account
    manager = AuthManager(cast(AccountsRepositoryPort, repo))

    with pytest.raises(RefreshError) as exc_info:
        await manager.refresh_account(expired_account)

    assert exc_info.value.code == "auth_refresh_invalid_grant"
    assert exc_info.value.is_permanent is True
    assert repo.status_payload is not None
    assert repo.status_payload["status"] == AccountStatus.REAUTH_REQUIRED
    assert repo.status_payload["deactivation_reason"] == "Refresh token grant invalid - re-login required"


@pytest.mark.asyncio
async def test_refresh_account_rejects_unknown_provider_without_openai_refresh(monkeypatch):
    async def _fake_refresh(_: str) -> TokenRefreshResult:
        raise AssertionError("OpenAI refresh should not run for an unknown provider")

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _fake_refresh)

    encryptor = TokenEncryptor()
    account = Account(
        id="acc_unknown_provider",
        provider="unknown",
        email="user@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-old"),
        refresh_token_encrypted=encryptor.encrypt("refresh-old"),
        id_token_encrypted=encryptor.encrypt("id-old"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    repo = _DummyRepo()
    manager = AuthManager(cast(AccountsRepositoryPort, repo))

    with pytest.raises(RefreshError) as exc_info:
        await manager.refresh_account(account)

    assert exc_info.value.code == "unsupported_provider"
    assert exc_info.value.is_permanent is True
    assert repo.tokens_payload is None
