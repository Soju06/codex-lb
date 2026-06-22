from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.core.auth.dependencies import require_dashboard_write_access
from app.core.clients.rate_limit_reset_credits import (
    ConsumeResetCreditError,
    ConsumeResetCreditResponse,
    RateLimitResetCreditsSnapshot,
    ResetCreditItem,
)
from app.core.crypto import TokenEncryptor
from app.core.exceptions import (
    DashboardAuthError,
    DashboardConflictError,
    DashboardNotFoundError,
    DashboardPermissionError,
    DashboardServiceUnavailableError,
)
from app.db.models import Account, AccountStatus
from app.modules.rate_limit_reset_credits import api as reset_credits_api
from app.modules.rate_limit_reset_credits.api import (
    ConsumeResetCreditResponseSchema,
    _redeem_soonest_reset_credit,
    _select_soonest_available_credit,
    consume_rate_limit_reset_credit,
    get_rate_limit_reset_credits,
)
from app.modules.rate_limit_reset_credits.store import RateLimitResetCreditsStore

pytestmark = pytest.mark.unit


class StubEncryptor(TokenEncryptor):
    def __init__(self) -> None:
        # Skip key-file I/O; tests only exercise decrypt().
        pass

    def decrypt(self, encrypted: bytes) -> str:
        return "decrypted-access-token"


def _account(account_id: str = "acc_1") -> Account:
    return Account(
        id=account_id,
        chatgpt_account_id="workspace-1",
        email=f"{account_id}@example.com",
        plan_type="plus",
        access_token_encrypted=b"encrypted",
        refresh_token_encrypted=b"refresh",
        id_token_encrypted=b"id",
        last_refresh=datetime(2025, 1, 1),
        status=AccountStatus.ACTIVE,
    )


def _account_with_state(
    account_id: str,
    *,
    status: AccountStatus = AccountStatus.ACTIVE,
    chatgpt_account_id: str | None = "workspace-1",
) -> Account:
    account = _account(account_id)
    account.status = status
    account.chatgpt_account_id = chatgpt_account_id
    return account


def _credit(
    credit_id: str,
    *,
    status: str = "available",
    expires_at: str | None = "2026-07-12T00:00:00Z",
) -> ResetCreditItem:
    return ResetCreditItem.model_validate({"id": credit_id, "status": status, "expires_at": expires_at})


def _snapshot(credits: list[ResetCreditItem], available_count: int | None = None) -> RateLimitResetCreditsSnapshot:
    expiries = [
        credit.expires_at for credit in credits if credit.status == "available" and credit.expires_at is not None
    ]
    return RateLimitResetCreditsSnapshot(
        available_count=available_count if available_count is not None else len(credits),
        nearest_expires_at=min(expiries) if expiries else None,
        credits=credits,
    )


# --- GET endpoint ---


@pytest.mark.asyncio
async def test_get_returns_null_when_no_snapshot_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    store = RateLimitResetCreditsStore()
    # Point the module-level singleton accessor at an empty store for isolation.
    monkeypatch.setattr(reset_credits_api, "get_rate_limit_reset_credits_store", lambda: store)
    response = await get_rate_limit_reset_credits("acc_missing")
    assert response is None


@pytest.mark.asyncio
async def test_get_returns_cached_snapshot_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    store = RateLimitResetCreditsStore()
    await store.set(
        "acc_1",
        _snapshot([_credit("c1"), _credit("c2", expires_at="2026-06-20T00:00:00Z")], available_count=2),
    )
    monkeypatch.setattr(reset_credits_api, "get_rate_limit_reset_credits_store", lambda: store)
    response = await get_rate_limit_reset_credits("acc_1")

    assert response is not None
    assert response.available_count == 2
    assert response.nearest_expires_at is not None
    assert {credit.id for credit in response.credits} == {"c1", "c2"}


# --- soonest-available selection helper ---


def test_select_soonest_available_credit_picks_smallest_expires_at() -> None:
    snapshot = _snapshot(
        [
            _credit("late", expires_at="2026-07-10T00:00:00Z"),
            _credit("soon", expires_at="2026-06-20T00:00:00Z"),
            _credit("used", status="redeemed", expires_at="2026-06-01T00:00:00Z"),
        ]
    )

    selected = _select_soonest_available_credit(snapshot)

    assert selected is not None
    assert selected.id == "soon"


def test_select_soonest_available_credit_returns_none_when_no_snapshot() -> None:
    assert _select_soonest_available_credit(None) is None


def test_select_soonest_available_credit_respects_zero_available_count() -> None:
    snapshot = _snapshot([_credit("cached_available")], available_count=0)
    assert _select_soonest_available_credit(snapshot) is None


def test_select_soonest_available_credit_returns_none_when_none_available() -> None:
    snapshot = _snapshot([_credit("c1", status="redeemed")])
    assert _select_soonest_available_credit(snapshot) is None


# --- POST consume: helper covers selection, uuid body, invalidation, shape ---


@pytest.mark.asyncio
async def test_redeem_returns_409_when_no_available_credit() -> None:
    store = RateLimitResetCreditsStore()
    await store.set("acc_1", _snapshot([_credit("c1", status="redeemed")]))

    with pytest.raises(DashboardConflictError) as excinfo:
        await _redeem_soonest_reset_credit(
            account=_account(),
            store=store,
            encryptor=StubEncryptor(),
            consume_fn=_raise_not_called,  # type: ignore[arg-type]
        )
    assert excinfo.value.code == "no_available_reset_credit"


@pytest.mark.asyncio
async def test_redeem_returns_409_when_cached_count_is_zero() -> None:
    store = RateLimitResetCreditsStore()
    await store.set("acc_1", _snapshot([_credit("cached_available")], available_count=0))

    with pytest.raises(DashboardConflictError) as excinfo:
        await _redeem_soonest_reset_credit(
            account=_account(),
            store=store,
            encryptor=StubEncryptor(),
            consume_fn=_raise_not_called,  # type: ignore[arg-type]
        )
    assert excinfo.value.code == "no_available_reset_credit"


@pytest.mark.asyncio
async def test_redeem_returns_409_when_snapshot_missing() -> None:
    store = RateLimitResetCreditsStore()
    with pytest.raises(DashboardConflictError):
        await _redeem_soonest_reset_credit(
            account=_account(),
            store=store,
            encryptor=StubEncryptor(),
            consume_fn=_raise_not_called,  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_redeem_selects_soonest_calls_upstream_and_invalidates_cache() -> None:
    store = RateLimitResetCreditsStore()
    await store.set(
        "acc_1",
        _snapshot(
            [
                _credit("late", expires_at="2026-07-10T00:00:00Z"),
                _credit("soon", expires_at="2026-06-20T00:00:00Z"),
            ]
        ),
    )

    captured: dict[str, Any] = {}

    async def consume_fn(
        access_token: str,
        account_id: str | None,
        credit_id: str,
        *,
        route: object | None = None,
        allow_direct_egress: bool = False,
    ) -> ConsumeResetCreditResponse:
        captured.update(
            {
                "access_token": access_token,
                "account_id": account_id,
                "credit_id": credit_id,
                "route": route,
                "allow_direct_egress": allow_direct_egress,
            }
        )
        return ConsumeResetCreditResponse.model_validate(
            {
                "code": "reset",
                "credit": {"id": credit_id, "status": "redeemed", "redeemed_at": "2026-06-13T13:12:31Z"},
                "windows_reset": 1,
            }
        )

    result = await _redeem_soonest_reset_credit(
        account=_account(),
        store=store,
        encryptor=StubEncryptor(),
        consume_fn=consume_fn,
    )

    # The soonest-expiring credit id was forwarded with the decrypted token + workspace id.
    assert captured == {
        "access_token": "decrypted-access-token",
        "account_id": "workspace-1",
        "credit_id": "soon",
        "route": None,
        "allow_direct_egress": True,
    }
    # Successful redemption invalidates the in-memory snapshot so the next
    # dashboard refresh repulls upstream state instead of serving a local edit.
    assert store.get("acc_1") is None
    # Response shape matches the documented {code, windows_reset, redeemed_at}.
    assert isinstance(result, ConsumeResetCreditResponseSchema)
    assert result.code == "reset"
    assert result.windows_reset == 1
    assert result.redeemed_at is not None
    assert result.redeemed_at.year == 2026


@pytest.mark.asyncio
async def test_redeem_serializes_requests_per_account() -> None:
    store = RateLimitResetCreditsStore()
    await store.set("acc_1", _snapshot([_credit("only")], available_count=1))

    started = asyncio.Event()
    release = asyncio.Event()
    consume_calls: list[str] = []

    async def consume_fn(
        access_token: str,
        account_id: str | None,
        credit_id: str,
        *,
        route: object | None = None,
        allow_direct_egress: bool = False,
    ) -> ConsumeResetCreditResponse:
        assert route is None
        assert allow_direct_egress is True
        consume_calls.append(credit_id)
        started.set()
        await release.wait()
        return ConsumeResetCreditResponse.model_validate(
            {
                "code": "reset",
                "credit": {"id": credit_id, "status": "redeemed", "redeemed_at": "2026-06-13T13:12:31Z"},
                "windows_reset": 1,
            }
        )

    first = asyncio.create_task(
        _redeem_soonest_reset_credit(
            account=_account(),
            store=store,
            encryptor=StubEncryptor(),
            consume_fn=consume_fn,
        )
    )
    await started.wait()

    second = asyncio.create_task(
        _redeem_soonest_reset_credit(
            account=_account(),
            store=store,
            encryptor=StubEncryptor(),
            consume_fn=consume_fn,
        )
    )
    await asyncio.sleep(0)

    assert consume_calls == ["only"]

    release.set()
    await first

    with pytest.raises(DashboardConflictError) as excinfo:
        await second
    assert excinfo.value.code == "no_available_reset_credit"
    assert consume_calls == ["only"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_exception"),
    [
        (401, DashboardAuthError),
        (403, DashboardPermissionError),
        (409, DashboardConflictError),
        (503, DashboardServiceUnavailableError),
        (0, DashboardServiceUnavailableError),
    ],
)
async def test_redeem_translates_upstream_consume_failures(
    status_code: int,
    expected_exception: type[Exception],
) -> None:
    store = RateLimitResetCreditsStore()
    await store.set("acc_1", _snapshot([_credit("only")], available_count=1))

    async def consume_fn(
        access_token: str,
        account_id: str | None,
        credit_id: str,
        *,
        route: object | None = None,
        allow_direct_egress: bool = False,
    ) -> ConsumeResetCreditResponse:
        assert route is None
        assert allow_direct_egress is True
        raise ConsumeResetCreditError(status_code, f"upstream failed {status_code}", code=f"upstream_{status_code}")

    with pytest.raises(expected_exception) as excinfo:
        await _redeem_soonest_reset_credit(
            account=_account(),
            store=store,
            encryptor=StubEncryptor(),
            consume_fn=consume_fn,
        )

    assert str(excinfo.value) == f"upstream failed {status_code}"
    assert getattr(excinfo.value, "code", None) == f"upstream_{status_code}"
    assert store.get("acc_1") is not None


# --- POST consume: handler-level 404 when account missing ---


@pytest.mark.asyncio
async def test_consume_handler_returns_404_when_account_missing() -> None:
    class _Repo:
        async def get_by_id(self, account_id: str) -> Account | None:
            return None

    fake_context = SimpleNamespace(repository=_Repo())

    with pytest.raises(DashboardNotFoundError):
        await consume_rate_limit_reset_credit(
            account_id="missing",
            _write_access=None,
            context=cast(Any, fake_context),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [AccountStatus.PAUSED, AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED])
async def test_consume_handler_rejects_ineligible_account_status_and_invalidates_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    status: AccountStatus,
) -> None:
    store = RateLimitResetCreditsStore()
    await store.set("acc_disabled", _snapshot([_credit("stale")], available_count=1))

    class _Repo:
        async def get_by_id(self, account_id: str) -> Account | None:
            return _account_with_state(account_id, status=status)

    async def _route_not_called(*args: Any, **kwargs: Any) -> object:
        raise AssertionError("ineligible accounts must be rejected before route resolution")

    monkeypatch.setattr(reset_credits_api, "get_rate_limit_reset_credits_store", lambda: store)
    monkeypatch.setattr(reset_credits_api, "resolve_upstream_route", _route_not_called)
    fake_context = SimpleNamespace(repository=_Repo())

    with pytest.raises(DashboardConflictError) as excinfo:
        await consume_rate_limit_reset_credit(
            account_id="acc_disabled",
            _write_access=None,
            context=cast(Any, fake_context),
        )

    assert excinfo.value.code == "reset_credit_account_ineligible"
    assert store.get("acc_disabled") is None


@pytest.mark.asyncio
async def test_consume_handler_rejects_account_without_chatgpt_account_id_and_invalidates_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = RateLimitResetCreditsStore()
    await store.set("acc_no_workspace", _snapshot([_credit("stale")], available_count=1))

    class _Repo:
        async def get_by_id(self, account_id: str) -> Account | None:
            return _account_with_state(account_id, chatgpt_account_id=None)

    monkeypatch.setattr(reset_credits_api, "get_rate_limit_reset_credits_store", lambda: store)
    fake_context = SimpleNamespace(repository=_Repo())

    with pytest.raises(DashboardConflictError) as excinfo:
        await consume_rate_limit_reset_credit(
            account_id="acc_no_workspace",
            _write_access=None,
            context=cast(Any, fake_context),
        )

    assert excinfo.value.code == "reset_credit_account_ineligible"
    assert store.get("acc_no_workspace") is None


@pytest.mark.asyncio
async def test_consume_handler_force_refreshes_usage_after_success(monkeypatch: pytest.MonkeyPatch) -> None:
    store = RateLimitResetCreditsStore()
    await store.set("acc_refresh", _snapshot([_credit("credit-refresh")], available_count=1))
    refresh_calls: list[str] = []
    invalidated: list[str] = []

    class _Repo:
        async def get_by_id(self, account_id: str) -> Account | None:
            return _account_with_state(account_id)

    class _UsageUpdater:
        def __init__(self, usage_repo: object, accounts_repo: object, additional_usage_repo: object) -> None:
            assert usage_repo is not None
            assert accounts_repo is not None
            assert additional_usage_repo is not None

        async def force_refresh(self, account: Account) -> bool:
            refresh_calls.append(account.id)
            return True

    class _SelectionCache:
        def invalidate(self) -> None:
            invalidated.append("selection")

    async def _consume_fn(*args: Any, **kwargs: Any) -> ConsumeResetCreditResponse:
        return ConsumeResetCreditResponse.model_validate(
            {
                "code": "reset",
                "credit": {
                    "id": "credit-refresh",
                    "status": "redeemed",
                    "redeemed_at": "2026-06-13T13:12:31Z",
                },
                "windows_reset": 1,
            }
        )

    async def _resolve_route(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(reset_credits_api, "get_rate_limit_reset_credits_store", lambda: store)
    monkeypatch.setattr(reset_credits_api, "consume_reset_credit", _consume_fn)
    monkeypatch.setattr(reset_credits_api, "resolve_upstream_route", _resolve_route)
    monkeypatch.setattr(reset_credits_api, "TokenEncryptor", lambda: StubEncryptor())
    monkeypatch.setattr(reset_credits_api, "UsageUpdater", _UsageUpdater)
    monkeypatch.setattr(reset_credits_api, "get_account_selection_cache", lambda: _SelectionCache())

    response = await consume_rate_limit_reset_credit(
        account_id="acc_refresh",
        _write_access=None,
        context=cast(Any, SimpleNamespace(repository=_Repo(), session=object())),
    )

    assert response.code == "reset"
    assert store.get("acc_refresh") is None
    assert refresh_calls == ["acc_refresh"]
    assert invalidated == ["selection"]


# --- POST consume: write-access gating refuses guests (full ASGI path) ---


@pytest.mark.asyncio
async def test_consume_refuses_read_only_guest(app_instance, async_client) -> None:  # type: ignore[no-untyped-def]
    async def _guest_refused(_request: Any = None) -> None:
        raise DashboardPermissionError(
            "Read-only dashboard access cannot modify dashboard state",
            code="read_only_access",
        )

    app_instance.dependency_overrides[require_dashboard_write_access] = _guest_refused
    try:
        response = await async_client.post("/api/accounts/acc_guest/rate-limit-reset-credits/consume")
    finally:
        app_instance.dependency_overrides.pop(require_dashboard_write_access, None)

    assert response.status_code == 403


async def _raise_not_called(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("consume_fn must not be called when no credit is available")
