"""Integration tests for the per-account SOCKS5 proxy API + service flow."""

from __future__ import annotations

import base64
import json

import pytest

from app.core.auth import generate_unique_account_id
from app.core.auth.models import OAuthTokenPayload
from app.core.clients import account_proxy_probe as probe_module
from app.core.clients.account_proxy_probe import ProbeReason, ProbeResult
from app.core.utils.time import utcnow
from app.db.models import AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository

pytestmark = pytest.mark.integration


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


async def _import_account(async_client) -> str:
    auth_json = {
        "tokens": {
            "idToken": _encode_jwt(
                {
                    "email": "proxy@example.com",
                    "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
                }
            ),
            "accessToken": "access",
            "refreshToken": "refresh",
            "accountId": "acc_proxy_int",
        },
    }
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200
    return response.json()["accountId"]


def _auth_json(
    *,
    email: str = "proxy@example.com",
    account_id: str = "acc_proxy_int",
    access_token: str = "access",
    refresh_token: str = "refresh",
    id_token: str | None = None,
) -> dict:
    return {
        "tokens": {
            "idToken": id_token
            or _encode_jwt(
                {
                    "email": email,
                    "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
                }
            ),
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "accountId": account_id,
        },
    }


@pytest.fixture(autouse=True)
def _probe_session_factory_reset():
    """Always restore the default probe session factory between tests."""
    probe_module._set_session_factory_for_test(None)
    yield
    probe_module._set_session_factory_for_test(None)


def _scripted_probe(result: ProbeResult, captured: dict | None = None):
    """Replace ``probe_account_proxy`` with a deterministic stub.

    The stub records the (host, port, username, password, remote_dns,
    refresh_token) arguments into ``captured`` so tests can assert the
    service decrypted the refresh token and reused the existing password
    on edit.
    """

    async def _stub(
        *,
        host,
        port,
        username,
        password,
        remote_dns,
        refresh_token,
        settings=None,
    ):
        if captured is not None:
            captured.update(
                host=host,
                port=port,
                username=username,
                password=password,
                remote_dns=remote_dns,
                refresh_token=refresh_token,
            )
        return result

    return _stub


def _ok_probe_result() -> ProbeResult:
    return ProbeResult(
        reason=ProbeReason.OK,
        upstream_status_code=200,
        checked_at=utcnow(),
        tokens=OAuthTokenPayload(
            access_token="rotated-access",
            refresh_token="rotated-refresh",
            id_token="rotated-id",
        ),
    )


def _proxy_auth_fixture(suffix: str = "primary") -> str:
    return f"proxy-fixture-value-{suffix}"


def _proxy_user_fixture() -> str:
    return "proxy-user-fixture"


@pytest.mark.asyncio
async def test_import_with_proxy_probes_before_account_becomes_visible(async_client, monkeypatch):
    from app.core.crypto import TokenEncryptor
    from app.modules.usage.updater import UsageUpdater

    captured: dict = {}
    usage_refresh_seen: dict = {}
    lifecycle_events: list[str] = []

    monkeypatch.setattr(
        "app.modules.accounts.service.probe_account_proxy",
        _scripted_probe(_ok_probe_result(), captured),
    )

    async def _capture_invalidate(account_id: str):
        lifecycle_events.append(f"invalidate:{account_id}")

    monkeypatch.setattr("app.modules.accounts.service.invalidate_account_client", _capture_invalidate)

    async def _capture_usage_refresh(self, accounts, latest_usage, **kwargs):
        lifecycle_events.append(f"usage:{accounts[0].id}")
        usage_refresh_seen["proxy_host"] = accounts[0].proxy_host
        return False

    monkeypatch.setattr(UsageUpdater, "refresh_accounts", _capture_usage_refresh)

    files = {
        "auth_json": (
            "auth.json",
            json.dumps(
                _auth_json(
                    email="atomic-proxy@example.com",
                    account_id="acc_atomic_proxy",
                )
            ),
            "application/json",
        )
    }
    response = await async_client.post(
        "/api/accounts/import",
        files=files,
        data={
            "proxyHost": "proxy.example.com",
            "proxyPort": "1080",
            "proxyUsername": _proxy_user_fixture(),
            "proxyPassword": _proxy_auth_fixture(),
            "proxyRemoteDns": "true",
            "proxyLabel": "house-1",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert captured["host"] == "proxy.example.com"
    assert captured["password"] == _proxy_auth_fixture()
    assert captured["refresh_token"] == "refresh"
    assert lifecycle_events == [
        f"invalidate:{body['accountId']}",
        f"usage:{body['accountId']}",
    ]
    assert usage_refresh_seen == {"proxy_host": "proxy.example.com"}

    list_response = await async_client.get("/api/accounts")
    target = next(a for a in list_response.json()["accounts"] if a["accountId"] == body["accountId"])
    assert target["proxy"]["host"] == "proxy.example.com"
    assert target["proxy"]["hasPassword"] is True

    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(body["accountId"])
        assert account is not None
        assert account.proxy_host == "proxy.example.com"
        assert encryptor.decrypt(account.access_token_encrypted) == "rotated-access"
        assert encryptor.decrypt(account.refresh_token_encrypted) == "rotated-refresh"
        assert encryptor.decrypt(account.id_token_encrypted) == "rotated-id"


@pytest.mark.asyncio
async def test_import_with_proxy_failure_does_not_persist_account(async_client, monkeypatch):
    monkeypatch.setattr(
        "app.modules.accounts.service.probe_account_proxy",
        _scripted_probe(ProbeResult(reason=ProbeReason.PROXY_AUTH, detail="bad creds", checked_at=utcnow())),
    )

    files = {
        "auth_json": (
            "auth.json",
            json.dumps(
                _auth_json(
                    email="proxy-fail-import@example.com",
                    account_id="acc_proxy_fail_import",
                )
            ),
            "application/json",
        )
    }
    response = await async_client.post(
        "/api/accounts/import",
        files=files,
        data={
            "proxyHost": "proxy.example.com",
            "proxyPort": "1080",
            "proxyPassword": _proxy_auth_fixture("invalid"),
        },
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "proxy_probe_failed"
    assert body["error"]["reason"] == "proxy_auth"

    list_response = await async_client.get("/api/accounts")
    assert all(account["email"] != "proxy-fail-import@example.com" for account in list_response.json()["accounts"])


@pytest.mark.asyncio
async def test_import_with_proxy_persists_duplicate_copy(async_client, monkeypatch):
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

    email = "proxy-copy@example.com"
    raw_account_id = "acc_proxy_copy"
    first = await async_client.post(
        "/api/accounts/import",
        files={
            "auth_json": (
                "auth.json",
                json.dumps(_auth_json(email=email, account_id=raw_account_id)),
                "application/json",
            )
        },
    )
    assert first.status_code == 200

    captured: dict = {}
    monkeypatch.setattr(
        "app.modules.accounts.service.probe_account_proxy",
        _scripted_probe(_ok_probe_result(), captured),
    )

    second = await async_client.post(
        "/api/accounts/import",
        files={
            "auth_json": (
                "auth.json",
                json.dumps(_auth_json(email=email, account_id=raw_account_id)),
                "application/json",
            )
        },
        data={
            "proxyHost": "copy-proxy.example.com",
            "proxyPort": "1080",
        },
    )

    assert second.status_code == 200, second.text
    body = second.json()
    base_account_id = generate_unique_account_id(raw_account_id, email)
    assert first.json()["accountId"] == base_account_id
    assert body["accountId"].startswith(f"{base_account_id}__copy")
    assert captured["host"] == "copy-proxy.example.com"

    list_response = await async_client.get("/api/accounts")
    target = next(account for account in list_response.json()["accounts"] if account["accountId"] == body["accountId"])
    assert target["proxy"]["host"] == "copy-proxy.example.com"


@pytest.mark.asyncio
async def test_import_with_proxy_overwrites_existing_row(async_client, monkeypatch):
    settings = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "importWithoutOverwrite": False,
            "totpRequiredOnLogin": False,
        },
    )
    assert settings.status_code == 200
    assert settings.json()["importWithoutOverwrite"] is False

    email = "proxy-overwrite@example.com"
    first = await async_client.post(
        "/api/accounts/import",
        files={
            "auth_json": (
                "auth.json",
                json.dumps(_auth_json(email=email, account_id="acc_proxy_overwrite_one")),
                "application/json",
            )
        },
    )
    assert first.status_code == 200
    existing_account_id = first.json()["accountId"]

    captured: dict = {}
    monkeypatch.setattr(
        "app.modules.accounts.service.probe_account_proxy",
        _scripted_probe(_ok_probe_result(), captured),
    )

    second = await async_client.post(
        "/api/accounts/import",
        files={
            "auth_json": (
                "auth.json",
                json.dumps(_auth_json(email=email, account_id="acc_proxy_overwrite_two")),
                "application/json",
            )
        },
        data={
            "proxyHost": "overwrite-proxy.example.com",
            "proxyPort": "1080",
        },
    )

    assert second.status_code == 200, second.text
    assert second.json()["accountId"] == existing_account_id
    assert captured["host"] == "overwrite-proxy.example.com"

    list_response = await async_client.get("/api/accounts")
    accounts = [account for account in list_response.json()["accounts"] if account["email"] == email]
    assert len(accounts) == 1
    assert accounts[0]["accountId"] == existing_account_id
    assert accounts[0]["proxy"]["host"] == "overwrite-proxy.example.com"


@pytest.mark.asyncio
async def test_set_proxy_persists_and_summary_does_not_leak_password(async_client, monkeypatch):
    account_id = await _import_account(async_client)
    captured: dict = {}
    monkeypatch.setattr(
        "app.modules.accounts.service.probe_account_proxy",
        _scripted_probe(
            _ok_probe_result(),
            captured,
        ),
    )

    payload = {
        "host": "proxy.example.com",
        "port": 1080,
        "username": _proxy_user_fixture(),
        "password": _proxy_auth_fixture(),
        "remoteDns": False,
        "label": "house-1",
    }
    response = await async_client.post(f"/api/accounts/{account_id}/proxy", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["host"] == "proxy.example.com"
    assert body["port"] == 1080
    assert body["username"] == _proxy_user_fixture()
    assert body["hasPassword"] is True
    assert body["remoteDns"] is False
    assert body["label"] == "house-1"
    assert body["lastValidatedAt"]
    # Hard guarantee: the API never serializes the password back.
    assert "password" not in body
    assert "passwordEncrypted" not in body

    # The probe ran with the decrypted refresh token + the proposed config.
    assert captured["host"] == "proxy.example.com"
    assert captured["port"] == 1080
    assert captured["password"] == _proxy_auth_fixture()
    assert captured["remote_dns"] is False
    assert captured["refresh_token"] == "refresh"

    # GET /api/accounts surfaces the same proxy summary on the account.
    list_response = await async_client.get("/api/accounts")
    assert list_response.status_code == 200
    accounts = list_response.json()["accounts"]
    target = next(a for a in accounts if a["accountId"] == account_id)
    proxy = target["proxy"]
    assert proxy["host"] == "proxy.example.com"
    assert proxy["hasPassword"] is True
    assert "password" not in proxy


@pytest.mark.asyncio
async def test_set_proxy_omits_password_to_keep_existing(async_client, monkeypatch):
    """Editing the label without re-typing the password keeps the secret."""

    account_id = await _import_account(async_client)
    captured: dict = {}
    monkeypatch.setattr(
        "app.modules.accounts.service.probe_account_proxy",
        _scripted_probe(
            _ok_probe_result(),
            captured,
        ),
    )
    # First set with a password.
    response = await async_client.post(
        f"/api/accounts/{account_id}/proxy",
        json={
            "host": "proxy.example.com",
            "port": 1080,
            "username": _proxy_user_fixture(),
            "password": _proxy_auth_fixture(),
            "remoteDns": True,
            "label": "first",
        },
    )
    assert response.status_code == 200, response.text

    # Edit label only — no `password` key in the payload.
    captured.clear()
    response = await async_client.post(
        f"/api/accounts/{account_id}/proxy",
        json={
            "host": "proxy.example.com",
            "port": 1080,
            "username": _proxy_user_fixture(),
            "remoteDns": True,
            "label": "renamed",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["label"] == "renamed"
    assert body["hasPassword"] is True
    # Service decrypted the previously stored password and used it for the probe.
    assert captured["password"] == _proxy_auth_fixture()


@pytest.mark.asyncio
async def test_set_proxy_explicit_null_password_clears_existing(async_client, monkeypatch):
    account_id = await _import_account(async_client)
    captured: dict = {}
    monkeypatch.setattr(
        "app.modules.accounts.service.probe_account_proxy",
        _scripted_probe(_ok_probe_result(), captured),
    )
    response = await async_client.post(
        f"/api/accounts/{account_id}/proxy",
        json={
            "host": "proxy.example.com",
            "port": 1080,
            "username": _proxy_user_fixture(),
            "password": _proxy_auth_fixture(),
        },
    )
    assert response.status_code == 200, response.text

    captured.clear()
    response = await async_client.post(
        f"/api/accounts/{account_id}/proxy",
        json={
            "host": "proxy.example.com",
            "port": 1080,
            "username": _proxy_user_fixture(),
            "password": None,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["hasPassword"] is False
    assert captured["password"] is None


@pytest.mark.asyncio
async def test_set_proxy_clear_password_wins_over_submitted_password(async_client, monkeypatch):
    account_id = await _import_account(async_client)
    captured: dict = {}
    monkeypatch.setattr(
        "app.modules.accounts.service.probe_account_proxy",
        _scripted_probe(_ok_probe_result(), captured),
    )

    response = await async_client.post(
        f"/api/accounts/{account_id}/proxy",
        json={
            "host": "proxy.example.com",
            "port": 1080,
            "username": _proxy_user_fixture(),
            "password": _proxy_auth_fixture(),
        },
    )
    assert response.status_code == 200, response.text

    captured.clear()
    response = await async_client.post(
        f"/api/accounts/{account_id}/proxy",
        json={
            "host": "proxy.example.com",
            "port": 1080,
            "username": _proxy_user_fixture(),
            "password": _proxy_auth_fixture("unused"),
            "clearPassword": True,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["hasPassword"] is False
    assert captured["password"] is None


@pytest.mark.asyncio
async def test_set_proxy_preserves_non_blank_password_whitespace(async_client, monkeypatch):
    account_id = await _import_account(async_client)
    captured: dict = {}
    monkeypatch.setattr(
        "app.modules.accounts.service.probe_account_proxy",
        _scripted_probe(_ok_probe_result(), captured),
    )

    response = await async_client.post(
        f"/api/accounts/{account_id}/proxy",
        json={
            "host": "proxy.example.com",
            "port": 1080,
            "password": f" {_proxy_auth_fixture('spaced')} ",
        },
    )
    assert response.status_code == 200, response.text
    assert captured["password"] == f" {_proxy_auth_fixture('spaced')} "


@pytest.mark.asyncio
async def test_set_proxy_does_not_reuse_password_for_different_proxy(async_client, monkeypatch):
    account_id = await _import_account(async_client)
    captured: dict = {}
    monkeypatch.setattr(
        "app.modules.accounts.service.probe_account_proxy",
        _scripted_probe(_ok_probe_result(), captured),
    )
    response = await async_client.post(
        f"/api/accounts/{account_id}/proxy",
        json={
            "host": "proxy-a.example.com",
            "port": 1080,
            "username": _proxy_user_fixture(),
            "password": _proxy_auth_fixture(),
        },
    )
    assert response.status_code == 200, response.text

    captured.clear()
    response = await async_client.post(
        f"/api/accounts/{account_id}/proxy",
        json={
            "host": "proxy-b.example.com",
            "port": 1080,
            "username": _proxy_user_fixture(),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["host"] == "proxy-b.example.com"
    assert response.json()["hasPassword"] is False
    assert captured["password"] is None


@pytest.mark.asyncio
async def test_set_proxy_probe_failure_returns_422_with_reason(async_client, monkeypatch):
    account_id = await _import_account(async_client)
    monkeypatch.setattr(
        "app.modules.accounts.service.probe_account_proxy",
        _scripted_probe(ProbeResult(reason=ProbeReason.PROXY_AUTH, detail="bad creds", checked_at=utcnow())),
    )

    response = await async_client.post(
        f"/api/accounts/{account_id}/proxy",
        json={"host": "proxy.example.com", "port": 1080, "password": _proxy_auth_fixture("invalid")},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "proxy_probe_failed"
    assert body["error"]["reason"] == "proxy_auth"
    assert "bad creds" not in body["error"]["message"]
    assert body["error"]["message"] == "Proxy validation failed: proxy_auth"

    # Probe failure did NOT persist anything.
    list_response = await async_client.get("/api/accounts")
    target = next(a for a in list_response.json()["accounts"] if a["accountId"] == account_id)
    assert target["proxy"] is None


@pytest.mark.asyncio
async def test_set_proxy_rejects_ok_probe_without_rotated_tokens(async_client, monkeypatch):
    account_id = await _import_account(async_client)
    monkeypatch.setattr(
        "app.modules.accounts.service.probe_account_proxy",
        _scripted_probe(
            ProbeResult(
                reason=ProbeReason.OK,
                upstream_status_code=200,
                checked_at=utcnow(),
                tokens=None,
            )
        ),
    )

    response = await async_client.post(
        f"/api/accounts/{account_id}/proxy",
        json={"host": "proxy.example.com", "port": 1080},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "proxy_probe_failed"
    assert body["error"]["reason"] == "invalid_response"

    list_response = await async_client.get("/api/accounts")
    target = next(a for a in list_response.json()["accounts"] if a["accountId"] == account_id)
    assert target["proxy"] is None


@pytest.mark.asyncio
async def test_set_proxy_validates_payload_at_the_envelope_layer(async_client):
    account_id = await _import_account(async_client)
    response = await async_client.post(
        f"/api/accounts/{account_id}/proxy",
        json={"host": "", "port": 1080},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_set_proxy_returns_404_for_unknown_account(async_client, monkeypatch):
    monkeypatch.setattr(
        "app.modules.accounts.service.probe_account_proxy",
        _scripted_probe(_ok_probe_result()),
    )
    response = await async_client.post(
        "/api/accounts/does_not_exist/proxy",
        json={"host": "proxy.example.com", "port": 1080},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "account_not_found"


@pytest.mark.asyncio
async def test_clear_proxy_resets_summary(async_client, monkeypatch):
    account_id = await _import_account(async_client)
    monkeypatch.setattr(
        "app.modules.accounts.service.probe_account_proxy",
        _scripted_probe(_ok_probe_result()),
    )
    set_response = await async_client.post(
        f"/api/accounts/{account_id}/proxy",
        json={"host": "proxy.example.com", "port": 1080, "password": _proxy_auth_fixture("short")},
    )
    assert set_response.status_code == 200, set_response.text

    clear_response = await async_client.delete(f"/api/accounts/{account_id}/proxy")
    assert clear_response.status_code == 200
    assert clear_response.json() == {"status": "cleared"}

    list_response = await async_client.get("/api/accounts")
    target = next(a for a in list_response.json()["accounts"] if a["accountId"] == account_id)
    assert target["proxy"] is None


@pytest.mark.asyncio
async def test_set_proxy_reactivates_proxy_unreachable_account(async_client, monkeypatch):
    account_id = await _import_account(async_client)
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        updated = await repo.update_status(
            account_id,
            AccountStatus.DEACTIVATED,
            "proxy_unreachable",
            blocked_at=None,
        )
        assert updated is True

    monkeypatch.setattr(
        "app.modules.accounts.service.probe_account_proxy",
        _scripted_probe(_ok_probe_result()),
    )
    response = await async_client.post(
        f"/api/accounts/{account_id}/proxy",
        json={"host": "proxy.example.com", "port": 1080},
    )
    assert response.status_code == 200, response.text

    list_response = await async_client.get("/api/accounts")
    target = next(a for a in list_response.json()["accounts"] if a["accountId"] == account_id)
    assert target["status"] == "active"
    assert target["deactivationReason"] is None


@pytest.mark.asyncio
async def test_clear_proxy_returns_404_for_unknown_account(async_client):
    response = await async_client.delete("/api/accounts/does_not_exist/proxy")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "account_not_found"


@pytest.mark.asyncio
async def test_set_proxy_persists_rotated_tokens_from_probe(async_client, monkeypatch):
    """The probe runs a real OAuth refresh through the proposed proxy,
    so the upstream may rotate the refresh token. We MUST persist the
    rotated tokens — otherwise the previously stored refresh token is
    now stale and the next real refresh will fail with ``invalid_grant``.
    """

    from app.core.crypto import TokenEncryptor

    account_id = await _import_account(async_client)

    rotated = OAuthTokenPayload(
        access_token="rotated-access",
        refresh_token="rotated-refresh",
        id_token="rotated-id",
    )
    monkeypatch.setattr(
        "app.modules.accounts.service.probe_account_proxy",
        _scripted_probe(
            ProbeResult(
                reason=ProbeReason.OK,
                upstream_status_code=200,
                checked_at=utcnow(),
                tokens=rotated,
            )
        ),
    )

    response = await async_client.post(
        f"/api/accounts/{account_id}/proxy",
        json={"host": "proxy.example.com", "port": 1080, "password": _proxy_auth_fixture()},
    )
    assert response.status_code == 200, response.text

    # Verify the rotated tokens landed in the DB.
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        assert account is not None
        assert encryptor.decrypt(account.access_token_encrypted) == "rotated-access"
        assert encryptor.decrypt(account.refresh_token_encrypted) == "rotated-refresh"
        assert encryptor.decrypt(account.id_token_encrypted) == "rotated-id"
