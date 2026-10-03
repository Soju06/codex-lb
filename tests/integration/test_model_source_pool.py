"""Responses pooling through real routes and local HTTP upstreams."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
from aiohttp import web
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select

from app.core.utils.time import utcnow
from app.db.models import Account, ApiKeyUsageReservation, ModelSourceOwnership, ModelSourceOwnershipHistory, RequestLog
from app.db.session import SessionLocal
from app.modules.proxy import api as proxy_api
from app.modules.proxy import source_pool as pool_module
from app.modules.proxy.source_admission import get_source_bulkhead
from app.modules.proxy.source_ownership import OwnershipScope
from tests.integration.model_source_helpers import (
    _AsgiStream,
    _create_model_source,
    _enable_api_key_auth,
    stub_source_upstreams,
)
from tests.simulation.virtual_time import VirtualClock

pytestmark = pytest.mark.integration
MODEL = "cd/pool-model"
ROUTES = ["/v1/responses", "/v1/responses/", "/backend-api/codex/responses", "/backend-api/codex/responses/"]


@dataclass
class PoolFixture:
    ids: list[str] = field(default_factory=list)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    statuses: dict[str, int] = field(default_factory=dict)
    key_id: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    retry_after: str = "37"
    counter: int = 0
    inspect_reservations: bool = False
    reservation_snapshots: list[list[str]] = field(default_factory=list)

    async def upstream(self, request: web.Request) -> web.StreamResponse:
        token = request.headers["Authorization"].removeprefix("Bearer token-")
        body = await request.json()
        self.calls.append((token, body))
        if self.inspect_reservations:
            self.reservation_snapshots.append(sorted(row.status for row in await reservations(self)))
        status = self.statuses.get(token, 200)
        if status != 200:
            return web.json_response(
                {"error": {"message": "upstream rejected", "type": "server_error", "code": "test_rejected"}},
                status=status,
                headers={"Retry-After": self.retry_after},
            )
        self.counter += 1
        response = {
            "id": f"resp_pool_{self.counter}",
            "object": "response",
            "status": "completed",
            "model": body["model"],
            "output": [],
            "usage": {"input_tokens": 3, "output_tokens": 1, "total_tokens": 4},
        }
        if body.get("stream"):
            return web.Response(
                text="data: " + json.dumps({"type": "response.completed", "response": response}) + "\n\n",
                content_type="text/event-stream",
            )
        return web.json_response(response)


@pytest.fixture
def pool_clock(monkeypatch: pytest.MonkeyPatch) -> VirtualClock:
    clock = VirtualClock()
    monkeypatch.setattr(pool_module, "_SOURCE_POOL", pool_module.SourcePool(clock=clock))
    monkeypatch.setattr(pool_module.random, "choice", lambda values: values[0])
    return clock


@pytest.fixture
async def make_pool(
    async_client: Any, pool_clock: VirtualClock
) -> AsyncIterator[Callable[..., Awaitable[PoolFixture]]]:
    await _enable_api_key_auth(async_client)
    async with stub_source_upstreams() as start:

        async def make(
            count: int = 3, handler: Callable[..., Awaitable[web.StreamResponse]] | None = None
        ) -> PoolFixture:
            pool = PoolFixture()
            url = await start(handler or pool.upstream, handler_cancellation=True, shutdown_timeout=0.1)
            for index in range(count):
                source_id = await _create_model_source(
                    async_client,
                    name=f"pool-{index}",
                    model=MODEL,
                    base_url=url,
                    supports_responses=True,
                    raw_metadata_json=json.dumps({"upstream_model": f"upstream-{index}"}),
                )
                pool.ids.append(source_id)
            key = await async_client.post(
                "/api/api-keys/",
                json={
                    "name": "pool-client",
                    "assignedSourceIds": pool.ids,
                    "allowedModels": [MODEL],
                    "limits": [{"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 100_000}],
                },
            )
            assert key.status_code == 200, key.text
            pool.key_id = key.json()["id"]
            pool.headers = {"Authorization": "Bearer " + key.json()["key"]}
            return pool

        try:
            yield make
        finally:
            await async_client._transport.app.state.proxy_service.drain_persistence_tasks(timeout_seconds=5)


def body(*, stream: bool = False, **extra: Any) -> dict[str, Any]:
    return {"model": MODEL, "instructions": "test", "input": "hello", "stream": stream, **extra}


async def reservations(pool: PoolFixture) -> list[ApiKeyUsageReservation]:
    async with SessionLocal() as session:
        return list(
            (
                await session.scalars(
                    select(ApiKeyUsageReservation).where(ApiKeyUsageReservation.api_key_id == pool.key_id)
                )
            ).all()
        )


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("stream", [False, True])
async def test_five_keys_rotate_and_apply_each_alias(
    async_client: Any, make_pool: Any, route: str, stream: bool
) -> None:
    pool = await make_pool(5)
    for _ in range(10):
        response = await async_client.post(route, headers=pool.headers, json=body(stream=stream))
        assert response.status_code == 200, response.text
        assert MODEL in response.text and "upstream-" not in response.text
    assert Counter(token for token, _ in pool.calls) == {f"pool-{index}": 2 for index in range(5)}
    for token, request in pool.calls:
        assert request["model"] == "upstream-" + token.removeprefix("pool-")
    assert all(get_source_bulkhead().in_flight(source_id) == 0 for source_id in pool.ids)
    assert all(reservation.status == "finalized" for reservation in await reservations(pool))


@pytest.mark.parametrize("status", [401, 403, 429, 500, 502, 503, 504])
@pytest.mark.parametrize("stream", [False, True])
async def test_failover_settles_before_next_key_and_cools_failed_source(
    async_client: Any, make_pool: Any, pool_clock: VirtualClock, status: int, stream: bool
) -> None:
    pool = await make_pool(2)
    pool.inspect_reservations = True
    pool.statuses["pool-0"] = status
    response = await async_client.post("/v1/responses", headers=pool.headers, json=body(stream=stream))
    assert response.status_code == 200, response.text
    assert [token for token, _ in pool.calls] == ["pool-0", "pool-1"]
    assert pool.reservation_snapshots == [["reserved"], ["released", "reserved"]]
    assert Counter(item.status for item in await reservations(pool)) == {"released": 1, "finalized": 1}
    async with SessionLocal() as session:
        rows = list(
            (
                await session.scalars(
                    select(RequestLog).where(RequestLog.api_key_id == pool.key_id).order_by(RequestLog.id)
                )
            ).all()
        )
    assert [(row.model_source_id, row.status) for row in rows] == [(pool.ids[0], "error"), (pool.ids[1], "success")]
    assert rows[0].upstream_status_code == status
    await async_client.post("/v1/responses", headers=pool.headers, json=body())
    assert pool.calls[-1][0] == "pool-1"
    pool_clock.advance(38)
    pool.statuses.clear()
    await async_client.post("/v1/responses", headers=pool.headers, json=body())
    assert pool.calls[-1][0] == "pool-0"


async def test_scope_disabled_streaming_and_saturation_are_respected(async_client: Any, make_pool: Any) -> None:
    pool = await make_pool(5)
    await async_client.patch(f"/api/api-keys/{pool.key_id}", json={"assignedSourceIds": pool.ids[:4]})
    await async_client.patch(f"/api/model-sources/{pool.ids[0]}", json={"isEnabled": False})
    await async_client.patch(
        f"/api/model-sources/{pool.ids[1]}", json={"models": [{"model": MODEL, "supportsStreaming": False}]}
    )
    await async_client.patch(f"/api/model-sources/{pool.ids[2]}", json={"maxConcurrency": 1})
    slot = get_source_bulkhead().try_acquire(pool.ids[2], 1)
    assert slot is not None
    try:
        response = await async_client.post("/backend-api/codex/responses", headers=pool.headers, json=body(stream=True))
    finally:
        get_source_bulkhead().release(slot)
    assert response.status_code == 200, response.text
    assert [token for token, _ in pool.calls] == ["pool-3"]


async def test_pool_unavailable_and_bounded_exhaustion_preserve_errors(async_client: Any, make_pool: Any) -> None:
    pool = await make_pool(6)
    pool.statuses = {f"pool-{index}": 429 for index in range(6)}
    response = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    assert response.status_code == 429 and response.headers["Retry-After"] == "37"
    assert len(pool.calls) == 5
    assert all(row.status == "released" for row in await reservations(pool))
    response = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    assert response.status_code == 429 and len(pool.calls) == 6
    response = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    assert response.status_code == 503 and response.headers["Retry-After"] == "37"
    assert len(pool.calls) == 6 and len(await reservations(pool)) == 6


@pytest.mark.parametrize("stream", [False, True])
async def test_known_anchor_survives_replica_change_and_cannot_switch(
    async_client: Any, make_pool: Any, monkeypatch: pytest.MonkeyPatch, stream: bool
) -> None:
    pool = await make_pool(3)
    first = await async_client.post("/v1/responses", headers=pool.headers, json=body(stream=stream))
    assert first.status_code == 200 and "resp_pool_1" in first.text
    anchor = "resp_pool_1"
    # A new replica has no local rotation memory; continuity comes from shared DB.
    monkeypatch.setattr(pool_module, "_SOURCE_POOL", pool_module.SourcePool())
    monkeypatch.setattr(pool_module.random, "choice", lambda values: values[-1])
    resumed = await async_client.post("/v1/responses", headers=pool.headers, json=body(previous_response_id=anchor))
    assert resumed.status_code == 200 and pool.calls[-1][0] == "pool-0"
    pool.statuses["pool-0"] = 503
    resumed = await async_client.post("/v1/responses", headers=pool.headers, json=body(previous_response_id=anchor))
    assert resumed.status_code == 503
    assert [token for token, _ in pool.calls] == ["pool-0", "pool-0", "pool-0"]
    await async_client.patch(f"/api/model-sources/{pool.ids[0]}", json={"isEnabled": False})
    resumed = await async_client.post("/v1/responses", headers=pool.headers, json=body(previous_response_id=anchor))
    assert resumed.status_code == 409
    assert len(pool.calls) == 3


async def test_missing_and_other_key_anchor_fail_closed(async_client: Any, make_pool: Any) -> None:
    pool = await make_pool(2)
    first = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    created = await async_client.post("/api/api-keys/", json={"name": "other-client", "assignedSourceIds": pool.ids})
    other_headers = {"Authorization": "Bearer " + created.json()["key"]}
    for headers, anchor in ((pool.headers, "unknown"), (other_headers, first.json()["id"])):
        response = await async_client.post("/v1/responses", headers=headers, json=body(previous_response_id=anchor))
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "previous_response_owner_unavailable"
    assert len(pool.calls) == 1


async def test_connection_failure_retries_but_request_errors_do_not(async_client: Any, make_pool: Any) -> None:
    pool = await make_pool(2)
    await async_client.patch(f"/api/model-sources/{pool.ids[0]}", json={"baseUrl": "http://127.0.0.1:1/v1"})
    response = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    assert response.status_code == 200 and pool.calls[-1][0] == "pool-1"
    assert Counter(row.status for row in await reservations(pool)) == {"released": 1, "finalized": 1}
    pool.statuses["pool-1"] = 400
    response = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    assert response.status_code == 400
    assert len(pool.calls) == 2


async def test_failure_to_release_reservation_inhibits_failover(
    async_client: Any, make_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = await make_pool(2)
    pool.statuses["pool-0"] = 429
    original = proxy_api._release_reservation

    async def fail_release(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("simulated reservation storage failure")

    monkeypatch.setattr(proxy_api, "_release_reservation", fail_release)
    response = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    assert response.status_code == 429 and len(pool.calls) == 1
    assert get_source_bulkhead().in_flight(pool.ids[0]) == 0
    # Let the service-owned cleanup complete after restoring the storage seam.
    monkeypatch.setattr(proxy_api, "_release_reservation", original)


async def test_stream_delivery_failure_never_replays(async_client: Any, make_pool: Any) -> None:
    calls: list[str] = []

    async def stream_error(request: web.Request) -> web.StreamResponse:
        calls.append(request.headers["Authorization"])
        await request.read()
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(
            b'data: {"type":"response.created","response":{"id":"resp_started","status":"in_progress"}}\n\n'
        )
        await response.write(
            b'data: {"type":"response.failed","response":{"id":"resp_started","status":"failed",'
            b'"error":{"code":"server_error","message":"failed"}}}\n\n'
        )
        await response.write_eof()
        return response

    pool = await make_pool(2, stream_error)
    response = await async_client.post("/v1/responses", headers=pool.headers, json=body(stream=True))
    assert response.status_code == 200 and "response.failed" in response.text
    assert len(calls) == 1
    assert all(get_source_bulkhead().in_flight(source_id) == 0 for source_id in pool.ids)


async def test_cancel_during_open_releases_without_failover(async_client: Any, make_pool: Any) -> None:
    entered = asyncio.Event()
    calls: list[str] = []

    async def stall(request: web.Request) -> web.StreamResponse:
        calls.append(request.headers["Authorization"])
        await request.read()
        entered.set()
        await asyncio.Event().wait()
        return web.Response()

    pool = await make_pool(2, stall)
    driver = _AsgiStream(
        app=async_client._transport.app,
        path="/v1/responses",
        headers=pool.headers,
        body=json.dumps(body(stream=True)).encode(),
    )
    task = asyncio.create_task(driver.run())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        driver.disconnect()
        await asyncio.wait_for(task, 5)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert len(calls) == 1
    assert all(get_source_bulkhead().in_flight(source_id) == 0 for source_id in pool.ids)
    assert all(row.status == "released" for row in await reservations(pool))


async def test_least_inflight_and_all_saturated_reserve_no_extra_usage(async_client: Any, make_pool: Any) -> None:
    pool = await make_pool(2)
    for source_id in pool.ids:
        changed = await async_client.patch(f"/api/model-sources/{source_id}", json={"maxConcurrency": 1})
        assert changed.status_code == 200
    first = get_source_bulkhead().try_acquire(pool.ids[0], 1)
    assert first is not None
    second = None
    try:
        response = await async_client.post("/v1/responses", headers=pool.headers, json=body())
        assert response.status_code == 200 and pool.calls[-1][0] == "pool-1"
        second = get_source_bulkhead().try_acquire(pool.ids[1], 1)
        assert second is not None
        response = await async_client.post("/v1/responses", headers=pool.headers, json=body())
        assert response.status_code == 503 and response.headers["Retry-After"] == "1"
        assert len(await reservations(pool)) == 1
    finally:
        get_source_bulkhead().release(first)
        if second is not None:
            get_source_bulkhead().release(second)


async def test_replacing_bad_key_clears_cooldown(async_client: Any, make_pool: Any) -> None:
    pool = await make_pool(2)
    pool.statuses["pool-0"] = 401
    response = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    assert response.status_code == 200
    updated = await async_client.patch(f"/api/model-sources/{pool.ids[0]}", json={"apiKey": "token-replacement"})
    assert updated.status_code == 200
    response = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    assert response.status_code == 200 and pool.calls[-1][0] == "replacement"


async def test_malformed_success_is_not_replayed(async_client: Any, make_pool: Any) -> None:
    calls: list[str] = []

    async def invalid_response(request: web.Request) -> web.Response:
        calls.append(request.headers["Authorization"])
        await request.read()
        return web.Response(text="not a valid response")

    pool = await make_pool(2, invalid_response)
    response = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    assert response.status_code == 502 and len(calls) == 1
    assert all(row.status == "released" for row in await reservations(pool))


async def test_unknown_completion_timeout_does_not_replay(async_client: Any, make_pool: Any) -> None:
    calls: list[str] = []

    async def slow_response(request: web.Request) -> web.Response:
        calls.append(request.headers["Authorization"])
        await request.read()
        await asyncio.Event().wait()
        return web.Response()

    pool = await make_pool(2, slow_response)
    for source_id in pool.ids:
        updated = await async_client.patch(f"/api/model-sources/{source_id}", json={"timeoutSeconds": 1})
        assert updated.status_code == 200
    response = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    assert response.status_code == 502 and len(calls) == 1
    assert all(row.status == "released" for row in await reservations(pool))


@pytest.mark.parametrize("owner_change", ["delete", "unassign", "ambiguous"])
async def test_known_owner_never_switches_when_its_source_becomes_inaccessible(
    async_client: Any, make_pool: Any, owner_change: str
) -> None:
    pool = await make_pool(2)
    first = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    anchor = first.json()["id"]
    if owner_change == "delete":
        changed = await async_client.delete(f"/api/model-sources/{pool.ids[0]}")
        assert changed.status_code == 204
    elif owner_change == "unassign":
        changed = await async_client.patch(f"/api/api-keys/{pool.key_id}", json={"assignedSourceIds": [pool.ids[1]]})
        assert changed.status_code == 200
    else:
        async with SessionLocal() as session:
            # Historical responses have only request logs; conflicting legacy
            # rows must still decline instead of selecting an arbitrary owner.
            await session.execute(
                delete(ModelSourceOwnership).where(
                    ModelSourceOwnership.reference_key == OwnershipScope(pool.key_id, MODEL).key("response", anchor)
                )
            )
            await session.execute(
                delete(ModelSourceOwnershipHistory).where(
                    ModelSourceOwnershipHistory.reference_key
                    == OwnershipScope(pool.key_id, MODEL).key("response", anchor)
                )
            )
            session.add(
                RequestLog(
                    request_id=anchor,
                    model_source_id=pool.ids[1],
                    api_key_id=pool.key_id,
                    model=MODEL,
                    status="success",
                )
            )
            await session.commit()
    resumed = await async_client.post("/v1/responses", headers=pool.headers, json=body(previous_response_id=anchor))
    assert resumed.status_code == 409 and len(pool.calls) == 1


async def test_ownership_lookup_failure_does_not_dispatch(
    async_client: Any, make_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = await make_pool(2)

    async def lookup_failed(*args: Any, **kwargs: Any) -> list[str]:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(proxy_api.RequestLogsRepository, "find_source_owner_revisions_for_response_id", lookup_failed)
    response = await async_client.post(
        "/v1/responses", headers=pool.headers, json=body(previous_response_id="resp_test")
    )
    assert response.status_code == 502 and not pool.calls
    assert await reservations(pool) == []


@pytest.mark.parametrize("route", ["/v1/responses", "/backend-api/codex/responses"])
@pytest.mark.parametrize("pin", ["file", "subscription_response"])
async def test_pool_cannot_override_file_or_subscription_ownership(
    async_client: Any, make_pool: Any, monkeypatch: pytest.MonkeyPatch, route: str, pin: str
) -> None:
    pool = await make_pool(2)
    payload = body()
    if pin == "file":
        payload["input"] = [{"role": "user", "content": [{"type": "input_file", "file_id": "file-owned"}]}]
    else:
        async with SessionLocal() as session:
            session.add(
                Account(
                    id="account-owner",
                    email="owner@example.test",
                    plan_type="plus",
                    access_token_encrypted=b"unused",
                    refresh_token_encrypted=b"unused",
                    id_token_encrypted=b"unused",
                    last_refresh=utcnow(),
                )
            )
            await session.flush()
            session.add(
                RequestLog(
                    request_id="resp_subscription",
                    account_id="account-owner",
                    api_key_id=pool.key_id,
                    model=MODEL,
                    status="success",
                )
            )
            await session.commit()
        payload["previous_response_id"] = "resp_subscription"
    account_dispatches: list[dict[str, Any]] = []

    async def account_path(_request: Any, parsed: Any, *_args: Any, **_kwargs: Any) -> JSONResponse:
        account_dispatches.append(parsed.model_dump())
        return JSONResponse({"id": "resp_account", "object": "response", "status": "completed", "output": []})

    monkeypatch.setattr(proxy_api, "_collect_responses", account_path)
    response = await async_client.post(route, headers=pool.headers, json=payload)
    assert response.status_code == 200, response.text
    assert len(account_dispatches) == 1 and pool.calls == []
    assert await reservations(pool) == []
