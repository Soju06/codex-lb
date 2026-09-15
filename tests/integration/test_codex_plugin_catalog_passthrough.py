from __future__ import annotations

import base64
import json

import pytest

import app.modules.proxy.service as proxy_module
from app.core.auth import generate_unique_account_id
from app.core.clients import proxy as core_proxy

pytestmark = pytest.mark.integration


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


def _make_auth_json(account_id: str, email: str) -> dict:
    payload = {
        "email": email,
        "chatgpt_account_id": account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    return {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "accountId": account_id,
        },
    }


async def _import_account(async_client, account_id: str, email: str) -> str:
    files = {"auth_json": ("auth.json", json.dumps(_make_auth_json(account_id, email)), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200
    return generate_unique_account_id(account_id, email)


_UPSTREAM_BODY = b'{"plugins":[{"id":"gmail@openai-curated-remote","name":"gmail"}]}'


@pytest.fixture
def upstream_calls(monkeypatch):
    calls: list[dict] = []

    async def fake_codex_control_request(
        path,
        *,
        method,
        payload,
        query_params,
        headers,
        access_token,
        account_id,
        timeout_seconds=None,
        **_kwargs,
    ):
        calls.append(
            {
                "path": path,
                "method": method,
                "payload": payload,
                "query": list(query_params),
                "access_token": access_token,
                "account_id": account_id,
            }
        )
        return core_proxy.CodexControlResponse(
            status_code=200,
            body=_UPSTREAM_BODY,
            headers={"content-type": "application/json", "cache-control": "private, max-age=60"},
        )

    monkeypatch.setattr(proxy_module, "core_codex_control_request", fake_codex_control_request)
    return calls


# Every catalog read Codex CLI 0.154 issues against ``chatgpt_base_url``: the
# list/installed/suggested/featured reads at startup and the per-plugin detail
# read it performs before installing from the remote marketplace.
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "query", "upstream_path"),
    [
        ("/ps/plugins/list", [("scope", "GLOBAL"), ("limit", "200")], "ps/plugins/list"),
        ("/ps/plugins/installed", [("limit", "200"), ("includeDownloadUrls", "true")], "ps/plugins/installed"),
        ("/ps/plugins/suggested/codex", [("scope", "GLOBAL")], "ps/plugins/suggested/codex"),
        ("/ps/plugins/gmail", [("includeDownloadUrls", "true")], "ps/plugins/gmail"),
        ("/plugins/featured", [("platform", "codex")], "plugins/featured"),
    ],
)
async def test_plugin_catalog_reads_forward_upstream_verbatim(
    async_client, upstream_calls, endpoint, query, upstream_path
):
    await _import_account(async_client, "acc_catalog", "catalog@example.com")

    # The caller's own identity: a different account than the one in the pool
    # would be the realistic case, and it must not leak into the upstream call.
    response = await async_client.get(
        endpoint,
        params=query,
        headers={"Authorization": "Bearer caller-token", "chatgpt-account-id": "caller_account"},
    )

    assert response.status_code == 200
    assert response.content == _UPSTREAM_BODY
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "private, max-age=60"

    assert len(upstream_calls) == 1
    call = upstream_calls[0]
    assert call["path"] == upstream_path
    assert call["method"] == "GET"
    assert call["payload"] is None
    assert call["query"] == query
    # Served with pool credentials, like every other Codex control request --
    # never with the caller's own bearer token or account id.
    assert call["account_id"] == "acc_catalog"
    assert call["access_token"] == "access-token"


@pytest.mark.asyncio
async def test_plugin_catalog_is_read_only(async_client, upstream_calls):
    await _import_account(async_client, "acc_catalog_ro", "catalog-ro@example.com")

    response = await async_client.post("/ps/plugins/list", json={})

    assert response.status_code == 405
    assert upstream_calls == []
