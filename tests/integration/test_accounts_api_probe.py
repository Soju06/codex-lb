from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock

import pytest

from app.core.auth import generate_unique_account_id
from app.core.auth.refresh import RefreshError
from app.core.usage.models import UsagePayload
from app.modules.accounts import api as accounts_api
from app.modules.accounts.schemas import AccountProbeResponse
from app.modules.accounts.service import AccountsService
from app.modules.usage.updater import AccountRefreshResult, UsageUpdater

pytestmark = pytest.mark.integration


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


async def _import_test_account(async_client, *, email: str, account_id: str, plan_type: str = "pro") -> str:
    payload = {
        "email": email,
        "chatgpt_account_id": account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": plan_type},
    }
    auth_json = {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access-token-not-a-real-secret",
            "refreshToken": "refresh",
            "accountId": account_id,
        },
    }
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200, response.text
    return generate_unique_account_id(account_id, email)


@pytest.mark.asyncio
async def test_probe_missing_account_returns_404(async_client):
    response = await async_client.post("/api/accounts/missing/probe")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "account_not_found"


@pytest.mark.asyncio
async def test_probe_paused_account_returns_409(async_client, monkeypatch):
    async def _fake_probe(self, **kwargs):  # noqa: ARG001 - signature match only
        raise AssertionError("paused account should not invoke upstream probe")

    monkeypatch.setattr(AccountsService, "_send_probe_request", _fake_probe)

    account_id = await _import_test_account(
        async_client,
        email="probe-paused@example.com",
        account_id="acc_probe_paused",
    )
    pause_resp = await async_client.post(f"/api/accounts/{account_id}/pause")
    assert pause_resp.status_code == 200

    response = await async_client.post(f"/api/accounts/{account_id}/probe")
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "account_not_probable"


@pytest.mark.asyncio
async def test_probe_refresh_failure_returns_structured_409(async_client, monkeypatch):
    async def _fail_probe(self, account_id, model=None):  # noqa: ARG001 - route-level error handling only
        raise RefreshError(
            code="invalid_grant",
            message="refresh token revoked",
            is_permanent=True,
        )

    monkeypatch.setattr(AccountsService, "probe_account", _fail_probe)

    response = await async_client.post("/api/accounts/acc_refresh_failed/probe")

    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "account_probe_refresh_failed"
    assert "refresh token revoked" in body["error"]["message"]


@pytest.mark.asyncio
async def test_probe_active_account_returns_snapshot(async_client, monkeypatch):
    captured: dict = {}
    record_probe_result = AsyncMock()

    async def _fake_probe(self, *, access_token, chatgpt_account_id, model):
        captured["model"] = model
        captured["chatgpt_account_id"] = chatgpt_account_id
        # Do not capture the access token — only assert it was non-empty.
        captured["had_token"] = bool(access_token)
        return 200

    async def _force_refresh_fetches_without_writing(self, account, *, ignore_refresh_disabled=False):  # noqa: ARG001
        return AccountRefreshResult(usage_written=False, fetch_succeeded=True)

    monkeypatch.setattr(AccountsService, "_send_probe_request", _fake_probe)
    monkeypatch.setattr(UsageUpdater, "force_refresh_result", _force_refresh_fetches_without_writing)

    proxy_service = type("_ProbeRecorder", (), {"record_account_probe_result": record_probe_result})()
    monkeypatch.setattr(accounts_api, "get_proxy_service_for_app", lambda app: proxy_service)

    account_id = await _import_test_account(
        async_client,
        email="probe-active@example.com",
        account_id="acc_probe_active",
    )

    response = await async_client.post(
        f"/api/accounts/{account_id}/probe",
        json={"model": "gpt-5.5-test"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "probed"
    assert body["accountId"] == account_id
    assert body["probeStatusCode"] == 200
    assert "usageRefreshSucceeded" not in body
    assert body["accountStatusBefore"] == "active"
    assert body["accountStatusAfter"] == "active"

    assert captured["model"] == "gpt-5.5-test"
    assert captured["chatgpt_account_id"] == "acc_probe_active"
    assert captured["had_token"] is True
    record_probe_result.assert_awaited_once_with(
        account_id=account_id,
        http_status=200,
    )


@pytest.mark.asyncio
async def test_probe_active_account_returns_snapshot_when_advisory_settlement_fails(async_client, monkeypatch):
    async def _fake_probe(self, *, access_token, chatgpt_account_id, model):
        del access_token
        del chatgpt_account_id
        del model
        return 200

    async def _force_refresh_fetches_without_writing(self, account, *, ignore_refresh_disabled=False):  # noqa: ARG001
        return AccountRefreshResult(usage_written=False, fetch_succeeded=True)

    record_probe_result = AsyncMock(side_effect=RuntimeError("local settlement unavailable"))

    monkeypatch.setattr(AccountsService, "_send_probe_request", _fake_probe)
    monkeypatch.setattr(UsageUpdater, "force_refresh_result", _force_refresh_fetches_without_writing)
    proxy_service = type("_ProbeRecorder", (), {"record_account_probe_result": record_probe_result})()
    monkeypatch.setattr(accounts_api, "get_proxy_service_for_app", lambda app: proxy_service)

    account_id = await _import_test_account(
        async_client,
        email="probe-settlement-fails@example.com",
        account_id="acc_probe_settlement_fails",
    )

    response = await async_client.post(f"/api/accounts/{account_id}/probe")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "probed"
    assert body["accountId"] == account_id
    assert body["probeStatusCode"] == 200
    record_probe_result.assert_awaited_once_with(
        account_id=account_id,
        http_status=200,
    )


@pytest.mark.asyncio
async def test_probe_success_skips_advisory_settlement_when_usage_refresh_fails(async_client, monkeypatch):
    record_probe_result = AsyncMock()

    async def _probe_without_usage_refresh(self, account_id, model=None):  # noqa: ARG001 - route orchestration only
        response = AccountProbeResponse(
            status="probed",
            account_id=account_id,
            probe_status_code=200,
            account_status_before="rate_limited",
            account_status_after="rate_limited",
        )
        response._usage_refresh_fetch_succeeded = False
        return response

    monkeypatch.setattr(AccountsService, "probe_account", _probe_without_usage_refresh)
    proxy_service = type("_ProbeRecorder", (), {"record_account_probe_result": record_probe_result})()
    monkeypatch.setattr(accounts_api, "get_proxy_service_for_app", lambda app: proxy_service)

    response = await async_client.post("/api/accounts/acc_probe_usage_refresh_failed/probe")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["probeStatusCode"] == 200
    assert "usageRefreshSucceeded" not in body
    record_probe_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_probe_failure_still_records_advisory_settlement_after_usage_refresh_fails(async_client, monkeypatch):
    record_probe_result = AsyncMock()

    async def _failed_probe_without_usage_refresh(self, account_id, model=None):  # noqa: ARG001 - route orchestration only
        response = AccountProbeResponse(
            status="probed",
            account_id=account_id,
            probe_status_code=429,
            account_status_before="rate_limited",
            account_status_after="rate_limited",
        )
        response._usage_refresh_fetch_succeeded = False
        return response

    monkeypatch.setattr(AccountsService, "probe_account", _failed_probe_without_usage_refresh)
    proxy_service = type("_ProbeRecorder", (), {"record_account_probe_result": record_probe_result})()
    monkeypatch.setattr(accounts_api, "get_proxy_service_for_app", lambda app: proxy_service)

    response = await async_client.post("/api/accounts/acc_probe_usage_refresh_failed_429/probe")

    assert response.status_code == 200, response.text
    record_probe_result.assert_awaited_once_with(
        account_id="acc_probe_usage_refresh_failed_429",
        http_status=429,
    )


@pytest.mark.asyncio
async def test_force_probe_persists_free_to_plus_plan_upgrade(async_client, monkeypatch):
    async def _fake_probe(self, *, access_token, chatgpt_account_id, model):  # noqa: ARG001
        return 200

    async def _fake_fetch_usage(**_kwargs):
        return UsagePayload.model_validate({"plan_type": "plus"})

    monkeypatch.setattr(AccountsService, "_send_probe_request", _fake_probe)
    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", _fake_fetch_usage)

    account_id = await _import_test_account(
        async_client,
        email="probe-plan-upgrade@example.com",
        account_id="acc_probe_plan_upgrade",
        plan_type="free",
    )

    response = await async_client.post(f"/api/accounts/{account_id}/probe")
    assert response.status_code == 200, response.text

    listing = await async_client.get("/api/accounts")
    assert listing.status_code == 200
    account = next(item for item in listing.json()["accounts"] if item["accountId"] == account_id)
    assert account["planType"] == "plus"


@pytest.mark.asyncio
async def test_force_probe_confirms_paid_to_free_plan_downgrade(async_client, monkeypatch):
    """Regression for #1456 at the product path: an expired paid subscription on
    a workspace-less account must surface as ``free`` on /api/accounts once a
    second probe confirms it, instead of keeping a stale paid label forever."""

    async def _fake_probe(self, *, access_token, chatgpt_account_id, model):  # noqa: ARG001
        return 200

    async def _fake_fetch_usage(**_kwargs):
        return UsagePayload.model_validate({"plan_type": "free"})

    monkeypatch.setattr(AccountsService, "_send_probe_request", _fake_probe)
    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", _fake_fetch_usage)

    account_id = await _import_test_account(
        async_client,
        email="probe-plan-downgrade@example.com",
        account_id="acc_probe_plan_downgrade",
        plan_type="plus",
    )

    async def _listed_plan_type() -> str:
        listing = await async_client.get("/api/accounts")
        assert listing.status_code == 200
        account = next(item for item in listing.json()["accounts"] if item["accountId"] == account_id)
        return account["planType"]

    first = await async_client.post(f"/api/accounts/{account_id}/probe")
    assert first.status_code == 200, first.text
    assert await _listed_plan_type() == "plus"

    second = await async_client.post(f"/api/accounts/{account_id}/probe")
    assert second.status_code == 200, second.text
    assert await _listed_plan_type() == "free"


@pytest.mark.asyncio
async def test_force_probe_keeps_paid_plan_for_unrecognized_payload_plan(async_client, monkeypatch):
    """The confirmation path is scoped to ``free``: an unrecognized plan value
    must never rewrite a stored paid plan, however often it repeats."""

    async def _fake_probe(self, *, access_token, chatgpt_account_id, model):  # noqa: ARG001
        return 200

    async def _fake_fetch_usage(**_kwargs):
        return UsagePayload.model_validate({"plan_type": "mystery"})

    monkeypatch.setattr(AccountsService, "_send_probe_request", _fake_probe)
    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", _fake_fetch_usage)

    account_id = await _import_test_account(
        async_client,
        email="probe-plan-unrecognized@example.com",
        account_id="acc_probe_plan_unrecognized",
        plan_type="plus",
    )

    for _ in range(3):
        response = await async_client.post(f"/api/accounts/{account_id}/probe")
        assert response.status_code == 200, response.text

    listing = await async_client.get("/api/accounts")
    assert listing.status_code == 200
    account = next(item for item in listing.json()["accounts"] if item["accountId"] == account_id)
    assert account["planType"] == "plus"


@pytest.mark.asyncio
async def test_pending_downgrade_evidence_is_persisted_for_all_replicas(async_client, monkeypatch):
    """The pending observation must be durable database state, not process memory.

    Product-path assertion for the cross-replica review item on #1456: a single
    Force probe leaves a row in ``account_plan_downgrade_observations`` that any
    replica sharing the database reads, so the confirming observation can be made
    by a different replica than the first one.
    """
    from sqlalchemy import select

    from app.db.models import AccountPlanDowngradeObservation
    from app.db.session import get_background_session

    async def _fake_probe(self, *, access_token, chatgpt_account_id, model):  # noqa: ARG001
        return 200

    async def _fake_fetch_usage(**_kwargs):
        return UsagePayload.model_validate({"plan_type": "free"})

    monkeypatch.setattr(AccountsService, "_send_probe_request", _fake_probe)
    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", _fake_fetch_usage)

    account_id = await _import_test_account(
        async_client,
        email="probe-persisted-evidence@example.com",
        account_id="acc_probe_persisted_evidence",
        plan_type="plus",
    )

    async def _evidence_rows() -> list[tuple[int, str, str]]:
        # Read the columns inside the session; returning ORM instances would
        # detach them and make attribute access fail.
        async with get_background_session() as session:
            result = await session.execute(
                select(
                    AccountPlanDowngradeObservation.observations,
                    AccountPlanDowngradeObservation.observed_plan_type,
                    AccountPlanDowngradeObservation.credential_fingerprint,
                ).where(AccountPlanDowngradeObservation.account_id == account_id)
            )
            return [tuple(row) for row in result.all()]

    first = await async_client.post(f"/api/accounts/{account_id}/probe")
    assert first.status_code == 200, first.text

    rows = await _evidence_rows()
    assert len(rows) == 1, "the first observation must be durable, shared state"
    observations, observed_plan_type, fingerprint = rows[0]
    assert observations == 1
    assert observed_plan_type == "free"
    assert fingerprint, "evidence must be pinned to the credential that produced it"

    second = await async_client.post(f"/api/accounts/{account_id}/probe")
    assert second.status_code == 200, second.text

    listing = await async_client.get("/api/accounts")
    account = next(item for item in listing.json()["accounts"] if item["accountId"] == account_id)
    assert account["planType"] == "free"
    assert await _evidence_rows() == [], "confirmed evidence must not linger"


@pytest.mark.asyncio
async def test_reimport_clears_pending_downgrade_evidence(async_client, monkeypatch):
    """Re-importing onto the existing row must reset its pending downgrade.

    Product-path assertion for the replaced-credential review item on #1456:
    with overwrite-on-import enabled (`importWithoutOverwrite: false`), account
    ids are deterministic and re-importing the same account applies fresh token
    material to the existing row. The evidence gathered before the re-import
    must be discarded — otherwise the new credential's first `free` payload
    would confirm a downgrade on a single sample. Asserted through the same
    `/api/accounts` response the dashboard reads. (With the default
    import-without-overwrite behavior a re-import creates a separate row instead
    and replaces no credential, so the original row's evidence rightly stands.)
    """
    from sqlalchemy import select

    from app.db.models import AccountPlanDowngradeObservation
    from app.db.session import get_background_session

    async def _fake_probe(self, *, access_token, chatgpt_account_id, model):  # noqa: ARG001
        return 200

    async def _fake_fetch_usage(**_kwargs):
        return UsagePayload.model_validate({"plan_type": "free"})

    monkeypatch.setattr(AccountsService, "_send_probe_request", _fake_probe)
    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", _fake_fetch_usage)

    settings = await async_client.put("/api/settings", json={"importWithoutOverwrite": False})
    assert settings.status_code == 200, settings.text
    assert settings.json()["importWithoutOverwrite"] is False

    account_id = await _import_test_account(
        async_client,
        email="probe-reimport-evidence@example.com",
        account_id="acc_probe_reimport_evidence",
        plan_type="plus",
    )

    async def _observation_counts() -> list[int]:
        async with get_background_session() as session:
            result = await session.execute(
                select(AccountPlanDowngradeObservation.observations).where(
                    AccountPlanDowngradeObservation.account_id == account_id
                )
            )
            return [row[0] for row in result.all()]

    first = await async_client.post(f"/api/accounts/{account_id}/probe")
    assert first.status_code == 200, first.text
    assert await _observation_counts() == [1]

    # The operator re-imports the same account: same deterministic id, fresh
    # credential material applied to the existing row.
    reimported_id = await _import_test_account(
        async_client,
        email="probe-reimport-evidence@example.com",
        account_id="acc_probe_reimport_evidence",
        plan_type="plus",
    )
    assert reimported_id == account_id
    assert await _observation_counts() == [], "re-import must discard evidence from the previous credential"

    second = await async_client.post(f"/api/accounts/{account_id}/probe")
    assert second.status_code == 200, second.text

    listing = await async_client.get("/api/accounts")
    account = next(item for item in listing.json()["accounts"] if item["accountId"] == account_id)
    assert account["planType"] == "plus", "the first post-re-import observation must not downgrade"
    assert await _observation_counts() == [1]

    third = await async_client.post(f"/api/accounts/{account_id}/probe")
    assert third.status_code == 200, third.text

    listing = await async_client.get("/api/accounts")
    account = next(item for item in listing.json()["accounts"] if item["accountId"] == account_id)
    assert account["planType"] == "free", "the re-imported credential converges on its own second observation"


@pytest.mark.asyncio
async def test_probe_uses_default_model_when_body_omitted(async_client, monkeypatch):
    captured: dict = {}

    async def _fake_probe(self, *, access_token, chatgpt_account_id, model):  # noqa: ARG001
        captured["model"] = model
        return 200

    monkeypatch.setattr(AccountsService, "_send_probe_request", _fake_probe)

    account_id = await _import_test_account(
        async_client,
        email="probe-default-model@example.com",
        account_id="acc_probe_default_model",
    )

    response = await async_client.post(f"/api/accounts/{account_id}/probe")
    assert response.status_code == 200, response.text
    # The default model is service-owned; assert the helper was called with
    # *some* model string rather than coupling the test to the constant.
    assert isinstance(captured["model"], str)
    assert captured["model"]
