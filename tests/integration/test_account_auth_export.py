from __future__ import annotations

import json
from typing import cast

import pytest

from app.core.auth import generate_unique_account_id

from .test_account_opencode_auth_export import _make_auth_json, _wait_for_audit_log

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_export_account_auth_combined(async_client) -> None:
    raw_account_id = "acc_export_combined"
    email = "export-combined@example.com"
    imported_account_id = generate_unique_account_id(raw_account_id, email)
    access_exp = 2_000_000_123

    import_response = await async_client.post(
        "/api/accounts/import",
        files={
            "auth_json": (
                "auth.json",
                json.dumps(_make_auth_json(raw_account_id, email, access_exp=access_exp)),
                "application/json",
            ),
        },
    )
    assert import_response.status_code == 200

    export_response = await async_client.post(f"/api/accounts/{imported_account_id}/export/auth")

    assert export_response.status_code == 200
    assert export_response.headers["cache-control"] == "no-store, no-cache, must-revalidate, private"
    assert export_response.headers["pragma"] == "no-cache"
    assert export_response.headers["expires"] == "0"

    payload = export_response.json()
    assert payload["filename"] == "opencode-auth-export-combined-example.com.json"

    assert payload["account"] == {
        "accountId": imported_account_id,
        "chatgptAccountId": raw_account_id,
        "email": email,
    }

    tokens = payload["tokens"]
    expected_auth = cast(dict[str, str], _make_auth_json(raw_account_id, email, access_exp=access_exp)["tokens"])
    assert tokens["idToken"] == expected_auth["idToken"]
    assert tokens["accessToken"] == expected_auth["accessToken"]
    assert tokens["refreshToken"] == "refresh-token"
    assert tokens["expiresAtMs"] == access_exp * 1000

    codex_auth = payload["codexAuthJson"]
    assert codex_auth["auth_mode"] == "chatgpt"
    assert codex_auth["OPENAI_API_KEY"] is None
    assert codex_auth["tokens"]["id_token"] == tokens["idToken"]
    assert codex_auth["tokens"]["access_token"] == tokens["accessToken"]
    assert codex_auth["tokens"]["refresh_token"] == tokens["refreshToken"]
    assert codex_auth["tokens"]["account_id"] == raw_account_id
    assert "last_refresh" in codex_auth

    opencode_auth = payload["opencodeAuthJson"]
    assert set(opencode_auth) == {"openai"}
    assert opencode_auth["openai"]["type"] == "oauth"
    assert opencode_auth["openai"]["refresh"] == "refresh-token"
    assert opencode_auth["openai"]["access"] == tokens["accessToken"]
    assert opencode_auth["openai"]["expires"] == access_exp * 1000
    assert opencode_auth["openai"]["accountId"] == raw_account_id

    audit_log = await _wait_for_audit_log("account_auth_exported")
    assert json.loads(audit_log.details or "{}") == {"account_id": imported_account_id}
    assert "refresh-token" not in (audit_log.details or "")


@pytest.mark.asyncio
async def test_export_account_auth_missing_account_returns_404(async_client) -> None:
    response = await async_client.post("/api/accounts/missing-account/export/auth")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "account_not_found"
