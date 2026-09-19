"""Guest session cookies are bound to ``dashboard_settings.guest_session_generation``.

Guest sessions are stateless Fernet cookies, so the generation counter is the
only server-side handle to log every guest out. Each trigger that changes what
a guest is (credential change, guest access disabled, explicit revocation) must
bump it, and a cookie minted under an older generation must stop validating.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import app.modules.dashboard_auth.api as dashboard_auth_api_module
from app.core.crypto import TokenEncryptor
from app.db.models import DashboardSettings
from app.db.session import SessionLocal
from app.modules.dashboard_auth.service import DASHBOARD_SESSION_COOKIE, get_dashboard_session_store

pytestmark = pytest.mark.integration

GUEST_PASSWORD = "guest-password-123"
ADMIN_PASSWORD = "admin-password-123"


async def _stored_generation() -> int:
    async with SessionLocal() as session:
        row = (await session.execute(select(DashboardSettings))).scalar_one()
        return row.guest_session_generation


def _cookie_generation(client: AsyncClient) -> int | None:
    state = get_dashboard_session_store().get(client.cookies.get(DASHBOARD_SESSION_COOKIE))
    assert state is not None
    return state.guest_session_generation


async def _put_settings(client: AsyncClient, **changes: object) -> dict[str, object]:
    current = (await client.get("/api/settings")).json()
    current.update(changes)
    response = await client.put("/api/settings", json=current)
    assert response.status_code == 200, response.text
    return response.json()


async def _remote_guest(app: FastAPI, host: str) -> AsyncClient:
    transport = ASGITransport(app=app, client=(host, 50001))
    return AsyncClient(transport=transport, base_url="http://lb.example")


def _local(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app, client=("127.0.0.1", 50000)), base_url="http://localhost")


async def _assert_guest_reads_ok(client: AsyncClient) -> None:
    response = await client.get("/api/usage/summary")
    assert response.status_code == 200, response.text


async def _assert_guest_session_dead(client: AsyncClient) -> None:
    response = await client.get("/api/usage/summary")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"
    session = await client.get("/api/dashboard-auth/session")
    assert session.json()["authenticated"] is False


@pytest.mark.asyncio
async def test_guest_password_change_revokes_existing_guest_sessions(app_instance: FastAPI) -> None:
    async with app_instance.router.lifespan_context(app_instance):
        async with _local(app_instance) as local_client:
            await _put_settings(local_client, guestAccessEnabled=True)
            set_password = await local_client.post(
                "/api/dashboard-auth/guest/password", json={"password": GUEST_PASSWORD}
            )
            assert set_password.status_code == 200

        async with await _remote_guest(app_instance, "203.0.113.31") as guest:
            login = await guest.post("/api/dashboard-auth/guest/login", json={"password": GUEST_PASSWORD})
            assert login.status_code == 200
            await _assert_guest_reads_ok(guest)

            async with _local(app_instance) as local_client:
                rotated = await local_client.post(
                    "/api/dashboard-auth/guest/password", json={"password": "new-password-456"}
                )
                assert rotated.status_code == 200

            await _assert_guest_session_dead(guest)

            relogin = await guest.post("/api/dashboard-auth/guest/login", json={"password": "new-password-456"})
            assert relogin.status_code == 200
            await _assert_guest_reads_ok(guest)


@pytest.mark.asyncio
async def test_removing_guest_password_revokes_password_guest_sessions(app_instance: FastAPI) -> None:
    async with app_instance.router.lifespan_context(app_instance):
        async with _local(app_instance) as local_client:
            await _put_settings(local_client, guestAccessEnabled=True)
            assert (
                await local_client.post("/api/dashboard-auth/guest/password", json={"password": GUEST_PASSWORD})
            ).status_code == 200

        async with await _remote_guest(app_instance, "203.0.113.32") as guest:
            assert (
                await guest.post("/api/dashboard-auth/guest/login", json={"password": GUEST_PASSWORD})
            ).status_code == 200
            await _assert_guest_reads_ok(guest)

            before = await _stored_generation()
            assert _cookie_generation(guest) == before

            async with _local(app_instance) as local_client:
                assert (await local_client.delete("/api/dashboard-auth/guest/password")).status_code == 200

            # Clearing the credential is a bump: the cookie's generation no longer
            # matches the stored one. Passwordless guest access is now open, so a
            # fresh cookie-less principal still serves reads, but the stale cookie
            # itself is never honoured.
            after = await _stored_generation()
            assert after == before + 1
            assert _cookie_generation(guest) == before
            stale = await guest.get("/api/dashboard-auth/session")
            assert stale.status_code == 200
            assert stale.json()["guestPasswordRequired"] is False


@pytest.mark.asyncio
async def test_disabling_guest_access_revokes_sessions_even_after_re_enable(app_instance: FastAPI) -> None:
    async with app_instance.router.lifespan_context(app_instance):
        async with _local(app_instance) as local_client:
            await _put_settings(local_client, guestAccessEnabled=True)
            assert (
                await local_client.post("/api/dashboard-auth/guest/password", json={"password": GUEST_PASSWORD})
            ).status_code == 200

        async with await _remote_guest(app_instance, "203.0.113.33") as guest:
            assert (
                await guest.post("/api/dashboard-auth/guest/login", json={"password": GUEST_PASSWORD})
            ).status_code == 200
            await _assert_guest_reads_ok(guest)

            async with _local(app_instance) as local_client:
                await _put_settings(local_client, guestAccessEnabled=False)
                await _put_settings(local_client, guestAccessEnabled=True)

            await _assert_guest_session_dead(guest)


@pytest.mark.asyncio
async def test_logout_all_guests_requires_security_write_and_revokes(
    app_instance: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with app_instance.router.lifespan_context(app_instance):
        async with _local(app_instance) as local_client:
            await _put_settings(local_client, guestAccessEnabled=True)
            assert (
                await local_client.post("/api/dashboard-auth/guest/password", json={"password": GUEST_PASSWORD})
            ).status_code == 200

        async with await _remote_guest(app_instance, "203.0.113.34") as guest:
            assert (
                await guest.post("/api/dashboard-auth/guest/login", json={"password": GUEST_PASSWORD})
            ).status_code == 200
            await _assert_guest_reads_ok(guest)

            # A guest cannot revoke guests.
            denied = await guest.post("/api/dashboard-auth/guest/logout-all")
            assert denied.status_code == 403
            assert denied.json()["error"]["code"] == "permission_required"
            assert denied.json()["error"]["param"] == "security:write"

            audit_events: list[tuple[str, dict[str, object]]] = []
            monkeypatch.setattr(
                dashboard_auth_api_module.AuditService,
                "log_async",
                lambda event, **kwargs: audit_events.append((event, kwargs)),
            )
            assert audit_events == []  # the guest's 403 above wrote nothing

            async with _local(app_instance) as local_client:
                # A cookie-bearing admin must survive a guest generation bump.
                assert (
                    await local_client.post("/api/dashboard-auth/password/setup", json={"password": ADMIN_PASSWORD})
                ).status_code == 200
                assert (
                    await local_client.post("/api/dashboard-auth/password/login", json={"password": ADMIN_PASSWORD})
                ).status_code == 200
                assert local_client.cookies.get(DASHBOARD_SESSION_COOKIE)

                revoked = await local_client.post("/api/dashboard-auth/guest/logout-all")
                assert revoked.status_code == 200
                # Guest settings themselves are untouched.
                settings = (await local_client.get("/api/settings")).json()
                assert settings["guestAccessEnabled"] is True
                assert settings["guestPasswordConfigured"] is True
                assert (await local_client.get("/api/dashboard-auth/session")).json()["authenticated"] is True

            revocations = [kwargs for event, kwargs in audit_events if event == "guest_sessions_revoked"]
            assert len(revocations) == 1
            assert revocations[0]["actor_ip"] == "127.0.0.1"

            await _assert_guest_session_dead(guest)


@pytest.mark.asyncio
async def test_unrelated_settings_save_keeps_guest_sessions(app_instance: FastAPI) -> None:
    async with app_instance.router.lifespan_context(app_instance):
        async with _local(app_instance) as local_client:
            await _put_settings(local_client, guestAccessEnabled=True)
            assert (
                await local_client.post("/api/dashboard-auth/guest/password", json={"password": GUEST_PASSWORD})
            ).status_code == 200

        async with await _remote_guest(app_instance, "203.0.113.35") as guest:
            assert (
                await guest.post("/api/dashboard-auth/guest/login", json={"password": GUEST_PASSWORD})
            ).status_code == 200

            async with _local(app_instance) as local_client:
                current = (await local_client.get("/api/settings")).json()
                await _put_settings(local_client, stickyThreadsEnabled=not current["stickyThreadsEnabled"])
                # Re-saving guestAccessEnabled=True (no transition) is not a bump either.
                await _put_settings(local_client, guestAccessEnabled=True)

            await _assert_guest_reads_ok(guest)


@pytest.mark.asyncio
async def test_legacy_guest_cookie_without_generation_is_rejected_on_the_product_path(app_instance: FastAPI) -> None:
    async with app_instance.router.lifespan_context(app_instance):
        async with _local(app_instance) as local_client:
            await _put_settings(local_client, guestAccessEnabled=True)
            assert (
                await local_client.post("/api/dashboard-auth/guest/password", json={"password": GUEST_PASSWORD})
            ).status_code == 200

        legacy_payload = json.dumps(
            {"exp": int(time.time()) + 3600, "pw": False, "tv": False, "role": "guest", "gv": True},
            separators=(",", ":"),
        )
        legacy_cookie = TokenEncryptor().encrypt(legacy_payload).decode("ascii")

        async with await _remote_guest(app_instance, "203.0.113.36") as guest:
            guest.cookies.set(DASHBOARD_SESSION_COOKIE, legacy_cookie, domain="lb.example")
            await _assert_guest_session_dead(guest)

            # Logging in again issues a generation-bound cookie that works.
            assert (
                await guest.post("/api/dashboard-auth/guest/login", json={"password": GUEST_PASSWORD})
            ).status_code == 200
            assert _cookie_generation(guest) == await _stored_generation()
            await _assert_guest_reads_ok(guest)


@pytest.mark.asyncio
async def test_guest_login_stamps_generation_from_the_database_not_the_cache(
    app_instance: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bump committed by a peer replica must not let this replica mint an already-stale cookie."""

    from types import SimpleNamespace

    async with app_instance.router.lifespan_context(app_instance):
        async with _local(app_instance) as local_client:
            await _put_settings(local_client, guestAccessEnabled=True)
            assert (
                await local_client.post("/api/dashboard-auth/guest/password", json={"password": GUEST_PASSWORD})
            ).status_code == 200

        # Freeze this replica's settings cache at the current generation, then
        # simulate a peer replica's bump by writing the row directly.
        async with SessionLocal() as session:
            row = (await session.execute(select(DashboardSettings))).scalar_one()
            stale_snapshot = SimpleNamespace(
                guest_access_enabled=row.guest_access_enabled,
                guest_password_hash=row.guest_password_hash,
                guest_session_generation=row.guest_session_generation,
                dashboard_session_ttl_seconds=row.dashboard_session_ttl_seconds,
                totp_required_on_login=row.totp_required_on_login,
            )
            stale_generation = row.guest_session_generation
            row.guest_session_generation += 1
            await session.commit()

        class _StaleCache:
            async def get(self) -> SimpleNamespace:
                return stale_snapshot

            async def invalidate(self, **_: object) -> None:
                return None

        monkeypatch.setattr(dashboard_auth_api_module, "get_settings_cache", lambda: _StaleCache())

        async with await _remote_guest(app_instance, "203.0.113.37") as guest:
            login = await guest.post("/api/dashboard-auth/guest/login", json={"password": GUEST_PASSWORD})
            assert login.status_code == 200
            assert _cookie_generation(guest) == stale_generation + 1
