from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.core.auth import generate_unique_account_id
from app.core.crypto import TokenEncryptor
from app.core.exceptions import DashboardPermissionError
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts import api as accounts_api_module
from app.modules.accounts.repository import BUNDLE_IMPORT_VALIDATION_PAUSE_REASON

from .test_account_opencode_auth_export import _make_auth_json

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_account_bundle_export_openapi_preserves_manual_json_body_schema(async_client) -> None:
    openapi = (await async_client.get("/openapi.json")).json()
    request_body = openapi["paths"]["/api/accounts/bundle/export"]["post"]["requestBody"]

    assert request_body["required"] is True
    schema = request_body["content"]["application/json"]["schema"]
    assert set(schema["required"]) == {"passphrase"}
    assert set(schema["properties"]) == {"accountIds", "passphrase"}


@pytest.mark.asyncio
async def test_account_bundle_export_authenticates_before_reading_body(async_client, app_instance) -> None:
    body_read = False
    synthetic_value = "-".join(("must", "not", "be", "read"))

    async def deny_write_access():
        raise DashboardPermissionError("Read-only dashboard access", code="read_only_access")

    async def body():
        nonlocal body_read
        body_read = True
        yield json.dumps({"accountIds": [], "passphrase": synthetic_value}).encode()

    app_instance.dependency_overrides[accounts_api_module.require_dashboard_write_access] = deny_write_access
    try:
        response = await async_client.post(
            "/api/accounts/bundle/export",
            content=body(),
            headers={"content-type": "application/json"},
        )
    finally:
        app_instance.dependency_overrides.pop(accounts_api_module.require_dashboard_write_access, None)

    assert response.status_code == 403
    assert body_read is False
    assert response.headers["cache-control"].startswith("no-store")


@pytest.mark.asyncio
async def test_account_bundle_export_request_body_is_bounded(async_client, monkeypatch) -> None:
    monkeypatch.setattr(
        accounts_api_module,
        "get_settings",
        lambda: SimpleNamespace(account_bundle_max_bytes=8),
    )

    response = await async_client.post(
        "/api/accounts/bundle/export",
        content=b'{"accountIds":[]}',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert response.headers["cache-control"].startswith("no-store")


def test_account_bundle_export_rejects_single_oversized_chunk_before_retaining_it() -> None:
    body = bytearray()
    oversized_chunk = b"123456789"

    with pytest.raises(accounts_api_module.MultipartPayloadTooLarge):
        accounts_api_module._extend_bounded_export_body(body, oversized_chunk, max_bytes=8)

    assert body == bytearray()


@pytest.mark.asyncio
async def test_account_bundle_export_preflight_skip_and_replace(async_client) -> None:
    raw_account_id = "bundle-workspace"
    email = "bundle-user@example.com"
    account_id = generate_unique_account_id(raw_account_id, email)
    imported = await async_client.post(
        "/api/accounts/import",
        files={
            "auth_json": (
                "auth.json",
                json.dumps(_make_auth_json(raw_account_id, email)),
                "application/json",
            )
        },
    )
    assert imported.status_code == 200
    assert (await async_client.put(f"/api/accounts/{account_id}/alias", json={"alias": "portable"})).status_code == 200

    exported = await async_client.post(
        "/api/accounts/bundle/export",
        json={"accountIds": [account_id], "passphrase": "test-passphrase"},
    )
    assert exported.status_code == 200
    assert exported.headers["cache-control"].startswith("no-store")
    assert exported.headers["content-disposition"].startswith("attachment;")
    assert b"refresh-token" not in exported.content
    assert email.encode() not in exported.content

    wrong = await async_client.post(
        "/api/accounts/bundle/import/preflight",
        files={"bundle": ("accounts.clb-account-bundle", exported.content)},
        data={"passphrase": "wrong-passphrase"},
    )
    assert wrong.status_code == 400
    assert wrong.headers["cache-control"].startswith("no-store")
    assert wrong.json()["error"]["code"] == "invalid_account_bundle"
    assert "refresh-token" not in wrong.text
    assert email not in wrong.text

    preflight = await async_client.post(
        "/api/accounts/bundle/import/preflight",
        files={"bundle": ("accounts.clb-account-bundle", exported.content)},
        data={"passphrase": "test-passphrase"},
    )
    assert preflight.status_code == 200
    preview = preflight.json()
    assert preview["accountCount"] == 1
    assert preview["newCount"] == 0
    assert preview["matchingCount"] == 1
    assert preview["accounts"][0]["maskedIdentity"] == "b***@example.com"
    assert "destinationAccountId" not in preview["accounts"][0]
    assert preview["accounts"][0]["metadata"]["alias"] == "portable"
    assert "refresh-token" not in preflight.text

    skipped = await async_client.post(
        "/api/accounts/bundle/import/commit",
        files={"bundle": ("accounts.clb-account-bundle", exported.content)},
        data={
            "passphrase": "test-passphrase",
            "integrity_token": preview["integrityToken"],
            "conflict_mode": "skip",
            "confirm_replace": "false",
        },
    )
    assert skipped.status_code == 200
    assert skipped.json()["summary"] == {"imported": 0, "replaced": 0, "skipped": 1, "failed": 0}

    alias_changed = await async_client.put(
        f"/api/accounts/{account_id}/alias",
        json={"alias": "destination"},
    )
    assert alias_changed.status_code == 200
    unconfirmed = await async_client.post(
        "/api/accounts/bundle/import/commit",
        files={"bundle": ("accounts.clb-account-bundle", exported.content)},
        data={
            "passphrase": "test-passphrase",
            "integrity_token": preview["integrityToken"],
            "conflict_mode": "replace",
            "confirm_replace": "false",
        },
    )
    assert unconfirmed.status_code == 400

    replaced = await async_client.post(
        "/api/accounts/bundle/import/commit",
        files={"bundle": ("accounts.clb-account-bundle", exported.content)},
        data={
            "passphrase": "test-passphrase",
            "integrity_token": preview["integrityToken"],
            "conflict_mode": "replace",
            "confirm_replace": "true",
        },
    )
    assert replaced.status_code == 200
    assert replaced.json()["summary"] == {"imported": 0, "replaced": 1, "skipped": 0, "failed": 0}
    accounts = (await async_client.get("/api/accounts")).json()["accounts"]
    restored = next(account for account in accounts if account["accountId"] == account_id)
    assert restored["alias"] == "portable"


@pytest.mark.asyncio
async def test_account_bundle_replace_reauth_required_stays_quarantined_when_validation_fails(
    async_client,
    monkeypatch,
) -> None:
    raw_account_id = "bundle-reauth-quarantine"
    email = "bundle-reauth-quarantine@example.invalid"
    account_id = generate_unique_account_id(raw_account_id, email)
    imported = await async_client.post(
        "/api/accounts/import",
        files={
            "auth_json": (
                "auth.json",
                json.dumps(_make_auth_json(raw_account_id, email)),
                "application/json",
            )
        },
    )
    assert imported.status_code == 200

    exported = await async_client.post(
        "/api/accounts/bundle/export",
        json={"accountIds": [account_id], "passphrase": "test-passphrase"},
    )
    assert exported.status_code == 200

    async with SessionLocal() as session:
        destination = await session.get(Account, account_id)
        assert destination is not None
        destination.status = AccountStatus.REAUTH_REQUIRED
        destination.deactivation_reason = "destination-reauth-required"
        await session.commit()

    preflight = await async_client.post(
        "/api/accounts/bundle/import/preflight",
        files={"bundle": ("accounts.clb-account-bundle", exported.content)},
        data={"passphrase": "test-passphrase"},
    )
    assert preflight.status_code == 200

    async def fail_validation(_service, _result):
        return None, False, False

    monkeypatch.setattr(
        "app.modules.accounts.service.AccountsService._validate_imported_bundle_account",
        fail_validation,
    )
    committed = await async_client.post(
        "/api/accounts/bundle/import/commit",
        files={"bundle": ("accounts.clb-account-bundle", exported.content)},
        data={
            "passphrase": "test-passphrase",
            "integrity_token": preflight.json()["integrityToken"],
            "conflict_mode": "replace",
            "confirm_replace": "true",
        },
    )

    assert committed.status_code == 200
    assert committed.json()["summary"] == {"imported": 0, "replaced": 1, "skipped": 0, "failed": 0}
    assert committed.json()["results"][0]["warning"] == "Account validation could not be completed."
    assert committed.json()["warnings"] == ["Some imported accounts could not be validated."]
    async with SessionLocal() as session:
        quarantined = await session.get(Account, account_id)
        assert quarantined is not None
        assert quarantined.status == AccountStatus.PAUSED
        assert quarantined.deactivation_reason == BUNDLE_IMPORT_VALIDATION_PAUSE_REASON


@pytest.mark.asyncio
async def test_account_bundle_supports_empty_export_and_rejects_changed_upload(async_client) -> None:
    exported = await async_client.post(
        "/api/accounts/bundle/export",
        json={"accountIds": [], "passphrase": "test-passphrase"},
    )
    assert exported.status_code == 200
    preflight = await async_client.post(
        "/api/accounts/bundle/import/preflight",
        files={"bundle": ("accounts.clb-account-bundle", exported.content)},
        data={"passphrase": "test-passphrase"},
    )
    assert preflight.status_code == 200
    assert preflight.json()["accountCount"] == 0

    changed = exported.content + b" "
    committed = await async_client.post(
        "/api/accounts/bundle/import/commit",
        files={"bundle": ("accounts.clb-account-bundle", changed)},
        data={
            "passphrase": "test-passphrase",
            "integrity_token": preflight.json()["integrityToken"],
            "conflict_mode": "skip",
            "confirm_replace": "false",
        },
    )
    assert committed.status_code == 400
    assert committed.json()["error"]["code"] == "invalid_account_bundle"
    assert committed.headers["cache-control"].startswith("no-store")


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_field", ["email", "refresh_token"])
async def test_account_bundle_export_rejects_blank_stored_data_safely(async_client, invalid_field: str) -> None:
    raw_account_id = f"bundle-blank-{invalid_field}"
    email = f"bundle-blank-{invalid_field}@example.invalid"
    account_id = generate_unique_account_id(raw_account_id, email)
    imported = await async_client.post(
        "/api/accounts/import",
        files={
            "auth_json": (
                "auth.json",
                json.dumps(_make_auth_json(raw_account_id, email)),
                "application/json",
            )
        },
    )
    assert imported.status_code == 200

    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None
        if invalid_field == "email":
            account.email = "   "
        else:
            account.refresh_token_encrypted = TokenEncryptor().encrypt("   ")
        await session.commit()

    response = await async_client.post(
        "/api/accounts/bundle/export",
        json={"accountIds": [account_id], "passphrase": "test-passphrase"},
    )

    assert response.status_code == 400
    assert response.headers["cache-control"].startswith("no-store")
    assert response.json()["error"]["code"] == "invalid_account_bundle"
    assert email not in response.text
    assert "refresh-token" not in response.text


@pytest.mark.asyncio
async def test_account_bundle_failures_are_safe_and_never_cacheable(async_client, monkeypatch) -> None:
    audit_events: list[tuple[str, dict[str, object] | None]] = []
    monkeypatch.setattr(
        accounts_api_module.AuditService,
        "log_async",
        lambda action, **kwargs: audit_events.append((action, kwargs.get("details"))),
    )
    malformed = await async_client.post(
        "/api/accounts/bundle/import/preflight",
        files={"bundle": ("accounts.clb-account-bundle", b"not-json")},
        data={"passphrase": "test-passphrase"},
    )
    assert malformed.status_code == 400
    assert malformed.headers["cache-control"].startswith("no-store")
    assert malformed.json()["error"] == {
        "code": "invalid_account_bundle",
        "message": "Invalid account bundle or passphrase",
    }

    unsupported_encoding = await async_client.post(
        "/api/accounts/bundle/import/preflight",
        files={"bundle": ("accounts.clb-account-bundle", b"opaque")},
        data={"passphrase": "test-passphrase"},
        headers={"content-encoding": "gzip"},
    )
    assert unsupported_encoding.status_code == 400
    assert unsupported_encoding.headers["cache-control"].startswith("no-store")
    assert unsupported_encoding.json()["error"]["message"] == "Compressed multipart uploads are not supported"

    validation_failure = await async_client.post(
        "/api/accounts/bundle/export",
        json={"accountIds": [], "passphrase": ""},
    )
    assert validation_failure.status_code == 422
    assert validation_failure.headers["cache-control"].startswith("no-store")
    assert validation_failure.json()["error"]["message"] == "Invalid request payload"

    monkeypatch.setattr(
        accounts_api_module,
        "get_settings",
        lambda: SimpleNamespace(account_bundle_max_bytes=4),
    )
    oversized = await async_client.post(
        "/api/accounts/bundle/import/preflight",
        files={"bundle": ("accounts.clb-account-bundle", b"12345")},
        data={"passphrase": "test-passphrase"},
    )
    assert oversized.status_code == 413
    assert oversized.headers["cache-control"].startswith("no-store")
    assert oversized.json()["error"]["code"] == "payload_too_large"
    assert [action for action, _details in audit_events].count("account_bundle_preflight_failed") == 1
    assert [action for action, _details in audit_events].count("account_bundle_request_failed") == 3
    assert all(
        details is None or set(details) <= {"operation", "outcome"}
        for _action, details in audit_events
        if _action == "account_bundle_request_failed"
    )
