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
            "workspace_id": workspace_id,
            "workspace_label": workspace_label,
            "seat_type": seat_type,
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

    async def _fake_refresh(_: str) -> TokenRefreshResult:
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
    async def _fake_refresh(_: str) -> TokenRefreshResult:
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
    async def _fake_refresh(_: str) -> TokenRefreshResult:
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
    async def _fake_refresh(_: str) -> TokenRefreshResult:
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
    async def _fake_refresh(_: str) -> TokenRefreshResult:
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
    async def _fake_refresh(_: str) -> TokenRefreshResult:
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
async def test_ensure_fresh_singleflights_concurrent_refreshes(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()
    refresh_calls = 0

    async def _fake_refresh(_: str) -> TokenRefreshResult:
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

    async def _fake_refresh(_: str) -> TokenRefreshResult:
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

    async def _fake_refresh(_: str) -> TokenRefreshResult:
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

    async def _fake_refresh(_: str) -> TokenRefreshResult:
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

    async def _fake_refresh(refresh_token: str) -> TokenRefreshResult:
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
    async def _fake_refresh(_: str) -> TokenRefreshResult:
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

    assert result is latest_account
    assert repo.status_payload is None
    assert stale_account.status == AccountStatus.ACTIVE


@pytest.mark.asyncio
async def test_refresh_account_deactivates_when_repo_only_reencrypted_same_refresh_token(monkeypatch):
    async def _fake_refresh(_: str) -> TokenRefreshResult:
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
    assert repo.status_payload["status"] == AccountStatus.DEACTIVATED


@pytest.mark.asyncio
async def test_refresh_account_deactivates_when_upstream_returns_token_expired(monkeypatch):
    """Regression for #383: a ``token_expired`` code from the OAuth refresh
    endpoint must classify as a permanent failure and deactivate the account,
    not loop retries forever while the account stays ``ACTIVE``.
    """

    async def _fake_refresh(_: str) -> TokenRefreshResult:
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
    assert repo.status_payload["status"] == AccountStatus.DEACTIVATED
    reason = repo.status_payload["deactivation_reason"]
    assert isinstance(reason, str)
    assert "re-login" in reason.lower() or "expired" in reason.lower()
