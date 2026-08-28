from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.core.auth import generate_unique_account_id
from app.modules.accounts import api as accounts_api_module

from .test_account_opencode_auth_export import _make_auth_json

pytestmark = pytest.mark.integration


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
