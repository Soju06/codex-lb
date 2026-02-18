from __future__ import annotations

import base64
import json
from datetime import timedelta

import pytest

from app.core.auth import generate_unique_account_id
from app.core.utils.time import utcnow
from app.db.models import UsageHistory
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


async def _import_account(async_client, email: str, raw_account_id: str) -> str:
    payload = {
        "email": email,
        "chatgpt_account_id": raw_account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    auth_json = {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access",
            "refreshToken": "refresh",
            "accountId": raw_account_id,
        },
    }
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200
    return generate_unique_account_id(raw_account_id, email)


async def _seed_usage_history(account_id: str, window: str, days_ago: int, used_percent: float) -> None:
    now = utcnow()
    recorded = now - timedelta(days=days_ago)
    async with SessionLocal() as session:
        entry = UsageHistory(
            account_id=account_id,
            used_percent=used_percent,
            window=window,
            recorded_at=recorded,
        )
        session.add(entry)
        await session.commit()


@pytest.mark.asyncio
async def test_list_accounts_does_not_contain_usage_trend(async_client):
    account_id = await _import_account(async_client, "trend@example.com", "acc_trend")
    await _seed_usage_history(account_id, "primary", 1, 30.0)

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    data = response.json()
    accounts = data["accounts"]

    matched = next((a for a in accounts if a["accountId"] == account_id), None)
    assert matched is not None
    assert "usageTrend" not in matched


@pytest.mark.asyncio
async def test_account_trends_endpoint(async_client):
    account_id = await _import_account(async_client, "detail@example.com", "acc_detail")
    await _seed_usage_history(account_id, "primary", 1, 25.0)
    await _seed_usage_history(account_id, "secondary", 1, 35.0)

    response = await async_client.get(f"/api/accounts/{account_id}/trends")
    assert response.status_code == 200
    data = response.json()
    assert data["accountId"] == account_id
    assert "primary" in data
    assert "secondary" in data
    assert len(data["primary"]) > 0
    assert len(data["secondary"]) > 0
    # At least one bucket should have actual usage data (not all 100%)
    primary_values = [p["v"] for p in data["primary"]]
    assert any(v != 100.0 for v in primary_values), "Expected at least one non-default value"
    # remaining = 100 - 25 = 75
    assert any(abs(v - 75.0) < 0.01 for v in primary_values)


@pytest.mark.asyncio
async def test_account_trends_missing_account_returns_404(async_client):
    response = await async_client.get("/api/accounts/nonexistent/trends")
    assert response.status_code == 404
    payload = response.json()
    assert payload["error"]["code"] == "account_not_found"


@pytest.mark.asyncio
async def test_account_trends_no_data(async_client):
    account_id = await _import_account(async_client, "empty@example.com", "acc_empty")

    response = await async_client.get(f"/api/accounts/{account_id}/trends")
    assert response.status_code == 200
    data = response.json()
    assert data["accountId"] == account_id
    # No usage data → empty lists
    assert data["primary"] == []
    assert data["secondary"] == []
