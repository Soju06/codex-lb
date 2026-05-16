from __future__ import annotations

import pytest
from sqlalchemy import delete

from app.db.models import RateLimitAttempt
from app.db.session import get_background_session

pytestmark = pytest.mark.integration


async def _clear_password_rate_limit_attempts() -> None:
    async with get_background_session() as session:
        await session.execute(delete(RateLimitAttempt).where(RateLimitAttempt.type == "password"))
        await session.commit()


@pytest.mark.asyncio
async def test_password_endpoints_setup_login_change_remove(async_client):
    weak = await async_client.post("/api/dashboard-auth/password/setup", json={"password": "short"})
    assert weak.status_code == 422
    assert weak.json()["error"]["code"] == "validation_error"

    setup = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
    )
    assert setup.status_code == 200
    assert setup.json()["passwordRequired"] is True

    setup_again = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
    )
    assert setup_again.status_code == 409

    logout = await async_client.post("/api/dashboard-auth/logout", json={})
    assert logout.status_code == 200

    invalid_login = await async_client.post(
        "/api/dashboard-auth/password/login",
        json={"password": "wrong-password"},
    )
    assert invalid_login.status_code == 401
    assert invalid_login.json()["error"]["code"] == "invalid_credentials"

    login = await async_client.post(
        "/api/dashboard-auth/password/login",
        json={"password": "password123"},
    )
    assert login.status_code == 200
    assert login.json()["authenticated"] is True

    bad_change = await async_client.post(
        "/api/dashboard-auth/password/change",
        json={"currentPassword": "wrong-password", "newPassword": "new-password-456"},
    )
    assert bad_change.status_code == 401
    assert bad_change.json()["error"]["code"] == "invalid_credentials"

    change = await async_client.post(
        "/api/dashboard-auth/password/change",
        json={"currentPassword": "password123", "newPassword": "new-password-456"},
    )
    assert change.status_code == 200

    logout_again = await async_client.post("/api/dashboard-auth/logout", json={})
    assert logout_again.status_code == 200

    old_login = await async_client.post(
        "/api/dashboard-auth/password/login",
        json={"password": "password123"},
    )
    assert old_login.status_code == 401

    new_login = await async_client.post(
        "/api/dashboard-auth/password/login",
        json={"password": "new-password-456"},
    )
    assert new_login.status_code == 200

    bad_remove = await async_client.request(
        "DELETE",
        "/api/dashboard-auth/password",
        json={"password": "wrong-password"},
    )
    assert bad_remove.status_code == 401
    assert bad_remove.json()["error"]["code"] == "invalid_credentials"

    remove = await async_client.request(
        "DELETE",
        "/api/dashboard-auth/password",
        json={"password": "new-password-456"},
    )
    assert remove.status_code == 200

    session = await async_client.get("/api/dashboard-auth/session")
    assert session.status_code == 200
    session_payload = session.json()
    assert session_payload["passwordRequired"] is False
    assert session_payload["authenticated"] is True
    assert session_payload["totpRequiredOnLogin"] is False


@pytest.mark.asyncio
async def test_password_login_rate_limit(async_client):
    await _clear_password_rate_limit_attempts()

    setup = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
    )
    assert setup.status_code == 200
    await async_client.post("/api/dashboard-auth/logout", json={})

    for _ in range(8):
        response = await async_client.post(
            "/api/dashboard-auth/password/login",
            json={"password": "wrong-password"},
        )
        assert response.status_code == 401

    limited = await async_client.post(
        "/api/dashboard-auth/password/login",
        json={"password": "wrong-password"},
    )
    assert limited.status_code == 429
    assert "Retry-After" in limited.headers

    await _clear_password_rate_limit_attempts()
    success = await async_client.post(
        "/api/dashboard-auth/password/login",
        json={"password": "password123"},
    )
    assert success.status_code == 200


@pytest.mark.asyncio
async def test_password_not_configured_requests_do_not_spend_login_budget(async_client):
    await _clear_password_rate_limit_attempts()

    for _ in range(8):
        response = await async_client.post(
            "/api/dashboard-auth/password/login",
            json={"password": "wrong-password"},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "password_not_configured"

    setup = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
    )
    assert setup.status_code == 200

    logout = await async_client.post("/api/dashboard-auth/logout", json={})
    assert logout.status_code == 200

    login = await async_client.post(
        "/api/dashboard-auth/password/login",
        json={"password": "password123"},
    )
    assert login.status_code == 200


@pytest.mark.asyncio
async def test_password_setup_rejects_overlong_password(async_client):
    # bcrypt enforces a hard 72-byte input limit and raises ValueError otherwise.
    # The API must surface this as a 422 validation error, not a 500.
    long_password = "a" * 73  # 73 ASCII bytes -> over the bcrypt limit
    response = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": long_password},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "password_too_long"
    assert "72 bytes" in body["error"]["message"]


@pytest.mark.asyncio
async def test_password_setup_accepts_72_byte_password(async_client):
    # Exactly 72 bytes must still be accepted.
    setup = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "a" * 72},
    )
    assert setup.status_code == 200


@pytest.mark.asyncio
async def test_password_setup_counts_utf8_bytes_not_codepoints(async_client):
    # 25 emoji code points = 100 UTF-8 bytes (each emoji is 4 bytes).
    # Even though len() reports 25 characters, the encoded length exceeds 72
    # bytes and must be rejected with the same clear error.
    long_emoji = "🦞" * 25  # 100 bytes when encoded
    response = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": long_emoji},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "password_too_long"


@pytest.mark.asyncio
async def test_password_change_rejects_overlong_new_password(async_client):
    # Establish a valid password so we can authenticate the change request.
    setup = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
    )
    assert setup.status_code == 200

    change = await async_client.post(
        "/api/dashboard-auth/password/change",
        json={"current_password": "password123", "new_password": "a" * 73},
    )
    assert change.status_code == 422
    body = change.json()
    assert body["error"]["code"] == "password_too_long"
