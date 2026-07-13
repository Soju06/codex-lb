from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import cast

import pytest

from app.core.auth.refresh import (
    RefreshError,
    TokenRefreshResult,
    pop_token_refresh_timeout_override,
    push_token_refresh_timeout_override,
)
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

    async def get_by_id(self, account_id: str) -> Account | None:
        return self.accounts_by_id.get(account_id)

    async def get_by_id_fresh(self, account_id: str) -> Account | None:
        return self.accounts_by_id.get(account_id)

    async def update_status(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
        blocked_at: int | None = None,
    ) -> bool:
        self.status_payload = {
            "account_id": account_id,
            "status": status,
            "deactivation_reason": deactivation_reason,
        }
        return True

    async def update_status_if_current(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
        *,
        expected_status: AccountStatus,
        expected_deactivation_reason: str | None = None,
        expected_reset_at: int | None = None,
        expected_refresh_token_encrypted: bytes | None = None,
    ) -> bool:
        latest = self.accounts_by_id.get(account_id)
        if latest is not None and (
            latest.status != expected_status
            or latest.deactivation_reason != expected_deactivation_reason
            or latest.reset_at != expected_reset_at
            or (
                expected_refresh_token_encrypted is not None
                and latest.refresh_token_encrypted != expected_refresh_token_encrypted
            )
        ):
            return False
        self.status_payload = {
            "account_id": account_id,
            "status": status,
            "deactivation_reason": deactivation_reason,
            "expected_refresh_token_encrypted": expected_refresh_token_encrypted,
        }
        return True

    async def update_tokens(
        self,
        account_id: str,
        access_token_encrypted: bytes,
        refresh_token_encrypted: bytes,
        id_token_encrypted: bytes,
        last_refresh: datetime,
        plan_type: str | None = None,
        email: str | None = None,
        chatgpt_account_id: str | None = None,
        chatgpt_user_id: str | None = None,
        workspace_id: str | None = None,
        workspace_label: str | None = None,
        seat_type: str | None = None,
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
            "chatgpt_user_id": chatgpt_user_id,
            "workspace_id": workspace_id,
            "workspace_label": workspace_label,
            "seat_type": seat_type,
            "expected_refresh_token_encrypted": expected_refresh_token_encrypted,
        }
        return True

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
    connection per disconnect-during-refresh (codex-lb pool-exhaustion spiral).
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
async def test_ensure_fresh_singleflight_coalesces_owned_and_nonowned_sessions(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()
    refresh_calls = 0
    scope_state = {"opened": False, "closed": False}

    async def _fake_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        nonlocal refresh_calls
        refresh_calls += 1
        started.set()
        await release.wait()
        return TokenRefreshResult(
            access_token="new-access",
            refresh_token="new-refresh",
            id_token="new-id",
            account_id="acc_sf_owner",
            plan_type="plus",
            email=None,
        )

    @asynccontextmanager
    async def _refresh_scope() -> AsyncIterator[AccountsRepositoryPort]:
        scope_state["opened"] = True
        try:
            yield cast(AccountsRepositoryPort, owned_repo)
        finally:
            scope_state["closed"] = True

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _fake_refresh)

    encryptor = TokenEncryptor()
    stale_refresh = utcnow().replace(year=utcnow().year - 1)
    account_payload = dict(
        id="acc_sf_owner",
        email="user@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-old"),
        refresh_token_encrypted=encryptor.encrypt("refresh-old"),
        id_token_encrypted=encryptor.encrypt("id-old"),
        last_refresh=stale_refresh,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    request_account = Account(**account_payload)
    owned_account = Account(**account_payload)
    request_repo = _DummyRepo()
    owned_repo = _DummyRepo()

    nonowned_manager = AuthManager(cast(AccountsRepositoryPort, request_repo))
    owned_manager = AuthManager(
        cast(AccountsRepositoryPort, request_repo),
        refresh_repo_factory=_refresh_scope,
    )

    nonowned_task = asyncio.create_task(nonowned_manager.ensure_fresh(request_account, force=True))
    await started.wait()
    owned_task = asyncio.create_task(owned_manager.ensure_fresh(owned_account, force=True))
    await asyncio.sleep(0.01)

    assert not owned_task.done()

    release.set()
    await asyncio.gather(nonowned_task, owned_task)

    assert refresh_calls == 1
    assert request_repo.tokens_payload is not None
    assert request_repo.tokens_payload["account_id"] == "acc_sf_owner"
    assert owned_repo.tokens_payload is None
    assert scope_state == {"opened": False, "closed": False}


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
        raise RefreshError("invalid_grant", "refresh failed", True)

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

    # The caller's object adopts the newer rotation instead of being handed the
    # repo-session-bound row (which would expire once that session closes).
    assert result is stale_account
    assert result.refresh_token_encrypted == latest_account.refresh_token_encrypted
    assert repo.status_payload is None
    assert stale_account.status == AccountStatus.ACTIVE


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
    # The downgrade CAS is conditioned on the freshly observed ciphertext, not
    # the (re-encrypted) material this attempt exchanged.
    assert repo.status_payload["expected_refresh_token_encrypted"] == latest_account.refresh_token_encrypted


class _TokenCasMissRepo(_DummyRepo):
    """Repo whose token compare-and-set only matches the *current* stored
    ciphertext, so a stale ``expected`` misses. ``get_by_id_fresh`` returns the
    row currently persisted so callers can re-read and retry against it."""

    def __init__(self, latest: Account) -> None:
        super().__init__()
        self._latest = latest
        self.accounts_by_id[latest.id] = latest
        self.update_attempts: list[bytes | None] = []

    async def get_by_id_fresh(self, account_id: str) -> Account | None:
        return self.accounts_by_id.get(account_id)

    async def update_tokens(
        self,
        account_id: str,
        access_token_encrypted: bytes,
        refresh_token_encrypted: bytes,
        id_token_encrypted: bytes,
        last_refresh: datetime,
        plan_type: str | None = None,
        email: str | None = None,
        chatgpt_account_id: str | None = None,
        chatgpt_user_id: str | None = None,
        workspace_id: str | None = None,
        workspace_label: str | None = None,
        seat_type: str | None = None,
        expected_refresh_token_encrypted: bytes | None = None,
    ) -> bool:
        self.update_attempts.append(expected_refresh_token_encrypted)
        stored = self._latest.refresh_token_encrypted
        if expected_refresh_token_encrypted is not None and expected_refresh_token_encrypted != stored:
            return False
        self._latest.refresh_token_encrypted = refresh_token_encrypted
        return await super().update_tokens(
            account_id,
            access_token_encrypted=access_token_encrypted,
            refresh_token_encrypted=refresh_token_encrypted,
            id_token_encrypted=id_token_encrypted,
            last_refresh=last_refresh,
            plan_type=plan_type,
            email=email,
            chatgpt_account_id=chatgpt_account_id,
            chatgpt_user_id=chatgpt_user_id,
            workspace_id=workspace_id,
            workspace_label=workspace_label,
            seat_type=seat_type,
            expected_refresh_token_encrypted=expected_refresh_token_encrypted,
        )


@pytest.mark.asyncio
async def test_refresh_persists_new_tokens_when_cas_misses_on_reencrypted_same_material(monkeypatch):
    """Regression: a successful refresh must not adopt a compare-and-set loser
    just because the stored ciphertext changed. A concurrent re-auth/import can
    re-encrypt the SAME refresh-token plaintext (Fernet is non-deterministic),
    which misses the CAS without any newer rotation. Adopting that row would
    discard the single-use token this attempt just exchanged and leave the
    account holding the already-consumed one. The refresh must retry the CAS
    against the observed ciphertext so its own rotation wins."""

    async def _fake_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        return TokenRefreshResult(
            access_token="access-new",
            refresh_token="refresh-new",
            id_token="id-new",
            account_id="acc_cas_reencrypt",
            plan_type="pro",
            email=None,
        )

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _fake_refresh)

    encryptor = TokenEncryptor()
    account = Account(
        id="acc_cas_reencrypt",
        email="user@example.com",
        plan_type="pro",
        access_token_encrypted=encryptor.encrypt("access-old"),
        refresh_token_encrypted=encryptor.encrypt("refresh-old"),
        id_token_encrypted=encryptor.encrypt("id-old"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    original_ciphertext = account.refresh_token_encrypted
    # The stored row holds the SAME plaintext re-encrypted to different bytes.
    reencrypted_same = encryptor.encrypt("refresh-old")
    assert reencrypted_same != original_ciphertext
    latest = Account(**{column.name: getattr(account, column.name) for column in Account.__table__.columns})
    latest.refresh_token_encrypted = reencrypted_same
    repo = _TokenCasMissRepo(latest)
    manager = AuthManager(cast(AccountsRepositoryPort, repo))

    result = await manager.refresh_account(account)

    # Our freshly issued single-use token wins; the re-encrypted old token is
    # never adopted.
    assert encryptor.decrypt(result.refresh_token_encrypted) == "refresh-new"
    assert repo.tokens_payload is not None
    assert encryptor.decrypt(cast(bytes, repo.tokens_payload["refresh_token_encrypted"])) == "refresh-new"
    # First attempt used the stale (pre-race) ciphertext and missed; the retry
    # used the freshly observed ciphertext and won.
    assert repo.update_attempts == [original_ciphertext, reencrypted_same]
    assert result.status == AccountStatus.ACTIVE


@pytest.mark.asyncio
async def test_refresh_adopts_peer_rotation_when_cas_misses_on_new_material(monkeypatch):
    """A compare-and-set miss caused by a genuinely newer refresh-token rotation
    from a peer must be adopted (never clobbered) and must not persist this
    attempt's now-consumed token."""

    async def _fake_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        return TokenRefreshResult(
            access_token="access-new",
            refresh_token="refresh-new",
            id_token="id-new",
            account_id="acc_cas_peer_rotation",
            plan_type="pro",
            email=None,
        )

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _fake_refresh)

    encryptor = TokenEncryptor()
    account = Account(
        id="acc_cas_peer_rotation",
        email="user@example.com",
        plan_type="pro",
        access_token_encrypted=encryptor.encrypt("access-old"),
        refresh_token_encrypted=encryptor.encrypt("refresh-old"),
        id_token_encrypted=encryptor.encrypt("id-old"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    original_ciphertext = account.refresh_token_encrypted
    # A peer committed a DIFFERENT refresh-token plaintext.
    peer_rotated = encryptor.encrypt("refresh-peer")
    latest = Account(**{column.name: getattr(account, column.name) for column in Account.__table__.columns})
    latest.refresh_token_encrypted = peer_rotated
    repo = _TokenCasMissRepo(latest)
    manager = AuthManager(cast(AccountsRepositoryPort, repo))

    result = await manager.refresh_account(account)

    # The peer's rotation is adopted; our exchanged token is not written.
    assert result is account
    assert encryptor.decrypt(result.refresh_token_encrypted) == "refresh-peer"
    assert repo.tokens_payload is None
    # Only the initial CAS ran; no retry once a real rotation was detected.
    assert repo.update_attempts == [original_ciphertext]


class _TokenCasAlwaysMissRepo(_DummyRepo):
    """Repo that never lets the token compare-and-set land: every
    ``get_by_id_fresh`` returns a row whose refresh-token ciphertext is a fresh
    re-encryption of the SAME plaintext (Fernet is non-deterministic), so the
    fingerprint never changes but the observed ciphertext keeps shifting under
    the writer, and ``update_tokens`` never matches ``expected``."""

    def __init__(self, account: Account, *, plaintext: str, encryptor: TokenEncryptor) -> None:
        super().__init__()
        self._plaintext = plaintext
        self._encryptor = encryptor
        self._row = account
        self.accounts_by_id[account.id] = account
        self.update_attempts: list[bytes | None] = []

    async def get_by_id_fresh(self, account_id: str) -> Account | None:
        row = self.accounts_by_id.get(account_id)
        if row is not None:
            # Re-encrypt the same plaintext to a fresh ciphertext each read.
            row.refresh_token_encrypted = self._encryptor.encrypt(self._plaintext)
        return row

    async def update_tokens(
        self,
        account_id: str,
        access_token_encrypted: bytes,
        refresh_token_encrypted: bytes,
        id_token_encrypted: bytes,
        last_refresh: datetime,
        plan_type: str | None = None,
        email: str | None = None,
        chatgpt_account_id: str | None = None,
        chatgpt_user_id: str | None = None,
        workspace_id: str | None = None,
        workspace_label: str | None = None,
        seat_type: str | None = None,
        expected_refresh_token_encrypted: bytes | None = None,
    ) -> bool:
        # The CAS always misses: the stored ciphertext has already been rotated
        # (re-encrypted) out from under this ``expected`` value.
        self.update_attempts.append(expected_refresh_token_encrypted)
        return False


@pytest.mark.asyncio
async def test_refresh_surfaces_transient_error_when_token_cas_never_persists(monkeypatch):
    """Regression: when the token compare-and-set keeps missing on same-plaintext
    re-encryption until ``_TOKEN_CAS_MAX_ATTEMPTS`` is exhausted, the rotated
    tokens were never persisted (the DB still holds the already-consumed
    single-use token). Returning ``None`` here would be the success sentinel, so
    the caller would release the refresh claim and treat the account as fresh
    while the DB retained dead material — the next request would then hit a
    permanent refresh-token-reuse failure. The refresh must instead raise a
    transient ``RefreshError`` so the caller retries rather than proceeding with
    unpersisted material."""

    async def _fake_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        return TokenRefreshResult(
            access_token="access-new",
            refresh_token="refresh-new",
            id_token="id-new",
            account_id="acc_cas_exhausted",
            plan_type="pro",
            email=None,
        )

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _fake_refresh)

    encryptor = TokenEncryptor()
    account = Account(
        id="acc_cas_exhausted",
        email="user@example.com",
        plan_type="pro",
        access_token_encrypted=encryptor.encrypt("access-old"),
        refresh_token_encrypted=encryptor.encrypt("refresh-old"),
        id_token_encrypted=encryptor.encrypt("id-old"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    repo = _TokenCasAlwaysMissRepo(account, plaintext="refresh-old", encryptor=encryptor)
    manager = AuthManager(cast(AccountsRepositoryPort, repo))

    with pytest.raises(RefreshError) as excinfo:
        await manager.refresh_account(account)

    # Transient (retryable) failure, never a permanent one that would be cached
    # or de-route the account.
    assert excinfo.value.transport_error is True
    assert excinfo.value.is_permanent is False
    assert excinfo.value.code == "token_persist_conflict"
    # The CAS was attempted the bounded number of times and never landed.
    assert len(repo.update_attempts) == auth_manager_module._TOKEN_CAS_MAX_ATTEMPTS
    assert repo.tokens_payload is None
    # The in-memory account was NOT mutated to advertise unpersisted material.
    assert encryptor.decrypt(account.refresh_token_encrypted) == "refresh-old"


@pytest.mark.asyncio
async def test_permanent_failure_status_cas_loses_to_rotation_after_fresh_read(monkeypatch):
    """Regression: a concurrent re-auth/import rotates the refresh token AFTER
    the permanent-failure guard's fresh re-read but BEFORE its status CAS. The
    stale REAUTH_REQUIRED write must lose (the CAS now also carries the
    expected refresh-token ciphertext), leaving the repaired account alone."""

    async def _fake_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        raise RefreshError("invalid_grant", "refresh failed", True)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _fake_refresh)

    encryptor = TokenEncryptor()
    stale_refresh = utcnow().replace(year=utcnow().year - 1)
    account = Account(
        id="acc_cas_race_window",
        email="user@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-old"),
        refresh_token_encrypted=encryptor.encrypt("refresh-old"),
        id_token_encrypted=encryptor.encrypt("id-old"),
        last_refresh=stale_refresh,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )

    class _RotatingAfterFreshReadRepo(_DummyRepo):
        async def get_by_id_fresh(self, account_id: str) -> Account | None:
            latest = self.accounts_by_id.get(account_id)
            if latest is None:
                return None
            snapshot = Account(**{column.name: getattr(latest, column.name) for column in Account.__table__.columns})
            # Concurrent re-auth commits a rotation in the window between this
            # fresh read and the status CAS (status/reason/reset untouched).
            latest.refresh_token_encrypted = encryptor.encrypt("refresh-rotated")
            return snapshot

    repo = _RotatingAfterFreshReadRepo()
    latest_account = Account(**{column.name: getattr(account, column.name) for column in Account.__table__.columns})
    repo.accounts_by_id[account.id] = latest_account
    manager = AuthManager(cast(AccountsRepositoryPort, repo))

    with pytest.raises(RefreshError) as exc_info:
        await manager.refresh_account(account)

    assert exc_info.value.code == "invalid_grant"
    assert repo.status_payload is None
    assert account.status == AccountStatus.ACTIVE


@pytest.mark.asyncio
async def test_claim_wait_is_capped_by_caller_refresh_budget(monkeypatch):
    """Regression: the shielded singleflight body outlives a cancelled caller,
    so a foreign refresh claim must not keep it polling for the full
    ``token_refresh_claim_wait_seconds`` (8s default) when the caller's
    remaining request budget is far smaller."""

    class _ForeignClaims:
        claimant_id = "this-replica"

        async def try_acquire(self, account_id: str, *, ttl_seconds: float, owner: str) -> bool:
            del account_id, ttl_seconds, owner
            return False

        async def release(self, account_id: str, *, owner: str) -> None:
            del account_id, owner

    async def _unexpected_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        raise AssertionError("no upstream exchange may run while a foreign claim is held")

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _unexpected_refresh)

    encryptor = TokenEncryptor()
    account = Account(
        id="acc_claim_budget_cap",
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
    repo.accounts_by_id[account.id] = account
    manager = AuthManager(cast(AccountsRepositoryPort, repo), refresh_claims=_ForeignClaims())

    # The proxy request path pushes its remaining budget as the refresh
    # timeout override; the claim wait must be capped by it (0.05s), not run
    # for the configured claim wait (8s default).
    override_token = push_token_refresh_timeout_override(0.05)
    try:
        started = time.monotonic()
        with pytest.raises(RefreshError) as exc_info:
            await manager.ensure_fresh(account, force=True)
        elapsed = time.monotonic() - started
    finally:
        pop_token_refresh_timeout_override(override_token)

    assert exc_info.value.code == "refresh_claim_timeout"
    assert exc_info.value.is_permanent is False
    assert exc_info.value.transport_error is True
    assert elapsed < 2.0


@pytest.mark.parametrize(
    ("error_code", "message"),
    [
        (
            "token_expired",
            "Provided authentication token is expired. Please try signing in again.",
        ),
        (
            "app_session_terminated",
            "Your session has been terminated. Please sign in again.",
        ),
    ],
)
@pytest.mark.asyncio
async def test_refresh_account_requires_reauth_when_upstream_session_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
    error_code: str,
    message: str,
) -> None:
    """Permanent OAuth session failures must block the account until re-authentication."""

    async def _fake_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        from app.core.auth.refresh import classify_refresh_error

        assert classify_refresh_error(error_code) is True
        raise RefreshError(error_code, message, classify_refresh_error(error_code))

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _fake_refresh)

    encryptor = TokenEncryptor()
    stale_refresh = utcnow().replace(year=utcnow().year - 1)
    expired_account = Account(
        id=f"acc_{error_code}",
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

    assert exc_info.value.code == error_code
    assert exc_info.value.is_permanent is True
    assert repo.status_payload is not None
    assert repo.status_payload["status"] == AccountStatus.REAUTH_REQUIRED
    reason = repo.status_payload["deactivation_reason"]
    assert isinstance(reason, str)
    assert "re-login" in reason.lower() or "expired" in reason.lower()
