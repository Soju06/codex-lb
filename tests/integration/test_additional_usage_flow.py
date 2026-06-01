"""Integration test: full additional-usage flow.

Parse upstream payload -> write to DB -> read from dashboard API.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from app.core.crypto import TokenEncryptor
from app.core.usage.models import UsagePayload
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository

pytestmark = pytest.mark.integration

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "upstream_usage_with_additional.json"


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


# ---------------------------------------------------------------------------
# Scenario 1: Parse additional_rate_limits from upstream payload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_additional_rate_limits_from_fixture(db_setup):
    raw = json.loads(FIXTURE_PATH.read_text())
    payload = UsagePayload.model_validate(raw)

    assert payload.additional_rate_limits is not None
    assert len(payload.additional_rate_limits) >= 1

    for item in payload.additional_rate_limits:
        assert item.limit_name
        assert item.metered_feature
        assert item.rate_limit is not None


# ---------------------------------------------------------------------------
# Scenario 2: UsageUpdater persists additional limits to DB
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_additional_usage_persisted_to_db(db_setup):
    account_id = "acc_additional_persist"
    now = utcnow()

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        additional_repo = AdditionalUsageRepository(session)

        await accounts_repo.upsert(_make_account(account_id, "additional_persist@example.com"))

        await additional_repo.add_entry(
            account_id=account_id,
            limit_name="GPT-5.3-Codex-Spark",
            metered_feature="codex_bengalfox",
            window="primary",
            used_percent=70.0,
            reset_at=1741500000,
            window_minutes=300,
            recorded_at=now,
        )

        # Query back via repository
        latest = await additional_repo.latest_by_account("codex_spark", "primary")
        assert account_id in latest

        entry = latest[account_id]
        assert entry.quota_key == "codex_spark"
        assert entry.limit_name == "GPT-5.3-Codex-Spark"
        assert entry.window == "primary"
        assert entry.used_percent == pytest.approx(70.0)
        assert entry.reset_at == 1741500000
        assert entry.window_minutes == 300


@pytest.mark.asyncio
async def test_accounts_list_returns_additional_quotas(async_client, db_setup):
    account_id = "acc_dash_additional"
    now = utcnow().replace(microsecond=0)

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        additional_repo = AdditionalUsageRepository(session)

        await accounts_repo.upsert(_make_account(account_id, "dash_additional@example.com"))

        # Seed primary usage so dashboard has data to render
        await usage_repo.add_entry(
            account_id,
            25.0,
            window="primary",
            recorded_at=now - timedelta(minutes=1),
        )

        # Seed additional usage
        await additional_repo.add_entry(
            account_id=account_id,
            limit_name="GPT-5.3-Codex-Spark",
            metered_feature="codex_bengalfox",
            window="primary",
            used_percent=55.0,
            reset_at=1741500000,
            window_minutes=300,
            recorded_at=now,
        )

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200

    data = response.json()
    assert "accounts" in data
    account = next(item for item in data["accounts"] if item["accountId"] == account_id)
    assert isinstance(account["additionalQuotas"], list)
    assert len(account["additionalQuotas"]) >= 1

    quota = account["additionalQuotas"][0]
    assert quota["quotaKey"] == "codex_spark"
    assert isinstance(quota["limitName"], str)
    assert quota["displayLabel"] == "GPT-5.3-Codex-Spark"
    assert quota["routingPolicy"] == "burn_first"
    assert quota["meteredFeature"] == "codex_bengalfox"
    assert quota["primaryWindow"] is not None
    assert quota["primaryWindow"]["usedPercent"] == pytest.approx(55.0)


@pytest.mark.asyncio
async def test_dashboard_overview_omits_additional_quotas(async_client, db_setup):
    account_id = "acc_dash_no_additional"
    now = utcnow().replace(microsecond=0)

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account(account_id, "no_additional@example.com"))

        await usage_repo.add_entry(
            account_id,
            10.0,
            window="primary",
            recorded_at=now - timedelta(minutes=1),
        )

    response = await async_client.get("/api/dashboard/overview")
    assert response.status_code == 200

    data = response.json()
    assert "additionalQuotas" not in data

    # depletion may be null (not enough data points) -- that is fine, not an error
    assert "depletionPrimary" in data
    assert data["depletionPrimary"] is None or isinstance(data["depletionPrimary"], dict)


@pytest.mark.asyncio
async def test_dashboard_overview_omits_aggregated_additional_quotas_even_when_present(async_client, db_setup):
    account_id = "acc_dash_additional"
    now = utcnow().replace(microsecond=0)

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        additional_repo = AdditionalUsageRepository(session)

        await accounts_repo.upsert(_make_account(account_id, "dash_additional@example.com"))
        await usage_repo.add_entry(
            account_id,
            25.0,
            window="primary",
            recorded_at=now - timedelta(minutes=1),
        )
        await additional_repo.add_entry(
            account_id=account_id,
            limit_name="GPT-5.3-Codex-Spark",
            metered_feature="codex_bengalfox",
            window="primary",
            used_percent=55.0,
            reset_at=1741500000,
            window_minutes=300,
            recorded_at=now,
        )

    response = await async_client.get("/api/dashboard/overview")
    assert response.status_code == 200

    data = response.json()
    assert "additionalQuotas" not in data
