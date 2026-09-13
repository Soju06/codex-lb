"""Route wiring and end-to-end routing of the subscription-exhaustion overflow (#2123 WP-C2, WP-D folded).

Two layers, deliberately separate:

* **Part A -- wiring.** ``api.py``'s hunks are driven with test doubles for the
  decision module's entry points (``resolve_subscription_overflow``,
  ``handshake_denial``, ``compact_pin_denial``, ``apply_usage_limit_hint``) so
  the route file's own obligations are proven independently of how the
  decision is made: a ``Response`` from the decision is returned verbatim, an
  ``OverflowDispatch`` is served through the hardened source route with the
  decision's claims handed through (never re-claimed, never a second owner),
  ``service_tier`` is stripped from the source body, the non-stream path
  commits the resolved pin before the JSON leaves and fails closed on a
  non-``written`` outcome, the two websocket handshakes deny with the
  decision's 426 before ``accept``, both compact routes answer the pin denial
  before any reservation, and only today's ``429 usage_limit_reached`` passes
  through the hint hook.
* **Part B -- the route matrix** (design v3 §13.4) against the real decision:
  overflow on the three routes, one lifecycle, one reservation, one row,
  telemetry stripped, pins written before the first content frame, turn-2
  routing across session and key, neutral release vs 400, compact 400, the
  pin-write failure pair and its fast decline, honest source errors, the
  bulkhead loser, abandonment, anchors and the database-outage coupling.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, cast

import pytest
from aiohttp import web
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.requests import Request
from starlette.testclient import WebSocketDenialResponse

import app.modules.proxy.service as proxy_module
from app.core.auth import generate_unique_account_id
from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from app.core.openai.models import CompactResponsePayload
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, ModelSource, ModelSourcePin, RequestLog
from app.db.session import SessionLocal, detach_session_objects
from app.modules.model_sources.repository import ModelSourcesRepository
from app.modules.proxy import api as proxy_api
from app.modules.proxy import model_source_pins as pins_module
from app.modules.proxy import overflow as overflow_module
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.proxy.affinity import _codex_backend_identity
from app.modules.proxy.model_source_pins import (
    PIN_KIND_ANCHOR,
    PIN_KIND_THREAD,
    ModelSourcePinRepository,
    PinIntent,
    PinWrite,
    PinWriteOutcome,
    anchor_pin_key,
    thread_pin_key,
)
from app.modules.proxy.overflow import (
    HANDSHAKE_DENIAL_CODE,
    HINT_HEADER,
    HINT_NATIVE_TEXT,
    MODEL_SOURCE_UNAVAILABLE_CODE,
    PIN_UNAVAILABLE_CODE,
    PIN_UNVERIFIED_CODE,
    REQUEST_LOG_SOURCE_FRESH,
    REQUEST_LOG_SOURCE_PINNED,
    ROUTE_CODEX_RESPONSES,
    ROUTE_V1_RESPONSES,
    SOURCE_UNAVAILABLE_CODE,
    UNSUPPORTED_INPUT_CODE,
    OverflowDispatch,
    OverflowFinishedHook,
)
from app.modules.proxy.selection_errors import USAGE_LIMIT_REACHED
from app.modules.proxy.source_admission import get_source_bulkhead
from app.modules.usage.repository import UsageRepository
from tests.integration.model_source_helpers import (
    _AsgiStream,
    _create_model_source,
    _enable_api_key_auth,
    stub_source_upstreams,
)
from tests.integration.test_model_source_dispatch import (
    _DELTA,
    _ITEM_ADDED,
    _USAGE,
    _app,
    _completed,
    _created,
    _drain,
    _source_rows,
    _sse_handler,
    _StubState,
)
from tests.unit.test_model_source_request_headers import (
    CODEX_TELEMETRY_REQUEST_HEADERS,
    assert_source_saw_only_constructed_headers,
)

pytestmark = pytest.mark.integration

_UpstreamHandler = Callable[[web.Request], Awaitable[web.StreamResponse]]

CODEX_ROUTE = "/backend-api/codex/responses"
V1_ROUTE = "/v1/responses"
COMPACT_ROUTES = ("/backend-api/codex/responses/compact", "/v1/responses/compact")
WS_ROUTES = ("/backend-api/codex/responses", "/v1/responses")

# A registry slug the subscription pool serves and the designated source lists (Part B).
REGISTRY_SLUG = "gpt-5.4"
# Native Codex identity: ``_is_native_codex_request`` keys on the User-Agent prefix / originator.
NATIVE_USER_AGENT = "codex_cli_rs/0.153.4 (Linux 6.8.0; x86_64) overflow-routing"


@pytest.fixture
async def source_upstream() -> AsyncIterator[Callable[..., Awaitable[str]]]:
    async with stub_source_upstreams() as start:
        yield start


# -- shared helpers ------------------------------------------------------------------------------------


def _native_headers(thread_id: str, *, session_id: str | None = None, **extra: str) -> dict[str, str]:
    headers = {"user-agent": NATIVE_USER_AGENT, "originator": "codex_cli_rs", "thread-id": thread_id}
    if session_id is not None:
        headers["session-id"] = session_id
    headers.update(extra)
    return headers


def _codex_body(model: str = REGISTRY_SLUG, **extra: Any) -> dict[str, Any]:
    return {
        "model": model,
        "instructions": "You are a test.",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        "stream": True,
        **extra,
    }


def _events(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            events.append(json.loads(line[6:]))
    return events


def _encode_jwt(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


async def _import_account(async_client: Any, account_id: str, email: str) -> str:
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
    assert response.status_code == 200, response.text
    return generate_unique_account_id(account_id, email)


async def _seed_exhausted_pool(async_client: Any, *, tag: str, reset_in_seconds: int = 1800) -> int:
    """One imported account, usage-proven exhausted (``QUOTA_EXCEEDED`` at 100 %); returns ``resets_at``.

    ``reset_in_seconds`` stays above ``SELECTOR_RETRY_HINT_MAX_SECONDS`` (300)
    so the 429 message is the capped constant and byte-stable across requests.
    """

    account_id = await _import_account(async_client, f"acc_{tag}", f"{tag}@example.com")
    now_epoch = int(time.time())
    reset_at = now_epoch + reset_in_seconds
    now = utcnow()
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None
        account.status = AccountStatus.QUOTA_EXCEEDED
        account.blocked_at = now_epoch
        account.reset_at = reset_at
        usage = UsageRepository(session)
        await usage.add_entry(
            account_id=account_id,
            used_percent=100.0,
            window="primary",
            reset_at=reset_at,
            window_minutes=300,
            recorded_at=now,
        )
        await usage.add_entry(
            account_id=account_id,
            used_percent=40.0,
            window="secondary",
            reset_at=reset_at + 6 * 86400,
            window_minutes=10080,
            recorded_at=now,
        )
        await session.commit()
    get_account_selection_cache().invalidate()
    return reset_at


def _forbid_subscription_stream(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The subscription relay must never be entered: fail loudly instead of hanging on a real upstream."""

    attempts: list[str] = []

    async def fail_fast(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        attempts.append(account_id)
        raise ProxyResponseError(500, {"error": {"message": "unexpected subscription attempt"}})
        yield  # pragma: no cover - async generator marker

    monkeypatch.setattr(proxy_module, "core_stream_responses", fail_fast)
    return attempts


def _canned_subscription_stream(monkeypatch: pytest.MonkeyPatch, *, response_id: str) -> list[dict[str, Any]]:
    """A deterministic subscription relay: ``response.created`` + ``response.completed`` with usage."""

    seen: list[dict[str, Any]] = []

    async def canned(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen.append(payload if isinstance(payload, dict) else payload.model_dump(exclude_none=True))
        yield (
            'data: {"type":"response.created","sequence_number":0,"response":{"id":"%s","object":"response",'
            '"status":"in_progress","output":[]}}\n\n' % response_id
        )
        yield (
            'data: {"type":"response.completed","sequence_number":1,"response":{"id":"%s","object":"response",'
            '"status":"completed","output":[],"usage":{"input_tokens":3,"output_tokens":2,"total_tokens":5}}}\n\n'
            % response_id
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", canned)
    return seen


async def _load_source(source_id: str) -> ModelSource:
    async with SessionLocal() as session:
        source = await ModelSourcesRepository(session).get_by_id(source_id)
        assert source is not None
        detach_session_objects(session)
        return source


async def _designate(async_client: Any, source_id: str | None) -> dict[str, Any]:
    response = await async_client.put("/api/settings", json={"subscriptionOverflowSourceId": source_id})
    assert response.status_code == 200, response.text
    return response.json()


async def _create_overflow_source(
    async_client: Any,
    source_upstream: Callable[..., Awaitable[str]],
    handler: _UpstreamHandler,
    *,
    name: str,
    model: str = REGISTRY_SLUG,
    designate: bool = True,
    **start_kwargs: Any,
) -> str:
    base_url = await source_upstream(handler, **start_kwargs)
    source_id = await _create_model_source(
        async_client,
        name=name,
        model=model,
        base_url=base_url,
        supports_responses=True,
        input_per_1m=2.0,
        output_per_1m=8.0,
    )
    if designate:
        await _designate(async_client, source_id)
    return source_id


async def _create_unscoped_key(async_client: Any, *, name: str, **extra: Any) -> tuple[str, str]:
    created = await async_client.post("/api/api-keys/", json={"name": name, **extra})
    assert created.status_code == 200, created.text
    payload = created.json()
    return payload["key"], payload["id"]


def _thread_key(thread_id: str) -> str:
    identity = _codex_backend_identity({"thread-id": thread_id})
    key = identity.thread_selection_key
    assert key is not None
    return key


async def _pin_rows() -> list[ModelSourcePin]:
    async with SessionLocal() as session:
        result = await session.execute(select(ModelSourcePin).order_by(ModelSourcePin.pin_key))
        return list(result.scalars().all())


async def _write_pin(
    pin_key: str,
    *,
    kind: str,
    source_id: str,
    api_key_id: str | None = None,
    now: datetime | None = None,
    drain_until: datetime | None = None,
) -> None:
    async with SessionLocal() as session:
        await ModelSourcePinRepository(session).upsert(
            [PinWrite(pin_key, kind, source_id, api_key_id)],
            now=now or datetime.now(timezone.utc),
            drain_until=drain_until,
        )
        await session.commit()


async def _all_rows() -> list[RequestLog]:
    async with SessionLocal() as session:
        result = await session.execute(select(RequestLog).order_by(RequestLog.id))
        return list(result.scalars().all())


# -- Part A: wiring doubles ----------------------------------------------------------------------------


def _double_owner_kwargs(self: OverflowDispatch) -> dict[str, Any]:
    """The contract's ``SourceDispatch`` kwargs: the owner fields C1 defines plus the decision module's real
    ``on_finished`` hook (so the pin-commit outcome counter is observed through the wiring as well)."""

    return {
        "request_log_source": self.request_log_source,
        "dispatch_kind": self.kind,
        "pin_intent": self.pin_intent,
        "pin_executor": self.pin_executor,
        "drain_until": self.drain_until,
        "pin_failure_error_code": PIN_UNAVAILABLE_CODE,
        "pin_unverified_error_code": PIN_UNVERIFIED_CODE,
        "on_finished": OverflowFinishedHook(self.route),
    }


class _OverflowCounter:
    """``codex_lb_subscription_overflow_total`` recorder: ``(route, outcome)`` in increment order."""

    def __init__(self) -> None:
        self.outcomes: list[tuple[str, str]] = []
        self._pending: tuple[str, str] | None = None

    def labels(self, **labels: str) -> _OverflowCounter:
        self._pending = (labels["route"], labels["outcome"])
        return self

    def inc(self, amount: float = 1) -> None:
        assert self._pending is not None
        self.outcomes.append(self._pending)


def _spy_overflow_counter(monkeypatch: pytest.MonkeyPatch) -> _OverflowCounter:
    counter = _OverflowCounter()
    monkeypatch.setattr(overflow_module, "subscription_overflow_total", counter)
    return counter


@dataclass(frozen=True, slots=True)
class _ResolvingIntent(PinIntent):
    """``PinIntent`` double: records the response id the route resolved it with."""

    resolved_with: list[str | None] = field(default_factory=list)

    def resolve(self, response_id: str | None) -> _ResolvingIntent:
        self.resolved_with.append(response_id)
        return _ResolvingIntent(writes=self.writes, thread_key=self.thread_key, resolved_with=self.resolved_with)


@dataclass(slots=True)
class _RecordingExecutor:
    outcome: str = "written"
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def commit(self, intent: Any, *, drain_until: Any, scheduler: Any, clock: Any) -> str:
        self.calls.append({"intent": intent, "drain_until": drain_until, "scheduler": scheduler, "clock": clock})
        return self.outcome


@dataclass(slots=True)
class _DecisionSpy:
    calls: list[dict[str, Any]] = field(default_factory=list)


def _install_decision(
    monkeypatch: pytest.MonkeyPatch,
    decide: Callable[[], Any],
) -> _DecisionSpy:
    """Replace ``resolve_subscription_overflow`` on the route file and give the dispatch double its owner kwargs."""

    spy = _DecisionSpy()

    async def fake_resolve(request, payload, context, api_key, **kwargs):
        spy.calls.append({"path": request.url.path, "payload": payload, "api_key": api_key, **kwargs})
        return decide()

    monkeypatch.setattr(proxy_api, "resolve_subscription_overflow", fake_resolve)
    monkeypatch.setattr(overflow_module.OverflowDispatch, "owner_kwargs", _double_owner_kwargs)
    return spy


def _dispatch_double(
    source: ModelSource,
    *,
    model: str,
    route: str,
    kind: str = "fresh",
    request_log_source: str = REQUEST_LOG_SOURCE_FRESH,
    pin_intent: PinIntent | None = None,
    pin_executor: Any = None,
    drain_until: datetime | None = None,
) -> OverflowDispatch:
    claims = proxy_api.try_claim_source_admission(source)
    assert claims is not None
    return OverflowDispatch(
        kind=cast(Any, kind),
        source=source,
        model=model,
        thread_key=None,
        body={},
        claims=claims,
        resets_at=None,
        selection=None,
        route=route,
        drain_until=drain_until,
        pin_intent=pin_intent if pin_intent is not None else PinIntent(writes=(), thread_key=None),
        pin_executor=pin_executor if pin_executor is not None else pins_module.PinWriteExecutor(),
        request_log_source=request_log_source,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "route"),
    [(CODEX_ROUTE, ROUTE_CODEX_RESPONSES), (V1_ROUTE, ROUTE_V1_RESPONSES)],
    ids=["codex", "v1"],
)
async def test_decision_answer_is_returned_verbatim_and_owns_nothing(
    async_client, monkeypatch: pytest.MonkeyPatch, path: str, route: str
) -> None:
    """A pinned/anchored answer from the decision (503/400) is the response; no reservation, row or relay follows."""

    attempts = _forbid_subscription_stream(monkeypatch)
    spy = _install_decision(
        monkeypatch,
        lambda: JSONResponse(
            status_code=503,
            content=openai_error(
                MODEL_SOURCE_UNAVAILABLE_CODE, "pinned source unavailable", error_type="upstream_error"
            ),
            headers={"Retry-After": "2"},
        ),
    )

    response = await async_client.post(
        path,
        json={**_codex_body(), "service_tier": "priority"},
        headers=_native_headers("thr_verbatim"),
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == MODEL_SOURCE_UNAVAILABLE_CODE
    assert response.headers["retry-after"] == "2"
    assert attempts == []
    assert await _all_rows() == []
    assert len(spy.calls) == 1
    call = spy.calls[0]
    assert call["route"] == route
    assert call["require_streaming"] is True
    assert call["source_route_excluded"] is False
    assert call["raw_model"] == REGISTRY_SLUG
    assert call["payload"].model == REGISTRY_SLUG
    assert call["payload"].service_tier == "priority"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "headers"),
    [
        (CODEX_ROUTE, _native_headers("thr_dispatch_native", session_id="sess_dispatch_native")),
        (V1_ROUTE, {"user-agent": "openai-python/1.99"}),
    ],
    ids=["codex-native", "v1-sdk"],
)
async def test_overflow_dispatch_streams_through_the_source_route_with_the_decisions_claims(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch, path: str, headers: dict[str, str]
) -> None:
    """One source lifecycle, the decision's claims transferred to the owner, telemetry and ``service_tier`` stripped."""

    attempts = _forbid_subscription_stream(monkeypatch)
    state = _StubState()
    source_model = "overflow-double-stream"
    source_id = await _create_overflow_source(
        async_client,
        source_upstream,
        _sse_handler(
            state, before_hold=[_created("resp_double"), _ITEM_ADDED, _DELTA, _completed(_USAGE, "resp_double")]
        ),
        name=source_model,
        model=source_model,
        designate=False,
    )
    source = await _load_source(source_id)
    dispatch = _dispatch_double(source, model=source_model, route=ROUTE_CODEX_RESPONSES)
    _install_decision(monkeypatch, lambda: dispatch)

    body = {
        **_codex_body(),
        "service_tier": "priority",
        "prompt_cache_key": "pck_verbatim",
        "client_metadata": {"session_id": "sess_dispatch", "thread_id": "thr_dispatch"},
        "stream_options": {"reasoning_summary_delivery": "final"},
    }
    async with async_client.stream("POST", path, json=body, headers=headers) as response:
        assert response.status_code == 200, await response.aread()
        text = (await response.aread()).decode()
    await _drain(async_client)

    events = _events(text)
    assert [event["type"] for event in events if event["type"] == "response.created"] == ["response.created"]
    terminals = [event for event in events if event["type"] in {"response.completed", "response.failed"}]
    assert [event["type"] for event in terminals] == ["response.completed"]
    assert terminals[0]["response"]["id"] == "resp_double"
    assert "x-codex-turn-state" not in {name.lower() for name in response.headers}
    assert "x-codex-lb-prompt-cache-mode" not in {name.lower() for name in response.headers}

    assert attempts == []
    assert len(state.requests) == 1
    sent = state.requests[0]
    assert sent["model"] == source_model
    assert sent["stream"] is True
    assert "service_tier" not in sent
    assert "client_metadata" not in sent
    assert "stream_options" not in sent
    assert "store" not in sent, "a client that omitted ``store`` leaves the source its default (§7.2 anchors)"
    assert sent["prompt_cache_key"] == "pck_verbatim"

    # The decision's claims were taken over by the owner and released exactly once by it.
    assert dispatch.claims.owner is not None
    assert dispatch.claims.released is True
    assert get_source_bulkhead().in_flight(source_id) == 0
    rows = await _source_rows(source_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.status == "success"
    assert row.source == REQUEST_LOG_SOURCE_FRESH
    assert row.account_id is None
    assert row.requested_service_tier == "priority"
    assert row.service_tier is None
    assert row.request_id == "resp_double"
    assert [other.id for other in await _all_rows()] == [row.id], "exactly one row for the whole request"


@pytest.mark.asyncio
async def test_overflow_dispatch_sends_only_constructed_headers_to_the_source(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preflight finding (v): no ChatGPT-internal telemetry header reaches the source on the overflow path."""

    _forbid_subscription_stream(monkeypatch)
    state = _StubState()
    source_model = "overflow-double-headers"
    source_id = await _create_overflow_source(
        async_client,
        source_upstream,
        _sse_handler(state, before_hold=[_created(), _ITEM_ADDED, _completed(_USAGE)]),
        name=source_model,
        model=source_model,
        designate=False,
    )
    source = await _load_source(source_id)
    _install_decision(monkeypatch, lambda: _dispatch_double(source, model=source_model, route=ROUTE_CODEX_RESPONSES))

    async with async_client.stream(
        "POST",
        CODEX_ROUTE,
        json=_codex_body(),
        headers={**CODEX_TELEMETRY_REQUEST_HEADERS, "authorization": "Bearer client-secret-never-forwarded"},
    ) as response:
        assert response.status_code == 200
        await response.aread()
    await _drain(async_client)

    assert len(state.headers) == 1
    assert_source_saw_only_constructed_headers(state.headers[0], source_token=f"token-{source_model}")


@pytest.mark.asyncio
async def test_overflow_non_stream_dispatch_commits_the_resolved_pin_before_the_json_leaves(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§6.5: after the JSON arrives the pin intent is resolved against the source response id and committed."""

    _forbid_subscription_stream(monkeypatch)
    state = _StubState()

    async def responses(request: web.Request) -> web.Response:
        state.requests.append(await request.json())
        return web.json_response(
            {
                "id": "resp_non_stream_pin",
                "object": "response",
                "status": "completed",
                "output": [{"id": "msg_1", "type": "message", "role": "assistant", "content": []}],
                "usage": _USAGE,
            }
        )

    source_model = "overflow-double-non-stream"
    source_id = await _create_overflow_source(
        async_client, source_upstream, responses, name=source_model, model=source_model, designate=False
    )
    source = await _load_source(source_id)
    intent = _ResolvingIntent(writes=(), thread_key=None)
    executor = _RecordingExecutor()
    drain_until = datetime(2026, 12, 1, tzinfo=timezone.utc)
    _install_decision(
        monkeypatch,
        lambda: _dispatch_double(
            source,
            model=source_model,
            route=ROUTE_V1_RESPONSES,
            kind="pinned",
            request_log_source=REQUEST_LOG_SOURCE_PINNED,
            pin_intent=intent,
            pin_executor=executor,
            drain_until=drain_until,
        ),
    )

    response = await async_client.post(V1_ROUTE, json={**_codex_body(), "stream": False})
    await _drain(async_client)

    assert response.status_code == 200, response.text
    assert response.json()["id"] == "resp_non_stream_pin"
    assert state.requests[0]["stream"] is False
    assert intent.resolved_with == ["resp_non_stream_pin"]
    assert len(executor.calls) == 1
    committed = executor.calls[0]
    assert isinstance(committed["intent"], _ResolvingIntent)
    assert committed["intent"].resolved_with is intent.resolved_with
    assert committed["drain_until"] == drain_until
    rows = await _source_rows(source_id)
    assert [(row.status, row.source, row.request_id) for row in rows] == [
        ("success", REQUEST_LOG_SOURCE_PINNED, "resp_non_stream_pin")
    ]
    assert get_source_bulkhead().in_flight(source_id) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "row_code", "counted"),
    [
        ("not_written", PIN_UNAVAILABLE_CODE, "pin_commit_failed"),
        ("unknown", PIN_UNVERIFIED_CODE, "pin_commit_unverified"),
    ],
)
async def test_overflow_non_stream_pin_failure_answers_503_and_writes_an_error_row(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch, outcome: str, row_code: str, counted: str
) -> None:
    """Never "proceed unpinned": a non-``written`` outcome fails the request closed with the pin-failure twin."""

    _forbid_subscription_stream(monkeypatch)
    counter = _spy_overflow_counter(monkeypatch)

    async def responses(request: web.Request) -> web.Response:
        await request.json()
        return web.json_response(
            {
                "id": "resp_pin_fails",
                "object": "response",
                "status": "completed",
                "output": [{"id": "msg_1", "type": "message", "role": "assistant", "content": []}],
                "usage": _USAGE,
            }
        )

    source_model = f"overflow-double-pin-{outcome.replace('_', '-')}"
    source_id = await _create_overflow_source(
        async_client, source_upstream, responses, name=source_model, model=source_model, designate=False
    )
    source = await _load_source(source_id)
    executor = _RecordingExecutor(outcome=outcome)
    _install_decision(
        monkeypatch,
        lambda: _dispatch_double(
            source,
            model=source_model,
            route=ROUTE_V1_RESPONSES,
            pin_intent=_ResolvingIntent(writes=(), thread_key=None),
            pin_executor=executor,
        ),
    )

    response = await async_client.post(V1_ROUTE, json={**_codex_body(), "stream": False})
    await _drain(async_client)

    assert response.status_code == 503, response.text
    error = response.json()["error"]
    assert error["code"] == PIN_UNAVAILABLE_CODE
    assert error["type"] == "server_error"
    assert response.headers["retry-after"] == "2"
    assert len(executor.calls) == 1
    rows = await _source_rows(source_id)
    assert [(row.status, row.error_code) for row in rows] == [("error", row_code)]
    assert get_source_bulkhead().in_flight(source_id) == 0
    assert counter.outcomes == [(ROUTE_V1_RESPONSES, counted)], "the pin-commit outcome is counted exactly once"


@pytest.mark.asyncio
async def test_overflow_non_stream_without_output_skips_the_pin_commit(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_subscription_stream(monkeypatch)

    async def responses(request: web.Request) -> web.Response:
        await request.json()
        return web.json_response(
            {"id": "resp_no_output", "object": "response", "status": "completed", "output": [], "usage": _USAGE}
        )

    source_model = "overflow-double-no-output"
    source_id = await _create_overflow_source(
        async_client, source_upstream, responses, name=source_model, model=source_model, designate=False
    )
    source = await _load_source(source_id)
    executor = _RecordingExecutor()
    _install_decision(
        monkeypatch,
        lambda: _dispatch_double(
            source,
            model=source_model,
            route=ROUTE_V1_RESPONSES,
            pin_intent=_ResolvingIntent(writes=(), thread_key=None),
            pin_executor=executor,
        ),
    )

    response = await async_client.post(V1_ROUTE, json={**_codex_body(), "stream": False})
    await _drain(async_client)

    assert response.status_code == 200, response.text
    assert executor.calls == []
    rows = await _source_rows(source_id)
    assert [row.status for row in rows] == ["success"]


@pytest.mark.asyncio
async def test_overflow_route_helper_releases_the_decisions_claims_when_admission_raises(
    async_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I13: whatever raises between the decision's claim and the owner's creation, the claims are released."""

    source_id = await _create_model_source(
        async_client,
        name="overflow-latch",
        model="overflow-latch-model",
        base_url="http://127.0.0.1:9/v1",
        supports_responses=True,
    )
    patched = await async_client.patch(f"/api/model-sources/{source_id}", json={"maxConcurrency": 1})
    assert patched.status_code == 200, patched.text
    source = await _load_source(source_id)
    monkeypatch.setattr(overflow_module.OverflowDispatch, "owner_kwargs", _double_owner_kwargs)
    dispatch = _dispatch_double(source, model="overflow-latch-model", route=ROUTE_V1_RESPONSES)
    assert get_source_bulkhead().in_flight(source_id) == 1

    def exploding_estimate(_payload: object) -> object:
        raise RuntimeError("estimate exploded")

    async def never_opened(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("the source must not be opened when admission fails")

    monkeypatch.setattr(proxy_api, "estimate_api_key_request_usage", exploding_estimate)
    monkeypatch.setattr(proxy_api, "stream_source_responses", never_opened)
    request = Request({"type": "http", "method": "POST", "path": V1_ROUTE, "headers": [], "client": ("203.0.113.9", 1)})
    payload = proxy_api.ResponsesRequest.model_validate(_codex_body())

    async def rate_limit_headers() -> dict[str, str]:
        return {}

    context = cast(
        Any,
        type(
            "Context", (), {"service": type("Service", (), {"rate_limit_headers": staticmethod(rate_limit_headers)})()}
        )(),
    )

    with pytest.raises(RuntimeError, match="estimate exploded"):
        await proxy_api._overflow_source_response(
            request, payload, context, None, dispatch, pre_normalization_effort=None
        )

    assert dispatch.claims.released is True
    assert dispatch.claims.owner is None
    assert get_source_bulkhead().in_flight(source_id) == 0
    # The forced-stream and model substitution happened on the way in.
    assert payload.model == "overflow-latch-model"
    assert payload.stream is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "route"),
    [(CODEX_ROUTE, ROUTE_CODEX_RESPONSES), (V1_ROUTE, ROUTE_V1_RESPONSES)],
    ids=["codex", "v1"],
)
async def test_decision_precedes_a_direct_source_dispatch_and_names_the_selected_source(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch, path: str, route: str
) -> None:
    """A directly owned model still consults the decision first (I7): its answer wins over the direct dispatch,
    it is handed the selected source, and ``None`` hands the request back to direct routing untouched."""

    attempts = _forbid_subscription_stream(monkeypatch)
    state = _StubState()
    direct_model = "direct-owned-model"
    source_id = await _create_overflow_source(
        async_client,
        source_upstream,
        _sse_handler(state, before_hold=[_created(), _ITEM_ADDED, _DELTA, _completed(_USAGE)]),
        name=f"direct-first-{route}",
        model=direct_model,
        designate=False,
    )
    answers: list[Any] = [
        JSONResponse(
            status_code=400,
            content=openai_error(SOURCE_UNAVAILABLE_CODE, "pinned elsewhere", error_type="invalid_request_error"),
        ),
        None,
    ]
    spy = _install_decision(monkeypatch, lambda: answers.pop(0))
    headers = _native_headers("thr_direct_first")

    refused = await async_client.post(path, json=_codex_body(model=direct_model), headers=headers)
    assert refused.status_code == 400, refused.text
    assert refused.json()["error"]["code"] == SOURCE_UNAVAILABLE_CODE
    assert state.requests == [], "the decision's answer precedes the direct dispatch"

    served = await async_client.post(path, json=_codex_body(model=direct_model), headers=headers)
    await _drain(async_client)
    assert served.status_code == 200, served.text
    assert [sent["model"] for sent in state.requests] == [direct_model]
    assert attempts == []
    assert len(spy.calls) == 2
    for call in spy.calls:
        assert call["route"] == route
        assert call["direct_source"] is not None and call["direct_source"].id == source_id
        assert call["payload"].model == direct_model
    assert get_source_bulkhead().in_flight(source_id) == 0


@pytest.mark.parametrize("path", WS_ROUTES, ids=["codex-ws", "v1-ws"])
def test_websocket_handshake_is_denied_with_the_decisions_426_before_accept(
    app_instance, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_handshake_denial(headers, *, context):
        calls.append({"thread_id": headers.get("thread-id"), "service": getattr(context, "service", None)})
        return JSONResponse(
            status_code=426,
            content=openai_error(
                HANDSHAKE_DENIAL_CODE, "pinned conversation; retry over HTTP", error_type="server_error"
            ),
        )

    async def allow_unauthenticated(_authorization: str | None, *, request: object | None = None):
        del request
        return None

    monkeypatch.setattr(proxy_api, "handshake_denial", fake_handshake_denial)
    # The websocket routes validate the key against the connection; the existing websocket
    # suites bypass that layer the same way so the handshake reaches the transport denials.
    monkeypatch.setattr(proxy_api, "validate_proxy_api_key_authorization", allow_unauthenticated)

    with TestClient(app_instance, client=("127.0.0.1", 50000)) as client:
        with pytest.raises(WebSocketDenialResponse) as denial:
            with client.websocket_connect(path, headers=_native_headers("thr_ws_denied")):
                pytest.fail("a denied handshake must not connect")

    assert denial.value.status_code == 426
    assert denial.value.json()["error"]["code"] == HANDSHAKE_DENIAL_CODE
    assert [call["thread_id"] for call in calls] == ["thr_ws_denied"]
    assert calls[0]["service"] is not None, "the handshake denial receives the websocket proxy context"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", COMPACT_ROUTES, ids=["codex-compact", "v1-compact"])
async def test_compact_routes_answer_the_pin_denial_before_any_reservation(
    async_client, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    calls: list[str] = []

    async def fake_compact_pin_denial(request, payload, *, context):
        calls.append(payload.model)
        return JSONResponse(
            status_code=400,
            content=openai_error(
                UNSUPPORTED_INPUT_CODE, "cannot be compacted there yet", error_type="invalid_request_error"
            ),
        )

    def never_estimated(_payload: object) -> object:
        raise AssertionError("the pin denial must precede the admission estimate and any reservation")

    async def never_compacted(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("a denied compaction must not reach upstream")

    monkeypatch.setattr(proxy_api, "compact_pin_denial", fake_compact_pin_denial)
    monkeypatch.setattr(proxy_api, "estimate_api_key_request_usage", never_estimated)
    monkeypatch.setattr(proxy_module, "core_compact_responses", never_compacted)

    response = await async_client.post(
        path,
        json={"model": "gpt-5.6-sol", "instructions": "", "input": []},
        headers=_native_headers("thr_compact_denied"),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == UNSUPPORTED_INPUT_CODE
    assert calls == ["gpt-5.6-sol"]
    assert await _all_rows() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("path", COMPACT_ROUTES, ids=["codex-compact", "v1-compact"])
async def test_compact_routes_are_unchanged_when_no_pin_denies(
    async_client, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    await _import_account(async_client, "acc_compact_ok", "compact-ok@example.com")

    async def no_denial(request, payload, *, context):
        return None

    async def fake_compact(payload, headers, access_token, account_id, **kwargs):
        del payload, headers, access_token, account_id, kwargs
        return CompactResponsePayload.model_validate(
            {
                "object": "response.compaction",
                "compaction_summary": {"id": "cmp_unpinned", "encrypted_content": "summary"},
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            }
        )

    monkeypatch.setattr(proxy_api, "compact_pin_denial", no_denial)
    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    response = await async_client.post(path, json={"model": "gpt-5.6-sol", "instructions": "", "input": []})

    assert response.status_code == 200, response.text
    assert response.json()["object"] == "response.compaction"


@pytest.mark.asyncio
async def test_only_the_usage_limit_429_passes_through_the_hint_hook() -> None:
    calls: list[tuple[int, Any, dict[str, str]]] = []

    def spy(request, content, headers):
        calls.append((request.scope["path"].count("/"), content, headers))
        return {**content, "hinted": True}, {**headers, HINT_HEADER: HINT_NATIVE_TEXT}

    request = Request({"type": "http", "method": "POST", "path": CODEX_ROUTE, "headers": [], "client": ("1.2.3.4", 1)})
    envelope = openai_error(
        USAGE_LIMIT_REACHED, "Rate limit exceeded. Try again in 300s", error_type=USAGE_LIMIT_REACHED, resets_at=1
    )

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(proxy_api, "apply_usage_limit_hint", spy)
        hinted = proxy_api._logged_error_json_response(
            request, 429, envelope, headers={"x-codex-primary-used-percent": "100"}
        )
        other_429 = proxy_api._logged_error_json_response(
            request, 429, openai_error("rate_limit_exceeded", "slow down", error_type="rate_limit_error")
        )
        not_429 = proxy_api._logged_error_json_response(request, 503, envelope)

    assert len(calls) == 1
    assert calls[0][1] == envelope
    assert calls[0][2] == {"x-codex-primary-used-percent": "100"}
    assert json.loads(bytes(hinted.body)) == {**envelope, "hinted": True}
    assert hinted.headers[HINT_HEADER] == HINT_NATIVE_TEXT
    assert hinted.headers["x-codex-primary-used-percent"] == "100"
    assert HINT_HEADER not in other_429.headers
    assert HINT_HEADER not in not_429.headers
    assert json.loads(bytes(not_429.body)) == envelope


@pytest.mark.asyncio
async def test_exhausted_pool_429_passes_through_the_hint_hook_once_on_the_route(
    async_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    reset_at = await _seed_exhausted_pool(async_client, tag="hint_hook")
    attempts = _forbid_subscription_stream(monkeypatch)
    _install_decision(monkeypatch, lambda: None)
    calls: list[Any] = []

    def identity(request, content, headers):
        calls.append(content)
        return content, headers

    monkeypatch.setattr(proxy_api, "apply_usage_limit_hint", identity)

    response = await async_client.post(CODEX_ROUTE, json=_codex_body(), headers=_native_headers("thr_hint_hook"))

    assert response.status_code == 429
    assert response.json()["error"] == {
        "message": "Rate limit exceeded. Try again in 300s",
        "type": USAGE_LIMIT_REACHED,
        "code": USAGE_LIMIT_REACHED,
        "resets_at": reset_at,
    }
    assert attempts == []
    assert len(calls) == 1
    assert calls[0]["error"]["code"] == USAGE_LIMIT_REACHED


# -- Part B: the route matrix against the real decision (design v3 §13.4) -------------------------------


@dataclass(slots=True)
class _Scene:
    """An exhausted pool, a designated stub source listing ``REGISTRY_SLUG`` and the stub's observations."""

    source_id: str
    state: _StubState
    reset_at: int


async def _exhausted_scene(
    async_client: Any,
    source_upstream: Callable[..., Awaitable[str]],
    *,
    tag: str,
    handler: _UpstreamHandler | None = None,
    frames: list[bytes] | None = None,
    hold: asyncio.Event | None = None,
    after_hold: list[bytes] | None = None,
    delay_headers: asyncio.Event | None = None,
    models: list[str] | None = None,
    **start_kwargs: Any,
) -> _Scene:
    reset_at = await _seed_exhausted_pool(async_client, tag=tag)
    state = _StubState()
    if handler is None:
        handler = _sse_handler(
            state,
            before_hold=frames if frames is not None else [_created(), _ITEM_ADDED, _DELTA, _completed(_USAGE)],
            hold=hold,
            after_hold=after_hold,
            delay_headers=delay_headers,
        )
    base_url = await source_upstream(handler, **start_kwargs)
    response = await async_client.post(
        "/api/model-sources/",
        json={
            "name": f"overflow-{tag}",
            "baseUrl": base_url,
            "apiKey": f"token-overflow-{tag}",
            "supportsChatCompletions": True,
            "supportsResponses": True,
            "models": [
                {
                    "model": model,
                    "displayName": model,
                    "contextWindow": 400_000,
                    "maxOutputTokens": 32_000,
                    "supportsStreaming": True,
                    "supportsTools": True,
                    "inputPer1M": 2.0,
                    "outputPer1M": 8.0,
                }
                for model in (models or [REGISTRY_SLUG])
            ],
        },
    )
    assert response.status_code == 200, response.text
    source_id = response.json()["id"]
    await _designate(async_client, source_id)
    return _Scene(source_id=source_id, state=state, reset_at=reset_at)


def _todays_429(reset_at: int) -> dict[str, Any]:
    return {
        "error": {
            "message": "Rate limit exceeded. Try again in 300s",
            "type": USAGE_LIMIT_REACHED,
            "code": USAGE_LIMIT_REACHED,
            "resets_at": reset_at,
        }
    }


def _lifecycle(events: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    created = [event["response"]["id"] for event in events if event["type"] == "response.created"]
    terminals = [event["type"] for event in events if event["type"] in {"response.completed", "response.failed"}]
    return created, terminals


async def _pool_is_healthy(async_client: Any, *, tag: str) -> None:
    await _import_account(async_client, f"acc_healthy_{tag}", f"healthy-{tag}@example.com")
    get_account_selection_cache().invalidate()


def _sqlite_backed() -> bool:
    import os

    return os.environ["CODEX_LB_DATABASE_URL"].startswith("sqlite")


@pytest.mark.asyncio
async def test_fresh_overflow_serves_the_codex_route_from_the_designated_source(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One source lifecycle, one row with the overflow attribution, telemetry and tier stripped, thread pinned."""

    attempts = _forbid_subscription_stream(monkeypatch)
    scene = await _exhausted_scene(async_client, source_upstream, tag="codex_fresh")
    thread_id = "thr_codex_fresh"

    async with async_client.stream(
        "POST",
        CODEX_ROUTE,
        json={
            **_codex_body(),
            # Codex sends ``store: false`` (client.rs): a thread pin, never an anchor row.
            "store": False,
            "service_tier": "priority",
            "prompt_cache_key": "pck_codex_fresh",
            "client_metadata": {"session_id": "sess_codex_fresh", "thread_id": thread_id},
            "stream_options": {"reasoning_summary_delivery": "final"},
        },
        headers=_native_headers(thread_id, session_id="sess_codex_fresh"),
    ) as response:
        assert response.status_code == 200, await response.aread()
        text = (await response.aread()).decode()
    await _drain(async_client)

    created, terminals = _lifecycle(_events(text))
    assert created == ["resp_dispatch_1"]
    assert terminals == ["response.completed"]
    lowered = {name.lower() for name in response.headers}
    assert "x-codex-turn-state" not in lowered
    assert "x-codex-lb-prompt-cache-mode" not in lowered
    assert attempts == []

    assert len(scene.state.requests) == 1
    sent = scene.state.requests[0]
    assert sent["model"] == REGISTRY_SLUG
    assert sent["stream"] is True
    assert "service_tier" not in sent
    assert "client_metadata" not in sent
    assert "stream_options" not in sent
    assert sent["store"] is False, "Codex's own ``store: false`` reaches the source verbatim"
    assert sent["prompt_cache_key"] == "pck_codex_fresh"

    rows = await _all_rows()
    assert len(rows) == 1, [(row.status, row.source, row.error_code) for row in rows]
    row = rows[0]
    assert row.model_source_id == scene.source_id
    assert row.source == REQUEST_LOG_SOURCE_FRESH
    assert row.account_id is None
    assert row.status == "success"
    assert row.requested_service_tier == "priority"
    assert row.service_tier is None
    assert row.input_tokens == 100
    assert row.output_tokens == 20
    assert row.cost_usd is not None and abs(row.cost_usd - (100 * 2.0 + 20 * 8.0) / 1_000_000) < 1e-9
    assert row.request_id == "resp_dispatch_1"

    pins = await _pin_rows()
    assert [(pin.kind, pin.pin_key, pin.source_id) for pin in pins] == [
        (PIN_KIND_THREAD, thread_pin_key(_thread_key(thread_id)), scene.source_id)
    ]
    assert get_source_bulkhead().in_flight(scene.source_id) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [True, False], ids=["stream", "non-stream"])
async def test_fresh_overflow_serves_v1_requests_from_the_designated_source(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch, stream: bool
) -> None:
    attempts = _forbid_subscription_stream(monkeypatch)

    async def handler(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        if body.get("stream"):
            return await _sse_handler(
                _StubState(), before_hold=[_created("resp_v1"), _ITEM_ADDED, _completed(_USAGE, "resp_v1")]
            )(request)
        return web.json_response(
            {
                "id": "resp_v1",
                "object": "response",
                "status": "completed",
                "output": [{"id": "msg_1", "type": "message", "role": "assistant", "content": []}],
                "usage": _USAGE,
            }
        )

    scene = await _exhausted_scene(
        async_client, source_upstream, tag=f"v1_{'stream' if stream else 'json'}", handler=handler
    )

    response = await async_client.post(
        V1_ROUTE,
        json={**_codex_body(), "stream": stream},
        headers={"user-agent": "openai-python/1.99"},
    )
    await _drain(async_client)

    assert response.status_code == 200, response.text
    if stream:
        created, terminals = _lifecycle(_events(response.text))
        assert created == ["resp_v1"]
        assert terminals == ["response.completed"]
    else:
        assert response.json()["id"] == "resp_v1"
    assert attempts == []
    rows = await _all_rows()
    assert [(row.status, row.source, row.model_source_id, row.account_id) for row in rows] == [
        ("success", REQUEST_LOG_SOURCE_FRESH, scene.source_id, None)
    ]
    assert get_source_bulkhead().in_flight(scene.source_id) == 0


@pytest.mark.asyncio
async def test_fresh_overflow_forwards_only_constructed_headers_to_the_source(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preflight (v) on the real path: a native request's telemetry headers never reach the source."""

    _forbid_subscription_stream(monkeypatch)
    scene = await _exhausted_scene(async_client, source_upstream, tag="hdr")
    # ``x-openai-memgen-request`` marks a memory-consolidation job, which never overflows (Q8); every other
    # telemetry header of the fixture rides along and must be dropped on the way to the source.
    headers = {
        name: value for name, value in CODEX_TELEMETRY_REQUEST_HEADERS.items() if name != "x-openai-memgen-request"
    }
    headers["x-openai-subagent"] = "review"

    async with async_client.stream("POST", CODEX_ROUTE, json=_codex_body(), headers=headers) as response:
        assert response.status_code == 200, await response.aread()
        await response.aread()
    await _drain(async_client)

    assert len(scene.state.headers) == 1
    assert_source_saw_only_constructed_headers(scene.state.headers[0], source_token="token-overflow-hdr")


@pytest.mark.asyncio
async def test_pin_lands_before_the_first_content_frame_and_the_thread_returns_across_session_and_key(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I11 + CP-17: no row while only bookkeeping frames exist; the pin precedes content; turn 2 follows the pin."""

    attempts = _forbid_subscription_stream(monkeypatch)
    await _enable_api_key_auth(async_client)
    key_a, key_a_id = await _create_unscoped_key(async_client, name="overflow-turn-1")
    key_b, _key_b_id = await _create_unscoped_key(async_client, name="overflow-turn-2")
    hold = asyncio.Event()
    scene = await _exhausted_scene(
        async_client,
        source_upstream,
        tag="turns",
        frames=[_created("resp_turn_1")],
        hold=hold,
        after_hold=[_ITEM_ADDED, _DELTA, _completed(_USAGE, "resp_turn_1")],
        handler_cancellation=True,
        shutdown_timeout=1.0,
    )
    thread_id = "thr_turns"

    first = _AsgiStream(
        app=_app(async_client),
        path=CODEX_ROUTE,
        headers={**_native_headers(thread_id, session_id="sess_turn_1"), "authorization": f"Bearer {key_a}"},
        body=json.dumps({**_codex_body(), "store": False}).encode(),
    )
    runner = asyncio.create_task(first.run())
    deadline = time.monotonic() + 5
    while not scene.state.requests and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert scene.state.requests, "the stub never received the first turn"
    await asyncio.sleep(0.2)
    # Only ``response.created`` exists so far: nothing pinned, nothing released to the client.
    assert await _pin_rows() == []
    assert first.chunks == []
    hold.set()
    await asyncio.wait_for(runner, timeout=10)
    await _drain(async_client)

    created, terminals = _lifecycle(_events(first.received().decode()))
    assert created == ["resp_turn_1"]
    assert terminals == ["response.completed"]
    pins = await _pin_rows()
    assert [(pin.kind, pin.pin_key, pin.source_id, pin.api_key_id) for pin in pins] == [
        (PIN_KIND_THREAD, thread_pin_key(_thread_key(thread_id)), scene.source_id, key_a_id)
    ]

    # Turn 2: same conversation, a new Codex process (new session id), a rotated key and a healthy pool.
    await _pool_is_healthy(async_client, tag="turns")
    response = await async_client.post(
        CODEX_ROUTE,
        json=_codex_body(),
        headers={**_native_headers(thread_id, session_id="sess_turn_2"), "authorization": f"Bearer {key_b}"},
    )
    await _drain(async_client)

    assert response.status_code == 200, response.text
    assert len(scene.state.requests) == 2
    assert attempts == []
    rows = await _all_rows()
    assert [(row.status, row.source) for row in rows] == [
        ("success", REQUEST_LOG_SOURCE_FRESH),
        ("success", REQUEST_LOG_SOURCE_PINNED),
    ]
    assert get_source_bulkhead().in_flight(scene.source_id) == 0


@pytest.mark.asyncio
async def test_failure_terminal_before_content_writes_no_pin(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_subscription_stream(monkeypatch)
    failed = (
        b'data: {"type":"response.failed","sequence_number":1,"response":{"id":"resp_dispatch_1","object":"response",'
        b'"status":"failed","output":[],"error":{"code":"server_error","message":"boom"}}}\n\n'
    )
    scene = await _exhausted_scene(async_client, source_upstream, tag="failed", frames=[_created(), failed])

    async with async_client.stream(
        "POST", CODEX_ROUTE, json=_codex_body(), headers=_native_headers("thr_failed")
    ) as response:
        assert response.status_code == 200
        text = (await response.aread()).decode()
    await _drain(async_client)

    created, terminals = _lifecycle(_events(text))
    assert created == ["resp_dispatch_1"]
    assert terminals == ["response.failed"]
    assert await _pin_rows() == []
    rows = await _all_rows()
    assert [(row.status, row.source) for row in rows] == [("error", REQUEST_LOG_SOURCE_FRESH)]
    assert get_source_bulkhead().in_flight(scene.source_id) == 0


@pytest.mark.asyncio
async def test_native_request_without_a_thread_id_keeps_todays_429_and_pins_nothing(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = _forbid_subscription_stream(monkeypatch)
    scene = await _exhausted_scene(async_client, source_upstream, tag="no_thread")

    response = await async_client.post(
        CODEX_ROUTE, json=_codex_body(), headers={"user-agent": NATIVE_USER_AGENT, "originator": "codex_cli_rs"}
    )

    assert response.status_code == 429
    assert response.json() == _todays_429(scene.reset_at)
    assert HINT_HEADER not in response.headers
    assert scene.state.requests == []
    assert attempts == []
    assert await _pin_rows() == []


@pytest.mark.asyncio
async def test_pinned_thread_on_a_disabled_source_is_released_neutral_when_source_free(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§7.2 CP-6: durable delete first, then the subscription serves the id-stripped transcript."""

    scene = await _exhausted_scene(async_client, source_upstream, tag="release")
    thread_id = "thr_release"
    await _write_pin(thread_pin_key(_thread_key(thread_id)), kind=PIN_KIND_THREAD, source_id=scene.source_id)
    disabled = await async_client.patch(f"/api/model-sources/{scene.source_id}", json={"isEnabled": False})
    assert disabled.status_code == 200, disabled.text
    await _pool_is_healthy(async_client, tag="release")
    relayed = _canned_subscription_stream(monkeypatch, response_id="resp_released")

    body = _codex_body()
    body["input"] = [
        {"type": "message", "id": "msg_src_1", "role": "user", "content": [{"type": "input_text", "text": "hi"}]},
        {"type": "message", "id": "msg_src_2", "role": "assistant", "content": [{"type": "output_text", "text": "yo"}]},
    ]
    async with async_client.stream("POST", CODEX_ROUTE, json=body, headers=_native_headers(thread_id)) as response:
        assert response.status_code == 200, await response.aread()
        text = (await response.aread()).decode()
    await _drain(async_client)

    created, terminals = _lifecycle(_events(text))
    assert created == ["resp_released"]
    assert terminals == ["response.completed"]
    assert scene.state.requests == []
    assert await _pin_rows() == [], "the pin is deleted durably before the subscription serves the thread"
    assert len(relayed) == 1
    relayed_input = relayed[0]["input"]
    assert [item.get("role") for item in relayed_input] == ["user", "assistant"]
    assert all("id" not in item for item in relayed_input), relayed_input


@pytest.mark.asyncio
async def test_pinned_thread_on_a_disabled_source_with_ciphertext_is_refused(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = _forbid_subscription_stream(monkeypatch)
    scene = await _exhausted_scene(async_client, source_upstream, tag="refuse")
    thread_id = "thr_refuse"
    await _write_pin(thread_pin_key(_thread_key(thread_id)), kind=PIN_KIND_THREAD, source_id=scene.source_id)
    disabled = await async_client.patch(f"/api/model-sources/{scene.source_id}", json={"isEnabled": False})
    assert disabled.status_code == 200, disabled.text
    await _pool_is_healthy(async_client, tag="refuse")

    body = _codex_body()
    body["input"] = [
        {"type": "message", "id": "msg_src_1", "role": "user", "content": [{"type": "input_text", "text": "hi"}]},
        {"type": "reasoning", "id": "rs_src_1", "summary": [], "encrypted_content": "c2VjcmV0"},
    ]
    response = await async_client.post(CODEX_ROUTE, json=body, headers=_native_headers(thread_id))

    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == SOURCE_UNAVAILABLE_CODE
    assert error["type"] == "invalid_request_error"
    assert attempts == []
    assert scene.state.requests == []
    assert len(await _pin_rows()) == 1, "a refused thread keeps its pin"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", COMPACT_ROUTES, ids=["codex-compact", "v1-compact"])
async def test_compaction_on_a_pinned_thread_is_refused(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    scene = await _exhausted_scene(async_client, source_upstream, tag=f"compact_{path.count('/')}")
    thread_id = f"thr_compact_{path.count('/')}"
    await _write_pin(thread_pin_key(_thread_key(thread_id)), kind=PIN_KIND_THREAD, source_id=scene.source_id)
    await _pool_is_healthy(async_client, tag=f"compact_{path.count('/')}")

    async def never_compacted(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("a pinned thread must not be compacted upstream")

    monkeypatch.setattr(proxy_module, "core_compact_responses", never_compacted)

    response = await async_client.post(
        path,
        json={"model": REGISTRY_SLUG, "instructions": "", "input": []},
        headers=_native_headers(thread_id),
    )

    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == UNSUPPORTED_INPUT_CODE
    assert error["type"] == "invalid_request_error"
    assert "compact" in error["message"].lower()


@pytest.mark.asyncio
async def test_pinned_thread_with_an_image_is_refused_without_vision_and_served_with_it(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§7.2 P16: an ``input_image`` on a pinned thread needs the source model's vision; the pin is kept either way."""

    attempts = _forbid_subscription_stream(monkeypatch)
    scene = await _exhausted_scene(async_client, source_upstream, tag="vision")
    thread_id = "thr_vision"
    await _write_pin(thread_pin_key(_thread_key(thread_id)), kind=PIN_KIND_THREAD, source_id=scene.source_id)
    await _pool_is_healthy(async_client, tag="vision")
    body = {
        **_codex_body(),
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "what is this"},
                    {"type": "input_image", "image_url": "data:image/png;base64,iVBORw0KGgo=", "detail": "auto"},
                ],
            }
        ],
    }
    headers = _native_headers(thread_id)

    refused = await async_client.post(CODEX_ROUTE, json=body, headers=headers)
    assert refused.status_code == 400, refused.text
    error = refused.json()["error"]
    assert error["code"] == UNSUPPORTED_INPUT_CODE
    assert error["type"] == "invalid_request_error"
    assert "images" in error["message"]
    assert scene.state.requests == []
    assert len(await _pin_rows()) == 1, "a refused turn keeps its pin"

    # The operator declares vision on the source model: the same turn is served by the pinned source.
    listed = await async_client.get("/api/model-sources/")
    assert listed.status_code == 200, listed.text
    (source,) = [entry for entry in listed.json()["sources"] if entry["id"] == scene.source_id]
    models = [
        {**{key: value for key, value in entry.items() if key not in {"id", "sourceId", "createdAt", "updatedAt"}}}
        for entry in source["models"]
    ]
    for entry in models:
        entry["supportsVision"] = True
    updated = await async_client.patch(f"/api/model-sources/{scene.source_id}", json={"models": models})
    assert updated.status_code == 200, updated.text

    async with async_client.stream("POST", CODEX_ROUTE, json=body, headers=headers) as response:
        assert response.status_code == 200, await response.aread()
        text = (await response.aread()).decode()
    await _drain(async_client)
    created, terminals = _lifecycle(_events(text))
    assert created == ["resp_dispatch_1"]
    assert terminals == ["response.completed"]
    assert len(scene.state.requests) == 1
    parts = scene.state.requests[0]["input"][0]["content"]
    assert [part["type"] for part in parts] == ["input_text", "input_image"]
    # The thread pin is kept; ``store`` was omitted, so the served dispatch also anchored the source response.
    pins = await _pin_rows()
    assert [(pin.kind, pin.pin_key) for pin in pins] == [
        (PIN_KIND_ANCHOR, anchor_pin_key(None, "resp_dispatch_1")),
        (PIN_KIND_THREAD, thread_pin_key(_thread_key(thread_id))),
    ]
    assert attempts == []


@pytest.mark.asyncio
async def test_pinned_thread_model_switch_listed_serves_unlisted_refuses(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = _forbid_subscription_stream(monkeypatch)
    scene = await _exhausted_scene(async_client, source_upstream, tag="switch", models=[REGISTRY_SLUG, "gpt-5.4-mini"])
    thread_id = "thr_switch"
    await _write_pin(thread_pin_key(_thread_key(thread_id)), kind=PIN_KIND_THREAD, source_id=scene.source_id)
    await _pool_is_healthy(async_client, tag="switch")
    ciphertext = [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]},
        {"type": "reasoning", "id": "rs_switch", "summary": [], "encrypted_content": "c2VjcmV0"},
    ]

    listed = await async_client.post(
        CODEX_ROUTE, json={**_codex_body(model="gpt-5.4-mini"), "input": ciphertext}, headers=_native_headers(thread_id)
    )
    await _drain(async_client)
    assert listed.status_code == 200, listed.text
    assert [sent["model"] for sent in scene.state.requests] == ["gpt-5.4-mini"]

    unlisted = await async_client.post(
        CODEX_ROUTE, json={**_codex_body(model="gpt-5.5"), "input": ciphertext}, headers=_native_headers(thread_id)
    )
    assert unlisted.status_code == 400, unlisted.text
    assert unlisted.json()["error"]["code"] == SOURCE_UNAVAILABLE_CODE
    assert len(scene.state.requests) == 1
    assert attempts == []


@pytest.mark.asyncio
async def test_pinned_thread_switching_to_a_directly_owned_model_is_decided_by_the_pin(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§7.2 / I7: another source serving the requested model directly never sees the pinned transcript.

    The pinned source does not list the model, so the unservable rules apply:
    source reasoning -> 400 and neither source is contacted; a source-free
    transcript -> the pin is deleted durably first, then the direct source
    serves the id-stripped body.
    """

    attempts = _forbid_subscription_stream(monkeypatch)
    scene = await _exhausted_scene(async_client, source_upstream, tag="direct_switch")
    direct_state = _StubState()
    direct_model = "direct-only-model"
    direct_source_id = await _create_overflow_source(
        async_client,
        source_upstream,
        _sse_handler(
            direct_state,
            before_hold=[_created("resp_direct_1"), _ITEM_ADDED, _DELTA, _completed(_USAGE, "resp_direct_1")],
        ),
        name="direct-switch",
        model=direct_model,
        designate=False,
    )
    thread_id = "thr_direct_switch"
    await _write_pin(thread_pin_key(_thread_key(thread_id)), kind=PIN_KIND_THREAD, source_id=scene.source_id)
    await _pool_is_healthy(async_client, tag="direct_switch")

    ciphertext = [
        {"type": "message", "id": "msg_src_1", "role": "user", "content": [{"type": "input_text", "text": "hi"}]},
        {"type": "reasoning", "id": "rs_src_1", "summary": [], "encrypted_content": "c2VjcmV0"},
    ]
    refused = await async_client.post(
        CODEX_ROUTE, json={**_codex_body(model=direct_model), "input": ciphertext}, headers=_native_headers(thread_id)
    )
    assert refused.status_code == 400, refused.text
    assert refused.json()["error"]["code"] == SOURCE_UNAVAILABLE_CODE
    assert direct_state.requests == [], "the other source never receives the pinned transcript"
    assert scene.state.requests == []
    assert len(await _pin_rows()) == 1, "a refused turn keeps its pin"

    source_free = [
        {"type": "message", "id": "msg_src_1", "role": "user", "content": [{"type": "input_text", "text": "hi"}]},
        {"type": "message", "id": "msg_src_2", "role": "assistant", "content": [{"type": "output_text", "text": "yo"}]},
    ]
    async with async_client.stream(
        "POST",
        CODEX_ROUTE,
        json={**_codex_body(model=direct_model), "input": source_free},
        headers=_native_headers(thread_id),
    ) as response:
        assert response.status_code == 200, await response.aread()
        text = (await response.aread()).decode()
    await _drain(async_client)

    created, terminals = _lifecycle(_events(text))
    assert created == ["resp_direct_1"]
    assert terminals == ["response.completed"]
    assert await _pin_rows() == [], "the pin is deleted durably before the direct source serves the thread"
    assert len(direct_state.requests) == 1
    sent_input = direct_state.requests[0]["input"]
    assert [item.get("role") for item in sent_input] == ["user", "assistant"]
    assert all("id" not in item for item in sent_input), sent_input
    assert scene.state.requests == []
    assert attempts == []
    rows = await _all_rows()
    assert [(row.model_source_id, row.account_id) for row in rows] == [(direct_source_id, None)]


@pytest.mark.asyncio
async def test_clearing_the_setting_drains_pinned_threads_and_declines_fresh_ones(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = _forbid_subscription_stream(monkeypatch)
    scene = await _exhausted_scene(async_client, source_upstream, tag="drain")
    pinned = "thr_drain_pinned"
    await _write_pin(thread_pin_key(_thread_key(pinned)), kind=PIN_KIND_THREAD, source_id=scene.source_id)
    settings = await _designate(async_client, None)
    assert settings["subscriptionOverflowSourceId"] is None
    assert settings["subscriptionOverflowDrainUntil"] is not None

    served = await async_client.post(CODEX_ROUTE, json=_codex_body(), headers=_native_headers(pinned))
    await _drain(async_client)
    fresh = await async_client.post(CODEX_ROUTE, json=_codex_body(), headers=_native_headers("thr_drain_fresh"))

    assert served.status_code == 200, served.text
    assert fresh.status_code == 429
    assert fresh.json() == _todays_429(scene.reset_at)
    assert len(scene.state.requests) == 1
    assert attempts == []
    rows = await _all_rows()
    assert [(row.status, row.source) for row in rows if row.model_source_id == scene.source_id] == [
        ("success", REQUEST_LOG_SOURCE_PINNED)
    ]


@pytest.mark.asyncio
async def test_pin_write_failure_yields_one_synthesized_pair_and_fast_declines_the_retry(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§8.5/§8.10: a verified non-write terminates the lifecycle with created + failed; the retry gets today's 429."""

    if not _sqlite_backed():
        pytest.skip("the writer-section hold is a file-backed SQLite mechanism")
    from app.db.session import sqlite_writer_section

    _forbid_subscription_stream(monkeypatch)
    counter = _spy_overflow_counter(monkeypatch)
    monkeypatch.setattr(pins_module, "PIN_WRITE_ACQUIRE_DEADLINE_SECONDS", 0.3)
    scene = await _exhausted_scene(
        async_client,
        source_upstream,
        tag="pin_fail",
        frames=[_created("resp_pin_fail"), _ITEM_ADDED, _DELTA, _completed(_USAGE, "resp_pin_fail")],
        handler_cancellation=True,
        shutdown_timeout=1.0,
    )
    thread_id = "thr_pin_fail"
    release_writer = asyncio.Event()
    writer_held = asyncio.Event()

    async def hold_writer() -> None:
        async with sqlite_writer_section():
            writer_held.set()
            await release_writer.wait()

    holder = asyncio.create_task(hold_writer())
    await asyncio.wait_for(writer_held.wait(), timeout=5)
    try:
        stream = _AsgiStream(
            app=_app(async_client),
            path=CODEX_ROUTE,
            headers=_native_headers(thread_id),
            body=json.dumps(_codex_body()).encode(),
        )
        runner = asyncio.create_task(stream.run())
        deadline = time.monotonic() + 5
        while not scene.state.requests and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert scene.state.requests, "the stub never received the dispatch"
        # Past the acquisition deadline: the write is ``not_written``; the row and the retry need the writer back.
        await asyncio.sleep(0.8)
    finally:
        release_writer.set()
        await holder
    await asyncio.wait_for(runner, timeout=15)
    await _drain(async_client)

    events = _events(stream.received().decode())
    assert [event["type"] for event in events] == ["response.created", "response.failed"]
    assert events[0]["response"]["id"] == "resp_pin_fail"
    assert events[1]["response"]["error"]["code"] == PIN_UNAVAILABLE_CODE
    assert events[1]["response"]["error"]["type"] == "server_error"
    assert await _pin_rows() == []
    rows = await _all_rows()
    assert [(row.status, row.error_code, row.source) for row in rows] == [
        ("error", PIN_UNAVAILABLE_CODE, REQUEST_LOG_SOURCE_FRESH)
    ]
    assert get_source_bulkhead().in_flight(scene.source_id) == 0

    retry = await async_client.post(CODEX_ROUTE, json=_codex_body(), headers=_native_headers(thread_id))
    assert retry.status_code == 429
    assert retry.json() == _todays_429(scene.reset_at)
    assert len(scene.state.requests) == 1, "the fast-declined retry must not pay for a second dispatch"
    assert counter.outcomes == [
        (ROUTE_CODEX_RESPONSES, "dispatched_fresh"),
        (ROUTE_CODEX_RESPONSES, "pin_commit_failed"),
        (ROUTE_CODEX_RESPONSES, "declined_pin_commit_recent_failure"),
    ]


@pytest.mark.asyncio
async def test_source_401_is_recoded_to_a_generic_502(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = _forbid_subscription_stream(monkeypatch)

    async def unauthorized(request: web.Request) -> web.Response:
        await request.json()
        return web.json_response(
            {"error": {"message": "Incorrect API key provided: sk-live-****abcd", "type": "invalid_request_error"}},
            status=401,
        )

    scene = await _exhausted_scene(async_client, source_upstream, tag="s401", handler=unauthorized)

    response = await async_client.post(CODEX_ROUTE, json=_codex_body(), headers=_native_headers("thr_401"))
    await _drain(async_client)

    assert response.status_code == 502, response.text
    assert response.json()["error"]["code"] == "model_source_credentials_error"
    assert "abcd" not in response.text
    assert "sk-live" not in response.text
    assert attempts == []
    rows = await _all_rows()
    assert [(row.status, row.upstream_status_code, row.source) for row in rows] == [
        ("error", 401, REQUEST_LOG_SOURCE_FRESH)
    ]
    assert get_source_bulkhead().in_flight(scene.source_id) == 0


@pytest.mark.asyncio
async def test_source_429_passes_through_with_its_retry_after(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = _forbid_subscription_stream(monkeypatch)

    async def rate_limited(request: web.Request) -> web.Response:
        await request.json()
        return web.json_response(
            {"error": {"message": "slow down", "type": "rate_limit_error", "code": "rate_limit_exceeded"}},
            status=429,
            headers={"Retry-After": "7"},
        )

    scene = await _exhausted_scene(async_client, source_upstream, tag="s429", handler=rate_limited)

    response = await async_client.post(CODEX_ROUTE, json=_codex_body(), headers=_native_headers("thr_429"))
    await _drain(async_client)

    assert response.status_code == 429
    error = response.json()["error"]
    assert error["code"] == "rate_limit_exceeded"
    assert "resets_at" not in error
    assert response.headers.get("retry-after") == "7"
    assert attempts == []
    rows = await _all_rows()
    assert [(row.status, row.upstream_status_code) for row in rows] == [("error", 429)]
    assert get_source_bulkhead().in_flight(scene.source_id) == 0


@pytest.mark.asyncio
async def test_bulkhead_loser_keeps_todays_429_without_a_second_dispatch(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CL-7: a fresh request losing the ``max_concurrency`` race declines to today's 429 and reserves nothing."""

    attempts = _forbid_subscription_stream(monkeypatch)
    hold = asyncio.Event()
    scene = await _exhausted_scene(
        async_client,
        source_upstream,
        tag="bulkhead",
        frames=[_created(), _ITEM_ADDED],
        hold=hold,
        after_hold=[_completed(_USAGE)],
        handler_cancellation=True,
        shutdown_timeout=1.0,
    )
    patched = await async_client.patch(f"/api/model-sources/{scene.source_id}", json={"maxConcurrency": 1})
    assert patched.status_code == 200, patched.text

    winner = _AsgiStream(
        app=_app(async_client),
        path=CODEX_ROUTE,
        headers=_native_headers("thr_bulkhead_winner"),
        body=json.dumps({**_codex_body(), "store": False}).encode(),
    )
    winner_runner = asyncio.create_task(winner.run())
    await winner.wait_for_text("response.output_item.added")
    assert get_source_bulkhead().in_flight(scene.source_id) == 1

    loser = await async_client.post(CODEX_ROUTE, json=_codex_body(), headers=_native_headers("thr_bulkhead_loser"))

    assert loser.status_code == 429
    assert loser.json() == _todays_429(scene.reset_at)
    assert len(scene.state.requests) == 1
    assert attempts == []

    hold.set()
    await asyncio.wait_for(winner_runner, timeout=10)
    await _drain(async_client)
    assert get_source_bulkhead().in_flight(scene.source_id) == 0
    rows = await _all_rows()
    assert [(row.status, row.source) for row in rows if row.model_source_id == scene.source_id] == [
        ("success", REQUEST_LOG_SOURCE_FRESH)
    ]
    pins = await _pin_rows()
    assert [pin.pin_key for pin in pins] == [thread_pin_key(_thread_key("thr_bulkhead_winner"))]


# -- abandonment (design §6.4, §13.4 a-e) on the overflow path -------------------------------------------


async def _wait_for_stub_request(state: _StubState) -> None:
    deadline = time.monotonic() + 5
    while not state.requests and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert state.requests, "the stub never received the open"


async def _wait_for_stub_cancel(state: _StubState) -> None:
    deadline = time.monotonic() + 5
    while state.cancelled == 0 and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert state.cancelled == 1, "the stub connection was not closed when the client left"


@pytest.mark.asyncio
async def test_abandonment_a_client_leaving_during_delayed_headers(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_subscription_stream(monkeypatch)
    delay_headers = asyncio.Event()
    scene = await _exhausted_scene(
        async_client,
        source_upstream,
        tag="abandon_a",
        delay_headers=delay_headers,
        handler_cancellation=True,
        shutdown_timeout=1.0,
    )
    stream = _AsgiStream(
        app=_app(async_client),
        path=CODEX_ROUTE,
        headers=_native_headers("thr_abandon_a"),
        body=json.dumps(_codex_body()).encode(),
    )
    runner = asyncio.create_task(stream.run())
    await _wait_for_stub_request(scene.state)
    left_at = time.monotonic()
    stream.disconnect()
    await asyncio.wait_for(runner, timeout=10)
    abandoned_after = time.monotonic() - left_at
    await _drain(async_client)
    delay_headers.set()

    assert abandoned_after < 2.0
    assert stream.chunks == []
    rows = await _all_rows()
    assert [(row.status, row.error_code, row.source) for row in rows] == [
        ("cancelled", "client_disconnected_during_open", REQUEST_LOG_SOURCE_FRESH)
    ]
    assert await _pin_rows() == []
    assert get_source_bulkhead().in_flight(scene.source_id) == 0
    await _wait_for_stub_cancel(scene.state)


@pytest.mark.asyncio
async def test_abandonment_b_disconnect_before_the_body_starts(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_subscription_stream(monkeypatch)
    hold = asyncio.Event()
    scene = await _exhausted_scene(
        async_client,
        source_upstream,
        tag="abandon_b",
        frames=[_created()],
        hold=hold,
        after_hold=[_ITEM_ADDED, _completed(_USAGE)],
        handler_cancellation=True,
        shutdown_timeout=1.0,
    )
    stream = _AsgiStream(
        app=_app(async_client),
        path=CODEX_ROUTE,
        headers=_native_headers("thr_abandon_b"),
        body=json.dumps(_codex_body()).encode(),
    )
    stream.disconnect()
    await asyncio.wait_for(stream.run(), timeout=10)
    await _drain(async_client)
    hold.set()

    rows = await _all_rows()
    assert len(rows) == 1
    assert rows[0].status == "cancelled"
    assert rows[0].error_code in {"client_disconnected_before_body", "client_disconnected"}
    assert rows[0].source == REQUEST_LOG_SOURCE_FRESH
    assert await _pin_rows() == []
    assert get_source_bulkhead().in_flight(scene.source_id) == 0
    await _wait_for_stub_cancel(scene.state)


@pytest.mark.asyncio
async def test_abandonment_c_client_leaving_after_the_stall_window(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.proxy import source_dispatch as dispatch_module

    monkeypatch.setattr(dispatch_module, "STALL_EVIDENCE_SECONDS", 0.3)
    _forbid_subscription_stream(monkeypatch)
    delay_headers = asyncio.Event()
    scene = await _exhausted_scene(
        async_client,
        source_upstream,
        tag="abandon_c",
        delay_headers=delay_headers,
        handler_cancellation=True,
        shutdown_timeout=1.0,
    )
    stream = _AsgiStream(
        app=_app(async_client),
        path=CODEX_ROUTE,
        headers=_native_headers("thr_abandon_c"),
        body=json.dumps(_codex_body()).encode(),
    )
    runner = asyncio.create_task(stream.run())
    await _wait_for_stub_request(scene.state)
    await asyncio.sleep(0.6)
    stream.disconnect()
    await asyncio.wait_for(runner, timeout=10)
    await _drain(async_client)
    delay_headers.set()

    rows = await _all_rows()
    assert [(row.status, row.error_code) for row in rows] == [("cancelled", "source_stall_abandoned")]
    assert get_source_bulkhead().in_flight(scene.source_id) == 0


@pytest.mark.asyncio
async def test_abandonment_d_cancellation_between_the_reservation_and_the_open(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_subscription_stream(monkeypatch)
    scene = await _exhausted_scene(async_client, source_upstream, tag="abandon_d")

    def interrupt(*args: object, **kwargs: object) -> dict[str, Any]:
        raise asyncio.CancelledError

    monkeypatch.setattr(proxy_api, "_shape_source_responses_payload", interrupt)
    stream = _AsgiStream(
        app=_app(async_client),
        path=CODEX_ROUTE,
        headers=_native_headers("thr_abandon_d"),
        body=json.dumps(_codex_body()).encode(),
    )
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(stream.run(), timeout=10)
    await _drain(async_client)

    assert scene.state.requests == []
    rows = await _all_rows()
    assert [(row.status, row.error_code, row.source) for row in rows] == [
        ("cancelled", "dispatch_interrupted", REQUEST_LOG_SOURCE_FRESH)
    ]
    assert get_source_bulkhead().in_flight(scene.source_id) == 0
    assert await _pin_rows() == []


@pytest.mark.asyncio
async def test_abandonment_e_sdk_client_leaving_after_the_heartbeat(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_subscription_stream(monkeypatch)
    hold = asyncio.Event()
    scene = await _exhausted_scene(
        async_client,
        source_upstream,
        tag="abandon_e",
        frames=[_created()],
        hold=hold,
        after_hold=[_ITEM_ADDED, _completed(_USAGE)],
        handler_cancellation=True,
        shutdown_timeout=1.0,
    )
    stream = _AsgiStream(
        app=_app(async_client),
        path=CODEX_ROUTE,
        headers={"user-agent": "python-httpx/0.28"},
        body=json.dumps(_codex_body()).encode(),
    )
    runner = asyncio.create_task(stream.run())
    await stream.wait_for_text("codex.keepalive")
    stream.disconnect()
    await asyncio.wait_for(runner, timeout=10)
    await _drain(async_client)
    hold.set()

    rows = await _all_rows()
    assert [(row.status, row.source) for row in rows] == [("cancelled", REQUEST_LOG_SOURCE_FRESH)]
    assert await _pin_rows() == []
    assert get_source_bulkhead().in_flight(scene.source_id) == 0


# -- anchors (SDK ``previous_response_id`` chains) -------------------------------------------------------


def _json_source_handler(state: _StubState, *, response_id: str) -> _UpstreamHandler:
    async def handler(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        state.requests.append(body)
        state.headers.append(dict(request.headers))
        if body.get("stream"):
            response = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            for frame in (_created(response_id), _ITEM_ADDED, _completed(_USAGE, response_id)):
                await response.write(frame)
            await response.write_eof()
            return response
        return web.json_response(
            {
                "id": response_id,
                "object": "response",
                "status": "completed",
                "output": [{"id": "msg_1", "type": "message", "role": "assistant", "content": []}],
                "usage": _USAGE,
            }
        )

    return handler


@pytest.mark.asyncio
async def test_anchor_row_follows_a_stored_sdk_response_and_routes_the_follow_up_to_the_source(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§7.2: ``store`` not ``false`` -> anchor row in the pin transaction; the chain returns to its source."""

    attempts = _forbid_subscription_stream(monkeypatch)
    state = _StubState()
    scene = await _exhausted_scene(
        async_client, source_upstream, tag="anchor", handler=_json_source_handler(state, response_id="resp_anchor_1")
    )
    scene.state = state

    # An SDK client that leaves ``store`` at the API default (stored) is anchored; Codex's explicit
    # ``store: false`` never is (``test_store_false_writes_no_anchor_row``).
    first = await async_client.post(V1_ROUTE, json={**_codex_body(), "stream": False})
    await _drain(async_client)
    assert first.status_code == 200, first.text
    assert first.json()["id"] == "resp_anchor_1"
    pins = await _pin_rows()
    assert [(pin.kind, pin.pin_key, pin.source_id) for pin in pins] == [
        (PIN_KIND_ANCHOR, anchor_pin_key(None, "resp_anchor_1"), scene.source_id)
    ]

    await _pool_is_healthy(async_client, tag="anchor")
    follow_up = await async_client.post(
        V1_ROUTE, json={**_codex_body(), "stream": False, "previous_response_id": "resp_anchor_1"}
    )
    await _drain(async_client)
    assert follow_up.status_code == 200, follow_up.text
    assert len(state.requests) == 2
    assert state.requests[1]["previous_response_id"] == "resp_anchor_1"
    # The ChatGPT-forced ``store: false`` never reaches the source: the client omitted ``store``, so the
    # source applies its default and the chain the anchor row points at actually exists there.
    assert all("store" not in sent for sent in state.requests), state.requests

    # Drain mode: the setting is cleared, anchored chains keep resolving to their source.
    await _designate(async_client, None)
    draining = await async_client.post(
        V1_ROUTE, json={**_codex_body(), "stream": False, "previous_response_id": "resp_anchor_1"}
    )
    await _drain(async_client)
    assert draining.status_code == 200, draining.text
    assert len(state.requests) == 3
    assert attempts == []
    rows = await _all_rows()
    assert [row.source for row in rows if row.model_source_id == scene.source_id] == [
        REQUEST_LOG_SOURCE_FRESH,
        REQUEST_LOG_SOURCE_PINNED,
        REQUEST_LOG_SOURCE_PINNED,
    ]


@pytest.mark.asyncio
async def test_store_false_writes_no_anchor_row(async_client, source_upstream, monkeypatch: pytest.MonkeyPatch) -> None:
    _forbid_subscription_stream(monkeypatch)
    state = _StubState()
    await _exhausted_scene(
        async_client,
        source_upstream,
        tag="no_anchor",
        handler=_json_source_handler(state, response_id="resp_no_anchor"),
    )

    response = await async_client.post(V1_ROUTE, json={**_codex_body(), "stream": False, "store": False})
    await _drain(async_client)

    assert response.status_code == 200, response.text
    assert await _pin_rows() == []
    assert state.requests[0]["store"] is False


@pytest.mark.asyncio
async def test_store_true_is_anchored_and_forwarded_verbatim(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit ``store: true`` survives the ChatGPT validator's ``False`` on the source body and anchors."""

    _forbid_subscription_stream(monkeypatch)
    state = _StubState()
    scene = await _exhausted_scene(
        async_client,
        source_upstream,
        tag="store_true",
        handler=_json_source_handler(state, response_id="resp_store_true"),
    )

    response = await async_client.post(V1_ROUTE, json={**_codex_body(), "stream": False, "store": True})
    await _drain(async_client)

    assert response.status_code == 200, response.text
    assert state.requests[0]["store"] is True
    pins = await _pin_rows()
    assert [(pin.kind, pin.pin_key, pin.source_id) for pin in pins] == [
        (PIN_KIND_ANCHOR, anchor_pin_key(None, "resp_store_true"), scene.source_id)
    ]


@pytest.mark.asyncio
async def test_unknown_previous_response_id_keeps_todays_fail_closed_answer(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = _forbid_subscription_stream(monkeypatch)
    reset_at = await _seed_exhausted_pool(async_client, tag="unknown_anchor")
    del reset_at
    body = {**_codex_body(), "stream": False, "previous_response_id": "resp_nobody_minted_this"}
    baseline = await async_client.post(V1_ROUTE, json=body)
    assert baseline.status_code != 200

    state = _StubState()
    base_url = await source_upstream(_json_source_handler(state, response_id="resp_unknown"))
    source_id = await _create_model_source(
        async_client, name="overflow-unknown-anchor", model=REGISTRY_SLUG, base_url=base_url, supports_responses=True
    )
    await _designate(async_client, source_id)

    designated = await async_client.post(V1_ROUTE, json=body)

    assert designated.status_code == baseline.status_code
    assert designated.content == baseline.content
    assert state.requests == []
    assert attempts == []


# -- coupling (CL-12): the database is a dependency while overflow is on --------------------------------


@pytest.mark.asyncio
async def test_database_outage_fails_thread_keyed_requests_closed_within_the_lookup_deadline(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = _forbid_subscription_stream(monkeypatch)
    scene = await _exhausted_scene(async_client, source_upstream, tag="db_down")

    async def hang(*_args: object, **_kwargs: object) -> object:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(pins_module, "_read_pin", hang)
    monkeypatch.setattr(pins_module, "_read_pins", hang, raising=False)

    started = time.monotonic()
    response = await async_client.post(CODEX_ROUTE, json=_codex_body(), headers=_native_headers("thr_db_down"))
    elapsed = time.monotonic() - started

    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == MODEL_SOURCE_UNAVAILABLE_CODE
    assert response.headers.get("retry-after") == "2"
    assert elapsed < pins_module.PIN_LOOKUP_DEADLINE_SECONDS + 2.0
    assert scene.state.requests == []
    assert attempts == []

    # No thread key and no ``previous_response_id``: the lookup is never mandated, the path is unchanged.
    await _pool_is_healthy(async_client, tag="db_down")
    relayed = _canned_subscription_stream(monkeypatch, response_id="resp_db_down_plain")
    plain = await async_client.post(V1_ROUTE, json=_codex_body(), headers={"user-agent": "openai-python/1.99"})
    assert plain.status_code == 200, plain.text
    assert len(relayed) == 1


# -- decision 78: an owed anchor must be durable before the first content frame -----------------------------


def _release_hold_on_pin_commit(monkeypatch: pytest.MonkeyPatch, event: asyncio.Event) -> None:
    """Let the stub's terminal frame out once the content-trigger pin write has committed.

    The frame split is what matters: while ``event`` is unset the stub has
    written the content frame and nothing else, so the pin hook runs with the
    source id still unminted. Gating the release on ``commit`` itself -- rather
    than on a wall-clock delay a slow fixture setup could outrun, which would
    hand the hook an already-minted id and silently retire the case -- makes
    that ordering deterministic. The release only keeps the *previous*
    behaviour terminating (an unanchored delivery would otherwise wait out the
    idle budget); on this head the turn is already refused when it fires.
    """

    commit = overflow_module.OverflowPinExecutor.commit

    async def release_after_commit(
        self: overflow_module.OverflowPinExecutor, *args: Any, **kwargs: Any
    ) -> PinWriteOutcome:
        try:
            return await commit(self, *args, **kwargs)
        finally:
            event.set()

    monkeypatch.setattr(overflow_module.OverflowPinExecutor, "commit", release_after_commit)


@pytest.mark.asyncio
async def test_sdk_turn_whose_source_mints_its_id_after_the_first_content_frame_fails_closed(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Decision 78: ``store`` omitted + an id that only arrives in the terminal -> the synthesized pair, no rows.

    The keyless SDK shape is the one with no other durable evidence: its
    anchor is the whole pin transaction, so before this rule the turn was
    delivered with zero rows in ``model_source_pins`` while ``commit``
    reported ``written``, and the ``resp_late`` id the client then chained on
    resolved nowhere.
    """

    attempts = _forbid_subscription_stream(monkeypatch)
    counter = _spy_overflow_counter(monkeypatch)
    hold = asyncio.Event()
    scene = await _exhausted_scene(
        async_client,
        source_upstream,
        tag="anchor_late",
        frames=[_DELTA],
        hold=hold,
        after_hold=[_completed(_USAGE, "resp_late")],
        handler_cancellation=True,
        shutdown_timeout=1.0,
    )
    _release_hold_on_pin_commit(monkeypatch, hold)
    try:
        response = await async_client.post(V1_ROUTE, json=_codex_body(), headers={"user-agent": "openai-python/1.99"})
    finally:
        hold.set()  # safety net: unblock the stub even if the commit is never reached
    await _drain(async_client)

    assert response.status_code == 200, response.text  # the lifecycle is carried by the SSE terminal
    events = _events(response.text)
    assert [event["type"] for event in events] == ["response.created", "response.failed"]
    assert events[1]["response"]["error"]["code"] == PIN_UNAVAILABLE_CODE
    assert "hello from the source" not in response.text, "no source content may reach an unanchored client"
    assert await _pin_rows() == []
    rows = await _all_rows()
    assert [(row.status, row.error_code, row.source) for row in rows] == [
        ("error", PIN_UNAVAILABLE_CODE, REQUEST_LOG_SOURCE_FRESH)
    ]
    assert counter.outcomes == [
        (ROUTE_V1_RESPONSES, "dispatched_fresh"),
        (ROUTE_V1_RESPONSES, "pin_commit_failed"),
    ]
    assert get_source_bulkhead().in_flight(scene.source_id) == 0
    assert attempts == []


@pytest.mark.asyncio
async def test_native_store_false_turn_pins_its_thread_without_any_source_response_id(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Over-reach guard: ``store: false`` owes no anchor, so an id-less content frame is still delivered.

    This is the contract the fail-closed rule must not touch -- every native
    Codex turn reaches its content trigger with the thread pin as its only
    write, and that write is legal on its own (design §6.3, §7.2).
    """

    attempts = _forbid_subscription_stream(monkeypatch)
    thread_id = "thr_native_idless"
    hold = asyncio.Event()
    scene = await _exhausted_scene(
        async_client,
        source_upstream,
        tag="native_idless",
        frames=[_DELTA],
        hold=hold,
        after_hold=[_completed(_USAGE, "resp_native_late")],
    )
    _release_hold_on_pin_commit(monkeypatch, hold)
    try:
        response = await async_client.post(
            CODEX_ROUTE, json={**_codex_body(), "store": False}, headers=_native_headers(thread_id)
        )
    finally:
        hold.set()  # safety net: unblock the stub even if the commit is never reached
    await _drain(async_client)

    assert response.status_code == 200, response.text
    events = _events(response.text)
    assert [event["type"] for event in events] == ["response.output_text.delta", "response.completed"]
    assert "hello from the source" in response.text
    assert [(pin.kind, pin.pin_key, pin.source_id) for pin in await _pin_rows()] == [
        (PIN_KIND_THREAD, thread_pin_key(_thread_key(thread_id)), scene.source_id)
    ]
    rows = await _all_rows()
    assert [(row.status, row.source) for row in rows] == [("success", REQUEST_LOG_SOURCE_FRESH)]
    assert attempts == []


@pytest.mark.asyncio
async def test_anchored_continuation_with_a_late_source_id_fails_closed_and_keeps_its_anchor(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``pinned``/``anchored`` continuation writes nothing but still owes the new turn's anchor."""

    attempts = _forbid_subscription_stream(monkeypatch)
    hold = asyncio.Event()
    scene = await _exhausted_scene(
        async_client,
        source_upstream,
        tag="anchor_chain",
        frames=[_DELTA],
        hold=hold,
        after_hold=[_completed(_USAGE, "resp_chain_late")],
        handler_cancellation=True,
        shutdown_timeout=1.0,
    )
    await _write_pin(anchor_pin_key(None, "resp_prev"), kind=PIN_KIND_ANCHOR, source_id=scene.source_id)
    await _pool_is_healthy(async_client, tag="anchor_chain")
    _release_hold_on_pin_commit(monkeypatch, hold)
    try:
        response = await async_client.post(
            V1_ROUTE,
            json={**_codex_body(), "previous_response_id": "resp_prev"},
            headers={"user-agent": "openai-python/1.99"},
        )
    finally:
        hold.set()  # safety net: unblock the stub even if the commit is never reached
    await _drain(async_client)

    events = _events(response.text)
    assert [event["type"] for event in events] == ["response.created", "response.failed"]
    assert events[1]["response"]["error"]["code"] == PIN_UNAVAILABLE_CODE
    assert "hello from the source" not in response.text
    # The continuation's own evidence survives a refused turn; no new anchor was minted.
    assert [(pin.kind, pin.pin_key) for pin in await _pin_rows()] == [
        (PIN_KIND_ANCHOR, anchor_pin_key(None, "resp_prev"))
    ]
    rows = await _all_rows()
    assert [(row.status, row.error_code, row.source) for row in rows] == [
        ("error", PIN_UNAVAILABLE_CODE, REQUEST_LOG_SOURCE_PINNED)
    ]
    assert attempts == []


@pytest.mark.asyncio
async def test_non_stream_answer_without_an_id_is_refused_instead_of_delivered_unanchored(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§6.5: the non-stream site resolves the same intent, so it inherits the refusal (no route change)."""

    attempts = _forbid_subscription_stream(monkeypatch)
    state = _StubState()

    async def idless_json(request: web.Request) -> web.StreamResponse:
        state.requests.append(await request.json())
        return web.json_response(
            {
                "object": "response",
                "status": "completed",
                "output": [{"id": "msg_1", "type": "message", "role": "assistant", "content": []}],
                "usage": _USAGE,
            }
        )

    await _exhausted_scene(async_client, source_upstream, tag="idless_json", handler=idless_json)

    response = await async_client.post(V1_ROUTE, json={**_codex_body(), "stream": False})
    await _drain(async_client)

    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == PIN_UNAVAILABLE_CODE
    assert response.headers.get("retry-after") == "2"
    assert len(state.requests) == 1
    assert await _pin_rows() == []
    rows = await _all_rows()
    assert [(row.status, row.error_code, row.source) for row in rows] == [
        ("error", PIN_UNAVAILABLE_CODE, REQUEST_LOG_SOURCE_FRESH)
    ]
    assert attempts == []
