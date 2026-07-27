from __future__ import annotations

import asyncio
import base64
import json

import pytest
from sqlalchemy import select

from app.core.auth import generate_unique_account_id
from app.core.cache.invalidation import (
    NAMESPACE_SETTINGS,
    NAMESPACE_UPSTREAM_ROUTE,
    get_cache_invalidation_poller,
)
from app.core.config.settings import get_settings
from app.core.upstream_proxy.cache import get_upstream_route_cache
from app.db.models import CacheInvalidation
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration


@pytest.fixture
def route_cache_ttl(monkeypatch):
    monkeypatch.setenv("CODEX_LB_UPSTREAM_ROUTE_CACHE_TTL_SECONDS", "60")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


async def _import_account(async_client, account_id: str, email: str) -> str:
    auth_json = {
        "tokens": {
            "idToken": _encode_jwt(
                {
                    "email": email,
                    "chatgpt_account_id": account_id,
                    "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
                }
            ),
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "accountId": account_id,
        },
    }
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200
    return generate_unique_account_id(account_id, email)


async def _create_pool_with_endpoint(async_client) -> str:
    endpoint = await async_client.post(
        "/api/settings/upstream-proxy/endpoints",
        json={"name": "Proxy A", "scheme": "http", "host": "proxy.internal", "port": 8080},
    )
    assert endpoint.status_code == 200
    pool = await async_client.post(
        "/api/settings/upstream-proxy/pools",
        json={"name": "Pool A", "endpointIds": [endpoint.json()["id"]]},
    )
    assert pool.status_code == 200
    return pool.json()["id"]


async def _upstream_route_namespace_version() -> int:
    async with SessionLocal() as session:
        version = await session.scalar(
            select(CacheInvalidation.version).where(CacheInvalidation.namespace == NAMESPACE_UPSTREAM_ROUTE)
        )
    return version or 0


def _seed_dummy_entry() -> None:
    cache = get_upstream_route_cache()
    cache.store_route("seeded-account", None, generation=cache.generation)
    assert cache.get("seeded-account") is not None


async def test_binding_upsert_clears_cache_and_bumps_namespace(async_client, route_cache_ttl) -> None:
    account_id = await _import_account(async_client, "acc-route-cache-binding", "route-cache-binding@example.com")
    pool_id = await _create_pool_with_endpoint(async_client)
    version_before = await _upstream_route_namespace_version()
    _seed_dummy_entry()

    response = await async_client.put(
        f"/api/settings/upstream-proxy/accounts/{account_id}/binding",
        json={"poolId": pool_id, "isActive": True},
    )

    assert response.status_code == 200
    assert get_upstream_route_cache().get("seeded-account") is None
    assert await _upstream_route_namespace_version() > version_before


async def test_pool_member_add_clears_cache_and_bumps_namespace(async_client, route_cache_ttl) -> None:
    endpoint = await async_client.post(
        "/api/settings/upstream-proxy/endpoints",
        json={"name": "Proxy B", "scheme": "http", "host": "proxy-b.internal", "port": 8081},
    )
    assert endpoint.status_code == 200
    pool = await async_client.post(
        "/api/settings/upstream-proxy/pools",
        json={"name": "Pool B", "endpointIds": []},
    )
    assert pool.status_code == 200
    # Endpoint/pool creation cannot be referenced by any cached outcome yet, so
    # neither bumps the namespace.
    assert await _upstream_route_namespace_version() == 0
    version_before = await _upstream_route_namespace_version()
    _seed_dummy_entry()

    response = await async_client.post(
        f"/api/settings/upstream-proxy/pools/{pool.json()['id']}/members",
        json={"endpointId": endpoint.json()["id"]},
    )

    assert response.status_code == 200
    assert get_upstream_route_cache().get("seeded-account") is None
    assert await _upstream_route_namespace_version() > version_before


async def test_account_delete_clears_cache_and_bumps_namespace(async_client, route_cache_ttl) -> None:
    # Deletion cascades the binding row away, and account ids are
    # deterministic, so delete-then-re-import must not replay the deleted
    # account's cached outcome.
    account_id = await _import_account(async_client, "acc-route-cache-delete", "route-cache-delete@example.com")
    version_before = await _upstream_route_namespace_version()
    _seed_dummy_entry()

    response = await async_client.delete(f"/api/accounts/{account_id}")

    assert response.status_code == 200
    assert get_upstream_route_cache().get("seeded-account") is None
    assert await _upstream_route_namespace_version() > version_before


@pytest.mark.parametrize("namespace", [NAMESPACE_UPSTREAM_ROUTE, NAMESPACE_SETTINGS])
async def test_namespace_bump_clears_route_cache_via_lifespan_poller(async_client, route_cache_ttl, namespace) -> None:
    # Peer replicas converge exclusively through the poller callbacks
    # registered in app/main.py; a durable bump observed by the running
    # lifespan poller must clear the route cache within one poll interval.
    _seed_dummy_entry()
    poller = get_cache_invalidation_poller()
    assert poller is not None
    assert await poller.bump(namespace)

    deadline = asyncio.get_event_loop().time() + 5.0
    while asyncio.get_event_loop().time() < deadline:
        if get_upstream_route_cache().get("seeded-account") is None:
            break
        await asyncio.sleep(0.05)
    assert get_upstream_route_cache().get("seeded-account") is None


async def test_settings_upstream_field_change_clears_local_cache(async_client, route_cache_ttl) -> None:
    settings_response = await async_client.get("/api/settings")
    assert settings_response.status_code == 200
    body = settings_response.json()
    body["upstreamProxyRoutingEnabled"] = True
    version_before = await _upstream_route_namespace_version()
    _seed_dummy_entry()

    response = await async_client.put("/api/settings", json=body)

    assert response.status_code == 200
    assert get_upstream_route_cache().get("seeded-account") is None
    # The settings-namespace bump alone enqueues no retry on write failure, so
    # the upstream field change must also durably bump ``upstream_route``.
    assert await _upstream_route_namespace_version() > version_before


async def test_settings_change_clears_route_cache_before_first_post_commit_await(
    async_client, route_cache_ttl, monkeypatch
) -> None:
    # The settings row is committed before the durable bumps are awaited; a
    # concurrent request served during those awaits must not resolve from the
    # stale route cache, so the clear must precede the settings-cache
    # invalidation await.
    from app.core.config.settings_cache import get_settings_cache

    settings_response = await async_client.get("/api/settings")
    body = settings_response.json()
    body["upstreamProxyRoutingEnabled"] = True
    _seed_dummy_entry()

    settings_cache = get_settings_cache()
    real_invalidate = settings_cache.invalidate
    observed: dict[str, bool] = {}

    async def spying_invalidate(*, propagate: bool = True) -> None:
        observed.setdefault(
            "route_cache_cleared_first",
            get_upstream_route_cache().get("seeded-account") is None,
        )
        await real_invalidate(propagate=propagate)

    monkeypatch.setattr(settings_cache, "invalidate", spying_invalidate)

    response = await async_client.put("/api/settings", json=body)

    assert response.status_code == 200
    assert observed["route_cache_cleared_first"] is True


async def test_repository_update_clears_route_cache_before_refresh_await(
    db_setup, route_cache_ttl, monkeypatch
) -> None:
    # The committed settings row is visible to concurrent requests as soon as
    # the commit returns; the clear must therefore run before the refresh
    # await inside SettingsRepository.commit_refresh, not after the repository
    # call returns.
    from app.modules.settings.repository import SettingsRepository

    async with SessionLocal() as session:
        repo = SettingsRepository(session)
        await repo.get_or_create()
        _seed_dummy_entry()
        real_refresh = session.refresh
        observed: dict[str, bool] = {}

        async def spying_refresh(instance, *args, **kwargs):
            observed.setdefault(
                "cleared_before_refresh",
                get_upstream_route_cache().get("seeded-account") is None,
            )
            return await real_refresh(instance, *args, **kwargs)

        monkeypatch.setattr(session, "refresh", spying_refresh)
        await repo.update(upstream_proxy_routing_enabled=True)

    assert observed["cleared_before_refresh"] is True
