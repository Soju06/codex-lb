from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.usage.repository import UsageRepository

pytestmark = pytest.mark.integration


def _make_account(account_id: str, email: str, plan_type: str = "plus") -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=email,
        plan_type=plan_type,
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


@pytest.mark.asyncio
async def test_usage_summary_empty_returns_zeroes(async_client):
    response = await async_client.get("/api/usage/summary")
    assert response.status_code == 200
    payload = response.json()

    primary = payload["primaryWindow"]
    assert primary["remainingPercent"] == 0.0
    assert primary["capacityCredits"] == 0.0
    assert primary["remainingCredits"] == 0.0
    assert primary["windowMinutes"] == 300

    secondary = payload["secondaryWindow"]
    assert secondary["remainingPercent"] == 0.0
    assert secondary["capacityCredits"] == 0.0
    assert secondary["remainingCredits"] == 0.0
    assert secondary["windowMinutes"] == 10080

    cost = payload["cost"]
    assert cost["currency"] == "USD"
    assert cost["totalUsd7d"] == 0.0

    metrics = payload["metrics"]
    assert metrics["requests7d"] == 0
    assert metrics["tokensSecondaryWindow"] == 0
    assert metrics["cachedTokensSecondaryWindow"] == 0
    assert metrics["errorRate7d"] is None
    assert metrics["topError"] is None


@pytest.mark.asyncio
async def test_usage_history_aggregates_per_account(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_a", "a@example.com"))
        await accounts_repo.upsert(_make_account("acc_b", "b@example.com"))

        await usage_repo.add_entry("acc_a", 10.0, recorded_at=now - timedelta(hours=3))
        await usage_repo.add_entry("acc_a", 30.0, recorded_at=now - timedelta(hours=2))

    response = await async_client.get("/api/usage/history?hours=24")
    assert response.status_code == 200
    payload = response.json()
    assert payload["windowHours"] == 24

    accounts = {item["accountId"]: item for item in payload["accounts"]}
    acc_a = accounts["acc_a"]
    acc_b = accounts["acc_b"]

    assert acc_a["remainingPercentAvg"] == pytest.approx(80.0)
    assert acc_a["capacityCredits"] == pytest.approx(225.0)
    assert acc_a["remainingCredits"] == pytest.approx(180.0)

    assert acc_b["remainingPercentAvg"] == pytest.approx(100.0)
    assert acc_b["capacityCredits"] == pytest.approx(225.0)
    assert acc_b["remainingCredits"] == pytest.approx(225.0)


@pytest.mark.asyncio
async def test_usage_window_secondary_uses_latest_window_minutes(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_sec", "sec@example.com"))
        await usage_repo.add_entry(
            "acc_sec",
            40.0,
            window="secondary",
            reset_at=1735689600,
            window_minutes=1440,
            recorded_at=now - timedelta(minutes=5),
        )
    response = await async_client.get("/api/usage/window?window=secondary")
    assert response.status_code == 200
    payload = response.json()
    assert payload["windowKey"] == "secondary"
    assert payload["windowMinutes"] == 1440

    accounts = {item["accountId"]: item for item in payload["accounts"]}
    entry = accounts["acc_sec"]
    assert entry["remainingPercentAvg"] == pytest.approx(60.0)
    assert entry["capacityCredits"] == pytest.approx(7560.0)
    assert entry["remainingCredits"] == pytest.approx(4536.0)


@pytest.mark.asyncio
async def test_usage_window_primary_excludes_weekly_only_accounts(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_plus", "plus@example.com", plan_type="plus"))
        await accounts_repo.upsert(_make_account("acc_free", "free@example.com", plan_type="free"))
        await usage_repo.add_entry(
            "acc_plus",
            20.0,
            window="primary",
            window_minutes=300,
            recorded_at=now - timedelta(minutes=2),
        )
        await usage_repo.add_entry(
            "acc_free",
            20.0,
            window="primary",
            window_minutes=10080,
            recorded_at=now - timedelta(minutes=1),
        )

    response = await async_client.get("/api/usage/window?window=primary")
    assert response.status_code == 200
    payload = response.json()
    assert payload["windowKey"] == "primary"
    assert payload["windowMinutes"] == 300

    accounts = {item["accountId"]: item for item in payload["accounts"]}
    assert accounts["acc_free"]["remainingPercentAvg"] is None


@pytest.mark.asyncio
async def test_usage_window_secondary_includes_weekly_only_primary_entries(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_with_secondary", "with-secondary@example.com", plan_type="plus"))
        await accounts_repo.upsert(_make_account("acc_weekly_only", "weekly-only@example.com", plan_type="free"))

        await usage_repo.add_entry(
            "acc_with_secondary",
            40.0,
            window="secondary",
            window_minutes=10080,
            recorded_at=now - timedelta(minutes=1),
        )
        await usage_repo.add_entry(
            "acc_weekly_only",
            60.0,
            window="primary",
            window_minutes=10080,
            recorded_at=now - timedelta(minutes=1),
        )

    response = await async_client.get("/api/usage/window?window=secondary")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}

    assert accounts["acc_with_secondary"]["remainingPercentAvg"] == pytest.approx(60.0)
    assert accounts["acc_weekly_only"]["remainingPercentAvg"] == pytest.approx(40.0)


@pytest.mark.asyncio
async def test_usage_history_team_plan_has_capacity(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_team", "team@example.com", plan_type="team"))
        await usage_repo.add_entry(
            "acc_team",
            20.0,
            window="primary",
            recorded_at=now - timedelta(hours=1),
        )

    response = await async_client.get("/api/usage/history?hours=24")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}
    entry = accounts["acc_team"]

    assert entry["remainingPercentAvg"] == pytest.approx(80.0)
    assert entry["capacityCredits"] == pytest.approx(225.0)
    assert entry["remainingCredits"] == pytest.approx(180.0)


@pytest.mark.asyncio
async def test_usage_history_invalid_hours_returns_validation_error(async_client):
    response = await async_client.get("/api/usage/history?hours=0")
    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_usage_window_invalid_query_returns_validation_error(async_client):
    response = await async_client.get("/api/usage/window?window=invalid")
    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_usage_summary_sql_aggregate_matches_legacy_python_summation(async_client, db_setup):
    """The SQL window aggregate must reproduce the legacy per-row Python
    summation exactly, including the reasoning-token fallback, per-row
    cached<=input clamp, NULL-cost model exclusion, and top-error pick."""
    from app.modules.request_logs.repository import RequestLogsRepository
    from app.modules.usage.builders import _cost_summary_from_logs, _usage_metrics

    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        usage_repo = UsageRepository(session)
        await accounts_repo.upsert(_make_account("acc_usage_eq", "usage-eq@example.com"))
        # Secondary window row so the summary resolves a 7-day window.
        await usage_repo.add_entry(
            "acc_usage_eq",
            10.0,
            window="secondary",
            window_minutes=10080,
            recorded_at=now - timedelta(minutes=5),
        )

        cases = [
            # (input, output, reasoning, cached, cost, status, error_code)
            (100, 50, None, 30, 0.5, "success", None),  # plain
            (100, None, 40, None, None, "success", None),  # reasoning fallback, no cost
            (100, None, None, 250, 0.25, "error", "rate_limit_exceeded"),  # cached > input clamps
            (None, 20, 5, 70, None, "error", "rate_limit_exceeded"),  # input None: cached unclamped
            (10, 0, 99, -5, 0.125, "error", "insufficient_quota"),  # output=0 wins over reasoning; negative cached
        ]
        for index, (inp, out, reasoning, cached, cost, status, error_code) in enumerate(cases):
            await logs_repo.add_log(
                account_id="acc_usage_eq",
                request_id=f"req_eq_{index}",
                model=f"gpt-eq-{index % 2}",
                input_tokens=inp,
                output_tokens=out,
                reasoning_tokens=reasoning,
                cached_input_tokens=cached,
                latency_ms=100,
                status=status,
                error_code=error_code,
                requested_at=now - timedelta(hours=index + 1),
                cost_usd=cost,
            )
        # Warmup rows are excluded by both paths.
        await logs_repo.add_log(
            account_id="acc_usage_eq",
            request_id="req_eq_warm",
            model="gpt-eq-0",
            input_tokens=999,
            output_tokens=999,
            latency_ms=100,
            status="success",
            error_code=None,
            requested_at=now - timedelta(hours=1),
            cost_usd=9.9,
            request_kind="warmup",
        )

    async with SessionLocal() as session:
        logs_repo = RequestLogsRepository(session)
        legacy_rows = await logs_repo.list_since(now - timedelta(minutes=10080))
        legacy_metrics = _usage_metrics(legacy_rows)
        legacy_cost = _cost_summary_from_logs(legacy_rows)
        aggregate = await logs_repo.aggregate_usage_metrics_since(now - timedelta(minutes=10080))

    response = await async_client.get("/api/usage/summary")
    assert response.status_code == 200
    payload = response.json()

    metrics = payload["metrics"]
    assert metrics["requests7d"] == legacy_metrics.requests_7d == 5
    assert metrics["tokensSecondaryWindow"] == legacy_metrics.tokens_secondary_window
    assert metrics["cachedTokensSecondaryWindow"] == legacy_metrics.cached_tokens_secondary_window
    assert metrics["errorRate7d"] == pytest.approx(legacy_metrics.error_rate_7d)
    assert metrics["topError"] == legacy_metrics.top_error == "rate_limit_exceeded"

    cost = payload["cost"]
    assert cost["totalUsd7d"] == pytest.approx(legacy_cost.total_usd_7d)

    # The response schema only exposes the total; compare the per-model
    # breakdown at the builder level.
    from app.modules.usage.builders import build_usage_cost_from_aggregate

    aggregate_cost = build_usage_cost_from_aggregate(aggregate)
    assert [(entry.model, entry.usd) for entry in aggregate_cost.by_model] == [
        (entry.model, entry.usd) for entry in legacy_cost.by_model
    ]
