"""CPA management composes with real user/guest sessions and browser CSRF checks."""

from __future__ import annotations

import pytest
from aiohttp import web
from httpx import ASGITransport, AsyncClient

from app.core.audit.service import drain_audit_log_tasks
from tests.integration.model_source_helpers import stub_source_upstreams

pytestmark = pytest.mark.integration


def _browser_headers(mode: str, origin: str) -> dict[str, str]:
    if mode == "same-origin":
        return {"Sec-Fetch-Site": "same-origin"}
    if mode == "origin-only":
        return {"Origin": origin}
    return {}


@pytest.mark.parametrize("browser_mode", ["headerless", "same-origin", "origin-only"])
async def test_guest_cannot_change_cpa_source_and_revocation_preserves_public_catalog(app_instance, browser_mode):
    async def catalog(request):
        assert request.headers["Authorization"] == "Bearer fixture-cpa-key"
        return web.json_response({"models": [{"slug": "guest-compatible-cpa", "context_window": 8192}]})

    admin_transport = ASGITransport(app=app_instance, client=("127.0.0.1", 50000))
    guest_transport = ASGITransport(app=app_instance, client=("203.0.113.31", 50001))
    admin_headers = _browser_headers(browser_mode, "http://localhost")
    guest_headers = _browser_headers(browser_mode, "http://lb.example")
    async with app_instance.router.lifespan_context(app_instance), stub_source_upstreams() as start:
        async with (
            AsyncClient(transport=admin_transport, base_url="http://localhost", headers=admin_headers) as admin,
            AsyncClient(transport=guest_transport, base_url="http://localhost", headers=admin_headers) as other_admin,
            AsyncClient(transport=guest_transport, base_url="http://lb.example", headers=guest_headers) as guest,
            AsyncClient(transport=guest_transport, base_url="http://lb.example", headers=guest_headers) as other_guest,
        ):
            native = (await admin.get("/v1/models")).json()["data"]
            settings = (await admin.get("/api/settings")).json()
            settings["guestAccessEnabled"] = True
            assert (await admin.put("/api/settings", json=settings)).status_code == 200
            password = await admin.post(
                "/api/dashboard-auth/guest/password", json={"password": "fixture-guest-password"}
            )
            assert password.status_code == 200
            setup = await admin.post("/api/dashboard-auth/password/setup", json={"password": "fixture-admin-password"})
            assert setup.status_code == 200, setup.text
            login = await other_admin.post(
                "/api/dashboard-auth/password/login",
                json={"username": "admin", "password": "fixture-admin-password"},
            )
            assert login.status_code == 200, login.text
            assert login.json()["user"]["id"] == setup.json()["user"]["id"]
            assert login.json()["user"]["role"]["slug"] == "admin"
            assert login.json()["authMethod"] == "password"
            assert "write" in login.json()["permissions"]
            for client in (guest, other_guest):
                login = await client.post(
                    "/api/dashboard-auth/guest/login", json={"password": "fixture-guest-password"}
                )
                assert login.status_code == 200, login.text
                assert login.json()["role"] == "guest"
                assert login.json()["user"] is None
                assert "read" in login.json()["permissions"]
                assert "write" not in login.json()["permissions"]

            payload = {
                "name": "Guest compatibility CPA",
                "baseUrl": await start(catalog),
                "apiKey": "fixture-cpa-key",
                "catalogMode": "cli_proxy_api",
                "supportsResponses": True,
            }
            created = await other_admin.post("/api/model-sources/", json=payload)
            assert created.status_code == 200, created.text
            source_id = created.json()["id"]
            baseline = (await admin.get("/api/model-sources/")).json()
            for rejected_headers in ({"Sec-Fetch-Site": "cross-site"}, {"Origin": "http://evil.example"}):
                # Clear defaults so foreign Origin is checked without Sec-Fetch-Site.
                original_headers = dict(other_admin.headers)
                other_admin.headers.clear()
                try:
                    for method, path, data in (
                        ("POST", "/api/model-sources/", payload),
                        ("PATCH", f"/api/model-sources/{source_id}", {"isEnabled": False}),
                        ("DELETE", f"/api/model-sources/{source_id}", None),
                        ("POST", "/api/dashboard-auth/guest/logout-all", None),
                        ("POST", "/api/dashboard-auth/logout-all", None),
                    ):
                        rejected = await other_admin.request(method, path, json=data, headers=rejected_headers)
                        assert rejected.status_code == 403, rejected.text
                        assert rejected.json()["error"]["code"] == "cross_site_request_rejected"
                finally:
                    other_admin.headers.update(original_headers)
                assert (await admin.get("/api/model-sources/")).json() == baseline
                for client in (admin, other_admin, guest, other_guest):
                    assert (await client.get("/api/model-sources/")).status_code == 200

            listed = await guest.get("/api/model-sources/", headers={"Sec-Fetch-Site": "cross-site"})
            assert listed.status_code == 200
            assert listed.json()["sources"][0]["id"] == source_id
            assert "fixture-cpa-key" not in listed.text
            denied = [
                await guest.post("/api/model-sources/", json=payload),
                await guest.patch(f"/api/model-sources/{source_id}", json={"isEnabled": False}),
                await guest.delete(f"/api/model-sources/{source_id}"),
            ]
            for response in denied:
                assert response.status_code == 403
                assert response.json()["error"]["code"] == "read_only_access"
            assert (await admin.get("/api/model-sources/")).json() == baseline
            updated = await other_admin.patch(f"/api/model-sources/{source_id}", json={"name": "Updated CPA"})
            assert updated.status_code == 200
            assert updated.json()["name"] == "Updated CPA"

            # Dashboard cookies do not bypass the separate proxy authentication gate.
            assert (await guest.get("/v1/models")).status_code == 401
            assert (await other_admin.get("/v1/models")).status_code == 401
            discovered = await admin.get("/v1/models", headers={"Sec-Fetch-Site": "cross-site"})
            assert discovered.status_code == 200
            ids = {model["id"] for model in discovered.json()["data"]}
            assert "guest-compatible-cpa" in ids
            assert {model["id"] for model in native} <= ids

            revoked = await other_admin.post("/api/dashboard-auth/guest/logout-all")
            assert revoked.status_code == 200
            for client in (guest, other_guest):
                stale_guest = await client.get("/api/model-sources/")
                assert stale_guest.status_code == 401
                assert stale_guest.json()["error"]["code"] == "authentication_required"
            for client in (admin, other_admin):
                assert (await client.get("/api/model-sources/")).status_code == 200
            login = await guest.post("/api/dashboard-auth/guest/login", json={"password": "fixture-guest-password"})
            assert login.status_code == 200

            # User logout-all revokes this account's sessions, leaving guests alive.
            revoked = await other_admin.post("/api/dashboard-auth/logout-all")
            assert revoked.status_code == 200
            for client in (admin, other_admin):
                stale_admin = await client.get("/api/model-sources/")
                assert stale_admin.status_code == 401
                assert stale_admin.json()["error"]["code"] == "authentication_required"
            assert (await guest.get("/api/model-sources/")).status_code == 200
            public_catalog = await admin.get("/v1/models")
            assert public_catalog.status_code == 200
            assert {model["id"] for model in public_catalog.json()["data"]} == ids
            login = await other_admin.post(
                "/api/dashboard-auth/password/login", json={"password": "fixture-admin-password"}
            )
            assert login.status_code == 200
            assert (await other_admin.delete(f"/api/model-sources/{source_id}")).status_code == 204
            assert (await guest.get("/api/model-sources/")).json()["sources"] == []

            assert await drain_audit_log_tasks(5.0)
            audit = await other_admin.get("/api/audit-logs", params={"target_type": "model_source"})
            assert audit.status_code == 200, audit.text
            entries = audit.json()
            # Only the three accepted mutations produce source audit events.
            assert sorted(entry["action"] for entry in entries) == [
                "model_source_created",
                "model_source_deleted",
                "model_source_updated",
            ]
            for entry in entries:
                assert entry["actor"] == {
                    "userId": setup.json()["user"]["id"],
                    "username": "admin",
                    "roleSlug": "admin",
                    "authMethod": "password",
                }
                assert entry["target"] == {"type": "model_source", "id": source_id}
                assert entry["actorIp"] == "203.0.113.31"
            assert "fixture-cpa-key" not in audit.text
            guest_audit = await guest.get("/api/audit-logs", params={"target_type": "model_source"})
            assert guest_audit.status_code == 403
            assert guest_audit.json()["error"]["code"] == "permission_required"
