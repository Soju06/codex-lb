from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, update

from app.db.models import ApiKey, RequestLog
from app.db.session import SessionLocal
from app.modules.accounts.usage_time_rollup import run_hourly_fold_pass

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime(2026, 9, 10, 12, 30)


async def create_key(client, name, group=None, path="/api/api-keys"):
    response = await client.post(path, json={"name": name, "usageGroup": group})
    assert response.status_code == 200
    return response.json()


async def group_usage(client, key):
    response = await client.get("/api/key-dashboard/group", headers={"Authorization": f"Bearer {key['key']}"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    return response.json()


async def seed_usage(
    key_id, requested_at, *, kind="normal", deleted=False, input_tokens=100, cached_input_tokens=20, cost_usd=0.123
):
    async with SessionLocal() as session:
        session.add(
            RequestLog(
                api_key_id=key_id,
                request_id=f"{key_id}-{requested_at}-{kind}",
                requested_at=requested_at,
                model="gpt-5.1",
                status="success",
                request_kind=kind,
                input_tokens=input_tokens,
                output_tokens=None,
                reasoning_tokens=25,
                cached_input_tokens=cached_input_tokens,
                cost_usd=cost_usd,
                deleted_at=NOW if deleted else None,
            )
        )
        await session.commit()


async def test_group_crud_defaults_normalization_and_regeneration(async_client, db_setup):
    key = await create_key(async_client, "Ungrouped")
    assert key["usageGroup"] is None
    assert (await group_usage(async_client, key))["members"] == []
    peer = await create_key(async_client, "Peer", "  Team A  ", path="/api/api-keys/")
    assert peer["usageGroup"] == "Team A"
    assigned = await async_client.patch(f"/api/api-keys/{key['id']}", json={"usageGroup": " Team A "})
    assert assigned.json()["usageGroup"] == "Team A"
    renamed = await async_client.patch(f"/api/api-keys/{key['id']}", json={"name": "Renamed"})
    assert renamed.json()["usageGroup"] == "Team A"
    assert len((await group_usage(async_client, key))["members"]) == 2
    regenerated = await async_client.post(f"/api/api-keys/{key['id']}/regenerate")
    assert regenerated.status_code == 200
    key = regenerated.json()
    assert key["usageGroup"] == "Team A"
    assert len((await group_usage(async_client, key))["members"]) == 2
    cleared = await async_client.patch(f"/api/api-keys/{key['id']}", json={"usageGroup": "   "})
    assert cleared.json()["usageGroup"] is None
    assert (await group_usage(async_client, key))["groupName"] is None
    for path in ("/api/api-keys", "/api/api-keys/"):
        invalid = await async_client.post(path, json={"name": "Invalid", "usageGroup": "x" * 129})
        assert invalid.status_code == 422


async def test_group_isolation_bounds_and_allowlist(async_client, db_setup, monkeypatch):
    monkeypatch.setattr("app.modules.key_dashboard.service.utcnow", lambda: NOW)
    caller = await create_key(async_client, "Alice", "Team A")
    peer = await create_key(async_client, "Bob", "Team A")
    empty = await create_key(async_client, "Empty", "Team A")
    outsider = await create_key(async_client, "Secret outsider", "team a")
    start = NOW - timedelta(days=30)
    await seed_usage(caller["id"], start)
    await seed_usage(caller["id"], start - timedelta(microseconds=1))
    await seed_usage(caller["id"], NOW)
    await seed_usage(caller["id"], NOW + timedelta(days=1))
    await seed_usage(peer["id"], NOW - timedelta(days=1), deleted=True)
    await seed_usage(peer["id"], NOW - timedelta(days=1), kind="warmup")
    await seed_usage(peer["id"], NOW - timedelta(days=1), kind="limit_warmup")
    await seed_usage(outsider["id"], NOW - timedelta(days=1))
    async with SessionLocal() as session:
        await session.execute(update(ApiKey).where(ApiKey.id == peer["id"]).values(is_active=False))
        await session.execute(update(ApiKey).where(ApiKey.id == empty["id"]).values(expires_at=start))
        await session.commit()
    response = await async_client.get(
        f"/api/key-dashboard/group?key_id={outsider['id']}&group=team%20a",
        headers={"Authorization": f"Bearer {caller['key']}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"groupName", "from", "until", "members"}
    assert data["from"] == start.isoformat() + "Z"
    assert data["until"] == NOW.isoformat() + "Z"
    assert [member["name"] for member in data["members"]] == ["Alice", "Bob", "Empty"]
    assert [member["isCurrentKey"] for member in data["members"]] == [True, False, False]
    for member in data["members"]:
        assert set(member) == {
            "name",
            "keyPrefix",
            "isCurrentKey",
            "requestCount",
            "totalTokens",
            "cachedInputTokens",
            "totalCostUsd",
            "dailyUsage",
        }
        used = member["name"] != "Empty"
        assert member["requestCount"] == int(used)
        assert member["totalTokens"] == (125 if used else 0)
        assert member["cachedInputTokens"] == (20 if used else 0)
        assert member["totalCostUsd"] == (0.123 if used else 0)
        assert member["keyPrefix"].endswith("…")
        days = member["dailyUsage"]
        assert len(days) == 31
        assert [day["date"] for day in days] == [
            (start.date() + timedelta(days=offset)).isoformat() for offset in range(31)
        ]
        assert all(set(day) == {"date", "totalTokens", "totalCostUsd"} for day in days)
        assert sum(day["totalTokens"] for day in days) == member["totalTokens"]
        assert round(sum(day["totalCostUsd"] for day in days), 6) == member["totalCostUsd"]
        if member["name"] == "Alice":
            assert days[0] == {"date": "2026-08-11", "totalTokens": 125, "totalCostUsd": 0.123}
            assert all(day["totalTokens"] == 0 for day in days[1:])
    for key in (caller, peer, empty, outsider):
        assert key["key"] not in response.text
        assert key["id"] not in response.text
    assert "Secret outsider" not in response.text


async def test_group_reads_fresh_membership_and_removes_deleted_members(async_client, db_setup):
    caller = await create_key(async_client, "Alice", "Team A")
    peer = await create_key(async_client, "Bob", "Team A")
    other = await create_key(async_client, "Other", "Team B")
    assert len((await group_usage(async_client, caller))["members"]) == 2
    # Leave the validated-key cache populated while changing persisted membership.
    async with SessionLocal() as session:
        await session.execute(update(ApiKey).where(ApiKey.id == caller["id"]).values(usage_group="Team B"))
        await session.commit()
    assert [m["name"] for m in (await group_usage(async_client, caller))["members"]] == ["Alice", "Other"]
    assert [m["name"] for m in (await group_usage(async_client, peer))["members"]] == ["Bob"]
    await async_client.delete(f"/api/api-keys/{other['id']}")
    assert len((await group_usage(async_client, caller))["members"]) == 1
    await async_client.patch(f"/api/api-keys/{caller['id']}", json={"usageGroup": None})
    assert (await group_usage(async_client, caller))["members"] == []


async def test_group_rollups_preserve_pruned_history_without_double_counting(async_client, db_setup, monkeypatch):
    monkeypatch.setattr("app.modules.key_dashboard.service.utcnow", lambda: NOW)
    caller = await create_key(async_client, "Alice", "Team A")
    await seed_usage(caller["id"], NOW - timedelta(days=2), deleted=True)
    await seed_usage(caller["id"], NOW - timedelta(minutes=1))
    await seed_usage(caller["id"], NOW - timedelta(days=3), input_tokens=None)
    await seed_usage(caller["id"], NOW - timedelta(days=4), cached_input_tokens=200)
    await seed_usage(caller["id"], NOW - timedelta(days=5), cached_input_tokens=-20)
    before = await group_usage(async_client, caller)
    assert before["members"][0]["requestCount"] == 5
    assert before["members"][0]["cachedInputTokens"] == 160
    days = {day["date"]: day for day in before["members"][0]["dailyUsage"]}
    assert days["2026-09-07"]["totalTokens"] == 25
    assert days["2026-09-08"] == {"date": "2026-09-08", "totalTokens": 125, "totalCostUsd": 0.123}
    assert days["2026-09-09"] == {"date": "2026-09-09", "totalTokens": 0, "totalCostUsd": 0}
    assert await run_hourly_fold_pass(now=NOW) > 0
    assert await group_usage(async_client, caller) == before

    async with SessionLocal() as session:
        await session.execute(delete(RequestLog).where(RequestLog.requested_at < NOW - timedelta(days=1)))
        await session.commit()
    assert await group_usage(async_client, caller) == before


async def test_group_daily_values_split_utc_midnight_and_round_cost(async_client, db_setup, monkeypatch):
    monkeypatch.setattr("app.modules.key_dashboard.service.utcnow", lambda: NOW)
    caller = await create_key(async_client, "Alice", "Team A")
    midnight = datetime(2026, 9, 9)
    await seed_usage(caller["id"], midnight - timedelta(microseconds=1), cost_usd=0.1234567)
    await seed_usage(caller["id"], midnight, input_tokens=200, cost_usd=0.7654321)
    await seed_usage(caller["id"], midnight + timedelta(hours=3), input_tokens=300, cost_usd=0.0000005)
    data = await group_usage(async_client, caller)
    member = data["members"][0]
    days = {day["date"]: day for day in member["dailyUsage"]}
    assert days["2026-09-08"] == {"date": "2026-09-08", "totalTokens": 125, "totalCostUsd": 0.123457}
    assert days["2026-09-09"] == {"date": "2026-09-09", "totalTokens": 550, "totalCostUsd": 0.765433}
    assert member["totalTokens"] == 675
    assert member["totalCostUsd"] == 0.88889
    assert round(sum(day["totalCostUsd"] for day in days.values()), 6) == member["totalCostUsd"]
    assert await run_hourly_fold_pass(now=NOW) > 0
    assert await group_usage(async_client, caller) == data


async def test_group_daily_midnight_end_has_thirty_days_and_zero_members(async_client, db_setup, monkeypatch):
    until = datetime(2026, 9, 10)
    monkeypatch.setattr("app.modules.key_dashboard.service.utcnow", lambda: until)
    caller = await create_key(async_client, "Alice", "Team A")
    await create_key(async_client, "Unused", "Team A")
    await seed_usage(caller["id"], until - timedelta(microseconds=1))
    await seed_usage(caller["id"], until)
    data = await group_usage(async_client, caller)
    for member in data["members"]:
        assert len(member["dailyUsage"]) == 30
        assert member["dailyUsage"][0]["date"] == "2026-08-11"
        assert member["dailyUsage"][-1]["date"] == "2026-09-09"
    assert data["members"][0]["dailyUsage"][-1]["totalTokens"] == 125
    assert all(day["totalTokens"] == day["totalCostUsd"] == 0 for day in data["members"][1]["dailyUsage"])


@pytest.mark.parametrize("credential", [None, "invalid", "inactive", "expired"])
async def test_group_requires_valid_key_with_proxy_auth_disabled(async_client, db_setup, credential):
    headers = {}
    if credential in ("inactive", "expired"):
        key = await create_key(async_client, "Denied", "Team A")
        payload = {"isActive": False} if credential == "inactive" else {"expiresAt": "2020-01-01T00:00:00Z"}
        await async_client.patch(f"/api/api-keys/{key['id']}", json=payload)
        headers = {"Authorization": f"Bearer {key['key']}"}
    elif credential:
        headers = {"Authorization": "Bearer invalid"}
    response = await async_client.get("/api/key-dashboard/group", headers=headers)
    assert response.status_code == 401
    assert "error" in response.json()


async def test_key_holder_can_read_group_but_cannot_change_membership(async_client, db_setup):
    key = await create_key(async_client, "Member", "Team A")
    setup = await async_client.post("/api/dashboard-auth/password/setup", json={"password": "password123"})
    assert setup.status_code == 200
    await async_client.post("/api/dashboard-auth/logout", json={})
    headers = {"Authorization": f"Bearer {key['key']}"}
    denied = await async_client.patch(f"/api/api-keys/{key['id']}", json={"usageGroup": "Team B"}, headers=headers)
    assert denied.status_code == 401
    assert (await group_usage(async_client, key))["groupName"] == "Team A"
