"""Operator endpoints for the cross-account cache isolation probe.

Covers the gates that stand between a dashboard click and real quota being
spent: the explicit confirmation, the shared hourly budget, and the refusal
when the pool is already under pressure. The upstream sender is stubbed, so
nothing here talks to an upstream.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.core.crypto import TokenEncryptor
from app.core.rate_limiter.db_rate_limiter import DatabaseRateLimiter
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.cache_isolation_probe import api as probe_api
from app.modules.cache_isolation_probe import service as probe_service
from app.modules.cache_isolation_probe.sender import ProbeSendResult
from app.modules.cache_isolation_probe.service import CacheIsolationProbeService

pytestmark = pytest.mark.integration

_INPUT_TOKENS = 28_168
_HIT_TOKENS = 28_032


async def _create_accounts(count: int, *, status: AccountStatus = AccountStatus.ACTIVE) -> None:
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        for index in range(count):
            account_id = f"probe-{index:02d}"
            await repo.upsert(
                Account(
                    id=account_id,
                    chatgpt_account_id=account_id,
                    email=f"{account_id}@example.com",
                    plan_type="plus",
                    access_token_encrypted=encryptor.encrypt(f"access-{account_id}"),
                    refresh_token_encrypted=encryptor.encrypt(f"refresh-{account_id}"),
                    id_token_encrypted=encryptor.encrypt(f"id-{account_id}"),
                    last_refresh=utcnow(),
                    status=status,
                    deactivation_reason=None,
                )
            )


class _StubSender:
    def __init__(self, *, cross_account_hit: bool = True) -> None:
        self.cross_account_hit = cross_account_hit
        self.calls: list[str] = []

    async def send(self, account_id: str, *, model: str, prefix: str) -> ProbeSendResult:
        del model, prefix
        self.calls.append(account_id)
        cached = _HIT_TOKENS if (self.cross_account_hit or account_id.endswith("00")) else 0
        return ProbeSendResult(ok=True, latency_ms=900, input_tokens=_INPUT_TOKENS, cached_tokens=cached)


@pytest.fixture
def stub_sender(app_instance, monkeypatch) -> Iterator[_StubSender]:
    sender = _StubSender()
    service = CacheIsolationProbeService(sender=sender)
    monkeypatch.setattr(probe_service, "default_probe_model", lambda: "gpt-probe")
    app_instance.dependency_overrides[probe_api.get_cache_isolation_probe_service] = lambda: service
    yield sender
    app_instance.dependency_overrides.pop(probe_api.get_cache_isolation_probe_service, None)


@pytest.fixture(autouse=True)
def fresh_rate_limit_budget(app_instance, monkeypatch):
    """Each test gets its own limiter ``type`` so the shared hourly budget of
    one test does not leak into the next."""

    limiter = DatabaseRateLimiter(max_attempts=3, window_seconds=3600, type=f"probe_{utcnow().timestamp()}")
    app_instance.dependency_overrides[probe_api.get_cache_isolation_probe_rate_limiter] = lambda: limiter
    monkeypatch.setattr(probe_api, "get_cache_isolation_probe_rate_limiter", lambda: limiter)
    yield limiter
    app_instance.dependency_overrides.pop(probe_api.get_cache_isolation_probe_rate_limiter, None)


async def test_plan_prices_the_run_and_reports_pool_health(async_client, stub_sender) -> None:
    await _create_accounts(6)

    response = await async_client.get("/api/diagnostics/cache-isolation-probe")

    assert response.status_code == 200
    body = response.json()
    assert body["seedAccount"]["accountId"] == "probe-00"
    # Every candidate, capped at five, so the dashboard can reprice a different
    # sibling count without another round trip.
    assert [account["accountId"] for account in body["availableOtherAccounts"]] == [
        "probe-01",
        "probe-02",
        "probe-03",
        "probe-04",
        "probe-05",
    ]
    # The totals still describe the default selection of four siblings.
    assert body["totalCalls"] == 7
    assert body["estimatedTotalInputTokens"] == body["estimatedInputTokensPerCall"] * 7
    assert body["maxSeedRepetitions"] == 5
    assert body["maxOtherAccounts"] == 5
    assert body["pressure"]["underPressure"] is False
    assert stub_sender.calls == []


async def test_run_without_confirmation_spends_nothing(async_client, stub_sender) -> None:
    await _create_accounts(6)

    response = await async_client.post("/api/diagnostics/cache-isolation-probe/run", json={"confirm": False})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "cache_probe_confirmation_required"
    assert stub_sender.calls == []


async def test_run_with_no_body_at_all_spends_nothing(async_client, stub_sender) -> None:
    await _create_accounts(6)

    response = await async_client.post("/api/diagnostics/cache-isolation-probe/run")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "cache_probe_confirmation_required"
    assert stub_sender.calls == []


async def test_confirmed_run_returns_the_result_table(async_client, stub_sender) -> None:
    await _create_accounts(6)

    response = await async_client.post(
        "/api/diagnostics/cache-isolation-probe/run",
        json={"confirm": True, "seedRepetitions": 2, "otherAccountCount": 3},
    )

    assert response.status_code == 200
    body = response.json()
    assert [call["sequence"] for call in body["calls"]] == [1, 2, 3, 4, 5]
    assert [call["role"] for call in body["calls"]] == ["seed", "seed", "other", "other", "other"]
    assert [call["accountId"] for call in body["calls"]] == [
        "probe-00",
        "probe-00",
        "probe-01",
        "probe-02",
        "probe-03",
    ]
    assert all(call["cachedTokens"] == _HIT_TOKENS for call in body["calls"])
    assert body["crossAccountHit"] is True
    assert body["verdict"] == "cross_account_sharing"
    assert stub_sender.calls == ["probe-00", "probe-00", "probe-01", "probe-02", "probe-03"]


async def test_counts_beyond_the_cap_are_rejected_before_any_call(async_client, stub_sender) -> None:
    await _create_accounts(8)

    response = await async_client.post(
        "/api/diagnostics/cache-isolation-probe/run",
        json={"confirm": True, "seedRepetitions": 9, "otherAccountCount": 9},
    )

    assert response.status_code == 422
    assert stub_sender.calls == []


async def test_a_pressured_pool_refuses_the_run(async_client, stub_sender) -> None:
    await _create_accounts(6)
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        for account_id in ("probe-04", "probe-05"):
            account = await repo.get_by_id_fresh(account_id)
            assert account is not None
            account.status = AccountStatus.RATE_LIMITED
        await session.commit()

    response = await async_client.post("/api/diagnostics/cache-isolation-probe/run", json={"confirm": True})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "pool_under_pressure"
    assert stub_sender.calls == []


async def test_a_pool_too_small_to_answer_the_question_refuses(async_client, stub_sender) -> None:
    await _create_accounts(1)

    response = await async_client.post("/api/diagnostics/cache-isolation-probe/run", json={"confirm": True})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "insufficient_eligible_accounts"
    assert stub_sender.calls == []


async def test_the_hourly_budget_is_shared_not_per_caller(async_client, stub_sender) -> None:
    await _create_accounts(6)
    payload = {"confirm": True, "seedRepetitions": 1, "otherAccountCount": 1}

    for _ in range(3):
        assert (await async_client.post("/api/diagnostics/cache-isolation-probe/run", json=payload)).status_code == 200

    throttled = await async_client.post("/api/diagnostics/cache-isolation-probe/run", json=payload)

    assert throttled.status_code == 429
    assert len(stub_sender.calls) == 6


async def test_an_unconfirmed_run_does_not_consume_the_budget(async_client, stub_sender) -> None:
    await _create_accounts(6)

    for _ in range(5):
        await async_client.post("/api/diagnostics/cache-isolation-probe/run", json={"confirm": False})
    accepted = await async_client.post(
        "/api/diagnostics/cache-isolation-probe/run",
        json={"confirm": True, "seedRepetitions": 1, "otherAccountCount": 1},
    )

    assert accepted.status_code == 200
