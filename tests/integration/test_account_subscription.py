from __future__ import annotations

import base64
import json
from datetime import datetime

import pytest

from app.db.models import Account
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration


def _jwt(payload: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"header.{body}.sig"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("deadline", "checked", "expected_deadline", "expected_checked"),
    [
        ("2026-10-10T07:00:00+07:00", "2026-09-27T01:00:00Z", "2026-10-10T00:00:00Z", "2026-09-27T01:00:00Z"),
        ("2026-10-10T00:00:00", None, "2026-10-10T00:00:00Z", None),
        ("2020-01-01T00:00:00Z", "bad", "2020-01-01T00:00:00Z", None),
        (None, "2026-09-27T00:00:00Z", None, "2026-09-27T00:00:00Z"),
        ("not-a-date", None, None, None),
        ("2026-10-10", None, None, None),
        ("0001-01-01T00:00:00+12:00", None, None, None),
        (1780000000, True, None, None),
        ({"unexpected": "value"}, [], None, None),
    ],
)
async def test_import_and_list_recorded_subscription(
    async_client, deadline, checked, expected_deadline, expected_checked
):
    id_token = _jwt(
        {
            "email": "subscription@example.com",
            "https://api.openai.com/auth": {
                "chatgpt_plan_type": "plus",
                "chatgpt_account_id": "subscription-account",
                "chatgpt_subscription_active_until": deadline,
                "chatgpt_subscription_last_checked": checked,
            },
        }
    )
    access_token = _jwt({"exp": 2000000000})
    auth = {"tokens": {"idToken": id_token, "accessToken": access_token, "refreshToken": "secret-refresh"}}
    imported = await async_client.post(
        "/api/accounts/import", files={"auth_json": ("auth.json", json.dumps(auth), "application/json")}
    )
    assert imported.status_code == 200
    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    account = next(item for item in response.json()["accounts"] if item["accountId"] == imported.json()["accountId"])
    assert account["email"] == "subscription@example.com"
    assert account["planType"] == "plus"
    assert account["subscription"] == {"activeUntil": expected_deadline, "lastCheckedAt": expected_checked}
    assert account["auth"]["access"]["expiresAt"] == "2033-05-18T03:33:20Z"
    assert account["auth"]["idToken"]["state"] == "parsed"
    assert account["status"] == "active"
    assert datetime.fromisoformat(account["lastRefreshAt"]).utcoffset() is not None
    assert id_token not in response.text
    assert access_token not in response.text
    assert "secret-refresh" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("current_plan", ["free", "unknown", "pro"])
async def test_changed_plan_does_not_report_previous_subscription(async_client, current_plan):
    token = _jwt(
        {
            "email": "previous-plan@example.com",
            "https://api.openai.com/auth": {
                "chatgpt_plan_type": "plus",
                "chatgpt_subscription_active_until": "2030-01-01T00:00:00Z",
            },
        }
    )
    auth = {"tokens": {"idToken": token, "accessToken": "access", "refreshToken": "refresh"}}
    response = await async_client.post(
        "/api/accounts/import", files={"auth_json": ("auth.json", json.dumps(auth), "application/json")}
    )
    assert response.status_code == 200
    account_id = response.json()["accountId"]
    async with SessionLocal() as session:
        stored = await session.get(Account, account_id)
        assert stored is not None
        stored.plan_type = current_plan
        await session.commit()
    listed = await async_client.get("/api/accounts")
    assert listed.status_code == 200
    account = next(item for item in listed.json()["accounts"] if item["accountId"] == account_id)
    assert account["subscription"] == {"activeUntil": None, "lastCheckedAt": None}
