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
async def test_export_account_opencode_auth_json(async_client) -> None:
    raw_account_id = "acc_export_opencode"
    email = "export-opencode@example.com"
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

    export_response = await async_client.post(f"/api/accounts/{imported_account_id}/export/opencode-auth")

    assert export_response.status_code == 200
    payload = export_response.json()
    assert payload["filename"] == "opencode-auth-export-opencode-example.com.json"
    assert payload["account"] == {
        "accountId": imported_account_id,
        "chatgptAccountId": raw_account_id,
        "email": email,
    }

    auth_json = payload["authJson"]
    assert set(auth_json) == {"openai"}
    assert auth_json["openai"] == {
        "type": "oauth",
        "refresh": "refresh-token",
        "access": cast(dict[str, str], _make_auth_json(raw_account_id, email, access_exp=access_exp)["tokens"])[
            "accessToken"
        ],
        "expires": access_exp * 1000,
        "accountId": raw_account_id,
    }
    assert "email" not in auth_json["openai"]
    assert "accounts" not in auth_json["openai"]

    audit_log = await _wait_for_audit_log("account_auth_exported")
    assert json.loads(audit_log.details or "{}") == {"account_id": imported_account_id}
    assert "refresh-token" not in (audit_log.details or "")


@pytest.mark.asyncio
async def test_export_account_opencode_auth_missing_account_returns_404(async_client) -> None:
    response = await async_client.post("/api/accounts/missing-account/export/opencode-auth")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "account_not_found"
