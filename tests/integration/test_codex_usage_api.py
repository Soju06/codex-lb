from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.crypto import TokenEncryptor
from app.core.usage.models import UsagePayload
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, ApiKeyLimit, LimitType, LimitWindow, UsageHistory
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyCreateData, ApiKeysService, LimitRuleInput
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository

pytestmark = pytest.mark.integration


def _make_account(
    account_id: str,
    email: str,
    *,
    chatgpt_account_id: str | None = None,
    plan_type: str = "plus",
) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        chatgpt_account_id=chatgpt_account_id,
        email=email,
        plan_type=plan_type,
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


async def _create_api_key(*, name: str, limits: list[LimitRuleInput] | None = None) -> tuple[str, str]:
    async with SessionLocal() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        created = await service.create_key(
            ApiKeyCreateData(
                name=name,
                allowed_models=None,
                limits=limits or [],
            )
        )
    return created.id, created.key


@pytest.fixture(autouse=True)
def stub_codex_usage_caller_validation(monkeypatch):
    async def stub_fetch_usage(*, access_token: str, account_id: str | None, **_: object) -> UsagePayload:
        assert access_token == "chatgpt-token"
        assert account_id is not None
        return UsagePayload.model_validate({"plan_type": "plus"})

    monkeypatch.setattr("app.core.auth.dependencies.fetch_usage", stub_fetch_usage)


@pytest.mark.asyncio
async def test_codex_usage_aggregates_windows(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_a", "a@example.com", chatgpt_account_id="workspace_acc_a"))
        await accounts_repo.upsert(_make_account("acc_b", "b@example.com", chatgpt_account_id="workspace_acc_b"))

        await usage_repo.add_entry(
            "acc_a",
            10.0,
            window="primary",
            reset_at=0,
            window_minutes=300,
            credits_has=True,
            credits_unlimited=False,
            credits_balance=12.5,
        )
        await usage_repo.add_entry(
            "acc_b",
            30.0,
            window="primary",
            reset_at=0,
            window_minutes=300,
            credits_has=False,
            credits_unlimited=False,
            credits_balance=2.5,
        )
        await usage_repo.add_entry(
            "acc_a",
            40.0,
            window="secondary",
            reset_at=0,
            window_minutes=10080,
        )
        await usage_repo.add_entry(
            "acc_b",
            60.0,
            window="secondary",
            reset_at=0,
            window_minutes=10080,
        )

    response = await async_client.get(
        "/api/codex/usage",
        headers={
            "Authorization": "Bearer chatgpt-token",
            "chatgpt-account-id": "workspace_acc_a",
        },
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["plan_type"] == "plus"
    rate_limit = payload["rate_limit"]
    assert rate_limit["allowed"] is True
    assert rate_limit["limit_reached"] is False

    primary = rate_limit["primary_window"]
    assert primary["used_percent"] == 20
    assert primary["limit_window_seconds"] == 18000
    assert primary["reset_after_seconds"] == 0
    assert primary["reset_at"] == 0

    secondary = rate_limit["secondary_window"]
    assert secondary["used_percent"] == 50
    assert secondary["limit_window_seconds"] == 604800
    assert secondary["reset_after_seconds"] == 0
    assert secondary["reset_at"] == 0

    credits = payload["credits"]
    assert credits["has_credits"] is True
    assert credits["unlimited"] is False
    assert credits["balance"] == "15.0"


@pytest.mark.asyncio
async def test_codex_usage_header_ignored(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_a", "a@example.com", chatgpt_account_id="workspace_acc_a"))
        await accounts_repo.upsert(_make_account("acc_b", "b@example.com", chatgpt_account_id="workspace_acc_b"))

        await usage_repo.add_entry(
            "acc_a",
            10.0,
            window="primary",
            reset_at=0,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            "acc_b",
            90.0,
            window="primary",
            reset_at=0,
            window_minutes=300,
        )

    response = await async_client.get(
        "/api/codex/usage",
        headers={
            "Authorization": "Bearer chatgpt-token",
            "chatgpt-account-id": "workspace_acc_b",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    primary = payload["rate_limit"]["primary_window"]
    assert primary["used_percent"] == 50


@pytest.mark.asyncio
async def test_codex_usage_prefers_newer_weekly_primary_over_stale_secondary(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(
            _make_account("acc_weekly", "weekly@example.com", chatgpt_account_id="workspace_weekly")
        )

        await usage_repo.add_entry(
            "acc_weekly",
            15.0,
            window="secondary",
            reset_at=1735689600,
            window_minutes=10080,
            recorded_at=now - timedelta(days=2),
        )
        await usage_repo.add_entry(
            "acc_weekly",
            80.0,
            window="primary",
            reset_at=1735862400,
            window_minutes=10080,
            recorded_at=now,
        )

    response = await async_client.get(
        "/api/codex/usage",
        headers={
            "Authorization": "Bearer chatgpt-token",
            "chatgpt-account-id": "workspace_weekly",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    rate_limit = payload["rate_limit"]
    assert rate_limit["primary_window"] is None
    assert rate_limit["secondary_window"]["used_percent"] == 80
    assert rate_limit["secondary_window"]["reset_at"] == 1735862400


@pytest.mark.asyncio
async def test_codex_usage_additional_limit_reached_when_secondary_exhausted(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        additional_repo = AdditionalUsageRepository(session)

        await accounts_repo.upsert(
            _make_account(
                "acc_additional_secondary",
                "additional-secondary@example.com",
                chatgpt_account_id="workspace_additional_secondary",
            )
        )
        await usage_repo.add_entry(
            "acc_additional_secondary",
            10.0,
            window="primary",
            reset_at=0,
            window_minutes=300,
        )
        await additional_repo.add_entry(
            account_id="acc_additional_secondary",
            limit_name="o-pro",
            metered_feature="o_pro",
            window="primary",
            used_percent=40.0,
            reset_at=0,
            window_minutes=300,
        )
        await additional_repo.add_entry(
            account_id="acc_additional_secondary",
            limit_name="o-pro",
            metered_feature="o_pro",
            window="secondary",
            used_percent=100.0,
            reset_at=0,
            window_minutes=10080,
        )

    response = await async_client.get(
        "/api/codex/usage",
        headers={
            "Authorization": "Bearer chatgpt-token",
            "chatgpt-account-id": "workspace_additional_secondary",
        },
    )
    assert response.status_code == 200

    additional_limit = response.json()["additional_rate_limits"][0]["rate_limit"]
    assert additional_limit["allowed"] is False
    assert additional_limit["limit_reached"] is True
    assert additional_limit["primary_window"]["used_percent"] == 40
    assert additional_limit["secondary_window"]["used_percent"] == 100


@pytest.mark.asyncio
async def test_codex_usage_additional_limit_supports_secondary_only(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        additional_repo = AdditionalUsageRepository(session)

        await accounts_repo.upsert(
            _make_account(
                "acc_additional_secondary_only",
                "additional-secondary-only@example.com",
                chatgpt_account_id="workspace_additional_secondary_only",
            )
        )
        await usage_repo.add_entry(
            "acc_additional_secondary_only",
            20.0,
            window="primary",
            reset_at=0,
            window_minutes=300,
        )
        await additional_repo.add_entry(
            account_id="acc_additional_secondary_only",
            limit_name="deep-research",
            metered_feature="deep_research",
            window="secondary",
            used_percent=65.0,
            reset_at=1735862400,
            window_minutes=10080,
        )

    response = await async_client.get(
        "/api/codex/usage",
        headers={
            "Authorization": "Bearer chatgpt-token",
            "chatgpt-account-id": "workspace_additional_secondary_only",
        },
    )
    assert response.status_code == 200

    additional_limit = response.json()["additional_rate_limits"][0]
    assert additional_limit["limit_name"] == "deep-research"
    assert additional_limit["metered_feature"] == "deep_research"
    assert additional_limit["rate_limit"]["allowed"] is True
    assert additional_limit["rate_limit"]["limit_reached"] is False
    assert additional_limit["rate_limit"]["primary_window"] is None
    assert additional_limit["rate_limit"]["secondary_window"]["used_percent"] == 65
    assert additional_limit["rate_limit"]["secondary_window"]["reset_at"] == 1735862400


@pytest.mark.asyncio
async def test_codex_usage_accepts_api_key_callers(async_client, db_setup):
    key_id, plain_key = await _create_api_key(
        name="codex-usage-api-key",
        limits=[
            LimitRuleInput(limit_type="credits", limit_window="5h", max_value=60),
            LimitRuleInput(limit_type="credits", limit_window="7d", max_value=1000),
        ],
    )
    now = utcnow()

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        await repo.replace_limits(
            key_id,
            [
                ApiKeyLimit(
                    api_key_id=key_id,
                    limit_type=LimitType.CREDITS,
                    limit_window=LimitWindow.FIVE_HOURS,
                    max_value=60,
                    current_value=12,
                    model_filter=None,
                    reset_at=now + timedelta(hours=5),
                ),
                ApiKeyLimit(
                    api_key_id=key_id,
                    limit_type=LimitType.CREDITS,
                    limit_window=LimitWindow.SEVEN_DAYS,
                    max_value=1000,
                    current_value=250,
                    model_filter=None,
                    reset_at=now + timedelta(days=7),
                ),
            ],
        )
        await session.commit()

    response = await async_client.get(
        "/api/codex/usage",
        headers={"Authorization": f"Bearer {plain_key}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["plan_type"] == "api_key"
    assert payload["rate_limit"]["allowed"] is True
    assert payload["rate_limit"]["limit_reached"] is False
    assert payload["rate_limit"]["primary_window"]["used_percent"] == 20
    assert payload["rate_limit"]["secondary_window"]["used_percent"] == 25
    assert payload["credits"] == {
        "has_credits": True,
        "unlimited": False,
        "balance": "750",
        "approx_local_messages": None,
        "approx_cloud_messages": None,
    }


@pytest.mark.asyncio
async def test_codex_usage_api_key_ignores_aggregate_workspace_limits(async_client, db_setup):
    now = utcnow()
    suffix = str(int(now.timestamp() * 1_000_000))

    async with SessionLocal() as session:
        session.add_all(
            [
                _make_account(f"acc-agg-a-{suffix}", f"agg-a-{suffix}@test.com"),
                _make_account(f"acc-agg-b-{suffix}", f"agg-b-{suffix}@test.com"),
                UsageHistory(
                    account_id=f"acc-agg-a-{suffix}",
                    recorded_at=now,
                    window="primary",
                    used_percent=80.0,
                    reset_at=int((now + timedelta(hours=4)).timestamp()),
                    window_minutes=300,
                ),
                UsageHistory(
                    account_id=f"acc-agg-b-{suffix}",
                    recorded_at=now,
                    window="primary",
                    used_percent=90.0,
                    reset_at=int((now + timedelta(hours=4)).timestamp()),
                    window_minutes=300,
                ),
                UsageHistory(
                    account_id=f"acc-agg-a-{suffix}",
                    recorded_at=now,
                    window="secondary",
                    used_percent=70.0,
                    reset_at=int((now + timedelta(days=6)).timestamp()),
                    window_minutes=10080,
                ),
                UsageHistory(
                    account_id=f"acc-agg-b-{suffix}",
                    recorded_at=now,
                    window="secondary",
                    used_percent=60.0,
                    reset_at=int((now + timedelta(days=6)).timestamp()),
                    window_minutes=10080,
                ),
            ]
        )
        await session.commit()

    key_id, plain_key = await _create_api_key(
        name="codex-usage-agg-test",
        limits=[
            LimitRuleInput(limit_type="credits", limit_window="5h", max_value=100),
            LimitRuleInput(limit_type="credits", limit_window="7d", max_value=500),
        ],
    )
    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        await repo.replace_limits(
            key_id,
            [
                ApiKeyLimit(
                    api_key_id=key_id,
                    limit_type=LimitType.CREDITS,
                    limit_window=LimitWindow.FIVE_HOURS,
                    max_value=100,
                    current_value=5,
                    model_filter=None,
                    reset_at=now + timedelta(hours=5),
                ),
                ApiKeyLimit(
                    api_key_id=key_id,
                    limit_type=LimitType.CREDITS,
                    limit_window=LimitWindow.SEVEN_DAYS,
                    max_value=500,
                    current_value=50,
                    model_filter=None,
                    reset_at=now + timedelta(days=7),
                ),
            ],
        )
        await session.commit()

    response = await async_client.get(
        "/api/codex/usage",
        headers={"Authorization": f"Bearer {plain_key}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["rate_limit"]["primary_window"]["used_percent"] == 5
    assert payload["rate_limit"]["secondary_window"]["used_percent"] == 10
    assert payload["credits"]["balance"] == "450"
