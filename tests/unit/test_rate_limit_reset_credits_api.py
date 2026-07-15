from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import Request

from app.core.auth.dependencies import require_dashboard_write_access
from app.core.auth.refresh import RefreshError
from app.core.clients.rate_limit_reset_credits import (
    ConsumeResetCreditError,
    ConsumeResetCreditResponse,
    RateLimitResetCreditsSnapshot,
    ResetCreditFetchError,
    ResetCreditItem,
    ResetCreditsResponse,
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
    ResetCreditRedeemRequestAlreadyPinned,
    _assert_account_can_redeem_reset_credit,
    _build_refresh_usage_callback,
    _redeem_soonest_reset_credit,
    _select_available_credit_by_id,
    _select_soonest_available_credit,
    _select_soonest_available_credit_from_response,
    consume_rate_limit_reset_credit,
    get_rate_limit_reset_credits,
    serialize_reset_credit_redeem,
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


def _credit(
    credit_id: str,
    *,
    status: str = "available",
    expires_at: str | None = "2026-07-12T00:00:00Z",
) -> ResetCreditItem:
    return ResetCreditItem.model_validate({"id": credit_id, "status": status, "expires_at": expires_at})


def _response(credits: list[ResetCreditItem], available_count: int | None = None) -> ResetCreditsResponse:
    count = available_count if available_count is not None else len(credits)
    return ResetCreditsResponse(credits=credits, available_count=count)


def _fake_request(host: str = "127.0.0.1") -> Request:
    return cast(Request, SimpleNamespace(client=SimpleNamespace(host=host)))


@pytest.fixture(autouse=True)
def fake_redeem_ledger(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, str], str]:
    """In-memory stand-in for the shared-DB redeem idempotency ledger.

    Autouse so every helper test is hermetic: the no-body dashboard path now
    synthesizes a redeem_request_id and pins it, so a success reaches
    ``pin_redeem_request`` even without an explicit fixture request. Tests that
    request it by name receive the same dict instance to assert ledger state.
    The real DB-backed ledger is covered by
    tests/integration/test_reset_credits_replica_safety.py.
    """
    ledger: dict[tuple[str, str], str] = {}

    async def get_pinned(account_id: str, redeem_request_id: str) -> str | None:
        return ledger.get((account_id, redeem_request_id))

    async def pin(account_id: str, redeem_request_id: str, credit_id: str) -> str:
        return ledger.setdefault((account_id, redeem_request_id), credit_id)

    monkeypatch.setattr(reset_credits_api, "get_pinned_redeem_credit_id", get_pinned)
    monkeypatch.setattr(reset_credits_api, "pin_redeem_request", pin)
    return ledger


def _static_fetch_fn(response: ResetCreditsResponse):
    async def fetch_fn(*args: Any, **kwargs: Any) -> ResetCreditsResponse:
        return response

    return fetch_fn


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
    monkeypatch.setattr(reset_credits_api, "get_rate_limit_reset_credits_store", lambda: store)

    class _Repo:
        async def get_by_id(self, account_id: str) -> Account | None:
            return None

    fake_context = SimpleNamespace(repository=_Repo())
    response = await get_rate_limit_reset_credits("acc_missing", context=cast(Any, fake_context))
    assert response is None


@pytest.mark.asyncio
async def test_get_returns_null_on_cache_miss_for_active_account(monkeypatch: pytest.MonkeyPatch) -> None:
    store = RateLimitResetCreditsStore()
    monkeypatch.setattr(reset_credits_api, "get_rate_limit_reset_credits_store", lambda: store)

    class _Repo:
        async def get_by_id(self, account_id: str) -> Account | None:
            return _account(account_id)

    fake_context = SimpleNamespace(
        repository=_Repo(),
        service=SimpleNamespace(_auth_manager=None),
    )
    response = await get_rate_limit_reset_credits("acc_1", context=cast(Any, fake_context))

    assert response is None
    assert store.get("acc_1") is None


@pytest.mark.asyncio
async def test_get_returns_cached_snapshot_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    store = RateLimitResetCreditsStore()
    await store.set(
        "acc_1",
        _snapshot([_credit("c1"), _credit("c2", expires_at="2026-06-20T00:00:00Z")], available_count=2),
    )
    monkeypatch.setattr(reset_credits_api, "get_rate_limit_reset_credits_store", lambda: store)

    class _Repo:
        async def get_by_id(self, account_id: str) -> Account | None:
            return _account(account_id)

    fake_context = SimpleNamespace(repository=_Repo())
    response = await get_rate_limit_reset_credits("acc_1", context=cast(Any, fake_context))

    assert response is not None
    assert response.available_count == 2
    assert response.nearest_expires_at is not None
    assert {credit.id for credit in response.credits} == {"c1", "c2"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [AccountStatus.PAUSED, AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED],
)
async def test_get_invalidates_cached_snapshot_for_ineligible_status(
    status: AccountStatus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = RateLimitResetCreditsStore()
    await store.set("acc_1", _snapshot([_credit("stale")], available_count=1))
    monkeypatch.setattr(reset_credits_api, "get_rate_limit_reset_credits_store", lambda: store)

    class _Repo:
        async def get_by_id(self, account_id: str) -> Account | None:
            account = _account(account_id)
            account.status = status
            return account

    fake_context = SimpleNamespace(repository=_Repo())

    response = await get_rate_limit_reset_credits("acc_1", context=cast(Any, fake_context))

    assert response is None
    assert store.get("acc_1") is None


@pytest.mark.asyncio
async def test_get_invalidates_cached_snapshot_without_chatgpt_account_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = RateLimitResetCreditsStore()
    await store.set("acc_1", _snapshot([_credit("stale")], available_count=1))
    monkeypatch.setattr(reset_credits_api, "get_rate_limit_reset_credits_store", lambda: store)

    class _Repo:
        async def get_by_id(self, account_id: str) -> Account | None:
            account = _account(account_id)
            account.chatgpt_account_id = None
            return account

    fake_context = SimpleNamespace(repository=_Repo())

    response = await get_rate_limit_reset_credits("acc_1", context=cast(Any, fake_context))

    assert response is None
    assert store.get("acc_1") is None


# --- soonest-available selection helper ---


def test_select_soonest_available_credit_picks_smallest_expires_at() -> None:
    credits = [
        _credit("late", expires_at="2026-07-10T00:00:00Z"),
        _credit("soon", expires_at="2026-06-20T00:00:00Z"),
        _credit("used", status="redeemed", expires_at="2026-06-01T00:00:00Z"),
    ]
    snapshot = _snapshot(credits)

    selected = _select_soonest_available_credit(snapshot)

    assert selected is not None
    assert selected.id == "soon"

    response_selected = _select_soonest_available_credit_from_response(_response(credits))
    assert response_selected is not None
    assert response_selected.id == "soon"


def test_select_soonest_available_credit_returns_none_when_no_snapshot() -> None:
    assert _select_soonest_available_credit(None) is None


def test_select_soonest_available_credit_respects_zero_available_count() -> None:
    snapshot = _snapshot([_credit("cached_available")], available_count=0)
    assert _select_soonest_available_credit(snapshot) is None


def test_select_soonest_available_credit_returns_none_when_none_available() -> None:
    snapshot = _snapshot([_credit("c1", status="redeemed")])
    assert _select_soonest_available_credit(snapshot) is None


def test_select_available_credit_by_id_returns_matching_available_credit() -> None:
    response = _response([_credit("wanted"), _credit("other")], available_count=2)
    selected = _select_available_credit_by_id(response, "wanted")
    assert selected is not None
    assert selected.id == "wanted"


def test_select_available_credit_by_id_rejects_missing_or_unavailable_credit() -> None:
    response = _response([_credit("wanted", status="redeemed")], available_count=1)
    assert _select_available_credit_by_id(response, "wanted") is None
    assert _select_available_credit_by_id(response, "missing") is None


# --- POST consume: helper covers selection, uuid body, invalidation, shape ---


@pytest.mark.asyncio
async def test_redeem_returns_409_when_no_available_credit() -> None:
    store = RateLimitResetCreditsStore()
    await store.set("acc_1", _snapshot([_credit("c1", status="redeemed")], available_count=1))

    with pytest.raises(DashboardConflictError) as excinfo:
        await _redeem_soonest_reset_credit(
            account=_account(),
            store=store,
            encryptor=StubEncryptor(),
            fetch_fn=_static_fetch_fn(_response([_credit("c1", status="redeemed")])),
            consume_fn=_raise_not_called,  # type: ignore[arg-type]
        )
    assert excinfo.value.code == "no_available_reset_credit"
    cached = store.get("acc_1")
    assert cached is not None
    assert cached.available_count == 1
    assert cached.credits[0].status == "redeemed"


@pytest.mark.asyncio
async def test_redeem_returns_409_when_cached_count_is_zero() -> None:
    store = RateLimitResetCreditsStore()
    await store.set("acc_1", _snapshot([_credit("cached_available")], available_count=0))

    with pytest.raises(DashboardConflictError) as excinfo:
        await _redeem_soonest_reset_credit(
            account=_account(),
            store=store,
            encryptor=StubEncryptor(),
            fetch_fn=_raise_not_called,  # type: ignore[arg-type]
            consume_fn=_raise_not_called,  # type: ignore[arg-type]
        )
    assert excinfo.value.code == "no_available_reset_credit"
    cached = store.get("acc_1")
    assert cached is not None
    assert cached.available_count == 0
    assert cached.credits[0].status == "available"


@pytest.mark.asyncio
async def test_redeem_returns_409_when_snapshot_missing() -> None:
    store = RateLimitResetCreditsStore()
    with pytest.raises(DashboardConflictError):
        await _redeem_soonest_reset_credit(
            account=_account(),
            store=store,
            encryptor=StubEncryptor(),
            fetch_fn=_raise_not_called,  # type: ignore[arg-type]
            consume_fn=_raise_not_called,  # type: ignore[arg-type]
        )
    assert store.get("acc_1") is None


@pytest.mark.asyncio
async def test_redeem_replaces_stale_cached_snapshot_when_fresh_fetch_has_no_available_credit() -> None:
    store = RateLimitResetCreditsStore()
    await store.set("acc_1", _snapshot([_credit("stale")], available_count=1))

    with pytest.raises(DashboardConflictError) as excinfo:
        await _redeem_soonest_reset_credit(
            account=_account(),
            store=store,
            encryptor=StubEncryptor(),
            fetch_fn=_static_fetch_fn(_response([], available_count=0)),
            consume_fn=_raise_not_called,  # type: ignore[arg-type]
        )

    assert excinfo.value.code == "no_available_reset_credit"
    cached = store.get("acc_1")
    assert cached is not None
    assert cached.available_count == 0
    assert cached.credits == []


@pytest.mark.asyncio
async def test_redeem_with_request_id_but_no_pin_returns_conflict_on_empty_fresh_fetch(
    fake_redeem_ledger: dict[tuple[str, str], str],
) -> None:
    """A redeem_request_id with NO durable pin is not proof of an idempotent
    retry: when the fresh pre-consume fetch reports nothing available, the
    stale cached credit MUST NOT be pinned and consumed. The endpoint returns a
    conflict, replaces the stale snapshot with the fresh empty one, and pins
    nothing."""
    store = RateLimitResetCreditsStore()
    await store.set("acc_1", _snapshot([_credit("cached")], available_count=1))

    with pytest.raises(DashboardConflictError) as excinfo:
        await _redeem_soonest_reset_credit(
            account=_account(),
            store=store,
            encryptor=StubEncryptor(),
            fetch_fn=_static_fetch_fn(_response([], available_count=0)),
            consume_fn=_raise_not_called,  # type: ignore[arg-type]
            redeem_request_id="retry-id",
        )

    assert excinfo.value.code == "no_available_reset_credit"
    cached = store.get("acc_1")
    assert cached is not None
    assert cached.available_count == 0
    assert cached.credits == []
    # Nothing pinned for the unproven retry.
    assert fake_redeem_ledger == {}


@pytest.mark.asyncio
async def test_redeem_retries_same_request_id_after_local_credit_vanishes_with_durable_pin(
    fake_redeem_ledger: dict[tuple[str, str], str],
) -> None:
    """When a durable pin already exists, a same-request retry consumes the
    pinned credit even though the fresh fetch shows nothing available."""
    store = RateLimitResetCreditsStore()
    fake_redeem_ledger[("acc_1", "retry-id")] = "cached"
    await store.set("acc_1", _snapshot([_credit("cached")], available_count=1))

    captured: dict[str, Any] = {}

    async def consume_fn(
        access_token: str,
        account_id: str | None,
        credit_id: str,
        **kwargs: Any,
    ) -> ConsumeResetCreditResponse:
        captured.update(
            {
                "access_token": access_token,
                "account_id": account_id,
                "credit_id": credit_id,
                "redeem_request_id": kwargs.get("redeem_request_id"),
            }
        )
        return ConsumeResetCreditResponse.model_validate(
            {
                "code": "already_redeemed",
                "credit": {"id": credit_id, "status": "redeemed", "redeemed_at": "2026-06-13T13:12:31Z"},
                "windows_reset": 0,
            }
        )

    result = await _redeem_soonest_reset_credit(
        account=_account(),
        store=store,
        encryptor=StubEncryptor(),
        fetch_fn=_static_fetch_fn(_response([], available_count=0)),
        consume_fn=consume_fn,
        redeem_request_id="retry-id",
    )

    assert captured == {
        "access_token": "decrypted-access-token",
        "account_id": "workspace-1",
        "credit_id": "cached",
        "redeem_request_id": "retry-id",
    }
    assert result.response.code == "already_redeemed"
    assert result.available_count_before == 0
    assert result.available_count_after == 0
    assert store.get("acc_1") is None
    # The pre-existing durable pin is preserved.
    assert fake_redeem_ledger == {("acc_1", "retry-id"): "cached"}


@pytest.mark.asyncio
async def test_redeem_skip_if_request_pinned_avoids_upstream_retry(
    fake_redeem_ledger: dict[tuple[str, str], str],
) -> None:
    store = RateLimitResetCreditsStore()
    fake_redeem_ledger[("acc_1", "auto-id")] = "cached"
    await store.set("acc_1", _snapshot([_credit("cached")], available_count=1))

    with pytest.raises(ResetCreditRedeemRequestAlreadyPinned) as excinfo:
        await _redeem_soonest_reset_credit(
            account=_account(),
            store=store,
            encryptor=StubEncryptor(),
            fetch_fn=_raise_not_called,  # type: ignore[arg-type]
            consume_fn=_raise_not_called,  # type: ignore[arg-type]
            redeem_request_id="auto-id",
            skip_if_redeem_request_pinned=True,
        )

    assert excinfo.value.account_id == "acc_1"
    assert excinfo.value.credit_id == "cached"
    assert fake_redeem_ledger == {("acc_1", "auto-id"): "cached"}


@pytest.mark.asyncio
async def test_redeem_retries_same_request_id_after_local_credit_vanishes(
    fake_redeem_ledger: dict[tuple[str, str], str],
) -> None:
    store = RateLimitResetCreditsStore()
    fake_redeem_ledger[("acc_1", "retry-id")] = "vanished"
    await store.set("acc_1", _snapshot([], available_count=0))

    captured: dict[str, Any] = {}

    async def consume_fn(
        access_token: str,
        account_id: str | None,
        credit_id: str,
        **kwargs: Any,
    ) -> ConsumeResetCreditResponse:
        captured.update(
            {
                "access_token": access_token,
                "account_id": account_id,
                "credit_id": credit_id,
                "redeem_request_id": kwargs.get("redeem_request_id"),
            }
        )
        return ConsumeResetCreditResponse.model_validate(
            {
                "code": "already_redeemed",
                "credit": {"id": credit_id, "status": "redeemed", "redeemed_at": "2026-06-13T13:12:31Z"},
                "windows_reset": 0,
            }
        )

    result = await _redeem_soonest_reset_credit(
        account=_account(),
        store=store,
        encryptor=StubEncryptor(),
        fetch_fn=_static_fetch_fn(_response([], available_count=0)),
        consume_fn=consume_fn,
        redeem_request_id="retry-id",
    )

    assert captured == {
        "access_token": "decrypted-access-token",
        "account_id": "workspace-1",
        "credit_id": "vanished",
        "redeem_request_id": "retry-id",
    }
    assert result.response.code == "already_redeemed"
    assert store.get("acc_1") is None


@pytest.mark.asyncio
async def test_redeem_retries_same_request_id_preserves_original_credit_when_another_is_available(
    fake_redeem_ledger: dict[tuple[str, str], str],
) -> None:
    store = RateLimitResetCreditsStore()
    fake_redeem_ledger[("acc_1", "retry-id")] = "original"
    await store.set("acc_1", _snapshot([_credit("new-cached")], available_count=1))

    captured: dict[str, Any] = {}

    async def consume_fn(
        access_token: str,
        account_id: str | None,
        credit_id: str,
        **kwargs: Any,
    ) -> ConsumeResetCreditResponse:
        captured.update(
            {
                "access_token": access_token,
                "account_id": account_id,
                "credit_id": credit_id,
                "redeem_request_id": kwargs.get("redeem_request_id"),
            }
        )
        return ConsumeResetCreditResponse.model_validate(
            {
                "code": "already_redeemed",
                "credit": {"id": credit_id, "status": "redeemed", "redeemed_at": "2026-06-13T13:12:31Z"},
                "windows_reset": 0,
            }
        )

    result = await _redeem_soonest_reset_credit(
        account=_account(),
        store=store,
        encryptor=StubEncryptor(),
        fetch_fn=_static_fetch_fn(_response([_credit("new-fresh")], available_count=1)),
        consume_fn=consume_fn,
        redeem_request_id="retry-id",
    )

    assert captured == {
        "access_token": "decrypted-access-token",
        "account_id": "workspace-1",
        "credit_id": "original",
        "redeem_request_id": "retry-id",
    }
    assert result.response.code == "already_redeemed"
    assert fake_redeem_ledger[("acc_1", "retry-id")] == "original"


@pytest.mark.asyncio
async def test_redeem_without_request_id_synthesizes_one_and_pins_ledger(
    fake_redeem_ledger: dict[tuple[str, str], str],
) -> None:
    """The no-body dashboard path (no client redeem_request_id) MUST still
    participate in the durable ledger: the endpoint synthesizes a UUID, pins the
    selected credit, and forwards that recorded id to upstream instead of an
    unrecorded one."""
    store = RateLimitResetCreditsStore()
    await store.set("acc_1", _snapshot([_credit("c1")], available_count=1))

    captured: dict[str, Any] = {}

    async def consume_fn(
        access_token: str,
        account_id: str | None,
        credit_id: str,
        **kwargs: Any,
    ) -> ConsumeResetCreditResponse:
        captured.update({"credit_id": credit_id, "redeem_request_id": kwargs.get("redeem_request_id")})
        return ConsumeResetCreditResponse.model_validate(
            {
                "code": "reset",
                "credit": {"id": credit_id, "status": "redeemed", "redeemed_at": "2026-06-13T13:12:31Z"},
                "windows_reset": 1,
            }
        )

    await _redeem_soonest_reset_credit(
        account=_account(),
        store=store,
        encryptor=StubEncryptor(),
        fetch_fn=_static_fetch_fn(_response([_credit("c1")], available_count=1)),
        consume_fn=consume_fn,
        # No redeem_request_id: the still-supported no-body consume path.
    )

    # A synthesized id was recorded and forwarded to upstream (not None).
    synthesized_id = captured["redeem_request_id"]
    assert isinstance(synthesized_id, str) and synthesized_id
    assert captured["credit_id"] == "c1"
    # Exactly one ledger row was pinned for the synthesized id -> selected credit.
    assert fake_redeem_ledger == {("acc_1", synthesized_id): "c1"}


@pytest.mark.asyncio
async def test_redeem_consumes_fresh_available_credit_when_cached_credit_disappears_upstream() -> None:
    store = RateLimitResetCreditsStore()
    await store.set("acc_1", _snapshot([_credit("stale")], available_count=1))

    captured: dict[str, Any] = {}

    async def consume_fn(
        access_token: str,
        account_id: str | None,
        credit_id: str,
        **kwargs: Any,
    ) -> ConsumeResetCreditResponse:
        captured.update({"access_token": access_token, "account_id": account_id, "credit_id": credit_id})
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
        fetch_fn=_static_fetch_fn(_response([_credit("other")], available_count=1)),
        consume_fn=consume_fn,
    )

    assert captured == {
        "access_token": "decrypted-access-token",
        "account_id": "workspace-1",
        "credit_id": "other",
    }
    assert result.available_count_before == 1
    assert result.available_count_after == 0
    assert store.get("acc_1") is None


@pytest.mark.asyncio
async def test_redeem_expected_credit_id_does_not_retarget_other_fresh_credit(
    fake_redeem_ledger: dict[tuple[str, str], str],
) -> None:
    store = RateLimitResetCreditsStore()
    await store.set("acc_1", _snapshot([_credit("target")], available_count=1))
    consume_calls: list[str] = []
    fresh_response = _response([_credit("later", expires_at="2026-08-01T00:00:00Z")], available_count=1)

    async def consume_fn(
        access_token: str,
        account_id: str | None,
        credit_id: str,
        **kwargs: Any,
    ) -> ConsumeResetCreditResponse:
        consume_calls.append(credit_id)
        return ConsumeResetCreditResponse.model_validate(
            {
                "code": "reset",
                "credit": {"id": credit_id, "status": "redeemed", "redeemed_at": "2026-06-13T13:12:31Z"},
                "windows_reset": 1,
            }
        )

    with pytest.raises(DashboardConflictError) as excinfo:
        await _redeem_soonest_reset_credit(
            account=_account(),
            store=store,
            encryptor=StubEncryptor(),
            fetch_fn=_static_fetch_fn(fresh_response),
            consume_fn=consume_fn,
            redeem_request_id="auto-id",
            expected_credit_id="target",
            expected_credit_expires_at=datetime(2026, 7, 12),
        )

    assert excinfo.value.code == "no_available_reset_credit"
    assert consume_calls == []
    assert fake_redeem_ledger == {}
    snapshot = store.get("acc_1")
    assert snapshot is not None
    assert [credit.id for credit in snapshot.credits] == ["later"]


@pytest.mark.asyncio
async def test_redeem_expected_credit_expiry_change_does_not_consume(
    fake_redeem_ledger: dict[tuple[str, str], str],
) -> None:
    store = RateLimitResetCreditsStore()
    await store.set("acc_1", _snapshot([_credit("target")], available_count=1))
    consume_calls: list[str] = []
    fresh_response = _response([_credit("target", expires_at="2026-08-01T00:00:00Z")], available_count=1)

    async def consume_fn(
        access_token: str,
        account_id: str | None,
        credit_id: str,
        **kwargs: Any,
    ) -> ConsumeResetCreditResponse:
        consume_calls.append(credit_id)
        return ConsumeResetCreditResponse.model_validate(
            {
                "code": "reset",
                "credit": {"id": credit_id, "status": "redeemed", "redeemed_at": "2026-06-13T13:12:31Z"},
                "windows_reset": 1,
            }
        )

    with pytest.raises(DashboardConflictError) as excinfo:
        await _redeem_soonest_reset_credit(
            account=_account(),
            store=store,
            encryptor=StubEncryptor(),
            fetch_fn=_static_fetch_fn(fresh_response),
            consume_fn=consume_fn,
            redeem_request_id="auto-id",
            expected_credit_id="target",
            expected_credit_expires_at=datetime(2026, 7, 12),
        )

    assert excinfo.value.code == "target_reset_credit_changed"
    assert consume_calls == []
    assert fake_redeem_ledger == {}
    snapshot = store.get("acc_1")
    assert snapshot is not None
    assert [credit.id for credit in snapshot.credits] == ["target"]


@pytest.mark.asyncio
async def test_redeem_reselects_soonest_available_credit_from_fresh_fetch() -> None:
    store = RateLimitResetCreditsStore()
    await store.set(
        "acc_1",
        _snapshot([_credit("cached", expires_at="2026-06-30T00:00:00Z")], available_count=1),
    )

    captured: dict[str, Any] = {}

    async def consume_fn(
        access_token: str,
        account_id: str | None,
        credit_id: str,
        **kwargs: Any,
    ) -> ConsumeResetCreditResponse:
        captured.update({"access_token": access_token, "account_id": account_id, "credit_id": credit_id})
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
        fetch_fn=_static_fetch_fn(
            _response(
                [
                    _credit("later", expires_at="2026-07-10T00:00:00Z"),
                    _credit("fresh-soonest", expires_at="2026-06-20T00:00:00Z"),
                ]
            )
        ),
        consume_fn=consume_fn,
    )

    assert captured == {
        "access_token": "decrypted-access-token",
        "account_id": "workspace-1",
        "credit_id": "fresh-soonest",
    }
    assert result.available_count_before == 2
    assert result.available_count_after == 1
    assert store.get("acc_1") is None


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
    refreshed: list[str] = []

    async def consume_fn(
        access_token: str,
        account_id: str | None,
        credit_id: str,
        **kwargs: Any,
    ) -> ConsumeResetCreditResponse:
        captured.update({"access_token": access_token, "account_id": account_id, "credit_id": credit_id})
        return ConsumeResetCreditResponse.model_validate(
            {
                "code": "reset",
                "credit": {"id": credit_id, "status": "redeemed", "redeemed_at": "2026-06-13T13:12:31Z"},
                "windows_reset": 1,
            }
        )

    async def refresh_usage(account: Account) -> None:
        refreshed.append(account.id)

    result = await _redeem_soonest_reset_credit(
        account=_account(),
        store=store,
        encryptor=StubEncryptor(),
        fetch_fn=_static_fetch_fn(
            _response(
                [
                    _credit("late", expires_at="2026-07-10T00:00:00Z"),
                    _credit("soon", expires_at="2026-06-20T00:00:00Z"),
                ]
            )
        ),
        consume_fn=consume_fn,
        refresh_usage=refresh_usage,
    )

    # The soonest-expiring credit id was forwarded with the decrypted token + workspace id.
    assert captured == {
        "access_token": "decrypted-access-token",
        "account_id": "workspace-1",
        "credit_id": "soon",
    }
    # Successful redemption invalidates the in-memory snapshot so the next
    # dashboard refresh repulls upstream state instead of serving a local edit.
    assert store.get("acc_1") is None
    assert result.available_count_before == 2
    assert result.available_count_after == 1
    assert isinstance(result.response, ConsumeResetCreditResponseSchema)
    assert result.response.code == "reset"
    assert result.response.windows_reset == 1
    assert result.response.redeemed_at is not None
    assert result.response.redeemed_at.year == 2026
    assert refreshed == ["acc_1"]


@pytest.mark.asyncio
async def test_redeem_restores_snapshot_when_usage_refresh_fails() -> None:
    store = RateLimitResetCreditsStore()
    await store.set("acc_1", _snapshot([_credit("only")], available_count=1))
    fetch_calls = 0

    async def fetch_fn(*args: Any, **kwargs: Any) -> ResetCreditsResponse:
        nonlocal fetch_calls
        fetch_calls += 1
        if fetch_calls == 1:
            return _response([_credit("only")], available_count=1)
        return _response([], available_count=0)

    async def consume_fn(
        access_token: str,
        account_id: str | None,
        credit_id: str,
        **kwargs: Any,
    ) -> ConsumeResetCreditResponse:
        return ConsumeResetCreditResponse.model_validate(
            {
                "code": "reset",
                "credit": {"id": credit_id, "status": "redeemed", "redeemed_at": "2026-06-13T13:12:31Z"},
                "windows_reset": 1,
            }
        )

    async def refresh_usage(account: Account) -> None:
        raise RuntimeError("usage refresh failed")

    await _redeem_soonest_reset_credit(
        account=_account(),
        store=store,
        encryptor=StubEncryptor(),
        fetch_fn=fetch_fn,
        consume_fn=consume_fn,
        refresh_usage=refresh_usage,
    )

    restored = store.get("acc_1")
    assert fetch_calls == 2
    assert restored is not None
    assert restored.available_count == 0


@pytest.mark.asyncio
async def test_redeem_restores_snapshot_when_force_refresh_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = RateLimitResetCreditsStore()
    await store.set("acc_1", _snapshot([_credit("only")], available_count=1))
    fetch_calls = 0

    async def fetch_fn(*args: Any, **kwargs: Any) -> ResetCreditsResponse:
        nonlocal fetch_calls
        fetch_calls += 1
        if fetch_calls == 1:
            return _response([_credit("only")], available_count=1)
        return _response([], available_count=0)

    async def consume_fn(
        access_token: str,
        account_id: str | None,
        credit_id: str,
        **kwargs: Any,
    ) -> ConsumeResetCreditResponse:
        return ConsumeResetCreditResponse.model_validate(
            {
                "code": "reset",
                "credit": {"id": credit_id, "status": "redeemed", "redeemed_at": "2026-06-13T13:12:31Z"},
                "windows_reset": 1,
            }
        )

    class _UsageUpdater:
        async def force_refresh(self, account: Account) -> bool:
            assert account.id == "acc_1"
            return False

    class _SelectionCache:
        def __init__(self) -> None:
            self.invalidated = 0

        def invalidate(self) -> None:
            self.invalidated += 1

    selection_cache = _SelectionCache()
    monkeypatch.setattr(reset_credits_api, "get_account_selection_cache", lambda: selection_cache)
    refresh_usage = _build_refresh_usage_callback(
        cast(Any, SimpleNamespace(service=SimpleNamespace(_usage_updater=_UsageUpdater())))
    )

    await _redeem_soonest_reset_credit(
        account=_account(),
        store=store,
        encryptor=StubEncryptor(),
        fetch_fn=fetch_fn,
        consume_fn=consume_fn,
        refresh_usage=refresh_usage,
    )

    restored = store.get("acc_1")
    assert fetch_calls == 2
    assert restored is not None
    assert restored.available_count == 0
    assert selection_cache.invalidated == 0


@pytest.mark.asyncio
async def test_redeem_serializes_requests_per_account() -> None:
    store = RateLimitResetCreditsStore()
    await store.set("acc_1", _snapshot([_credit("only")], available_count=1))
    fetch_calls = 0

    async def fetch_fn(*args: Any, **kwargs: Any) -> ResetCreditsResponse:
        nonlocal fetch_calls
        fetch_calls += 1
        if fetch_calls == 1:
            return _response([_credit("only")], available_count=1)
        return _response([], available_count=0)

    started = asyncio.Event()
    release = asyncio.Event()
    consume_calls: list[str] = []

    async def consume_fn(
        access_token: str,
        account_id: str | None,
        credit_id: str,
        **kwargs: Any,
    ) -> ConsumeResetCreditResponse:
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
            fetch_fn=fetch_fn,
            consume_fn=consume_fn,
        )
    )
    await started.wait()

    second = asyncio.create_task(
        _redeem_soonest_reset_credit(
            account=_account(),
            store=store,
            encryptor=StubEncryptor(),
            fetch_fn=fetch_fn,
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
async def test_serialize_reset_credit_redeem_uses_postgresql_advisory_lock() -> None:
    calls: list[tuple[str, dict[str, str] | None]] = []

    class _FakeSession:
        def get_bind(self) -> Any:
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        async def execute(self, statement: Any, params: dict[str, str] | None = None) -> None:
            calls.append((str(statement), params))

    async with serialize_reset_credit_redeem("acc_1", session=cast(Any, _FakeSession())):
        pass

    assert calls == [
        (
            "SELECT pg_advisory_xact_lock(hashtext(:lock_key))",
            {"lock_key": "reset-credit-redeem:acc_1"},
        )
    ]


class _FakeSqliteSession:
    def get_bind(self) -> Any:
        return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))


@pytest.mark.asyncio
async def test_serialize_reset_credit_redeem_uses_durable_claim_on_sqlite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, str]] = []

    async def fake_acquire(account_id: str, holder_id: str) -> None:
        events.append(("acquire", account_id))

    async def fake_release(account_id: str, holder_id: str) -> None:
        events.append(("release", account_id))

    monkeypatch.setattr(reset_credits_api, "acquire_redeem_claim", fake_acquire)
    monkeypatch.setattr(reset_credits_api, "release_redeem_claim", fake_release)

    async with serialize_reset_credit_redeem("acc_1", session=cast(Any, _FakeSqliteSession())):
        events.append(("locked", "acc_1"))

    assert events == [("acquire", "acc_1"), ("locked", "acc_1"), ("release", "acc_1")]


@pytest.mark.asyncio
async def test_serialize_reset_credit_redeem_renews_sqlite_claim_while_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sqlite branch keeps a heartbeat task alive for the section and cancels it before release."""
    events: list[str] = []
    heartbeat_running = asyncio.Event()

    async def fake_acquire(account_id: str, holder_id: str) -> None:
        events.append("acquire")

    async def fake_release(account_id: str, holder_id: str) -> None:
        events.append("release")

    async def fake_heartbeat(account_id: str, holder_id: str) -> None:
        events.append("heartbeat-start")
        heartbeat_running.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            events.append("heartbeat-cancelled")
            raise

    monkeypatch.setattr(reset_credits_api, "acquire_redeem_claim", fake_acquire)
    monkeypatch.setattr(reset_credits_api, "release_redeem_claim", fake_release)
    monkeypatch.setattr(reset_credits_api, "renew_redeem_claim_periodically", fake_heartbeat)

    async with serialize_reset_credit_redeem("acc_1", session=cast(Any, _FakeSqliteSession())):
        await asyncio.wait_for(heartbeat_running.wait(), timeout=5)
        events.append("locked")

    assert events == ["acquire", "heartbeat-start", "locked", "heartbeat-cancelled", "release"]


@pytest.mark.asyncio
async def test_serialize_reset_credit_redeem_propagates_sqlite_claim_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.rate_limit_reset_credits.redeem_coordination import RedeemClaimTimeoutError

    async def fake_acquire(account_id: str, holder_id: str) -> None:
        raise RedeemClaimTimeoutError("claim held")

    released: list[str] = []

    async def fake_release(account_id: str, holder_id: str) -> None:
        released.append(account_id)

    monkeypatch.setattr(reset_credits_api, "acquire_redeem_claim", fake_acquire)
    monkeypatch.setattr(reset_credits_api, "release_redeem_claim", fake_release)

    with pytest.raises(RedeemClaimTimeoutError):
        async with serialize_reset_credit_redeem("acc_1", session=cast(Any, _FakeSqliteSession())):
            raise AssertionError("locked section must not run after a claim timeout")

    assert released == []


@pytest.mark.asyncio
async def test_redeem_soonest_maps_claim_timeout_to_dashboard_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dashboard surface renders claim contention as its conflict envelope."""
    from app.modules.rate_limit_reset_credits.redeem_coordination import RedeemClaimTimeoutError

    async def fake_acquire(account_id: str, holder_id: str) -> None:
        raise RedeemClaimTimeoutError("claim held")

    monkeypatch.setattr(reset_credits_api, "acquire_redeem_claim", fake_acquire)

    with pytest.raises(DashboardConflictError) as excinfo:
        await _redeem_soonest_reset_credit(
            account=_account(),
            store=RateLimitResetCreditsStore(),
            encryptor=StubEncryptor(),
            lock_session=cast(Any, _FakeSqliteSession()),
        )

    assert excinfo.value.code == "reset_credit_redeem_in_progress"


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
async def test_redeem_translates_upstream_fetch_failures(
    status_code: int,
    expected_exception: type[Exception],
) -> None:
    store = RateLimitResetCreditsStore()
    await store.set("acc_1", _snapshot([_credit("only")], available_count=1))

    async def fetch_fn(*args: Any, **kwargs: Any) -> ResetCreditsResponse:
        raise ResetCreditFetchError(status_code, f"upstream fetch failed {status_code}", code=f"fetch_{status_code}")

    with pytest.raises(expected_exception) as excinfo:
        await _redeem_soonest_reset_credit(
            account=_account(),
            store=store,
            encryptor=StubEncryptor(),
            fetch_fn=fetch_fn,
            consume_fn=_raise_not_called,  # type: ignore[arg-type]
        )

    assert str(excinfo.value) == f"upstream fetch failed {status_code}"
    assert getattr(excinfo.value, "code", None) == f"fetch_{status_code}"
    cached = store.get("acc_1")
    assert cached is not None
    assert [credit.id for credit in cached.credits] == ["only"]


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
        **kwargs: Any,
    ) -> ConsumeResetCreditResponse:
        raise ConsumeResetCreditError(status_code, f"upstream failed {status_code}", code=f"upstream_{status_code}")

    with pytest.raises(expected_exception) as excinfo:
        await _redeem_soonest_reset_credit(
            account=_account(),
            store=store,
            encryptor=StubEncryptor(),
            fetch_fn=_static_fetch_fn(_response([_credit("only")], available_count=1)),
            consume_fn=consume_fn,
        )

    assert str(excinfo.value) == f"upstream failed {status_code}"
    assert getattr(excinfo.value, "code", None) == f"upstream_{status_code}"
    cached = store.get("acc_1")
    assert cached is not None
    assert [credit.id for credit in cached.credits] == ["only"]


@pytest.mark.asyncio
async def test_redeem_reports_zero_available_count_after_last_credit() -> None:
    store = RateLimitResetCreditsStore()
    await store.set("acc_1", _snapshot([_credit("only")], available_count=1))

    async def consume_fn(
        access_token: str,
        account_id: str | None,
        credit_id: str,
        **kwargs: Any,
    ) -> ConsumeResetCreditResponse:
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
        fetch_fn=_static_fetch_fn(_response([_credit("only")], available_count=1)),
        consume_fn=consume_fn,
    )

    assert result.available_count_after == 0


@pytest.mark.parametrize(
    "status",
    [AccountStatus.PAUSED, AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED],
)
def test_assert_account_can_redeem_reset_credit_rejects_non_applicable_statuses(status: AccountStatus) -> None:
    account = _account()
    account.status = status
    with pytest.raises(DashboardConflictError) as excinfo:
        _assert_account_can_redeem_reset_credit(account)
    assert excinfo.value.code == "account_not_reset_credit_applicable"


def test_assert_account_can_redeem_reset_credit_rejects_missing_chatgpt_account_id() -> None:
    account = _account()
    account.chatgpt_account_id = None
    with pytest.raises(DashboardConflictError) as excinfo:
        _assert_account_can_redeem_reset_credit(account)
    assert excinfo.value.code == "account_not_reset_credit_applicable"


# --- POST consume: handler-level 404 when account missing ---


@pytest.mark.asyncio
async def test_consume_handler_returns_404_when_account_missing() -> None:
    class _Repo:
        async def get_by_id(self, account_id: str) -> Account | None:
            return None

    fake_context = SimpleNamespace(repository=_Repo())

    with pytest.raises(DashboardNotFoundError):
        await consume_rate_limit_reset_credit(
            _fake_request(),
            account_id="missing",
            _write_access=None,
            context=cast(Any, fake_context),
        )


@pytest.mark.asyncio
async def test_consume_handler_audits_live_available_count_before_when_cache_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = RateLimitResetCreditsStore()
    monkeypatch.setattr(reset_credits_api, "get_rate_limit_reset_credits_store", lambda: store)

    class _Repo:
        async def get_by_id(self, account_id: str) -> Account | None:
            return _account(account_id)

    logged: dict[str, Any] = {}

    def _log_async(event: str, **kwargs: Any) -> None:
        logged["event"] = event
        logged.update(kwargs)

    async def _redeem(**kwargs: Any) -> Any:
        return reset_credits_api._RedeemResetCreditOutcome(
            response=ConsumeResetCreditResponseSchema(code="reset", windows_reset=1, redeemed_at=None),
            available_count_before=3,
            available_count_after=2,
        )

    monkeypatch.setattr(reset_credits_api, "_redeem_soonest_reset_credit", _redeem)
    monkeypatch.setattr(reset_credits_api.AuditService, "log_async", _log_async)

    fake_context = SimpleNamespace(
        repository=_Repo(),
        service=SimpleNamespace(_auth_manager=None, _usage_updater=None),
    )
    response = await consume_rate_limit_reset_credit(
        _fake_request(),
        account_id="acc_1",
        _write_access=None,
        context=cast(Any, fake_context),
    )

    assert response.code == "reset"
    assert logged["event"] == "account_rate_limit_reset_credit_consumed"
    assert logged["details"]["available_reset_credits_before"] == 3
    assert logged["details"]["available_reset_credits_after"] == 2


@pytest.mark.asyncio
async def test_consume_handler_invalidates_selection_cache_on_permanent_refresh_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Repo:
        async def get_by_id(self, account_id: str) -> Account | None:
            return _account(account_id)

    class _SelectionCache:
        def __init__(self) -> None:
            self.invalidated = 0

        def invalidate(self) -> None:
            self.invalidated += 1

    async def _redeem(**kwargs: Any) -> Any:
        raise RefreshError("invalid_grant", "refresh token expired", True)

    selection_cache = _SelectionCache()
    monkeypatch.setattr(reset_credits_api, "_redeem_soonest_reset_credit", _redeem)
    monkeypatch.setattr(reset_credits_api, "get_account_selection_cache", lambda: selection_cache)

    fake_context = SimpleNamespace(
        repository=_Repo(),
        service=SimpleNamespace(_auth_manager=None, _usage_updater=None),
    )
    with pytest.raises(DashboardConflictError) as excinfo:
        await consume_rate_limit_reset_credit(
            _fake_request(),
            account_id="acc_1",
            _write_access=None,
            context=cast(Any, fake_context),
        )

    assert excinfo.value.code == "account_reset_credit_refresh_failed"
    assert selection_cache.invalidated == 1


@pytest.mark.asyncio
async def test_consume_handler_keeps_selection_cache_on_transient_refresh_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Repo:
        async def get_by_id(self, account_id: str) -> Account | None:
            return _account(account_id)

    class _SelectionCache:
        def __init__(self) -> None:
            self.invalidated = 0

        def invalidate(self) -> None:
            self.invalidated += 1

    async def _redeem(**kwargs: Any) -> Any:
        raise RefreshError("transport_error", "timeout", False)

    selection_cache = _SelectionCache()
    monkeypatch.setattr(reset_credits_api, "_redeem_soonest_reset_credit", _redeem)
    monkeypatch.setattr(reset_credits_api, "get_account_selection_cache", lambda: selection_cache)

    fake_context = SimpleNamespace(
        repository=_Repo(),
        service=SimpleNamespace(_auth_manager=None, _usage_updater=None),
    )
    with pytest.raises(DashboardConflictError) as excinfo:
        await consume_rate_limit_reset_credit(
            _fake_request(),
            account_id="acc_1",
            _write_access=None,
            context=cast(Any, fake_context),
        )

    assert excinfo.value.code == "account_reset_credit_refresh_failed"
    assert selection_cache.invalidated == 0


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
