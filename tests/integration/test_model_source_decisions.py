"""Source routing for the OpenAI Decisions API and the vLLM structured-decision extension.

``POST /v1/decisions`` and ``POST /v1/systemone`` are served only by model
sources that opt in through their own capability flag, with no
subscription-backed fallback -- the same contract as ``/v1/embeddings``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import pytest
from aiohttp import web
from sqlalchemy import select

from app.db.models import ApiKeyUsageReservation, RequestLog
from app.db.session import SessionLocal
from app.modules.api_keys.repository import ApiKeysRepository
from tests.integration.model_source_helpers import (
    _create_model_source,
    _enable_api_key_auth,
    _free_port,
    stub_source_upstreams,
)

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class _DecisionEndpoint:
    path: str
    capability: Literal["decisions", "systemone"]
    build_body: Callable[[str], dict[str, Any]]
    # An optional field each body may carry explicitly as null.
    nullable_extra: str

    @property
    def upstream_path(self) -> str:
        return self.path


_DECISIONS = _DecisionEndpoint(
    path="/v1/decisions",
    capability="decisions",
    build_body=lambda model: {
        "model": model,
        "input": "The parcel arrived crushed and the seal was broken.",
        "questions": [
            {"type": "predicate", "name": "damaged", "instructions": "Is the item damaged?"},
        ],
    },
    nullable_extra="safety_identifier",
)

_SYSTEMONE = _DecisionEndpoint(
    path="/v1/systemone",
    capability="systemone",
    build_body=lambda model: {
        "model": model,
        "state": "The parcel arrived crushed and the seal was broken.",
        "questions": {
            "damaged": {"type": "predicate", "instructions": "Is the item damaged?"},
        },
    },
    nullable_extra="instructions",
)

_ENDPOINTS = [
    pytest.param(_DECISIONS, id="decisions"),
    pytest.param(_SYSTEMONE, id="systemone"),
]

_DECISION_USAGE = {
    "input_tokens": 40,
    "input_tokens_details": {"cached_tokens": 8, "cache_write_tokens": 0},
    "output_tokens": 2,
    "output_tokens_details": {"reasoning_tokens": 0},
    "total_tokens": 42,
}


def _decision_payload(model: str, *, usage: dict[str, Any] | None = _DECISION_USAGE) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "answers": [{"type": "predicate", "name": "damaged", "probability": 0.93}],
    }
    if usage is not None:
        payload["usage"] = usage
    return payload


@pytest.fixture
async def source_upstream():
    async with stub_source_upstreams() as start:
        yield start


async def _source_with_key(
    async_client,
    endpoint: _DecisionEndpoint,
    *,
    name: str,
    model: str,
    base_url: str,
    capability: bool = True,
    limited: bool = False,
) -> tuple[str, str, str]:
    source_id = await _create_model_source(
        async_client,
        name=name,
        model=model,
        base_url=base_url,
        input_per_1m=1.0,
        cached_input_per_1m=0.1,
        output_per_1m=4.0,
        supports_decisions=capability and endpoint.capability == "decisions",
        supports_systemone=capability and endpoint.capability == "systemone",
    )
    key_body: dict[str, Any] = {"name": f"{name}-key", "assignedSourceIds": [source_id]}
    if limited:
        key_body["limits"] = [{"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 1_000}]
    created = await async_client.post("/api/api-keys/", json=key_body)
    assert created.status_code == 200
    return source_id, created.json()["key"], created.json()["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", _ENDPOINTS)
async def test_decision_routes_payload_and_settles_usage(
    async_client, source_upstream, endpoint: _DecisionEndpoint
) -> None:
    await _enable_api_key_auth(async_client)
    captured: dict[str, Any] = {}
    model = "clef-flash"

    async def decide(request: web.Request) -> web.Response:
        captured["path"] = request.path
        captured["authorization"] = request.headers.get("authorization")
        captured["payload"] = await request.json()
        return web.json_response(_decision_payload(model))

    base_url = await source_upstream(decide)
    source_id, key, key_id = await _source_with_key(
        async_client, endpoint, name="decider", model=model, base_url=base_url, limited=True
    )
    body = {**endpoint.build_body(model), "priority": 1}

    response = await async_client.post(endpoint.path, headers={"Authorization": f"Bearer {key}"}, json=body)

    assert response.status_code == 200
    assert response.json() == _decision_payload(model)
    assert captured["path"] == endpoint.upstream_path
    assert captured["authorization"] == "Bearer token-decider"
    # Everything beyond the routing key passes through verbatim.
    assert captured["payload"] == body

    async with SessionLocal() as session:
        limits = await ApiKeysRepository(session).get_limits_by_key(key_id)
        assert len(limits) == 1
        assert limits[0].current_value == 42
        reservations = await session.execute(
            select(ApiKeyUsageReservation).where(ApiKeyUsageReservation.status == "reserved")
        )
        assert reservations.scalars().all() == []

        result = await session.execute(select(RequestLog).where(RequestLog.model == model))
        log = result.scalar_one()
        assert log.account_id is None
        assert log.model_source_id == source_id
        assert log.source == "model_source"
        assert log.status == "success"
        assert log.input_tokens == 40
        assert log.cached_input_tokens == 8
        assert log.output_tokens == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", _ENDPOINTS)
async def test_decision_preserves_explicit_nulls_and_omissions(
    async_client, source_upstream, endpoint: _DecisionEndpoint
) -> None:
    seen: list[dict[str, Any]] = []
    model = "presence-decider"

    async def decide(request: web.Request) -> web.Response:
        seen.append(await request.json())
        return web.json_response(_decision_payload(model))

    base_url = await source_upstream(decide)
    await _create_model_source(
        async_client,
        name="presence-decider",
        model=model,
        base_url=base_url,
        supports_decisions=endpoint.capability == "decisions",
        supports_systemone=endpoint.capability == "systemone",
    )

    with_null = {**endpoint.build_body(model), endpoint.nullable_extra: None}
    assert (await async_client.post(endpoint.path, json=with_null)).status_code == 200
    assert (await async_client.post(endpoint.path, json=endpoint.build_body(model))).status_code == 200

    assert seen[0][endpoint.nullable_extra] is None
    assert endpoint.nullable_extra not in seen[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", _ENDPOINTS)
async def test_decision_without_capability_is_model_not_found(
    async_client, source_upstream, endpoint: _DecisionEndpoint
) -> None:
    """A source that serves the model for chat but did not opt in never receives decision traffic."""

    called = False

    async def decide(_request: web.Request) -> web.Response:
        nonlocal called
        called = True
        return web.json_response(_decision_payload("chat-only"))

    base_url = await source_upstream(decide)
    await _create_model_source(async_client, name="chat-only", model="chat-only", base_url=base_url)

    response = await async_client.post(endpoint.path, json=endpoint.build_body("chat-only"))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "model_not_found"
    assert called is False
    async with SessionLocal() as session:
        result = await session.execute(select(RequestLog).where(RequestLog.model == "chat-only"))
        assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_decision_capabilities_are_independent(async_client, source_upstream) -> None:
    """``supports_decisions`` does not imply the vLLM ``/systemone`` extension, nor the reverse."""

    async def decide(_request: web.Request) -> web.Response:
        return web.json_response(_decision_payload("decisions-only"))

    base_url = await source_upstream(decide)
    await _create_model_source(
        async_client, name="decisions-only", model="decisions-only", base_url=base_url, supports_decisions=True
    )
    await _create_model_source(
        async_client, name="systemone-only", model="systemone-only", base_url=base_url, supports_systemone=True
    )

    assert (await async_client.post("/v1/decisions", json=_DECISIONS.build_body("decisions-only"))).status_code == 200
    assert (await async_client.post("/v1/systemone", json=_SYSTEMONE.build_body("decisions-only"))).status_code == 404
    assert (await async_client.post("/v1/systemone", json=_SYSTEMONE.build_body("systemone-only"))).status_code == 200
    assert (await async_client.post("/v1/decisions", json=_DECISIONS.build_body("systemone-only"))).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", _ENDPOINTS)
async def test_decision_upstream_validation_error_keeps_status_and_logs(
    async_client, source_upstream, endpoint: _DecisionEndpoint
) -> None:
    await _enable_api_key_auth(async_client)
    model = "strict-decider"

    async def reject(_request: web.Request) -> web.Response:
        return web.json_response(
            {"detail": [{"loc": ["body", "questions"], "msg": "Field required", "type": "missing"}]},
            status=422,
        )

    base_url = await source_upstream(reject)
    source_id, key, _ = await _source_with_key(
        async_client, endpoint, name="strict-decider", model=model, base_url=base_url
    )

    response = await async_client.post(
        endpoint.path, headers={"Authorization": f"Bearer {key}"}, json=endpoint.build_body(model)
    )

    assert response.status_code == 422
    async with SessionLocal() as session:
        result = await session.execute(select(RequestLog).where(RequestLog.model == model))
        log = result.scalar_one()
        assert log.status == "error"
        assert log.model_source_id == source_id
        assert log.upstream_status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", _ENDPOINTS)
async def test_decision_transport_failure_logs_without_upstream_status(
    async_client, endpoint: _DecisionEndpoint
) -> None:
    await _enable_api_key_auth(async_client)
    model = "unreachable-decider"
    source_id, key, _ = await _source_with_key(
        async_client,
        endpoint,
        name="unreachable-decider",
        model=model,
        base_url=f"http://127.0.0.1:{_free_port()}/v1",
    )

    response = await async_client.post(
        endpoint.path, headers={"Authorization": f"Bearer {key}"}, json=endpoint.build_body(model)
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "model_source_unreachable"
    async with SessionLocal() as session:
        result = await session.execute(select(RequestLog).where(RequestLog.model == model))
        log = result.scalar_one()
        assert log.status == "error"
        assert log.model_source_id == source_id
        assert log.upstream_status_code is None


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", _ENDPOINTS)
async def test_decision_without_usage_fails_closed_for_limited_key(
    async_client, source_upstream, endpoint: _DecisionEndpoint
) -> None:
    await _enable_api_key_auth(async_client)
    model = "usage-less-decider"

    async def decide(_request: web.Request) -> web.Response:
        return web.json_response(_decision_payload(model, usage=None))

    base_url = await source_upstream(decide)
    _, key, _ = await _source_with_key(
        async_client, endpoint, name="usage-less-decider", model=model, base_url=base_url, limited=True
    )

    response = await async_client.post(
        endpoint.path, headers={"Authorization": f"Bearer {key}"}, json=endpoint.build_body(model)
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "usage_unavailable"


@pytest.mark.asyncio
async def test_systemone_requires_model_as_routing_key(async_client, source_upstream) -> None:
    """vLLM treats ``model`` as optional; the proxy cannot route without it, so it rejects before dispatch."""

    called = False

    async def decide(_request: web.Request) -> web.Response:
        nonlocal called
        called = True
        return web.json_response(_decision_payload("anything"))

    base_url = await source_upstream(decide)
    await _create_model_source(
        async_client, name="modelless", model="modelless", base_url=base_url, supports_systemone=True
    )
    body = _SYSTEMONE.build_body("modelless")
    del body["model"]

    response = await async_client.post("/v1/systemone", json=body)

    # ``/v1`` request validation answers in the OpenAI error envelope.
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request_error"
    assert called is False
