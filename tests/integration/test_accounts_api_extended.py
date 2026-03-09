from __future__ import annotations

import base64
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from app.core.auth import fallback_account_id, generate_unique_account_id
from app.core.auth.refresh import TokenRefreshResult
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import UsageRepository

pytestmark = pytest.mark.integration


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


def _make_auth_json(
    account_id: str | None,
    email: str,
    plan_type: str = "plus",
    *,
    access_exp: int | None = None,
    refresh_token: str = "refresh",
) -> dict:
    payload = {
        "email": email,
        "https://api.openai.com/auth": {"chatgpt_plan_type": plan_type},
    }
    if account_id:
        payload["chatgpt_account_id"] = account_id
    access_token = "access"
    if access_exp is not None:
        access_token = _encode_jwt({"exp": access_exp})
    tokens: dict[str, object] = {
        "idToken": _encode_jwt(payload),
        "accessToken": access_token,
        "refreshToken": refresh_token,
    }
    if account_id:
        tokens["accountId"] = account_id
    return {"tokens": tokens}


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


def _iso_utc(epoch_seconds: int) -> str:
    return (
        datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)
        .isoformat()
        .replace(
            "+00:00",
            "Z",
        )
    )


@pytest.mark.asyncio
async def test_import_invalid_json_returns_400(async_client):
    files = {"auth_json": ("auth.json", "not-json", "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "invalid_auth_json"


@pytest.mark.asyncio
async def test_import_missing_tokens_returns_400(async_client):
    files = {"auth_json": ("auth.json", json.dumps({"foo": "bar"}), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "invalid_auth_json"


@pytest.mark.asyncio
async def test_import_falls_back_to_email_based_account_id(async_client):
    email = "fallback@example.com"
    auth_json = _make_auth_json(None, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200
    payload = response.json()
    assert payload["accountId"] == fallback_account_id(email)
    assert payload["email"] == email


@pytest.mark.asyncio
async def test_batch_import_reports_successes_and_failures(async_client):
    valid = _make_auth_json("acc_batch", "batch@example.com")
    files = [
        ("auth_json", ("valid.json", json.dumps(valid), "application/json")),
        ("auth_json", ("broken.json", "not-json", "application/json")),
    ]

    response = await async_client.post("/api/accounts/import/batch", files=files)

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["imported"]) == 1
    assert payload["imported"][0]["filename"] == "valid.json"
    assert payload["imported"][0]["email"] == "batch@example.com"
    assert payload["failed"] == [
        {
            "filename": "broken.json",
            "code": "invalid_auth_json",
            "message": "Invalid auth.json payload",
        }
    ]


@pytest.mark.asyncio
async def test_import_refreshes_expired_access_token_when_refresh_is_valid(async_client, monkeypatch):
    old_refresh_token = "refresh-old"
    new_access_token = _encode_jwt({"exp": int((utcnow() + timedelta(hours=2)).timestamp())})
    new_id_token = _encode_jwt(
        {
            "email": "refresh-import@example.com",
            "chatgpt_account_id": "acc_refresh_import",
            "https://api.openai.com/auth": {"chatgpt_plan_type": "pro"},
        }
    )

    async def _fake_refresh(refresh_token: str) -> TokenRefreshResult:
        assert refresh_token == old_refresh_token
        return TokenRefreshResult(
            access_token=new_access_token,
            refresh_token="refresh-new",
            id_token=new_id_token,
            account_id="acc_refresh_import",
            plan_type="pro",
            email="refresh-import@example.com",
        )

    monkeypatch.setattr("app.modules.accounts.service.refresh_access_token", _fake_refresh)

    expired_auth = _make_auth_json(
        "acc_refresh_import",
        "refresh-import@example.com",
        "plus",
        access_exp=int((utcnow() - timedelta(hours=1)).timestamp()),
        refresh_token=old_refresh_token,
    )

    response = await async_client.post(
        "/api/accounts/import",
        files={"auth_json": ("expired.json", json.dumps(expired_auth), "application/json")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["refreshedOnImport"] is True
    assert payload["planType"] == "pro"

    account_id = generate_unique_account_id("acc_refresh_import", "refresh-import@example.com")
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        saved = await repo.get_by_id(account_id)

    assert saved is not None
    encryptor = TokenEncryptor()
    assert encryptor.decrypt(saved.access_token_encrypted) == new_access_token
    assert encryptor.decrypt(saved.refresh_token_encrypted) == "refresh-new"
    assert encryptor.decrypt(saved.id_token_encrypted) == new_id_token


@pytest.mark.asyncio
async def test_export_accounts_downloads_zip_with_current_auth_payloads(async_client):
    alpha = _make_auth_json("acc_export_alpha", "alpha@example.com", "plus")
    beta = _make_auth_json("acc_export_beta", "beta@example.com", "team")

    first = await async_client.post(
        "/api/accounts/import",
        files={"auth_json": ("alpha.json", json.dumps(alpha), "application/json")},
    )
    second = await async_client.post(
        "/api/accounts/import",
        files={"auth_json": ("beta.json", json.dumps(beta), "application/json")},
    )
    assert first.status_code == 200
    assert second.status_code == 200

    response = await async_client.get("/api/accounts/export")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "attachment; filename=" in response.headers["content-disposition"]

    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = sorted(archive.namelist())
    assert any(name.endswith("/auth.json") for name in names)
    assert any("alpha-example.com__" in name for name in names)
    assert any("beta-example.com__" in name for name in names)

    exported = [json.loads(archive.read(name).decode("utf-8")) for name in names if name.endswith("/auth.json")]
    exported_by_account = {item["tokens"]["accountId"]: item for item in exported}
    assert exported_by_account["acc_export_alpha"]["tokens"]["refreshToken"] == "refresh"
    assert exported_by_account["acc_export_beta"]["tokens"]["idToken"]
    assert exported_by_account["acc_export_alpha"]["lastRefreshAt"]


@pytest.mark.asyncio
async def test_import_overwrites_by_default_for_same_account_identity(async_client):
    email = "same-default@example.com"
    raw_account_id = "acc_same_default"

    files_one = {
        "auth_json": (
            "auth.json",
            json.dumps(_make_auth_json(raw_account_id, email, "plus")),
            "application/json",
        )
    }
    first = await async_client.post("/api/accounts/import", files=files_one)
    assert first.status_code == 200

    files_two = {
        "auth_json": (
            "auth.json",
            json.dumps(_make_auth_json(raw_account_id, email, "team")),
            "application/json",
        )
    }
    second = await async_client.post("/api/accounts/import", files=files_two)
    assert second.status_code == 200

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    assert first.json()["accountId"] == expected_account_id
    assert second.json()["accountId"] == expected_account_id
    assert second.json()["planType"] == "team"

    accounts_response = await async_client.get("/api/accounts")
    assert accounts_response.status_code == 200
    accounts = [entry for entry in accounts_response.json()["accounts"] if entry["email"] == email]
    assert len(accounts) == 1
    assert accounts[0]["accountId"] == expected_account_id
    assert accounts[0]["planType"] == "team"


@pytest.mark.asyncio
async def test_import_without_overwrite_keeps_same_account_identity_separate(async_client):
    settings = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "importWithoutOverwrite": True,
            "totpRequiredOnLogin": False,
        },
    )
    assert settings.status_code == 200
    assert settings.json()["importWithoutOverwrite"] is True

    email = "same-separate@example.com"
    raw_account_id = "acc_same_separate"

    files_one = {
        "auth_json": (
            "auth.json",
            json.dumps(_make_auth_json(raw_account_id, email, "plus")),
            "application/json",
        )
    }
    first = await async_client.post("/api/accounts/import", files=files_one)
    assert first.status_code == 200

    files_two = {
        "auth_json": (
            "auth.json",
            json.dumps(_make_auth_json(raw_account_id, email, "team")),
            "application/json",
        )
    }
    second = await async_client.post("/api/accounts/import", files=files_two)
    assert second.status_code == 200

    base_account_id = generate_unique_account_id(raw_account_id, email)
    first_id = first.json()["accountId"]
    second_id = second.json()["accountId"]
    assert first_id == base_account_id
    assert second_id != first_id
    assert second_id.startswith(f"{base_account_id}__copy")

    accounts_response = await async_client.get("/api/accounts")
    assert accounts_response.status_code == 200
    accounts = [entry for entry in accounts_response.json()["accounts"] if entry["email"] == email]
    assert len(accounts) == 2
    ids = {entry["accountId"] for entry in accounts}
    assert ids == {first_id, second_id}


@pytest.mark.asyncio
async def test_import_returns_409_when_overwrite_mode_cannot_resolve_duplicate_email(async_client):
    enable_separate = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "importWithoutOverwrite": True,
            "totpRequiredOnLogin": False,
        },
    )
    assert enable_separate.status_code == 200
    assert enable_separate.json()["importWithoutOverwrite"] is True

    email = "conflict@example.com"
    raw_account_id = "acc_conflict_base"

    first = await async_client.post(
        "/api/accounts/import",
        files={
            "auth_json": (
                "auth.json",
                json.dumps(_make_auth_json(raw_account_id, email, "plus")),
                "application/json",
            )
        },
    )
    assert first.status_code == 200

    second = await async_client.post(
        "/api/accounts/import",
        files={
            "auth_json": (
                "auth.json",
                json.dumps(_make_auth_json(raw_account_id, email, "team")),
                "application/json",
            )
        },
    )
    assert second.status_code == 200
    assert second.json()["accountId"] != first.json()["accountId"]

    enable_overwrite = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "importWithoutOverwrite": False,
            "totpRequiredOnLogin": False,
        },
    )
    assert enable_overwrite.status_code == 200
    assert enable_overwrite.json()["importWithoutOverwrite"] is False

    conflict = await async_client.post(
        "/api/accounts/import",
        files={
            "auth_json": (
                "auth.json",
                json.dumps(_make_auth_json("acc_conflict_new", email, "pro")),
                "application/json",
            )
        },
    )
    assert conflict.status_code == 409
    payload = conflict.json()
    assert payload["error"]["code"] == "duplicate_identity_conflict"

    accounts_response = await async_client.get("/api/accounts")
    assert accounts_response.status_code == 200
    accounts = [entry for entry in accounts_response.json()["accounts"] if entry["email"] == email]
    assert len(accounts) == 2
    assert all(entry["planType"] != "pro" for entry in accounts)


@pytest.mark.asyncio
async def test_delete_account_removes_from_list(async_client):
    email = "delete@example.com"
    raw_account_id = "acc_delete"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    actual_account_id = generate_unique_account_id(raw_account_id, email)
    delete = await async_client.delete(f"/api/accounts/{actual_account_id}")
    assert delete.status_code == 200
    assert delete.json()["status"] == "deleted"

    accounts = await async_client.get("/api/accounts")
    assert accounts.status_code == 200
    data = accounts.json()["accounts"]
    assert all(account["accountId"] != actual_account_id for account in data)


@pytest.mark.asyncio
async def test_accounts_list_includes_per_account_reset_times(async_client, db_setup):
    primary_a = 1735689600
    primary_b = 1735693200
    secondary_a = 1736294400
    secondary_b = 1736380800

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_reset_a", "a@example.com"))
        await accounts_repo.upsert(_make_account("acc_reset_b", "b@example.com"))

        await usage_repo.add_entry(
            "acc_reset_a",
            10.0,
            window="primary",
            reset_at=primary_a,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            "acc_reset_b",
            20.0,
            window="primary",
            reset_at=primary_b,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            "acc_reset_a",
            30.0,
            window="secondary",
            reset_at=secondary_a,
            window_minutes=10080,
        )
        await usage_repo.add_entry(
            "acc_reset_b",
            40.0,
            window="secondary",
            reset_at=secondary_b,
            window_minutes=10080,
        )

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}

    assert accounts["acc_reset_a"]["resetAtPrimary"] == _iso_utc(primary_a)
    assert accounts["acc_reset_b"]["resetAtPrimary"] == _iso_utc(primary_b)
    assert accounts["acc_reset_a"]["resetAtSecondary"] == _iso_utc(secondary_a)
    assert accounts["acc_reset_b"]["resetAtSecondary"] == _iso_utc(secondary_b)
    assert accounts["acc_reset_a"]["windowMinutesPrimary"] == 300
    assert accounts["acc_reset_b"]["windowMinutesPrimary"] == 300
    assert accounts["acc_reset_a"]["windowMinutesSecondary"] == 10080
    assert accounts["acc_reset_b"]["windowMinutesSecondary"] == 10080


@pytest.mark.asyncio
async def test_accounts_list_includes_request_usage_cost_rollup(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)

        await accounts_repo.upsert(_make_account("acc_cost", "cost@example.com"))
        await accounts_repo.upsert(_make_account("acc_other", "other@example.com"))

        await logs_repo.add_log(
            account_id="acc_cost",
            request_id="req_cost_1",
            model="gpt-5.3-codex",
            input_tokens=100_000,
            output_tokens=20_000,
            cached_input_tokens=90_000,
            latency_ms=200,
            status="success",
            error_code=None,
        )
        await logs_repo.add_log(
            account_id="acc_cost",
            request_id="req_cost_2",
            model="gpt-5.1-codex",
            input_tokens=50_000,
            output_tokens=10_000,
            cached_input_tokens=0,
            latency_ms=180,
            status="success",
            error_code=None,
        )
        await logs_repo.add_log(
            account_id="acc_other",
            request_id="req_other_1",
            model="gpt-5.1-codex-mini",
            input_tokens=1_000,
            output_tokens=500,
            cached_input_tokens=0,
            latency_ms=150,
            status="success",
            error_code=None,
        )

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}

    request_usage = accounts["acc_cost"]["requestUsage"]
    assert request_usage is not None
    assert request_usage["requestCount"] == 2
    assert request_usage["totalTokens"] == 180_000
    assert request_usage["cachedInputTokens"] == 90_000
    assert request_usage["totalCostUsd"] == pytest.approx(0.47575, abs=1e-6)

    other_usage = accounts["acc_other"]["requestUsage"]
    assert other_usage is not None
    assert other_usage["requestCount"] == 1
    assert other_usage["totalTokens"] == 1_500


@pytest.mark.asyncio
async def test_accounts_list_request_usage_cost_rollup_respects_service_tier(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)

        await accounts_repo.upsert(_make_account("acc_priority_cost", "priority-cost@example.com"))

        await logs_repo.add_log(
            account_id="acc_priority_cost",
            request_id="req_priority_cost_1",
            model="gpt-5.4",
            service_tier="priority",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            latency_ms=200,
            status="success",
            error_code=None,
        )

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}

    request_usage = accounts["acc_priority_cost"]["requestUsage"]
    assert request_usage is not None
    assert request_usage["requestCount"] == 1
    assert request_usage["totalTokens"] == 2_000_000
    assert request_usage["cachedInputTokens"] == 0
    assert request_usage["totalCostUsd"] == pytest.approx(35.0, abs=1e-6)


@pytest.mark.asyncio
async def test_accounts_list_maps_weekly_only_primary_to_secondary(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_free_like", "free@example.com", plan_type="free"))
        await usage_repo.add_entry(
            "acc_free_like",
            24.0,
            window="primary",
            window_minutes=10080,
        )

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}

    account = accounts["acc_free_like"]
    assert account["usage"]["primaryRemainingPercent"] is None
    assert account["usage"]["secondaryRemainingPercent"] == pytest.approx(76.0)
    assert account["windowMinutesPrimary"] is None
    assert account["windowMinutesSecondary"] == 10080


@pytest.mark.asyncio
async def test_accounts_list_prefers_newer_weekly_primary_over_stale_secondary(async_client, db_setup):
    now = utcnow()
    stale_reset = 1735689600
    weekly_reset = 1735862400

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_weekly_stale", "weekly-stale@example.com", plan_type="free"))
        await usage_repo.add_entry(
            "acc_weekly_stale",
            15.0,
            window="secondary",
            reset_at=stale_reset,
            window_minutes=10080,
            recorded_at=now - timedelta(days=2),
        )
        await usage_repo.add_entry(
            "acc_weekly_stale",
            80.0,
            window="primary",
            reset_at=weekly_reset,
            window_minutes=10080,
            recorded_at=now,
        )

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}

    account = accounts["acc_weekly_stale"]
    assert account["usage"]["primaryRemainingPercent"] is None
    assert account["usage"]["secondaryRemainingPercent"] == pytest.approx(20.0)
    assert account["windowMinutesPrimary"] is None
    assert account["windowMinutesSecondary"] == 10080
    assert account["resetAtSecondary"] == _iso_utc(weekly_reset)
