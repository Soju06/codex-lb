from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select

import app.modules.oauth_live.api as oauth_live_api_module
from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus, OAuthLivePolicy, OAuthLivePolicyAccount
from app.db.session import SessionLocal
from app.modules.oauth_live.repository import GLOBAL_POLICY_ID, OAuthLivePolicyRepository

pytestmark = pytest.mark.integration


def _account(account_id: str, *, status: AccountStatus = AccountStatus.ACTIVE) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        chatgpt_account_id=f"workspace-{account_id}",
        chatgpt_user_id=f"user-{account_id}",
        email=f"{account_id}@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt(f"access-{account_id}"),
        refresh_token_encrypted=encryptor.encrypt(f"refresh-{account_id}"),
        id_token_encrypted=encryptor.encrypt(f"id-{account_id}"),
        last_refresh=datetime.now(timezone.utc),
        status=status,
    )


async def _seed_accounts(*accounts: Account) -> None:
    async with SessionLocal() as session:
        session.add_all(accounts)
        await session.commit()


@pytest.mark.asyncio
async def test_global_policy_defaults_inactive_and_round_trips_assignments(async_client) -> None:
    await _seed_accounts(_account("allowed-b"), _account("allowed-a"))

    missing = await async_client.get("/api/oauth-live-policy")
    assert missing.status_code == 200
    assert missing.json() == {
        "isActive": False,
        "allowedAccountIds": [],
        "createdAt": None,
        "updatedAt": None,
    }

    saved = await async_client.put(
        "/api/oauth-live-policy",
        json={"isActive": False, "allowedAccountIds": ["allowed-b", "allowed-a", "allowed-b"]},
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["isActive"] is False
    assert body["allowedAccountIds"] == ["allowed-a", "allowed-b"]
    assert body["createdAt"] is not None
    assert body["updatedAt"] is not None
    assert "callerAccountId" not in body

    fetched = await async_client.get("/api/oauth-live-policy")
    assert fetched.status_code == 200
    assert fetched.json() == body


@pytest.mark.asyncio
async def test_global_policy_active_requires_explicit_nonempty_set_and_is_atomic(async_client) -> None:
    await _seed_accounts(_account("allowed"))
    seeded = await async_client.put(
        "/api/oauth-live-policy",
        json={"isActive": True, "allowedAccountIds": ["allowed"]},
    )
    assert seeded.status_code == 200

    rejected = await async_client.put(
        "/api/oauth-live-policy",
        json={"isActive": True, "allowedAccountIds": []},
    )
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "invalid_oauth_live_policy"

    preserved = await async_client.get("/api/oauth-live-policy")
    assert preserved.json()["isActive"] is True
    assert preserved.json()["allowedAccountIds"] == ["allowed"]


@pytest.mark.asyncio
async def test_global_policy_rejects_unknown_allowed_accounts(async_client) -> None:
    rejected = await async_client.put(
        "/api/oauth-live-policy",
        json={"isActive": False, "allowedAccountIds": ["missing"]},
    )
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "invalid_oauth_live_policy"


@pytest.mark.asyncio
async def test_global_policy_update_writes_credential_safe_audit_metadata(async_client, monkeypatch) -> None:
    await _seed_accounts(_account("allowed"))
    audit_events: list[tuple[str, dict[str, object] | None]] = []

    def record_audit(action: str, *, actor_ip=None, details=None) -> None:
        del actor_ip
        audit_events.append((action, details))

    monkeypatch.setattr(oauth_live_api_module.AuditService, "log_async", record_audit)

    response = await async_client.put(
        "/api/oauth-live-policy",
        json={"isActive": True, "allowedAccountIds": ["allowed"]},
    )
    assert response.status_code == 200
    assert audit_events == [
        (
            "oauth_live_policy_updated",
            {"is_active": True, "allowed_account_count": 1},
        )
    ]


@pytest.mark.asyncio
async def test_runtime_policy_lookup_fails_closed_and_filters_inactive_accounts(db_setup) -> None:
    del db_setup
    await _seed_accounts(
        _account("active-allowed"),
        _account("inactive-allowed", status=AccountStatus.PAUSED),
    )

    async with SessionLocal() as session:
        repository = OAuthLivePolicyRepository(session)
        assert await repository.get_active_allowed_account_ids() == frozenset()
        await repository.replace_policy(is_active=False, allowed_account_ids=["active-allowed"])
        assert await repository.get_active_allowed_account_ids() == frozenset()
        await repository.replace_policy(
            is_active=True,
            allowed_account_ids=["active-allowed", "inactive-allowed"],
        )
        assert await repository.get_active_allowed_account_ids() == frozenset({"active-allowed"})


@pytest.mark.asyncio
async def test_allowed_account_deletion_cascades_assignment_and_keeps_global_policy(db_setup) -> None:
    del db_setup
    await _seed_accounts(_account("allowed"))

    async with SessionLocal() as session:
        repository = OAuthLivePolicyRepository(session)
        await repository.replace_policy(is_active=True, allowed_account_ids=["allowed"])
        await session.execute(delete(Account).where(Account.id == "allowed"))
        await session.commit()

    async with SessionLocal() as session:
        assert await session.get(OAuthLivePolicy, GLOBAL_POLICY_ID) is not None
        assert (await session.execute(select(OAuthLivePolicyAccount))).scalars().all() == []
