from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy.exc import OperationalError

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
        self.metadata_payload: dict[str, object] | None = None
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

    async def rotate_tokens(
        self,
        account_id: str,
        access_token_encrypted: bytes,
        refresh_token_encrypted: bytes,
        id_token_encrypted: bytes,
        last_refresh: datetime,
        *,
        expected_refresh_token_encrypted: bytes,
        plan_type: str | None = None,
        email: str | None = None,
        chatgpt_account_id: str | None = None,
        chatgpt_user_id: str | None = None,
        workspace_id: str | None = None,
        workspace_label: str | None = None,
        seat_type: str | None = None,
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

    async def update_account_metadata(
        self,
        account_id: str,
        *,
        plan_type: str | None = None,
        email: str | None = None,
        chatgpt_account_id: str | None = None,
        chatgpt_user_id: str | None = None,
        workspace_id: str | None = None,
        workspace_label: str | None = None,
        seat_type: str | None = None,
        last_refresh: datetime | None = None,
    ) -> bool:
        self.metadata_payload = {
            "account_id": account_id,
            "plan_type": plan_type,
            "email": email,
            "chatgpt_account_id": chatgpt_account_id,
            "chatgpt_user_id": chatgpt_user_id,
            "workspace_id": workspace_id,
            "workspace_label": workspace_label,
            "seat_type": seat_type,
            "last_refresh": last_refresh,
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
async def test_ensure_fresh_retry_after_persist_conflict_re_exchanges(monkeypatch):
    """A post-exchange ``token_persist_conflict`` MUST NOT be cached.

    ``token_persist_conflict`` is transient (``transport_error=True``): after it,
    the DB may still hold the just-consumed refresh token, so a retry MUST re-run
    the WHOLE refresh (a fresh upstream re-exchange) rather than reusing a cached
    result or being treated as an immediate permanent knockout. This mirrors the
    genuine-transport-error not-cached behavior and proves the retry re-exchanges.
    """
    refresh_calls = 0

    async def _fake_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        nonlocal refresh_calls
        refresh_calls += 1
        # Simulate the post-exchange persist CAS loss surfacing as the transient
        # conflict (raised after the exchange in production; here the singleflight
        # caching semantics are what we assert).
        raise RefreshError("token_persist_conflict", "cas never landed", False, transport_error=True)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _fake_refresh)
    monkeypatch.setattr(
        auth_manager_module,
        "get_settings",
        lambda: SimpleNamespace(proxy_refresh_failure_cooldown_seconds=30.0),
    )

    encryptor = TokenEncryptor()
    stale_refresh = utcnow().replace(year=utcnow().year - 1)
    account = Account(
        id="acc_persist_conflict_cache",
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

    with pytest.raises(RefreshError) as first:
        await manager.ensure_fresh(account, force=True)
    assert first.value.code == "token_persist_conflict"
    assert first.value.transport_error is True
    await asyncio.sleep(0)
    with pytest.raises(RefreshError):
        await manager.ensure_fresh(account, force=True)

    # Two full refresh invocations: the transient conflict was not cached, so the
    # retry re-exchanged instead of reusing a cached failure.
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

    async def rotate_tokens(
        self,
        account_id: str,
        access_token_encrypted: bytes,
        refresh_token_encrypted: bytes,
        id_token_encrypted: bytes,
        last_refresh: datetime,
        *,
        expected_refresh_token_encrypted: bytes,
        plan_type: str | None = None,
        email: str | None = None,
        chatgpt_account_id: str | None = None,
        chatgpt_user_id: str | None = None,
        workspace_id: str | None = None,
        workspace_label: str | None = None,
        seat_type: str | None = None,
    ) -> bool:
        self.update_attempts.append(expected_refresh_token_encrypted)
        stored = self._latest.refresh_token_encrypted
        if expected_refresh_token_encrypted is not None and expected_refresh_token_encrypted != stored:
            return False
        self._latest.refresh_token_encrypted = refresh_token_encrypted
        return await super().rotate_tokens(
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
    """Repo where NO conditional write ever lands: every ``get_by_id_fresh``
    returns a row whose refresh-token ciphertext is a fresh re-encryption of the
    SAME plaintext (Fernet is non-deterministic), so the stored plaintext never
    changes but the observed ciphertext keeps shifting under the writer, and
    every conditional ``update_tokens`` misses. This models a sustained
    re-encryption storm the refresh can never win an atomic compare-and-set
    window against.

    Under the no-unconditional-write invariant, once the bounded guarded CAS AND
    the dedicated final-persist retries are exhausted the persistence path fails
    CLOSED via the guarded status CAS (flag ``REAUTH_REQUIRED``) rather than
    falling back to a clobber-prone unconditional write or a bare transient
    conflict that a blind retry would knock the account out on. An
    ``expected=None`` (unconditional) write is FORBIDDEN here; if a regression
    ever reintroduces it, this repo applies it via the base repo and records the
    ``None`` in ``update_attempts`` so the test's ``None not in update_attempts``
    assertion trips. The inherited ``update_status_if_current`` performs a real
    guarded status CAS, so the safe terminal REAUTH_REQUIRED flag lands here."""

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

    async def rotate_tokens(
        self,
        account_id: str,
        access_token_encrypted: bytes,
        refresh_token_encrypted: bytes,
        id_token_encrypted: bytes,
        last_refresh: datetime,
        *,
        expected_refresh_token_encrypted: bytes,
        plan_type: str | None = None,
        email: str | None = None,
        chatgpt_account_id: str | None = None,
        chatgpt_user_id: str | None = None,
        workspace_id: str | None = None,
        workspace_label: str | None = None,
        seat_type: str | None = None,
    ) -> bool:
        self.update_attempts.append(expected_refresh_token_encrypted)
        if expected_refresh_token_encrypted is None:
            # FORBIDDEN unconditional write: the no-unconditional-write invariant
            # means this must never happen. Apply it via the base repo so a
            # regression that reintroduces it is caught by the test assertions.
            return await super().rotate_tokens(
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
        # Conditional CAS: always missed by the same-plaintext re-encryption.
        return False


class _TokenCasPeerRotationAtExhaustionRepo(_DummyRepo):
    """Repo where the conditional token compare-and-set keeps missing on
    same-plaintext re-encryption, and a genuinely DIFFERENT peer rotation lands
    on the final re-read — exactly where the old code force-wrote
    ``expected=None`` and would have clobbered the peer's valid tokens with the
    already-consumed material. Proves the refresh adopts the peer rotation at
    the exhaustion boundary instead of forcing an unconditional write.

    ``update_tokens`` misses for every conditional CAS. An ``expected=None``
    write is FORBIDDEN and, if ever attempted, records a clobbering payload so
    the regression is caught."""

    def __init__(
        self,
        account: Account,
        *,
        plaintext: str,
        peer_ciphertext: bytes,
        encryptor: TokenEncryptor,
        rotate_on_read: int,
    ) -> None:
        super().__init__()
        self._plaintext = plaintext
        self._peer_ciphertext = peer_ciphertext
        self._encryptor = encryptor
        self._rotate_on_read = rotate_on_read
        self._reads = 0
        self.accounts_by_id[account.id] = account
        self.update_attempts: list[bytes | None] = []

    async def get_by_id_fresh(self, account_id: str) -> Account | None:
        row = self.accounts_by_id.get(account_id)
        if row is None:
            return None
        self._reads += 1
        if self._reads >= self._rotate_on_read:
            # A peer committed a genuinely different rotation right as the
            # bounded budget runs out.
            row.refresh_token_encrypted = self._peer_ciphertext
        else:
            # Same plaintext, merely re-encrypted (non-deterministic Fernet).
            row.refresh_token_encrypted = self._encryptor.encrypt(self._plaintext)
        return row

    async def rotate_tokens(
        self,
        account_id: str,
        access_token_encrypted: bytes,
        refresh_token_encrypted: bytes,
        id_token_encrypted: bytes,
        last_refresh: datetime,
        *,
        expected_refresh_token_encrypted: bytes,
        plan_type: str | None = None,
        email: str | None = None,
        chatgpt_account_id: str | None = None,
        chatgpt_user_id: str | None = None,
        workspace_id: str | None = None,
        workspace_label: str | None = None,
        seat_type: str | None = None,
    ) -> bool:
        self.update_attempts.append(expected_refresh_token_encrypted)
        if expected_refresh_token_encrypted is None:
            # Forbidden unconditional write: record the clobber for the assertion.
            return await super().rotate_tokens(
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
        return False


@pytest.mark.asyncio
async def test_refresh_adopts_peer_rotation_at_cas_exhaustion_boundary(monkeypatch):
    """Regression (FINDING 1): when the token compare-and-set keeps missing on
    same-plaintext re-encryption until ``_TOKEN_CAS_MAX_ATTEMPTS`` is exhausted
    and a genuinely different peer rotation lands on the final re-read, the
    refresh MUST adopt the peer rotation and MUST NOT force an unconditional
    ``expected=None`` write. The old forced write dropped the ciphertext
    predicate and could clobber the peer's newer valid tokens with the material
    this attempt already consumed upstream."""

    async def _fake_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        return TokenRefreshResult(
            access_token="access-new",
            refresh_token="refresh-new",
            id_token="id-new",
            account_id="acc_cas_peer_exhaustion",
            plan_type="pro",
            email=None,
        )

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _fake_refresh)

    encryptor = TokenEncryptor()
    account = Account(
        id="acc_cas_peer_exhaustion",
        email="user@example.com",
        plan_type="pro",
        access_token_encrypted=encryptor.encrypt("access-old"),
        refresh_token_encrypted=encryptor.encrypt("refresh-old"),
        id_token_encrypted=encryptor.encrypt("id-old"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    peer_ciphertext = encryptor.encrypt("refresh-peer")
    repo = _TokenCasPeerRotationAtExhaustionRepo(
        account,
        plaintext="refresh-old",
        peer_ciphertext=peer_ciphertext,
        encryptor=encryptor,
        # The peer rotation appears on the last re-read of the bounded loop,
        # exactly where the old code would have force-written ``expected=None``.
        rotate_on_read=auth_manager_module._TOKEN_CAS_MAX_ATTEMPTS,
    )
    manager = AuthManager(cast(AccountsRepositoryPort, repo))

    result = await manager.refresh_account(account)

    # The peer's rotation is adopted; our consumed token is never written.
    assert result is account
    assert result.refresh_token_encrypted == peer_ciphertext
    assert encryptor.decrypt(result.refresh_token_encrypted) == "refresh-peer"
    # No unconditional (``expected=None``) forced write was ever issued, so the
    # peer rotation could not be clobbered.
    assert None not in repo.update_attempts
    assert repo.tokens_payload is None
    # Only bounded conditional CAS attempts ran before adoption.
    assert all(expected is not None for expected in repo.update_attempts)
    assert len(repo.update_attempts) <= auth_manager_module._TOKEN_CAS_MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_refresh_flags_reauth_when_cas_never_lands_on_same_plaintext_storm(monkeypatch):
    """Regression (P1 "Do not retry after dropping rotated tokens"): when the
    guarded compare-and-set keeps missing on a sustained same-plaintext
    re-encryption storm through BOTH the bounded budget AND the dedicated
    final-persist retries, the refresh MUST NOT (a) fall back to an unconditional
    ``expected=None`` write, nor (b) drop the freshly rotated token behind a bare
    transient ``token_persist_conflict`` that releases the claim and lets a later
    blind retry re-exchange the still-stored consumed token into an
    ``invalid_grant``/reauth PERMANENT knockout of a healthy account.

    Instead it FAILS CLOSED to a SAFE TERMINAL OUTCOME: it flags the account
    ``REAUTH_REQUIRED`` through the SAME ciphertext-guarded status path, so the
    dead stored token is explicitly surfaced to operators rather than left
    silently holding a consumed token that a blind retry would knock out. Every
    write remains a guarded compare-and-set, so nothing is ever clobbered.

    The account is never PERMANENTLY knocked out by this race: ``REAUTH_REQUIRED``
    is a recoverable, operator-visible state (the DB genuinely holds a dead,
    already-consumed token), NOT a blind-retry ``invalid_grant`` knockout."""

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

    # No transient raise: the SAFE TERMINAL OUTCOME flags the account REAUTH_REQUIRED.
    result = await manager.refresh_account(account)

    assert result is account
    assert result.status == AccountStatus.REAUTH_REQUIRED
    assert result.deactivation_reason is not None
    assert "re-login" in result.deactivation_reason
    # The reauth flag landed through the guarded status compare-and-set (keyed on
    # the last-observed ciphertext), NOT an unguarded status write.
    assert repo.status_payload is not None
    assert repo.status_payload["status"] == AccountStatus.REAUTH_REQUIRED
    assert repo.status_payload["expected_refresh_token_encrypted"] is not None
    # The bounded guarded CAS retries plus the DEDICATED final-persist retries ran;
    # NO unconditional write was ever issued, and no rotate_tokens ever landed
    # (the storm never let a token persist land), so nothing was clobbered.
    assert len(repo.update_attempts) == (
        auth_manager_module._TOKEN_CAS_MAX_ATTEMPTS + auth_manager_module._FINAL_PERSIST_MAX_ATTEMPTS
    )
    assert None not in repo.update_attempts
    assert all(expected is not None for expected in repo.update_attempts)
    assert repo.tokens_payload is None


@pytest.mark.asyncio
async def test_ensure_chatgpt_account_id_backfill_never_writes_token_material(monkeypatch):
    """Regression (finding #1, root enforcement): ensure_fresh ends with
    _ensure_chatgpt_account_id, which for a legacy account missing
    chatgpt_account_id previously issued an UNCONDITIONAL token write
    (expected_refresh_token_encrypted=None) OUTSIDE any refresh claim, rewriting
    the refresh-token ciphertext from the in-memory selection snapshot. A
    concurrent peer rotation of the single-use refresh token in that
    read->write window was clobbered with already-consumed material.

    The backfill now routes through the metadata-only writer, which
    STRUCTURALLY cannot write token ciphertext (no parameter for it). It can
    only persist chatgpt_account_id, so a peer rotation can never be clobbered
    by this sibling regardless of how stale the snapshot is."""

    monkeypatch.setattr(auth_manager_module, "_chatgpt_account_id_from_id_token", lambda _token: "chatgpt-derived-id")

    encryptor = TokenEncryptor()
    account = Account(
        id="acc_legacy_backfill",
        email="user@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-old"),
        refresh_token_encrypted=encryptor.encrypt("refresh-old"),
        id_token_encrypted=encryptor.encrypt("id-old"),
        last_refresh=utcnow(),  # fresh: ensure_fresh takes the no-refresh fast path
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
        chatgpt_account_id=None,
    )
    repo = _DummyRepo()
    repo.accounts_by_id[account.id] = account
    manager = AuthManager(cast(AccountsRepositoryPort, repo), refresh_claims=None)

    result = await manager.ensure_fresh(account)

    assert result.chatgpt_account_id == "chatgpt-derived-id"
    # Metadata-only write: chatgpt_account_id is persisted, and NO token
    # ciphertext write was ever issued (the token path is structurally
    # unreachable from this backfill).
    assert repo.metadata_payload is not None
    assert repo.metadata_payload["chatgpt_account_id"] == "chatgpt-derived-id"
    assert repo.tokens_payload is None


@pytest.mark.asyncio
async def test_persist_cas_deadline_flags_reauth_when_final_retries_exhaust(monkeypatch):
    """Regression (P1 "Do not retry after dropping rotated tokens"): the
    post-exchange token-persist compare-and-set retry loop runs while the
    cross-replica refresh claim is held and is bounded by the claim/caller
    deadline -- a contended DB write must not keep that RETRY LOOP (and the held
    claim) spinning past the budget. But the deadline MUST bound only the RETRY
    LOOP, never drop the freshly rotated single-use token unpersisted: on deadline
    expiry the persist path STILL runs the DEDICATED final-persist retries, which
    are DELIBERATELY SEPARATE from the deadline (persisting a valid rotated token
    is worth a few extra milliseconds over budget).

    Here the write is a sustained same-plaintext re-encryption storm that never
    lets an atomic CAS window land (the PATHOLOGICAL case), so even the dedicated
    final retries all miss on unchanged material. Rather than dropping the rotated
    token behind a bare transient conflict -- which would release the claim and
    let a later blind retry re-exchange the still-stored consumed token into an
    ``invalid_grant``/reauth PERMANENT knockout -- the persist path FAILS CLOSED to
    the SAFE TERMINAL OUTCOME: it flags the account ``REAUTH_REQUIRED`` through the
    guarded status path. The DB genuinely holds a dead token in this rare storm,
    so the account is explicitly surfaced for re-auth (recoverable), never left
    silently holding a consumed token, and never clobbered."""

    async def _fake_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        return TokenRefreshResult(
            access_token="access-new",
            refresh_token="refresh-new",
            id_token="id-new",
            account_id="acc_persist_deadline",
            plan_type="pro",
            email=None,
        )

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _fake_refresh)

    encryptor = TokenEncryptor()
    account = Account(
        id="acc_persist_deadline",
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

    # No transient raise: the deadline cuts the RETRY loop, the dedicated final
    # retries still run and exhaust, and the SAFE TERMINAL OUTCOME flags REAUTH.
    result = await manager._perform_refresh(
        account,
        refresh_token_encrypted=account.refresh_token_encrypted,
        deadline=time.monotonic() - 1.0,
    )

    assert result is account
    assert result.status == AccountStatus.REAUTH_REQUIRED
    assert result.deactivation_reason is not None
    assert "re-login" in result.deactivation_reason
    assert repo.status_payload is not None
    assert repo.status_payload["status"] == AccountStatus.REAUTH_REQUIRED
    assert repo.status_payload["expected_refresh_token_encrypted"] is not None
    # The deadline cut the retry loop after the first guarded write, but the
    # DEDICATED final-persist retries (which the deadline does NOT bound) still ran
    # before the safe terminal flag: 1 retry-loop write + _FINAL_PERSIST_MAX_ATTEMPTS
    # dedicated writes, NOT the full _TOKEN_CAS_MAX_ATTEMPTS loop, and NO
    # unconditional write.
    assert len(repo.update_attempts) == 1 + auth_manager_module._FINAL_PERSIST_MAX_ATTEMPTS
    assert None not in repo.update_attempts
    assert all(expected is not None for expected in repo.update_attempts)
    assert repo.tokens_payload is None


class _TokenCasStabilizesOnSecondFinalAttemptRepo(_DummyRepo):
    """Repo modelling a same-plaintext re-encryption storm that QUIETS mid-way
    through the DEDICATED final-persist retries. For the first ``storm_writes``
    guarded writes a concurrent writer keeps re-encrypting the SAME consumed
    plaintext right as we try to write, so each guarded compare-and-set misses and
    the observed ciphertext keeps shifting; after that the storm stops and the next
    guarded write against the last-observed ciphertext LANDS.

    Sized so the storm outlasts the whole main bounded loop AND the FIRST dedicated
    final-persist attempt, then quiets so the SECOND final attempt lands: this
    proves the dedicated final retries make MULTIPLE attempts (not just one) and
    persist the freshly rotated token T2 the instant the ciphertext stabilizes --
    no transient raise, no dropped token, no reauth flag. An ``expected=None``
    (unconditional) write is FORBIDDEN; if a regression reintroduces it, this repo
    records the ``None`` so the test's assertions trip."""

    def __init__(self, account: Account, *, plaintext: str, encryptor: TokenEncryptor, storm_writes: int) -> None:
        super().__init__()
        self._plaintext = plaintext
        self._encryptor = encryptor
        self._storm_writes = storm_writes
        self._writes = 0
        # A re-encryption of the consumed plaintext DISTINCT from the caller's
        # original ciphertext, so the very first guarded write misses.
        self._db_ciphertext = encryptor.encrypt(plaintext)
        self.accounts_by_id[account.id] = account
        self.update_attempts: list[bytes | None] = []

    async def get_by_id_fresh(self, account_id: str) -> Account | None:
        row = self.accounts_by_id.get(account_id)
        if row is None:
            return None
        # Report the current stored ciphertext without mutating it here; the storm
        # (if still active) shifts the stored ciphertext on the WRITE attempt.
        row.refresh_token_encrypted = self._db_ciphertext
        return row

    async def rotate_tokens(
        self,
        account_id: str,
        access_token_encrypted: bytes,
        refresh_token_encrypted: bytes,
        id_token_encrypted: bytes,
        last_refresh: datetime,
        *,
        expected_refresh_token_encrypted: bytes,
        plan_type: str | None = None,
        email: str | None = None,
        chatgpt_account_id: str | None = None,
        chatgpt_user_id: str | None = None,
        workspace_id: str | None = None,
        workspace_label: str | None = None,
        seat_type: str | None = None,
    ) -> bool:
        self.update_attempts.append(expected_refresh_token_encrypted)
        if expected_refresh_token_encrypted is None:
            # FORBIDDEN unconditional write: apply via the base repo so a
            # regression that reintroduces it is caught by the test assertions.
            return await super().rotate_tokens(
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
        self._writes += 1
        if self._writes <= self._storm_writes:
            # Storm still raging: a concurrent writer re-encrypts the SAME consumed
            # plaintext, moving the row out from under our guarded write -> miss and
            # the stored ciphertext shifts.
            self._db_ciphertext = self._encryptor.encrypt(self._plaintext)
            return False
        # Storm quiet: honest guarded compare-and-set against the current stored
        # ciphertext. Lands only when our expected still matches (it does -- the
        # last re-read observed exactly this ciphertext).
        if expected_refresh_token_encrypted != self._db_ciphertext:
            return False
        self._db_ciphertext = refresh_token_encrypted
        return await super().rotate_tokens(
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
async def test_persist_cas_stabilizes_on_second_final_attempt_persists_rotated_token(monkeypatch):
    """Regression (P1 "Do not retry after dropping rotated tokens"): the DEDICATED
    final-persist retries make MULTIPLE bounded attempts. When the same-plaintext
    re-encryption storm keeps missing through the whole main bounded loop AND the
    FIRST final-persist attempt, but the ciphertext STABILIZES by the SECOND final
    attempt, the freshly rotated token T2 MUST be persisted right then -- no
    transient ``token_persist_conflict``, no dropped token, and no reauth flag.

    This is exactly the headroom the dedicated retry loop buys: giving up after a
    single final attempt would have stranded the account holding the consumed
    token; the extra attempt lands the rotation the instant contention clears."""

    async def _fake_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        return TokenRefreshResult(
            access_token="access-new",
            refresh_token="refresh-new",
            id_token="id-new",
            account_id="acc_persist_stabilizes",
            plan_type="pro",
            email=None,
        )

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _fake_refresh)

    encryptor = TokenEncryptor()
    account = Account(
        id="acc_persist_stabilizes",
        email="user@example.com",
        plan_type="pro",
        access_token_encrypted=encryptor.encrypt("access-old"),
        refresh_token_encrypted=encryptor.encrypt("refresh-old"),
        id_token_encrypted=encryptor.encrypt("id-old"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    # The storm outlasts the whole main bounded loop AND the first dedicated final
    # attempt, then quiets so the SECOND final attempt lands.
    repo = _TokenCasStabilizesOnSecondFinalAttemptRepo(
        account,
        plaintext="refresh-old",
        encryptor=encryptor,
        storm_writes=auth_manager_module._TOKEN_CAS_MAX_ATTEMPTS + 1,
    )
    manager = AuthManager(cast(AccountsRepositoryPort, repo))

    result = await manager.refresh_account(account)

    # The freshly rotated token T2 is persisted; the account stays healthy.
    assert result is account
    assert result.status == AccountStatus.ACTIVE
    assert result.deactivation_reason is None
    assert encryptor.decrypt(result.refresh_token_encrypted) == "refresh-new"
    # The guarded token write landed (never a reauth flag, never an unconditional
    # write), persisting the newly rotated token and evicting the consumed one.
    assert repo.status_payload is None
    assert repo.tokens_payload is not None
    assert encryptor.decrypt(cast(bytes, repo.tokens_payload["refresh_token_encrypted"])) == "refresh-new"
    assert repo.tokens_payload["expected_refresh_token_encrypted"] is not None
    # It landed on the SECOND dedicated final attempt: main bounded loop + 2 final
    # attempts, with NO unconditional write anywhere.
    assert len(repo.update_attempts) == auth_manager_module._TOKEN_CAS_MAX_ATTEMPTS + 2
    assert None not in repo.update_attempts
    assert all(expected is not None for expected in repo.update_attempts)


class _TokenCasLandsOnFinalGuardedPersistRepo(_DummyRepo):
    """Real compare-and-set repo whose reads are STABLE: ``get_by_id_fresh``
    reflects current DB truth on the row but does not mutate it, so a guarded
    write keyed on the just-read ciphertext lands. Seeded with a re-encryption of
    the consumed plaintext so the FIRST guarded write (against the caller's
    original ciphertext) misses; the FINAL guarded persist keyed on the
    last-observed ciphertext then lands. Models the deadline-cut path where
    nothing actually changed since the last read, so the freshly rotated token is
    persisted rather than dropped."""

    def __init__(self, account: Account, *, plaintext: str, encryptor: TokenEncryptor) -> None:
        super().__init__()
        self._encryptor = encryptor
        self.accounts_by_id[account.id] = account
        self._db_ciphertext = encryptor.encrypt(plaintext)
        self.update_attempts: list[bytes | None] = []

    async def get_by_id_fresh(self, account_id: str) -> Account | None:
        row = self.accounts_by_id.get(account_id)
        if row is None:
            return None
        row.refresh_token_encrypted = self._db_ciphertext
        return row

    async def rotate_tokens(
        self,
        account_id: str,
        access_token_encrypted: bytes,
        refresh_token_encrypted: bytes,
        id_token_encrypted: bytes,
        last_refresh: datetime,
        *,
        expected_refresh_token_encrypted: bytes,
        plan_type: str | None = None,
        email: str | None = None,
        chatgpt_account_id: str | None = None,
        chatgpt_user_id: str | None = None,
        workspace_id: str | None = None,
        workspace_label: str | None = None,
        seat_type: str | None = None,
    ) -> bool:
        self.update_attempts.append(expected_refresh_token_encrypted)
        if expected_refresh_token_encrypted is not None and expected_refresh_token_encrypted != self._db_ciphertext:
            return False
        self._db_ciphertext = refresh_token_encrypted
        return await super().rotate_tokens(
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
async def test_persist_cas_deadline_lands_final_guarded_persist(monkeypatch):
    """Regression (finding: do not drop rotated tokens after CAS misses): when the
    claim/caller deadline has ALREADY elapsed after a successful upstream exchange
    and the stored plaintext is still exactly the consumed token (only
    re-encrypted), the FINAL ciphertext-guarded persist keyed on the last-observed
    ciphertext MUST land the freshly rotated token. The DB no longer holds the
    consumed token and NO transient conflict is raised -- the deadline bounds the
    retry loop, not this one safety persist."""

    async def _fake_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        return TokenRefreshResult(
            access_token="access-new",
            refresh_token="refresh-new",
            id_token="id-new",
            account_id="acc_persist_deadline_lands",
            plan_type="pro",
            email=None,
        )

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _fake_refresh)

    encryptor = TokenEncryptor()
    account = Account(
        id="acc_persist_deadline_lands",
        email="user@example.com",
        plan_type="pro",
        access_token_encrypted=encryptor.encrypt("access-old"),
        refresh_token_encrypted=encryptor.encrypt("refresh-old"),
        id_token_encrypted=encryptor.encrypt("id-old"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    repo = _TokenCasLandsOnFinalGuardedPersistRepo(account, plaintext="refresh-old", encryptor=encryptor)
    manager = AuthManager(cast(AccountsRepositoryPort, repo))

    result = await manager._perform_refresh(
        account,
        refresh_token_encrypted=account.refresh_token_encrypted,
        deadline=time.monotonic() - 1.0,
    )

    # The freshly rotated token is persisted via the final guarded CAS; the DB no
    # longer holds the consumed token and no transient was raised.
    assert result is account
    assert encryptor.decrypt(result.refresh_token_encrypted) == "refresh-new"
    assert repo.tokens_payload is not None
    assert encryptor.decrypt(cast(bytes, repo.tokens_payload["refresh_token_encrypted"])) == "refresh-new"
    assert encryptor.decrypt(repo._db_ciphertext) == "refresh-new"
    # First guarded write missed (seeded re-encryption); the final guarded write
    # landed. No unconditional (``expected=None``) write was ever issued.
    assert len(repo.update_attempts) == 2
    assert None not in repo.update_attempts


class _TokenCasPeerRotationOnFinalPersistRepo(_DummyRepo):
    """Real compare-and-set repo where the deadline cuts the retry loop after one
    same-plaintext re-encryption miss, then a genuinely DIFFERENT peer rotation
    lands right before the FINAL guarded persist. The final guarded write misses
    the peer's ciphertext (clobbering nothing) and the persist re-reads, sees the
    different plaintext, and ADOPTS the peer rotation instead of overwriting with
    the already-consumed token."""

    def __init__(
        self,
        account: Account,
        *,
        consumed_plaintext: str,
        peer_ciphertext: bytes,
        encryptor: TokenEncryptor,
    ) -> None:
        super().__init__()
        self._encryptor = encryptor
        self._peer_ciphertext = peer_ciphertext
        self.accounts_by_id[account.id] = account
        # Seed a re-encryption of the consumed token so the first guarded write misses.
        self._db_ciphertext = encryptor.encrypt(consumed_plaintext)
        self._reads = 0
        self.update_attempts: list[bytes | None] = []

    async def get_by_id_fresh(self, account_id: str) -> Account | None:
        row = self.accounts_by_id.get(account_id)
        if row is None:
            return None
        self._reads += 1
        row.refresh_token_encrypted = self._db_ciphertext
        if self._reads == 1:
            # After the first miss's re-read (same plaintext), a genuine peer
            # rotation commits before the final guarded persist runs.
            self._db_ciphertext = self._peer_ciphertext
        return row

    async def rotate_tokens(
        self,
        account_id: str,
        access_token_encrypted: bytes,
        refresh_token_encrypted: bytes,
        id_token_encrypted: bytes,
        last_refresh: datetime,
        *,
        expected_refresh_token_encrypted: bytes,
        plan_type: str | None = None,
        email: str | None = None,
        chatgpt_account_id: str | None = None,
        chatgpt_user_id: str | None = None,
        workspace_id: str | None = None,
        workspace_label: str | None = None,
        seat_type: str | None = None,
    ) -> bool:
        self.update_attempts.append(expected_refresh_token_encrypted)
        if expected_refresh_token_encrypted is not None and expected_refresh_token_encrypted != self._db_ciphertext:
            return False
        self._db_ciphertext = refresh_token_encrypted
        return await super().rotate_tokens(
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
async def test_persist_cas_adopts_peer_rotation_on_final_guarded_persist(monkeypatch):
    """Regression (finding: do not drop rotated tokens after CAS misses): when the
    deadline cuts the retry loop and a genuinely different peer rotation lands
    right before the FINAL guarded persist, the persist MUST adopt the peer
    rotation (its freshly rotated token is legitimately superseded) rather than
    overwrite it. Every write is a ciphertext-guarded CAS, so the final write
    misses the peer's ciphertext (clobbering nothing) and the peer row is adopted;
    no unconditional write is ever issued and no transient conflict is raised."""

    async def _fake_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        return TokenRefreshResult(
            access_token="access-new",
            refresh_token="refresh-new",
            id_token="id-new",
            account_id="acc_persist_deadline_peer",
            plan_type="pro",
            email=None,
        )

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _fake_refresh)

    encryptor = TokenEncryptor()
    account = Account(
        id="acc_persist_deadline_peer",
        email="user@example.com",
        plan_type="pro",
        access_token_encrypted=encryptor.encrypt("access-old"),
        refresh_token_encrypted=encryptor.encrypt("refresh-old"),
        id_token_encrypted=encryptor.encrypt("id-old"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    peer_ciphertext = encryptor.encrypt("refresh-peer")
    repo = _TokenCasPeerRotationOnFinalPersistRepo(
        account,
        consumed_plaintext="refresh-old",
        peer_ciphertext=peer_ciphertext,
        encryptor=encryptor,
    )
    manager = AuthManager(cast(AccountsRepositoryPort, repo))

    result = await manager._perform_refresh(
        account,
        refresh_token_encrypted=account.refresh_token_encrypted,
        deadline=time.monotonic() - 1.0,
    )

    # The peer's rotation is adopted, never overwritten with the consumed token.
    assert result is account
    assert encryptor.decrypt(result.refresh_token_encrypted) == "refresh-peer"
    # No unconditional (``expected=None``) write, and this attempt persisted
    # nothing of its own (the peer's row was adopted).
    assert None not in repo.update_attempts
    assert all(expected is not None for expected in repo.update_attempts)
    assert repo.tokens_payload is None


class _TokenCasPeerRotationInReadWriteGapRepo(_DummyRepo):
    """Reproduces the exact TOCTOU the guarded CAS closes.

    The refresh's confirming re-read observes the SAME refresh-token plaintext it
    exchanged FROM (only re-encrypted), so it decides its rotation is still safe
    to persist. But a genuinely DIFFERENT peer rotation lands in the read->write
    gap — AFTER that plaintext-confirming read and BEFORE the persist. Because
    every persist is a ciphertext-guarded compare-and-set (never an unconditional
    write), the guarded write MISSES the peer's ciphertext and clobbers nothing;
    the refresh re-reads, sees the different plaintext, and ADOPTS the peer
    rotation. An unconditional ``expected=None`` write would instead overwrite the
    peer's valid tokens with the already-consumed material — the clobber this
    finding removes.

    ``_db_ciphertext`` tracks the authoritative stored bytes independently of the
    row object handed back, so the guard compares against DB truth. An
    ``expected=None`` write is FORBIDDEN; if reintroduced it is applied and
    recorded so the assertions trip."""

    def __init__(
        self,
        account: Account,
        *,
        consumed_plaintext: str,
        peer_ciphertext: bytes,
        encryptor: TokenEncryptor,
    ) -> None:
        super().__init__()
        self._encryptor = encryptor
        self._consumed_plaintext = consumed_plaintext
        self._peer_ciphertext = peer_ciphertext
        self.accounts_by_id[account.id] = account
        # Seed DB truth with a re-encryption of the consumed token so the FIRST
        # guarded write (against the caller's original ciphertext) already misses.
        self._db_ciphertext = encryptor.encrypt(consumed_plaintext)
        self._reads = 0
        self.update_attempts: list[bytes | None] = []

    async def get_by_id_fresh(self, account_id: str) -> Account | None:
        row = self.accounts_by_id.get(account_id)
        if row is None:
            return None
        self._reads += 1
        # Reflect current DB truth on the row the caller inspects.
        row.refresh_token_encrypted = self._db_ciphertext
        if self._reads == 1:
            # This confirming re-read returned the consumed token (same plaintext,
            # merely re-encrypted). A genuine peer rotation now lands in the gap
            # BEFORE the refresh issues its next guarded write.
            self._db_ciphertext = self._peer_ciphertext
        return row

    async def rotate_tokens(
        self,
        account_id: str,
        access_token_encrypted: bytes,
        refresh_token_encrypted: bytes,
        id_token_encrypted: bytes,
        last_refresh: datetime,
        *,
        expected_refresh_token_encrypted: bytes,
        plan_type: str | None = None,
        email: str | None = None,
        chatgpt_account_id: str | None = None,
        chatgpt_user_id: str | None = None,
        workspace_id: str | None = None,
        workspace_label: str | None = None,
        seat_type: str | None = None,
    ) -> bool:
        self.update_attempts.append(expected_refresh_token_encrypted)
        if expected_refresh_token_encrypted is not None and expected_refresh_token_encrypted != self._db_ciphertext:
            # Guarded miss: DB truth changed (peer rotation) after the read.
            return False
        # A landing write (guarded match) or a FORBIDDEN unconditional write.
        self._db_ciphertext = refresh_token_encrypted
        return await super().rotate_tokens(
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
async def test_refresh_adopts_peer_rotation_landing_in_read_write_gap(monkeypatch):
    """Regression (FINDING: no-unconditional-write / TOCTOU): a genuinely different
    peer rotation that lands AFTER the plaintext-confirming re-read but BEFORE the
    persist MUST NOT be overwritten. Because the persist is a ciphertext-guarded
    compare-and-set, the write misses the peer's ciphertext (clobbering nothing) and
    the refresh adopts the peer's rotation instead. The removed unconditional write
    would have clobbered the peer's valid tokens with the already-consumed material."""

    async def _fake_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        return TokenRefreshResult(
            access_token="access-new",
            refresh_token="refresh-new",
            id_token="id-new",
            account_id="acc_cas_gap_peer",
            plan_type="pro",
            email=None,
        )

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _fake_refresh)

    encryptor = TokenEncryptor()
    account = Account(
        id="acc_cas_gap_peer",
        email="user@example.com",
        plan_type="pro",
        access_token_encrypted=encryptor.encrypt("access-old"),
        refresh_token_encrypted=encryptor.encrypt("refresh-old"),
        id_token_encrypted=encryptor.encrypt("id-old"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    peer_ciphertext = encryptor.encrypt("refresh-peer")
    repo = _TokenCasPeerRotationInReadWriteGapRepo(
        account,
        consumed_plaintext="refresh-old",
        peer_ciphertext=peer_ciphertext,
        encryptor=encryptor,
    )
    manager = AuthManager(cast(AccountsRepositoryPort, repo))

    result = await manager.refresh_account(account)

    # The peer rotation is adopted, never overwritten with the consumed token.
    assert result is account
    assert encryptor.decrypt(result.refresh_token_encrypted) == "refresh-peer"
    # No unconditional (``expected=None``) write was ever issued, so the peer
    # rotation could not be clobbered, and this attempt persisted nothing.
    assert None not in repo.update_attempts
    assert repo.tokens_payload is None


class _TokenCasSamePlaintextInReadWriteGapRepo(_DummyRepo):
    """Companion to the peer-rotation gap repo: here the material that lands in the
    read->write gap is a same-plaintext RE-ENCRYPTION (Fernet non-determinism), not a
    genuine rotation. The guarded write misses the shifted ciphertext, but because the
    stored plaintext is still the consumed token, the refresh retries the guarded CAS
    against the freshly observed ciphertext and its own freshly rotated token LANDS —
    it is never dropped. The gap re-encryption happens only once, so a later guarded
    retry can win."""

    def __init__(self, account: Account, *, plaintext: str, encryptor: TokenEncryptor) -> None:
        super().__init__()
        self._encryptor = encryptor
        self._plaintext = plaintext
        self.accounts_by_id[account.id] = account
        # Seed DB truth with a re-encryption so the first guarded write misses.
        self._db_ciphertext = encryptor.encrypt(plaintext)
        self._reads = 0
        self.update_attempts: list[bytes | None] = []

    async def get_by_id_fresh(self, account_id: str) -> Account | None:
        row = self.accounts_by_id.get(account_id)
        if row is None:
            return None
        self._reads += 1
        row.refresh_token_encrypted = self._db_ciphertext
        if self._reads == 1:
            # One same-plaintext re-encryption lands in the read->write gap.
            self._db_ciphertext = self._encryptor.encrypt(self._plaintext)
        return row

    async def rotate_tokens(
        self,
        account_id: str,
        access_token_encrypted: bytes,
        refresh_token_encrypted: bytes,
        id_token_encrypted: bytes,
        last_refresh: datetime,
        *,
        expected_refresh_token_encrypted: bytes,
        plan_type: str | None = None,
        email: str | None = None,
        chatgpt_account_id: str | None = None,
        chatgpt_user_id: str | None = None,
        workspace_id: str | None = None,
        workspace_label: str | None = None,
        seat_type: str | None = None,
    ) -> bool:
        self.update_attempts.append(expected_refresh_token_encrypted)
        if expected_refresh_token_encrypted is not None and expected_refresh_token_encrypted != self._db_ciphertext:
            return False
        self._db_ciphertext = refresh_token_encrypted
        return await super().rotate_tokens(
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
async def test_refresh_persists_new_token_when_same_plaintext_reencryption_lands_in_gap(monkeypatch):
    """Regression (no-unconditional-write, horn A): a same-plaintext re-encryption
    landing in the read->write gap misses the guarded compare-and-set, but the refresh
    does NOT give up — it retries the guarded CAS against the freshly observed
    ciphertext and its own freshly rotated token still LANDS. This proves removing the
    unconditional write does not drop the freshly rotated token when no peer rotated."""

    async def _fake_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        return TokenRefreshResult(
            access_token="access-new",
            refresh_token="refresh-new",
            id_token="id-new",
            account_id="acc_cas_gap_same",
            plan_type="pro",
            email=None,
        )

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _fake_refresh)

    encryptor = TokenEncryptor()
    account = Account(
        id="acc_cas_gap_same",
        email="user@example.com",
        plan_type="pro",
        access_token_encrypted=encryptor.encrypt("access-old"),
        refresh_token_encrypted=encryptor.encrypt("refresh-old"),
        id_token_encrypted=encryptor.encrypt("id-old"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    repo = _TokenCasSamePlaintextInReadWriteGapRepo(account, plaintext="refresh-old", encryptor=encryptor)
    manager = AuthManager(cast(AccountsRepositoryPort, repo))

    result = await manager.refresh_account(account)

    # Our freshly rotated token lands via the guarded retry; it is never dropped
    # and never adopted the re-encrypted consumed token.
    assert result is account
    assert encryptor.decrypt(result.refresh_token_encrypted) == "refresh-new"
    assert repo.tokens_payload is not None
    assert encryptor.decrypt(cast(bytes, repo.tokens_payload["refresh_token_encrypted"])) == "refresh-new"
    # Every persist was guarded; no unconditional (``expected=None``) write.
    assert None not in repo.update_attempts
    assert all(expected is not None for expected in repo.update_attempts)


@pytest.mark.asyncio
async def test_permanent_failure_adopts_peer_rotation_landing_after_fresh_read(monkeypatch):
    """Regression: a concurrent re-auth/import rotates the refresh token AFTER
    the permanent-failure guard's fresh re-read but BEFORE its status CAS.

    The status CAS misses because the stored ciphertext now carries a
    genuinely different rotation. The guard must ADOPT that repaired rotation
    (returning the refreshed row) rather than returning ``None`` and letting
    ``_perform_refresh`` re-raise the original permanent ``RefreshError``.
    Re-raising would send proxy callers into ``LoadBalancer.mark_permanent_failure()``,
    whose ``update_status`` path is NOT guarded by this refresh-token CAS, so it
    would clobber the peer's valid rotation with ``REAUTH_REQUIRED`` and tear
    down sessions for an account a peer just repaired."""

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
    rotated_ciphertext = encryptor.encrypt("refresh-rotated")

    class _RotatingAfterFreshReadRepo(_DummyRepo):
        async def get_by_id_fresh(self, account_id: str) -> Account | None:
            latest = self.accounts_by_id.get(account_id)
            if latest is None:
                return None
            snapshot = Account(**{column.name: getattr(latest, column.name) for column in Account.__table__.columns})
            # Concurrent re-auth commits a genuinely different rotation in the
            # window between this fresh read and the status CAS
            # (status/reason/reset untouched).
            latest.refresh_token_encrypted = rotated_ciphertext
            return snapshot

    repo = _RotatingAfterFreshReadRepo()
    latest_account = Account(**{column.name: getattr(account, column.name) for column in Account.__table__.columns})
    repo.accounts_by_id[account.id] = latest_account
    manager = AuthManager(cast(AccountsRepositoryPort, repo))

    # No RefreshError: the guard adopts the peer's repaired rotation instead of
    # re-raising the permanent error.
    result = await manager.refresh_account(account)

    assert result is account
    # The caller's object adopts the peer's freshly rotated refresh token.
    assert account.refresh_token_encrypted == rotated_ciphertext
    assert encryptor.decrypt(account.refresh_token_encrypted) == "refresh-rotated"
    # The account is NEVER downgraded to REAUTH_REQUIRED.
    assert repo.status_payload is None
    assert account.status == AccountStatus.ACTIVE
    assert account.deactivation_reason is None


class _StatusCasAlwaysMissRepo(_DummyRepo):
    """Repo where the permanent-failure status compare-and-set can never win an
    atomic window: ``get_by_id_fresh`` re-encrypts the SAME refresh-token
    plaintext on each read (Fernet is non-deterministic), so the fingerprint
    stays constant (no peer rotation to adopt) while the observed ciphertext
    keeps shifting under the writer, and every conditional
    ``update_status_if_current`` misses. Models a sustained same-plaintext
    re-encryption storm that exhausts the bounded status-downgrade budget while
    the account still holds the material that just failed permanently."""

    def __init__(self, account: Account, *, plaintext: str, encryptor: TokenEncryptor) -> None:
        super().__init__()
        self._plaintext = plaintext
        self._encryptor = encryptor
        self.accounts_by_id[account.id] = account
        self.status_cas_attempts: list[bytes | None] = []

    async def get_by_id_fresh(self, account_id: str) -> Account | None:
        row = self.accounts_by_id.get(account_id)
        if row is not None:
            # Same plaintext, merely re-encrypted (non-deterministic Fernet):
            # fingerprint unchanged, ciphertext shifted.
            row.refresh_token_encrypted = self._encryptor.encrypt(self._plaintext)
        return row

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
        # The conditional CAS is always conditioned on the freshly observed
        # ciphertext; record it and miss to model the storm.
        self.status_cas_attempts.append(expected_refresh_token_encrypted)
        return False


@pytest.mark.asyncio
async def test_permanent_failure_status_cas_exhaustion_surfaces_transient_error(monkeypatch):
    """Regression: a genuine permanent refresh failure whose status downgrade
    CAS is EXHAUSTED by a same-plaintext re-encryption storm MUST surface a
    TRANSIENT (``transport_error``, non-permanent) ``RefreshError`` — not return
    ``None`` and re-raise the original permanent error.

    Returning ``None`` here re-raises the permanent error, which sends proxy
    callers into ``LoadBalancer.mark_permanent_failure()`` whose
    ``update_status`` write is NOT guarded by the refresh-token ciphertext CAS.
    In a storm — or if a genuine peer re-auth/import rotation lands after the
    final re-read but before that unguarded write — a REPAIRED account would be
    clobbered with ``REAUTH_REQUIRED``, exactly the clobber the CAS guards
    prevent. Surfacing a transient error makes the caller RETRY instead of
    running the unguarded permanent mark, and keeps the failure out of the
    permanent-failure cooldown cache."""

    async def _fake_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        raise RefreshError("invalid_grant", "refresh failed", True)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _fake_refresh)

    encryptor = TokenEncryptor()
    stale_refresh = utcnow().replace(year=utcnow().year - 1)
    account = Account(
        id="acc_status_cas_storm",
        email="user@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-old"),
        refresh_token_encrypted=encryptor.encrypt("refresh-storm"),
        id_token_encrypted=encryptor.encrypt("id-old"),
        last_refresh=stale_refresh,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    repo = _StatusCasAlwaysMissRepo(account, plaintext="refresh-storm", encryptor=encryptor)
    manager = AuthManager(cast(AccountsRepositoryPort, repo))

    with pytest.raises(RefreshError) as exc_info:
        await manager.refresh_account(account)

    # Transient (retryable) failure, never a permanent one that would be cached
    # or leave the proxy to run the unguarded permanent mark.
    assert exc_info.value.transport_error is True
    assert exc_info.value.is_permanent is False
    assert exc_info.value.code == "status_downgrade_conflict"
    # The status downgrade never landed: the account was NOT marked REAUTH.
    assert repo.status_payload is None
    assert account.status == AccountStatus.ACTIVE
    assert account.deactivation_reason is None
    # Exactly the bounded conditional CAS attempts ran, all conditioned on a
    # freshly observed ciphertext (never an unconditional ``expected=None``).
    assert len(repo.status_cas_attempts) == auth_manager_module._TOKEN_CAS_MAX_ATTEMPTS
    assert all(expected is not None for expected in repo.status_cas_attempts)


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


@pytest.mark.asyncio
async def test_successful_refresh_survives_claim_release_error(monkeypatch):
    """Regression (FINDING 2): the claim-release runs in ``finally`` after the
    token update has already committed. A transient DB error while releasing the
    claim (a SQLite lock past the busy timeout, a dropped Postgres connection)
    MUST NOT replace the successful refresh return value with a failure. The
    caller still receives the refreshed account and the stale claim is left to
    expire by its TTL."""

    async def _fake_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        return TokenRefreshResult(
            access_token="access-new",
            refresh_token="refresh-new",
            id_token="id-new",
            account_id="acc_release_error",
            plan_type="pro",
            email=None,
        )

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _fake_refresh)

    release_calls = 0

    class _ReleaseFailingClaims:
        claimant_id = "this-replica"

        async def try_acquire(self, account_id: str, *, ttl_seconds: float, owner: str) -> bool:
            del account_id, ttl_seconds, owner
            return True

        async def release(self, account_id: str, *, owner: str) -> None:
            nonlocal release_calls
            del account_id, owner
            release_calls += 1
            # Model a transient DB error releasing the claim row.
            raise OperationalError("DELETE FROM account_refresh_claims", {}, Exception("database is locked"))

    encryptor = TokenEncryptor()
    account = Account(
        id="acc_release_error",
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
    repo.accounts_by_id[account.id] = account
    manager = AuthManager(cast(AccountsRepositoryPort, repo), refresh_claims=_ReleaseFailingClaims())

    # The release error is swallowed: the successful refresh result is returned.
    result = await manager.refresh_account(account)

    assert encryptor.decrypt(result.refresh_token_encrypted) == "refresh-new"
    assert result.status == AccountStatus.ACTIVE
    # The token update committed before the release failure.
    assert repo.tokens_payload is not None
    assert encryptor.decrypt(cast(bytes, repo.tokens_payload["refresh_token_encrypted"])) == "refresh-new"
    # The release was retried the bounded number of times before being suppressed.
    assert release_calls == auth_manager_module._CLAIM_RELEASE_MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_claim_release_error_does_not_mask_refresh_body_error(monkeypatch):
    """Regression (FINDING 2): suppressing the release error must not swallow a
    genuine failure from the refresh body. When the upstream exchange raises, the
    ORIGINAL error must still propagate even though the ``finally`` release also
    fails."""

    async def _failing_refresh(_: str, **_kwargs: object) -> TokenRefreshResult:
        raise RefreshError("upstream_timeout", "boom", False, transport_error=True)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", _failing_refresh)

    class _ReleaseFailingClaims:
        claimant_id = "this-replica"

        async def try_acquire(self, account_id: str, *, ttl_seconds: float, owner: str) -> bool:
            del account_id, ttl_seconds, owner
            return True

        async def release(self, account_id: str, *, owner: str) -> None:
            del account_id, owner
            raise OperationalError("DELETE FROM account_refresh_claims", {}, Exception("database is locked"))

    encryptor = TokenEncryptor()
    account = Account(
        id="acc_release_error_body",
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
    repo.accounts_by_id[account.id] = account
    manager = AuthManager(cast(AccountsRepositoryPort, repo), refresh_claims=_ReleaseFailingClaims())

    # The body's RefreshError propagates, NOT the release OperationalError.
    with pytest.raises(RefreshError) as excinfo:
        await manager.refresh_account(account)
    assert excinfo.value.code == "upstream_timeout"


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
