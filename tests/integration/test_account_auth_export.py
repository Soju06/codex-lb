from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Mapping
from typing import cast

import pytest
from sqlalchemy import select

from app.core.auth import generate_unique_account_id
from app.db.models import AuditLog
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration


def _encode_jwt(payload: Mapping[str, object]) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


def _make_auth_json(account_id: str, email: str, *, access_exp: int = 2_000_000_000) -> dict[str, object]:
    id_payload = {
        "email": email,
        "chatgpt_account_id": account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    access_payload = {
        "exp": access_exp,
        "https://api.openai.com/auth": {"chatgpt_account_id": account_id},
    }
    return {
        "tokens": {
            "idToken": _encode_jwt(id_payload),
            "accessToken": _encode_jwt(access_payload),
            "refreshToken": "refresh-token",
            "accountId": account_id,
        },
    }


def _make_auth_json_without_account_id(email: str, *, access_exp: int = 2_000_000_000) -> dict[str, object]:
    id_payload = {
        "email": email,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    access_payload = {"exp": access_exp}
    return {
        "tokens": {
            "idToken": _encode_jwt(id_payload),
            "accessToken": _encode_jwt(access_payload),
            "refreshToken": "refresh-token",
        },
    }


async def _wait_for_audit_log(action: str, *, attempts: int = 20) -> AuditLog:
    for _ in range(attempts):
        async with SessionLocal() as session:
            result = await session.execute(
                select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id.desc())
            )
            row = result.scalars().first()
            if row is not None:
                return row
        await asyncio.sleep(0.05)
    raise AssertionError(f"audit log not written for action={action}")


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


@pytest.mark.asyncio
async def test_export_account_auth_keeps_unknown_account_id_null(async_client) -> None:
    email = "unknown-combined@example.com"

    import_response = await async_client.post(
        "/api/accounts/import",
        files={
            "auth_json": (
                "auth.json",
                json.dumps(_make_auth_json_without_account_id(email)),
                "application/json",
            ),
        },
    )
    assert import_response.status_code == 200
    imported_account_id = import_response.json()["accountId"]

    export_response = await async_client.post(f"/api/accounts/{imported_account_id}/export/auth")

    assert export_response.status_code == 200
    payload = export_response.json()
    assert payload["account"]["accountId"] == imported_account_id
    assert payload["account"]["chatgptAccountId"] is None
    assert payload["codexAuthJson"]["tokens"]["account_id"] is None
    assert payload["opencodeAuthJson"]["openai"]["accountId"] is None


@pytest.mark.asyncio
async def test_deprecated_export_routes_are_retired(async_client) -> None:
    # The SPA catch-all turns an unmatched POST into a 405 partial match; either
    # way the route is not served and no credential material leaves the server.
    for path in ("/api/accounts/any-account/export", "/api/accounts/any-account/export/opencode-auth"):
        response = await async_client.post(path)
        assert response.status_code in (404, 405), path
        assert not {"authJson", "tokens", "codexAuthJson", "opencodeAuthJson"} & response.json().keys(), path
