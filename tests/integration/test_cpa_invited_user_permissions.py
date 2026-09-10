"""Invited users retain CPA permissions and lose stale role access immediately."""

from __future__ import annotations

import pytest
from aiohttp import web
from httpx import ASGITransport, AsyncClient

from app.core.auth.dashboard_access import PRESET_ROLE_IDS, PresetRoleSlug
from tests.integration.model_source_helpers import stub_source_upstreams

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("headers", [{}, {"Sec-Fetch-Site": "same-origin"}, {"Origin": "http://localhost"}])
async def test_invited_operator_role_change_revokes_cpa_write_access(app_instance, headers):
    async def catalog(request):
        return web.json_response({"models": [{"slug": "invited-cpa", "context_window": 8192}]})

    async with app_instance.router.lifespan_context(app_instance), stub_source_upstreams() as start:
        async with (
            AsyncClient(
                transport=ASGITransport(app=app_instance, client=("127.0.0.1", 50000)),
                base_url="http://localhost",
                headers=headers,
            ) as admin,
            AsyncClient(
                transport=ASGITransport(app=app_instance, client=("203.0.113.51", 50001)),
                base_url="http://localhost",
                headers=headers,
            ) as invited,
        ):
            setup = await admin.post("/api/dashboard-auth/password/setup", json={"password": "fixture-admin-password"})
            assert setup.status_code == 200
            created_user = await admin.post(
                "/api/dashboard-users",
                json={"username": "cpa-operator", "roleId": PRESET_ROLE_IDS[PresetRoleSlug.OPERATOR]},
            )
            assert created_user.status_code == 201, created_user.text
            user_id = created_user.json()["user"]["id"]
            credentials = {"token": created_user.json()["invite"]["token"], "password": "fixture-invited-password"}
            rejected = await invited.post(
                "/api/dashboard-auth/invite/accept", json=credentials, headers={"Sec-Fetch-Site": "cross-site"}
            )
            assert rejected.status_code == 403
            assert rejected.json()["error"]["code"] == "cross_site_request_rejected"
            accepted = await invited.post("/api/dashboard-auth/invite/accept", json=credentials)
            assert accepted.status_code == 200, accepted.text
            assert accepted.json()["user"]["id"] == user_id
            assert accepted.json()["user"]["role"]["slug"] == "operator"
            source = await invited.post(
                "/api/model-sources/",
                json={
                    "name": "Invited CPA",
                    "baseUrl": await start(catalog),
                    "catalogMode": "cli_proxy_api",
                    "supportsResponses": True,
                },
            )
            assert source.status_code == 200, source.text
            source_id = source.json()["id"]
            discovered = await admin.get("/v1/models")
            assert discovered.status_code == 200
            assert "invited-cpa" in {model["id"] for model in discovered.json()["data"]}
            assert (await invited.get("/v1/models")).status_code == 401
            baseline = (await admin.get("/api/model-sources/")).json()

            changed = await admin.patch(
                f"/api/dashboard-users/{user_id}", json={"roleId": PRESET_ROLE_IDS[PresetRoleSlug.VIEWER]}
            )
            assert changed.status_code == 200, changed.text
            assert (await invited.get("/api/model-sources/")).status_code == 401
            login = await invited.post(
                "/api/dashboard-auth/password/login",
                json={"username": "cpa-operator", "password": "fixture-invited-password"},
            )
            assert login.status_code == 200
            assert login.json()["user"]["role"]["slug"] == "viewer"
            assert (await invited.get("/api/model-sources/")).json() == baseline
            for response in (
                await invited.patch(f"/api/model-sources/{source_id}", json={"isEnabled": False}),
                await invited.delete(f"/api/model-sources/{source_id}"),
            ):
                assert response.status_code == 403
                assert response.json()["error"]["code"] == "read_only_access"
            assert (await admin.get("/api/model-sources/")).json() == baseline
            disabled = await admin.patch(f"/api/dashboard-users/{user_id}", json={"status": "disabled"})
            assert disabled.status_code == 200
            assert (await invited.get("/api/model-sources/")).status_code == 401
            assert (await admin.get("/api/model-sources/")).json() == baseline
            assert (await admin.delete(f"/api/model-sources/{source_id}")).status_code == 204
