from __future__ import annotations

from datetime import timedelta, timezone

import pytest

from app.core.clients.rate_limit_reset_credits import RateLimitResetCreditsSnapshot, ResetCreditItem
from app.core.clients.usage import ConsumeRateLimitResetCreditResponse
from app.core.crypto import TokenEncryptor
from app.core.usage.models import UsagePayload
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, ApiKeyLimit, LimitType, LimitWindow, UsageHistory
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyCreateData, ApiKeysService, LimitRuleInput
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.rate_limit_reset_credits.store import get_rate_limit_reset_credits_store
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository

pytestmark = pytest.mark.integration


def _make_account(
    account_id: str,
    email: str,
    *,
    chatgpt_account_id: str | None = None,
    plan_type: str = "plus",
    workspace_id: str | None = None,
    workspace_label: str | None = None,
) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        chatgpt_account_id=chatgpt_account_id,
        email=email,
        workspace_id=workspace_id,
        workspace_label=workspace_label,
        plan_type=plan_type,
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


def _reset_credit_snapshot(credit_id: str) -> RateLimitResetCreditsSnapshot:
    credit = ResetCreditItem(
        id=credit_id,
        status="available",
        expires_at=utcnow() + timedelta(days=7),
    )
    return RateLimitResetCreditsSnapshot(
        available_count=1,
        nearest_expires_at=credit.expires_at,
        credits=[credit],
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
    now_epoch = int(utcnow().replace(tzinfo=timezone.utc).timestamp())
    primary_reset = now_epoch + 300
    secondary_reset = now_epoch + 5 * 24 * 3600
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_a", "a@example.com", chatgpt_account_id="workspace_acc_a"))
        await accounts_repo.upsert(_make_account("acc_b", "b@example.com", chatgpt_account_id="workspace_acc_b"))

        await usage_repo.add_entry(
            "acc_a",
            10.0,
            window="primary",
            reset_at=primary_reset,
            window_minutes=300,
            credits_has=True,
            credits_unlimited=False,
            credits_balance=12.5,
        )
        await usage_repo.add_entry(
            "acc_b",
            30.0,
            window="primary",
            reset_at=primary_reset,
            window_minutes=300,
            credits_has=False,
            credits_unlimited=False,
            credits_balance=2.5,
        )
        await usage_repo.add_entry(
            "acc_a",
            40.0,
            window="secondary",
            reset_at=secondary_reset,
            window_minutes=10080,
        )
        await usage_repo.add_entry(
            "acc_b",
            60.0,
            window="secondary",
            reset_at=secondary_reset,
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
    assert 0 < primary["reset_after_seconds"] <= 300
    assert primary["reset_at"] == primary_reset

    secondary = rate_limit["secondary_window"]
    assert secondary["used_percent"] == 50
    assert secondary["limit_window_seconds"] == 604800
    assert 0 < secondary["reset_after_seconds"] <= 5 * 24 * 3600
    assert secondary["reset_at"] == secondary_reset

    credits = payload["credits"]
    assert credits["has_credits"] is True
    assert credits["unlimited"] is False
    assert credits["balance"] == "15.0"


@pytest.mark.asyncio
async def test_codex_usage_uses_monthly_only_rows_for_credits(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(
            _make_account("acc_monthly", "monthly@example.com", chatgpt_account_id="workspace_acc_monthly")
        )

        monthly_reset = int(utcnow().replace(tzinfo=timezone.utc).timestamp()) + 30 * 24 * 3600
        await usage_repo.add_entry(
            "acc_monthly",
            42.0,
            window="monthly",
            reset_at=monthly_reset,
            window_minutes=43200,
            credits_has=True,
            credits_unlimited=False,
            credits_balance=8.75,
        )

    response = await async_client.get(
        "/api/codex/usage",
        headers={
            "Authorization": "Bearer chatgpt-token",
            "chatgpt-account-id": "workspace_acc_monthly",
        },
    )
    assert response.status_code == 200
    payload = response.json()

    monthly = payload["rate_limit"]["monthly_window"]
    assert monthly["used_percent"] == 42
    assert monthly["limit_window_seconds"] == 2592000
    assert monthly["reset_at"] == monthly_reset
    assert payload["credits"] == {
        "has_credits": True,
        "unlimited": False,
        "balance": "8.75",
        "approx_local_messages": None,
        "approx_cloud_messages": None,
    }


@pytest.mark.asyncio
async def test_codex_usage_monthly_free_exhaustion_does_not_block_paid_capacity(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(
            _make_account(
                "acc_free_monthly_full",
                "free-full@example.com",
                chatgpt_account_id="workspace_free_monthly_full",
                plan_type="free",
            )
        )
        await accounts_repo.upsert(
            _make_account(
                "acc_paid_available",
                "paid-available@example.com",
                chatgpt_account_id="workspace_paid_available",
                plan_type="plus",
            )
        )

        now_epoch = int(utcnow().replace(tzinfo=timezone.utc).timestamp())
        await usage_repo.add_entry(
            "acc_free_monthly_full",
            100.0,
            window="monthly",
            reset_at=now_epoch + 30 * 24 * 3600,
            window_minutes=43200,
        )
        await usage_repo.add_entry(
            "acc_paid_available",
            10.0,
            window="primary",
            reset_at=now_epoch + 300,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            "acc_paid_available",
            20.0,
            window="secondary",
            reset_at=now_epoch + 5 * 24 * 3600,
            window_minutes=10080,
        )

    response = await async_client.get(
        "/api/codex/usage",
        headers={
            "Authorization": "Bearer chatgpt-token",
            "chatgpt-account-id": "workspace_paid_available",
        },
    )
    assert response.status_code == 200
    rate_limit = response.json()["rate_limit"]
    assert rate_limit["allowed"] is True
    assert rate_limit["limit_reached"] is False
    assert rate_limit["primary_window"]["used_percent"] == 10
    assert rate_limit["secondary_window"]["used_percent"] == 20
    assert rate_limit["monthly_window"]["used_percent"] == 100


@pytest.mark.asyncio
async def test_codex_usage_header_ignored(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_a", "a@example.com", chatgpt_account_id="workspace_acc_a"))
        await accounts_repo.upsert(_make_account("acc_b", "b@example.com", chatgpt_account_id="workspace_acc_b"))

        primary_reset = int(utcnow().replace(tzinfo=timezone.utc).timestamp()) + 300
        await usage_repo.add_entry(
            "acc_a",
            10.0,
            window="primary",
            reset_at=primary_reset,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            "acc_b",
            90.0,
            window="primary",
            reset_at=primary_reset,
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

        now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
        weekly_reset = now_epoch + 6 * 24 * 3600
        await usage_repo.add_entry(
            "acc_weekly",
            15.0,
            window="secondary",
            reset_at=now_epoch + 5 * 24 * 3600,
            window_minutes=10080,
            recorded_at=now - timedelta(days=2),
        )
        await usage_repo.add_entry(
            "acc_weekly",
            80.0,
            window="primary",
            reset_at=weekly_reset,
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
    assert rate_limit["secondary_window"]["reset_at"] == weekly_reset


@pytest.mark.asyncio
async def test_codex_usage_expires_elapsed_primary_rows(async_client, db_setup):
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(
            _make_account("acc_expired", "expired@example.com", chatgpt_account_id="workspace_expired")
        )

        # Upstream stopped reporting the primary window: the last primary row
        # keeps a fully-used sample with an elapsed reset while the weekly
        # window stays fresh.
        await usage_repo.add_entry(
            "acc_expired",
            100.0,
            window="primary",
            reset_at=now_epoch - 7200,
            window_minutes=300,
            recorded_at=now - timedelta(hours=3),
        )
        await usage_repo.add_entry(
            "acc_expired",
            40.0,
            window="secondary",
            reset_at=now_epoch + 5 * 24 * 3600,
            window_minutes=10080,
            recorded_at=now,
        )

    response = await async_client.get(
        "/api/codex/usage",
        headers={
            "Authorization": "Bearer chatgpt-token",
            "chatgpt-account-id": "workspace_expired",
        },
    )
    assert response.status_code == 200
    rate_limit = response.json()["rate_limit"]
    assert rate_limit["limit_reached"] is False
    # The expired primary window is omitted instead of advertising a frozen
    # 100% sample with a reset time in the past.
    assert rate_limit["primary_window"] is None
    assert rate_limit["secondary_window"]["used_percent"] == 40


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
async def test_codex_usage_api_key_exposes_monthly_credit_window(async_client, db_setup):
    key_id, plain_key = await _create_api_key(
        name="codex-usage-api-key-monthly",
        limits=[
            LimitRuleInput(limit_type="credits", limit_window="monthly", max_value=1000),
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
                    limit_window=LimitWindow.MONTHLY,
                    max_value=1000,
                    current_value=250,
                    model_filter=None,
                    reset_at=now + timedelta(days=30),
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
    assert payload["rate_limit"]["primary_window"] is None
    assert payload["rate_limit"]["secondary_window"] is None
    assert payload["rate_limit"]["monthly_window"]["used_percent"] == 25
    assert payload["credits"]["balance"] == "750"


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


@pytest.mark.asyncio
async def test_codex_usage_exposes_reset_credit_availability(async_client, db_setup, monkeypatch):
    raw_chatgpt_account_id = "workspace_reset_available"
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        await accounts_repo.upsert(
            _make_account(
                "acc_reset_available",
                "reset-available@example.com",
                chatgpt_account_id=raw_chatgpt_account_id,
            )
        )

    async def stub_fetch_usage(*, access_token: str, account_id: str | None, **_: object) -> UsagePayload:
        assert access_token == "chatgpt-token"
        assert account_id == raw_chatgpt_account_id
        return UsagePayload.model_validate(
            {
                "plan_type": "plus",
                "rate_limit_reset_credits": {"available_count": 3},
            }
        )

    monkeypatch.setattr("app.core.auth.dependencies.fetch_usage", stub_fetch_usage)

    response = await async_client.get(
        "/api/codex/usage",
        headers={
            "Authorization": "Bearer chatgpt-token",
            "chatgpt-account-id": raw_chatgpt_account_id,
        },
    )
    assert response.status_code == 200
    assert response.json()["rate_limit_reset_credits"] == {"available_count": 3}


@pytest.mark.asyncio
async def test_codex_usage_reset_consume_forwards_and_refreshes(async_client, db_setup, monkeypatch):
    raw_chatgpt_account_id = "workspace_reset_consume"
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        await accounts_repo.upsert(
            _make_account("acc_reset_consume", "reset-consume@example.com", chatgpt_account_id=raw_chatgpt_account_id)
        )

    async def stub_fetch_usage(*, access_token: str, account_id: str | None, **_: object) -> UsagePayload:
        assert access_token == "chatgpt-token"
        assert account_id == raw_chatgpt_account_id
        return UsagePayload.model_validate({"plan_type": "plus"})

    consume_calls: list[dict[str, object]] = []

    async def stub_consume_rate_limit_reset_credit(**kwargs: object) -> ConsumeRateLimitResetCreditResponse:
        consume_calls.append(kwargs)
        return ConsumeRateLimitResetCreditResponse.model_validate({"code": "reset", "windows_reset": 2})

    refreshed_account_ids: list[str] = []

    class StubUsageUpdater:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def force_refresh(
            self,
            account: Account,
            *,
            ignore_refresh_disabled: bool = False,
            access_token_override: str | None = None,
        ) -> bool:
            refreshed_account_ids.append(f"{account.id}:{ignore_refresh_disabled}:{access_token_override}")
            return True

    monkeypatch.setattr("app.core.auth.dependencies.fetch_usage", stub_fetch_usage)
    monkeypatch.setattr("app.modules.proxy.api.consume_rate_limit_reset_credit", stub_consume_rate_limit_reset_credit)
    monkeypatch.setattr("app.modules.proxy.api.UsageUpdater", StubUsageUpdater)
    cache_generation = get_account_selection_cache().generation
    await get_rate_limit_reset_credits_store().set("acc_reset_consume", _reset_credit_snapshot("credit-codex"))

    response = await async_client.post(
        "/api/codex/rate-limit-reset-credits/consume",
        headers={
            "Authorization": "Bearer chatgpt-token",
            "chatgpt-account-id": raw_chatgpt_account_id,
        },
        json={"redeem_request_id": "redeem-123"},
    )
    assert response.status_code == 200
    assert response.json() == {"code": "reset", "windows_reset": 2}
    assert consume_calls == [
        {
            "access_token": "chatgpt-token",
            "account_id": raw_chatgpt_account_id,
            "redeem_request_id": "redeem-123",
            "route": None,
            "allow_direct_egress": True,
        }
    ]
    assert refreshed_account_ids == ["acc_reset_consume:True:chatgpt-token"]
    assert get_account_selection_cache().generation > cache_generation
    assert get_rate_limit_reset_credits_store().get("acc_reset_consume") is None


@pytest.mark.asyncio
async def test_codex_usage_reset_consume_refreshes_matched_workspace_account(async_client, db_setup, monkeypatch):
    raw_chatgpt_account_id = "workspace_reset_multi"
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        await accounts_repo.upsert(
            _make_account(
                "workspace_reset_multi_default",
                "reset-multi@example.com",
                chatgpt_account_id=raw_chatgpt_account_id,
            )
        )
        await accounts_repo.upsert(
            _make_account(
                "workspace_reset_multi_29c4834a",
                "reset-multi@example.com",
                chatgpt_account_id=raw_chatgpt_account_id,
                workspace_id="team-alpha",
                workspace_label="Team Alpha",
            )
        )

    async def stub_fetch_usage(*, access_token: str, account_id: str | None, **_: object) -> UsagePayload:
        assert access_token == "chatgpt-token"
        assert account_id == raw_chatgpt_account_id
        return UsagePayload.model_validate({"plan_type": "plus", "workspace_id": "team-alpha"})

    async def stub_consume_rate_limit_reset_credit(**_: object) -> ConsumeRateLimitResetCreditResponse:
        return ConsumeRateLimitResetCreditResponse.model_validate({"code": "reset", "windows_reset": 1})

    refreshed_account_ids: list[str] = []

    class StubUsageUpdater:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def force_refresh(
            self,
            account: Account,
            *,
            ignore_refresh_disabled: bool = False,
            access_token_override: str | None = None,
        ) -> bool:
            del ignore_refresh_disabled
            assert access_token_override == "chatgpt-token"
            refreshed_account_ids.append(account.id)
            return True

    monkeypatch.setattr("app.core.auth.dependencies.fetch_usage", stub_fetch_usage)
    monkeypatch.setattr("app.modules.proxy.api.consume_rate_limit_reset_credit", stub_consume_rate_limit_reset_credit)
    monkeypatch.setattr("app.modules.proxy.api.UsageUpdater", StubUsageUpdater)

    response = await async_client.post(
        "/api/codex/rate-limit-reset-credits/consume",
        headers={
            "Authorization": "Bearer chatgpt-token",
            "chatgpt-account-id": raw_chatgpt_account_id,
        },
        json={"redeem_request_id": "redeem-workspace"},
    )

    assert response.status_code == 200
    assert refreshed_account_ids == ["workspace_reset_multi_29c4834a"]


@pytest.mark.asyncio
async def test_codex_usage_reset_consume_rejects_api_key_callers(async_client, db_setup):
    _, plain_key = await _create_api_key(name="reset-api-key")

    response = await async_client.post(
        "/api/codex/rate-limit-reset-credits/consume",
        headers={"Authorization": f"Bearer {plain_key}"},
        json={"redeem_request_id": "redeem-123"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


@pytest.mark.asyncio
async def test_codex_usage_reset_consume_rejects_empty_redeem_request_id(async_client, db_setup, monkeypatch):
    raw_chatgpt_account_id = "workspace_reset_empty"
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        await accounts_repo.upsert(
            _make_account("acc_reset_empty", "reset-empty@example.com", chatgpt_account_id=raw_chatgpt_account_id)
        )

    async def stub_fetch_usage(**_: object) -> UsagePayload:
        return UsagePayload.model_validate({"plan_type": "plus"})

    async def should_not_consume(**_: object) -> ConsumeRateLimitResetCreditResponse:
        raise AssertionError("empty redeem_request_id should not be forwarded upstream")

    monkeypatch.setattr("app.core.auth.dependencies.fetch_usage", stub_fetch_usage)
    monkeypatch.setattr("app.modules.proxy.api.consume_rate_limit_reset_credit", should_not_consume)

    response = await async_client.post(
        "/api/codex/rate-limit-reset-credits/consume",
        headers={
            "Authorization": "Bearer chatgpt-token",
            "chatgpt-account-id": raw_chatgpt_account_id,
        },
        json={"redeem_request_id": "  "},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request_error"
