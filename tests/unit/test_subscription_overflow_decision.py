"""Route-admission overflow decision (#2123 WP-C2, design v3 §4.2, §7.2, §7.3, §8.1, §8.7; owner decisions 2026-09-09).

Everything runs without a database: the settings cache, the bounded pin
lookups, the source selection and the unservable-cause probe are fakes on the
module's own seams; the exhaustion probe is the real one against a fake
admission service; the breaker, bulkhead and fast-decline set are real and
driven by a ``VirtualClock``. Covered: the ship-dark structural guarantee
(both columns off => the coroutine completes without suspending and touches
nothing), the decision table for every decline reason, the ordering rules
(pins before turn-state, anchors before the drain check, portability before
claims), drain mode, ``CancelledError`` holding nothing, the fast-decline
window, pinned/anchored dispatch with the 400/503 split and the neutral
release, the compact and handshake pin checks, the hint, and the named mutants
of §13.5 (allowlist->denylist, walk before probe, probe without the tier,
negative cache / trusting the positive cache on release, anchor for
``store:false``, pinned ignores the open breaker, serve before the delete
commits).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi.responses import JSONResponse
from starlette.requests import Request

from app.core.balancer import USAGE_LIMIT_REACHED
from app.core.openai.model_registry import MODEL_SOURCE_KIND_OPENAI_COMPATIBLE
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.core.types import JsonValue
from app.core.utils.time import utcnow
from app.db.models import ModelSource, ModelSourceModel
from app.modules.api_keys.service import TRAFFIC_CLASS_OPPORTUNISTIC, ApiKeyData
from app.modules.proxy import overflow as overflow_module
from app.modules.proxy import source_admission as admission_module
from app.modules.proxy.affinity import _CodexBackendIdentity
from app.modules.proxy.load_balancer import AccountSelection
from app.modules.proxy.model_source_pins import (
    PIN_KIND_ANCHOR,
    PIN_KIND_BOUNCE,
    PIN_KIND_THREAD,
    PIN_TOUCH_INTERVAL_SECONDS,
    PinCache,
    PinLookupResult,
    PinLookupTimeout,
    PinRecord,
    anchor_pin_key,
    bounce_pin_key,
    thread_pin_key,
)
from app.modules.proxy.overflow import (
    BREAKER_OPEN_SECONDS,
    COMPACT_DENIAL_MESSAGE,
    DISPATCH_KIND_ANCHOR,
    DISPATCH_KIND_FRESH,
    DISPATCH_KIND_PINNED,
    HANDSHAKE_DENIAL_CODE,
    HINT_HEADER,
    HINT_NATIVE_TEXT,
    HINT_SDK_SENTENCE,
    HINT_STATE_ATTRIBUTE,
    MODEL_SOURCE_BUSY_CODE,
    MODEL_SOURCE_UNAVAILABLE_CODE,
    OVERFLOW_OUTCOMES,
    PIN_FAILURE_FAST_DECLINE_SECONDS,
    PIN_UNAVAILABLE_CODE,
    PIN_UNVERIFIED_CODE,
    REQUEST_LOG_SOURCE_FRESH,
    REQUEST_LOG_SOURCE_PINNED,
    ROUTE_CODEX_RESPONSES,
    ROUTE_COMPACT,
    ROUTE_V1_RESPONSES,
    ROUTE_WEBSOCKET_HANDSHAKE,
    SOURCE_UNAVAILABLE_CODE,
    UNSUPPORTED_INPUT_CODE,
    DeclineReason,
    FastDeclineSet,
    OverflowDispatch,
    OverflowFinishedHook,
    PinToucher,
    SourceBreaker,
    apply_usage_limit_hint,
    background_job_allowed,
    compact_pin_denial,
    handshake_denial,
    overflow_thread_key,
    record_overflow_outcome,
    record_overflow_transport_decision,
    resolve_subscription_overflow,
)
from app.modules.proxy.source_admission import SourceAdmission, SourceBulkhead
from app.modules.settings.subscription_overflow import PIN_IDLE_TTL, PIN_TOMBSTONE_GRACE
from tests.simulation.virtual_time import VirtualClock, VirtualScheduler

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
SRC = "src_overflow"
MODEL = "gpt-5.5"
THREAD_ID = "thread-7f3a"
RESETS_AT = 1_700_001_800
NATIVE = {"originator": "codex_cli_rs", "thread-id": THREAD_ID, "session-id": "sess-1"}
NATIVE_NO_THREAD = {"originator": "codex_cli_rs", "session-id": "sess-1"}
SDK = {"user-agent": "openai-python/1.0"}
LOGGER = "app.modules.proxy.overflow"


# --- doubles -----------------------------------------------------------------------------------


def _request(headers: Mapping[str, str] | None = None, *, path: str = "/backend-api/codex/responses") -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()],
        "client": ("203.0.113.9", 54321),
        "asgi": {"version": "3.0", "spec_version": "2.3"},
    }
    return Request(scope)


def _user(text: str) -> dict[str, JsonValue]:
    return {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}


def _payload(*, store: bool | None = False, **overrides: JsonValue) -> ResponsesRequest:
    body: dict[str, JsonValue] = {
        "model": MODEL,
        "instructions": "You are Codex.",
        "input": [_user("hello")],
        "tools": [{"type": "function", "name": "shell", "parameters": {"type": "object", "properties": {}}}],
        "tool_choice": "auto",
        "parallel_tool_calls": False,
        "reasoning": {"effort": "medium", "summary": "auto"},
        "include": ["reasoning.encrypted_content"],
        "stream": True,
        "service_tier": "priority",
        "prompt_cache_key": "cache-1",
        "client_metadata": {"session_id": "sess-1", "thread_id": THREAD_ID},
    }
    if store is not None:
        body["store"] = store
    body.update(overrides)
    return ResponsesRequest.model_validate(body)


def _source(source_id: str = SRC, *, max_concurrency: int | None = None, model: str = MODEL) -> ModelSource:
    return ModelSource(
        id=source_id,
        name="Overflow",
        kind=MODEL_SOURCE_KIND_OPENAI_COMPATIBLE,
        base_url="http://127.0.0.1:8000/v1",
        is_enabled=True,
        supports_chat_completions=True,
        supports_responses=True,
        max_concurrency=max_concurrency,
        models=[ModelSourceModel(model=model, is_enabled=True, supports_streaming=True, supports_tools=True)],
    )


def _api_key(**overrides: object) -> ApiKeyData:
    fields: dict[str, object] = {
        "id": "key_1",
        "name": "key",
        "key_prefix": "sk-key",
        "allowed_models": None,
        "enforced_model": None,
        "enforced_reasoning_effort": None,
        "enforced_service_tier": None,
        "expires_at": None,
        "is_active": True,
        "created_at": utcnow(),
        "last_used_at": None,
    }
    fields.update(overrides)
    return ApiKeyData(**cast(dict, fields))


def _thread_key(thread_id: str = THREAD_ID) -> str:
    key = _CodexBackendIdentity(process_session=None, thread_id=thread_id).thread_selection_key
    assert key is not None
    return key


def _pin_record(
    pin_key: str,
    *,
    now: datetime,
    source_id: str = SRC,
    kind: str = PIN_KIND_THREAD,
    expired: bool = False,
    last_seen_at: datetime | None = None,
    api_key_id: str | None = None,
) -> PinRecord:
    last_seen = (
        last_seen_at if last_seen_at is not None else (now - PIN_IDLE_TTL - timedelta(hours=1) if expired else now)
    )
    return PinRecord(
        pin_key=pin_key,
        kind=kind,
        source_id=source_id,
        api_key_id=api_key_id,
        created_at=last_seen,
        last_seen_at=last_seen,
        expires_at=last_seen + PIN_IDLE_TTL,
        purge_at=last_seen + PIN_IDLE_TTL + PIN_TOMBSTONE_GRACE,
    )


def _exhausted() -> AccountSelection:
    return AccountSelection(
        account=None,
        error_message="Rate limit exceeded. Try again in 30m",
        error_code=USAGE_LIMIT_REACHED,
        resets_at=RESETS_AT,
    )


def _healthy() -> AccountSelection:
    return AccountSelection(account=None, error_message="No active accounts available", error_code="no_accounts")


class _Service:
    """Partial ``ProxyService``: the admission probe, the seams and the cleanup scheduler."""

    def __init__(self, selection: AccountSelection, *, clock: VirtualClock, scheduler: VirtualScheduler) -> None:
        self._clock = clock
        self._scheduler = scheduler
        self.selection = selection
        self.admission_calls: list[dict[str, object]] = []
        self.cleanup_actions: list[str] = []

    async def check_opportunistic_admission(self, **kwargs: object) -> AccountSelection:
        self.admission_calls.append(kwargs)
        return self.selection

    def _schedule_cancel_safe_cleanup(self, coro: Any, *, action: str, request_id: str) -> None:
        self.cleanup_actions.append(action)
        coro.close()


class _Pins:
    """``lookup_pin_bounded`` double: answers per pin key; ``uncached`` answers only the ``cache=None`` re-read."""

    def __init__(self) -> None:
        self.answers: dict[str, PinLookupResult] = {}
        self.uncached: dict[str, PinLookupResult] = {}
        self.calls: list[tuple[str, bool]] = []
        self.error: Exception | None = None
        self.hang = False

    def live(self, record: PinRecord) -> None:
        self.answers[record.pin_key] = PinLookupResult("live", record)

    def expired(self, record: PinRecord) -> None:
        self.answers[record.pin_key] = PinLookupResult("expired", record)

    async def lookup(
        self, pin_key: str, *, cache: PinCache | None, scheduler: object, clock: object
    ) -> PinLookupResult:
        self.calls.append((pin_key, cache is not None))
        if self.hang:
            await asyncio.Event().wait()
        if self.error is not None:
            raise self.error
        if cache is None and pin_key in self.uncached:
            return self.uncached[pin_key]
        return self.answers.get(pin_key, PinLookupResult("none", None))

    async def lookup_many(
        self, pin_keys: Any, *, cache: PinCache | None, scheduler: object, clock: object
    ) -> dict[str, PinLookupResult]:
        results: dict[str, PinLookupResult] = {}
        for pin_key in pin_keys:
            results[pin_key] = await self.lookup(pin_key, cache=cache, scheduler=scheduler, clock=clock)
        return results


class _Executor:
    def __init__(self) -> None:
        self.delete_outcome = "written"
        self.deleted: list[tuple[str, bool]] = []

    async def delete_durably(self, pin_key: str, *, scheduler: object, cache: PinCache | None) -> str:
        self.deleted.append((pin_key, cache is not None))
        return self.delete_outcome

    async def commit(self, intent: object, *, drain_until: object, scheduler: object, clock: object) -> str:
        raise AssertionError("the decision never commits a pin")


class _Metrics:
    def __init__(self) -> None:
        self.outcomes: list[tuple[str, str]] = []
        self._pending: tuple[str, str] | None = None

    def labels(self, **labels: str) -> _Metrics:
        self._pending = (labels["route"], labels["outcome"])
        return self

    def inc(self, amount: float = 1) -> None:
        assert self._pending is not None
        self.outcomes.append(self._pending)


@dataclass
class _Env:
    clock: VirtualClock
    scheduler: VirtualScheduler
    service: _Service
    breaker: SourceBreaker
    bulkhead: SourceBulkhead
    fast_decline: FastDeclineSet
    cache: PinCache
    executor: _Executor
    toucher: PinToucher
    pins: _Pins
    metrics: _Metrics
    sources: dict[str, ModelSource] = field(default_factory=dict)
    select_calls: list[dict[str, object]] = field(default_factory=list)
    unservable_cause: str = "source_disabled"
    probe_calls: int = 0
    view_calls: int = 0
    claim_calls: int = 0
    dump_calls: int = 0
    cache_gets: int = 0
    settings_row: Any = None

    @property
    def context(self) -> Any:
        return SimpleNamespace(service=self.service)

    def settings(self, designated: str | None, drain_until: datetime | None = None) -> None:
        self.settings_row = SimpleNamespace(
            subscription_overflow_source_id=designated,
            subscription_overflow_drain_until=None if drain_until is None else drain_until.replace(tzinfo=None),
            routing_strategy="capacity_weighted",
            single_account_id=None,
        )

    @property
    def outcomes(self) -> list[str]:
        return [outcome for _route, outcome in self.metrics.outcomes]

    def open_breaker(self) -> None:
        for _ in range(3):
            token = self.breaker.claim(SRC, self.clock.monotonic())
            assert token is not None
            token.settle("failure")
        assert self.breaker.state(SRC, self.clock.monotonic()) == "open"

    async def resolve(
        self,
        headers: Mapping[str, str] = NATIVE,
        payload: ResponsesRequest | None = None,
        api_key: ApiKeyData | None = None,
        *,
        request: Request | None = None,
        source_route_excluded: bool = False,
        route: str = ROUTE_CODEX_RESPONSES,
        raw_model: str | None = None,
        require_streaming: bool = True,
        direct_source: ModelSource | None = None,
    ) -> OverflowDispatch | JSONResponse | None:
        result = await resolve_subscription_overflow(
            request or _request(headers),
            payload if payload is not None else _payload(),
            self.context,
            api_key,
            raw_model=raw_model,
            require_streaming=require_streaming,
            source_route_excluded=source_route_excluded,
            route=route,
            direct_source=direct_source,
        )
        return cast("OverflowDispatch | JSONResponse | None", result)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> _Env:
    clock = VirtualClock(epoch_value=_T0.timestamp())
    scheduler = VirtualScheduler(clock)
    service = _Service(_exhausted(), clock=clock, scheduler=scheduler)
    bulkhead = SourceBulkhead()
    monkeypatch.setattr(admission_module, "_BULKHEAD", bulkhead)
    env = _Env(
        clock=clock,
        scheduler=scheduler,
        service=service,
        breaker=SourceBreaker(clock=clock),
        bulkhead=bulkhead,
        fast_decline=FastDeclineSet(),
        cache=PinCache(),
        executor=_Executor(),
        toucher=PinToucher(),
        pins=_Pins(),
        metrics=_Metrics(),
    )
    env.sources[SRC] = _source()
    env.settings(SRC)

    class _Cache:
        async def get(self) -> Any:
            env.cache_gets += 1
            return env.settings_row

    real_probe = overflow_module.probe_pool_usage_exhaustion
    real_view = overflow_module.overflow_portability_view
    real_claim = overflow_module.try_claim_overflow
    real_dump = ResponsesRequest.model_dump_for_forwarding

    async def probe(*args: Any, **kwargs: Any) -> Any:
        env.probe_calls += 1
        return await real_probe(*args, **kwargs)

    def view(body: Any) -> Any:
        env.view_calls += 1
        return real_view(body)

    def claim(*args: Any, **kwargs: Any) -> Any:
        env.claim_calls += 1
        return real_claim(*args, **kwargs)

    def dump(self: ResponsesRequest) -> Any:
        env.dump_calls += 1
        return real_dump(self)

    async def select(
        source_id: str, model: str, api_key: ApiKeyData | None, *, raw_model: str | None, require_streaming: bool
    ):
        env.select_calls.append(
            {
                "source_id": source_id,
                "model": model,
                "api_key": api_key,
                "raw_model": raw_model,
                "require_streaming": require_streaming,
            }
        )
        source = env.sources.get(source_id)
        if source is None or not any(entry.model == model and entry.is_enabled for entry in source.models):
            return None
        return source, model

    async def unservable_cause(source_id: str) -> str:
        return env.unservable_cause

    monkeypatch.setattr(overflow_module, "get_settings_cache", lambda: _Cache())
    monkeypatch.setattr(overflow_module, "probe_pool_usage_exhaustion", probe)
    monkeypatch.setattr(overflow_module, "overflow_portability_view", view)
    monkeypatch.setattr(overflow_module, "try_claim_overflow", claim)
    monkeypatch.setattr(ResponsesRequest, "model_dump_for_forwarding", dump)
    monkeypatch.setattr(overflow_module, "select_overflow_model_source", select)
    monkeypatch.setattr(overflow_module, "_source_unservable_cause", unservable_cause)
    monkeypatch.setattr(overflow_module, "lookup_pin_bounded", env.pins.lookup)
    monkeypatch.setattr(overflow_module, "lookup_pins_bounded", env.pins.lookup_many)
    monkeypatch.setattr(overflow_module, "get_source_breaker", lambda: env.breaker)
    monkeypatch.setattr(overflow_module, "get_fast_decline_set", lambda: env.fast_decline)
    monkeypatch.setattr(overflow_module, "get_overflow_pin_executor", lambda: env.executor)
    monkeypatch.setattr(overflow_module, "get_pin_cache", lambda: env.cache)
    monkeypatch.setattr(overflow_module, "get_pin_toucher", lambda: env.toucher)
    monkeypatch.setattr(overflow_module, "subscription_overflow_total", env.metrics)
    monkeypatch.setattr(overflow_module, "_warned_at", {})
    return env


def _assert_nothing_claimed(env: _Env) -> None:
    assert env.bulkhead.in_flight(SRC) == 0
    assert env.breaker.claim(SRC, env.clock.monotonic()) is not None or env.breaker.is_open(SRC, env.clock.monotonic())


# --- ship-dark ------------------------------------------------------------------------------------


def test_both_columns_null_completes_without_suspending_and_calls_nothing(env: _Env) -> None:
    """I9: two attribute reads + one comparison on the warm settings row; the coroutine never yields."""

    env.settings(None, None)
    request = _request(NATIVE)
    coro = resolve_subscription_overflow(
        request,
        _payload(),
        env.context,
        _api_key(),
        raw_model=None,
        require_streaming=True,
        source_route_excluded=False,
        route=ROUTE_CODEX_RESPONSES,
    )
    with pytest.raises(StopIteration) as stop:
        coro.send(None)
    assert stop.value.value is None
    assert env.cache_gets == 1
    assert env.probe_calls == 0 and env.service.admission_calls == []
    assert env.pins.calls == []
    assert env.select_calls == []
    assert env.view_calls == 0 and env.dump_calls == 0 and env.claim_calls == 0
    assert env.metrics.outcomes == []
    assert getattr(request.state, HINT_STATE_ATTRIBUTE, None) is None


@pytest.mark.asyncio
async def test_expired_drain_deadline_is_the_fast_path_too(env: _Env) -> None:
    env.settings(None, _T0 - timedelta(seconds=1))
    assert await env.resolve() is None
    assert env.pins.calls == [] and env.probe_calls == 0 and env.metrics.outcomes == []


@pytest.mark.asyncio
async def test_compact_and_handshake_checks_are_dark_without_a_designation(env: _Env) -> None:
    env.settings(None, None)
    compact = ResponsesCompactRequest.model_validate({"model": MODEL, "instructions": "x", "input": [_user("hi")]})
    assert await compact_pin_denial(_request(NATIVE), compact, context=env.context) is None
    assert await handshake_denial(NATIVE, context=env.context) is None
    assert env.pins.calls == [] and env.metrics.outcomes == []


# --- fresh dispatch --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fresh_dispatch_shape_claims_last_and_owner_kwargs(env: _Env) -> None:
    api_key = _api_key()
    result = await env.resolve(api_key=api_key, raw_model="gpt-5.5")

    assert isinstance(result, OverflowDispatch)
    assert result.kind == DISPATCH_KIND_FRESH
    assert result.source is env.sources[SRC] and result.model == MODEL
    assert result.thread_key == _thread_key()
    assert result.resets_at == RESETS_AT and result.selection is env.service.selection
    assert result.route == ROUTE_CODEX_RESPONSES and result.drain_until is None
    assert result.request_log_source == REQUEST_LOG_SOURCE_FRESH
    # Claims are held for the route helper / owner (I13).
    assert isinstance(result.claims, SourceAdmission) and env.bulkhead.in_flight(SRC) == 1
    assert result.claims.trial is not None and result.claims.owner is None
    # Thread pin intent for the content trigger; Codex sent ``store: false`` -> never anchored.
    assert [(write.pin_key, write.kind, write.source_id, write.api_key_id) for write in result.pin_intent.writes] == [
        (thread_pin_key(_thread_key()), PIN_KIND_THREAD, SRC, "key_1")
    ]
    assert result.pin_intent.thread_key == _thread_key()
    assert result.pin_intent.anchor is False and result.pin_intent.source_id == SRC
    assert result.pin_executor is env.executor
    # Source body: telemetry and the tier stripped, the cache key verbatim (P9, P10).
    assert "client_metadata" not in result.body and "service_tier" not in result.body
    assert result.body["prompt_cache_key"] == "cache-1" and result.body["model"] == MODEL
    assert result.owner_kwargs() == {
        "request_log_source": REQUEST_LOG_SOURCE_FRESH,
        "dispatch_kind": DISPATCH_KIND_FRESH,
        "pin_intent": result.pin_intent,
        "pin_executor": env.executor,
        "drain_until": None,
        "pin_failure_error_code": PIN_UNAVAILABLE_CODE,
        "pin_unverified_error_code": PIN_UNVERIFIED_CODE,
        "on_finished": OverflowFinishedHook(ROUTE_CODEX_RESPONSES),
    }
    assert env.metrics.outcomes == [(ROUTE_CODEX_RESPONSES, "dispatched_fresh")]
    # The probe asked the real question with the step-1 settings snapshot (mutant: probe without service_tier).
    assert env.probe_calls == 1
    assert env.service.admission_calls == [
        {"api_key": api_key, "model": MODEL, "service_tier": "priority", "lease_kind": None, "observe_only": True}
    ]
    assert env.select_calls == [
        {"source_id": SRC, "model": MODEL, "api_key": api_key, "raw_model": "gpt-5.5", "require_streaming": True}
    ]
    result.claims.release_if_unowned()
    assert env.bulkhead.in_flight(SRC) == 0


@pytest.mark.asyncio
async def test_sdk_request_without_thread_key_dispatches_and_anchors_when_store_is_omitted(env: _Env) -> None:
    result = await env.resolve(SDK, _payload(store=None), route=ROUTE_V1_RESPONSES)
    assert isinstance(result, OverflowDispatch)
    assert result.thread_key is None and result.pin_intent.writes == ()
    assert result.pin_intent.anchor is True and result.pin_intent.anchor_api_key_id is None
    assert env.metrics.outcomes == [(ROUTE_V1_RESPONSES, "dispatched_fresh")]
    result.claims.release_if_unowned()


@pytest.mark.asyncio
async def test_sdk_store_false_never_anchors(env: _Env) -> None:
    """Mutant: anchor for ``store:false``."""

    result = await env.resolve(SDK, _payload(store=False), route=ROUTE_V1_RESPONSES)
    assert isinstance(result, OverflowDispatch)
    assert result.pin_intent.anchor is False
    result.claims.release_if_unowned()


@pytest.mark.asyncio
async def test_pool_not_exhausted_returns_none_before_walking_the_body(env: _Env) -> None:
    """Mutant: walk before probe."""

    env.service.selection = _healthy()
    assert await env.resolve() is None
    assert env.probe_calls == 1
    assert env.select_calls == [] and env.dump_calls == 0 and env.view_calls == 0 and env.claim_calls == 0
    assert env.metrics.outcomes == []
    _assert_nothing_claimed(env)


# --- decision table ------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reason", "headers", "api_key", "source_route_excluded", "arrange"),
    [
        ("pin_commit_recent_failure", NATIVE, None, False, "fast_decline"),
        ("turn_state_bound", {**NATIVE, "x-codex-turn-state": "client-turn-7f3a"}, None, False, None),
        ("opportunistic", NATIVE, _api_key(traffic_class=TRAFFIC_CLASS_OPPORTUNISTIC), False, None),
        ("key_scope", NATIVE, _api_key(source_assignment_scope_enabled=True, assigned_source_ids=[SRC]), False, None),
        ("no_thread_key", NATIVE_NO_THREAD, None, False, None),
        ("background_job", {**NATIVE, "x-openai-subagent": "memory_consolidation"}, None, False, None),
        ("background_job", {**NATIVE, "x-openai-subagent": "guardian"}, None, False, None),
        (
            "background_job",
            {**NATIVE, "x-openai-subagent": "review", "x-openai-memgen-request": "true"},
            None,
            False,
            None,
        ),
        ("source_excluded", NATIVE, None, True, None),
        ("breaker_open", NATIVE, None, False, "open_breaker"),
    ],
)
async def test_o1_declines_return_none_before_probing(
    env: _Env,
    reason: DeclineReason,
    headers: Mapping[str, str],
    api_key: ApiKeyData | None,
    source_route_excluded: bool,
    arrange: str | None,
) -> None:
    if arrange == "fast_decline":
        env.fast_decline.mark(_thread_key(), env.clock.monotonic())
    elif arrange == "open_breaker":
        env.open_breaker()
    assert await env.resolve(headers, api_key=api_key, source_route_excluded=source_route_excluded) is None
    assert env.outcomes == [f"declined_{reason}"]
    assert env.probe_calls == 0 and env.dump_calls == 0 and env.claim_calls == 0
    assert env.bulkhead.in_flight(SRC) == 0


@pytest.mark.asyncio
async def test_synthesized_turn_state_is_not_binding(env: _Env) -> None:
    result = await env.resolve({**NATIVE, "x-codex-turn-state": "turn_" + "0123456789abcdef" * 2})
    assert isinstance(result, OverflowDispatch)
    result.claims.release_if_unowned()


@pytest.mark.asyncio
async def test_allowlisted_background_jobs_overflow(env: _Env) -> None:
    for label in ("review", "compact", "collab_spawn"):
        result = await env.resolve({**NATIVE, "x-openai-subagent": label})
        assert isinstance(result, OverflowDispatch), label
        result.claims.release_if_unowned()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cause", "reason"),
    [("source_disabled", "no_source"), ("source_deleted", "no_source"), ("model_unlisted", "model_unlisted")],
)
async def test_unresolvable_designated_source_declines_with_a_configuration_warning(
    env: _Env, cause: str, reason: str, caplog
) -> None:
    env.sources.clear()
    env.unservable_cause = cause
    caplog.set_level(logging.WARNING, logger=LOGGER)
    assert await env.resolve() is None
    assert env.outcomes == [f"declined_{reason}"]
    assert env.probe_calls == 1 and env.dump_calls == 0 and env.claim_calls == 0
    assert f"reason={reason} detail={cause}:{MODEL}" in caplog.text


@pytest.mark.asyncio
async def test_configuration_warning_is_rate_limited_per_source_and_reason(env: _Env, caplog) -> None:
    env.sources.clear()
    caplog.set_level(logging.WARNING, logger=LOGGER)
    await env.resolve()
    await env.resolve()
    assert caplog.text.count("reason=no_source") == 1
    env.clock.advance(overflow_module.CONFIGURATION_WARN_INTERVAL_SECONDS)
    await env.resolve()
    assert caplog.text.count("reason=no_source") == 2


@pytest.mark.asyncio
async def test_not_portable_history_declines_with_the_hint_only(env: _Env) -> None:
    request = _request(NATIVE)
    payload = _payload(input=[_user("hello"), {"type": "reasoning", "id": "rs_1", "encrypted_content": "..."}])
    assert await env.resolve(request=request, payload=payload) is None
    assert env.outcomes == ["declined_not_portable_history"]
    assert getattr(request.state, HINT_STATE_ATTRIBUTE, None) == "not_portable_history"
    assert env.claim_calls == 0 and env.bulkhead.in_flight(SRC) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        _payload(unknown_field=1),
        _payload(tools=[{"type": "namespace", "name": "collaboration"}]),
        _payload(input=[{"type": "additional_tools", "tools": []}, _user("hi")]),
    ],
    ids=["unknown_field", "namespace_tool", "lite_bundle"],
)
async def test_other_portability_classes_decline_as_not_portable_input_without_a_hint(
    env: _Env, payload: ResponsesRequest, caplog
) -> None:
    """Mutant: hint on ``not_portable_tools`` / a Lite body declined as history."""

    request = _request(NATIVE)
    caplog.set_level(logging.WARNING, logger=LOGGER)
    assert await env.resolve(request=request, payload=payload) is None
    assert env.outcomes == ["declined_not_portable_input"]
    assert getattr(request.state, HINT_STATE_ATTRIBUTE, None) is None
    assert "reason=not_portable_input detail=not_portable_" in caplog.text
    assert env.claim_calls == 0


@pytest.mark.asyncio
async def test_bulkhead_saturated_declines_source_busy_without_a_reservation(env: _Env) -> None:
    env.sources[SRC] = _source(max_concurrency=1)
    occupant = env.bulkhead.try_acquire(SRC, 1)
    assert occupant is not None
    assert await env.resolve() is None
    assert env.outcomes == ["declined_source_busy"]
    assert env.bulkhead.in_flight(SRC) == 1
    env.bulkhead.release(occupant)


@pytest.mark.asyncio
async def test_half_open_trial_is_taken_by_the_first_dispatch_and_denied_to_the_second(env: _Env) -> None:
    env.open_breaker()
    env.clock.advance(BREAKER_OPEN_SECONDS)
    first = await env.resolve()
    assert isinstance(first, OverflowDispatch)
    second = await env.resolve()
    assert second is None
    assert env.outcomes == ["dispatched_fresh", "declined_breaker_open"]
    first.claims.release("success")
    assert env.breaker.state(SRC, env.clock.monotonic()) == "closed"


# --- ordering ----------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_portability_decline_while_half_open_leaves_the_trial_available(env: _Env) -> None:
    """CL-2: portability (7) runs before the claims (8), so a decline never takes the trial."""

    env.open_breaker()
    env.clock.advance(BREAKER_OPEN_SECONDS)
    payload = _payload(input=[_user("hello"), {"type": "reasoning", "id": "rs_1", "encrypted_content": "..."}])
    assert await env.resolve(payload=payload) is None
    assert env.outcomes == ["declined_not_portable_history"]
    assert env.claim_calls == 0
    trial = env.breaker.claim(SRC, env.clock.monotonic())
    assert trial is not None and trial.kind == "trial"


@pytest.mark.asyncio
async def test_live_pin_beats_a_binding_turn_state(env: _Env) -> None:
    """Pins (2) before turn-state (4): durable knowledge beats a client claim about an upstream owner."""

    env.pins.live(_pin_record(thread_pin_key(_thread_key()), now=env.clock.now()))
    result = await env.resolve({**NATIVE, "x-codex-turn-state": "client-turn-7f3a"})
    assert isinstance(result, OverflowDispatch) and result.kind == DISPATCH_KIND_PINNED
    assert env.probe_calls == 0, "pinned dispatch never probes"
    result.claims.release_if_unowned()


@pytest.mark.asyncio
async def test_anchor_resolves_before_the_drain_check(env: _Env) -> None:
    """Anchors (3) before ``designated is None`` (P8): SDK chains keep their source while draining."""

    env.settings(None, _T0 + timedelta(days=20))
    api_key = _api_key()
    record = _pin_record(
        anchor_pin_key("key_1", "resp_src_1"), now=env.clock.now(), kind=PIN_KIND_ANCHOR, api_key_id="key_1"
    )
    env.pins.live(record)
    result = await env.resolve(
        SDK, _payload(store=None, previous_response_id="resp_src_1"), api_key, route=ROUTE_V1_RESPONSES
    )
    assert isinstance(result, OverflowDispatch)
    assert result.kind == DISPATCH_KIND_ANCHOR and result.request_log_source == REQUEST_LOG_SOURCE_PINNED
    assert result.drain_until == _T0 + timedelta(days=20)
    assert result.pin_intent.writes == () and result.pin_intent.anchor is True
    assert result.resets_at is None and result.selection is None
    assert env.outcomes == ["dispatched_anchor"]
    assert env.probe_calls == 0
    result.claims.release_if_unowned()


@pytest.mark.asyncio
async def test_drain_mode_never_dispatches_fresh(env: _Env) -> None:
    env.settings(None, _T0 + timedelta(days=20))
    assert await env.resolve() is None
    assert env.outcomes == ["declined_drain_mode"]
    assert env.probe_calls == 0 and env.select_calls == []
    # The thread lookup still happened: pinned conversations keep resolving while draining.
    assert env.pins.calls == [(thread_pin_key(_thread_key()), True)]


@pytest.mark.asyncio
async def test_unknown_previous_response_id_leaves_the_fail_closed_path_unchanged(env: _Env) -> None:
    request = _request(SDK)
    result = await env.resolve(
        SDK, _payload(store=None, previous_response_id="resp_unknown"), request=request, route=ROUTE_V1_RESPONSES
    )
    assert result is None
    # Design §4.2 (3): the continuation lives elsewhere, so today's owner fail-closed path answers
    # unchanged -- the fresh pipeline never runs (no probe, no select) and no hint is left behind.
    assert env.outcomes == ["declined_not_portable_history"]
    assert env.pins.calls == [(anchor_pin_key(None, "resp_unknown"), True)]
    assert env.probe_calls == 0 and env.select_calls == []
    assert getattr(request.state, HINT_STATE_ATTRIBUTE, None) is None


# --- cancellation and the fast-decline window -------------------------------------------------


@pytest.mark.asyncio
async def test_cancelled_error_mid_decision_holds_nothing(env: _Env, monkeypatch: pytest.MonkeyPatch) -> None:
    env.open_breaker()
    env.clock.advance(BREAKER_OPEN_SECONDS)

    async def hanging_probe(*args: Any, **kwargs: Any) -> Any:
        await asyncio.Event().wait()

    monkeypatch.setattr(overflow_module, "probe_pool_usage_exhaustion", hanging_probe)
    task = asyncio.get_running_loop().create_task(env.resolve())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert env.bulkhead.in_flight(SRC) == 0
    assert env.breaker.state(SRC, env.clock.monotonic()) == "half_open"
    assert env.breaker.claim(SRC, env.clock.monotonic()) is not None, "the trial is still free"
    assert env.metrics.outcomes == []


@pytest.mark.asyncio
async def test_fast_decline_mark_declines_then_expires(env: _Env) -> None:
    env.fast_decline.mark(_thread_key(), env.clock.monotonic())
    assert await env.resolve() is None
    env.clock.advance(PIN_FAILURE_FAST_DECLINE_SECONDS - 0.001)
    assert await env.resolve() is None
    env.clock.advance(0.002)
    result = await env.resolve()
    assert isinstance(result, OverflowDispatch)
    assert env.outcomes == [
        "declined_pin_commit_recent_failure",
        "declined_pin_commit_recent_failure",
        "dispatched_fresh",
    ]
    result.claims.release_if_unowned()


# --- pinned dispatch -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_pin_dispatches_to_its_source_regardless_of_the_pool(env: _Env) -> None:
    env.service.selection = _healthy()
    stale = env.clock.now() - timedelta(seconds=PIN_TOUCH_INTERVAL_SECONDS + 1)
    env.pins.live(_pin_record(thread_pin_key(_thread_key()), now=env.clock.now(), last_seen_at=stale))
    api_key = _api_key()
    result = await env.resolve(api_key=api_key)

    assert isinstance(result, OverflowDispatch)
    assert result.kind == DISPATCH_KIND_PINNED and result.request_log_source == REQUEST_LOG_SOURCE_PINNED
    assert result.source is env.sources[SRC] and result.model == MODEL
    assert result.pin_intent.writes == () and result.pin_intent.thread_key == _thread_key()
    assert result.pin_intent.anchor is False and result.pin_intent.anchor_api_key_id == "key_1"
    assert env.bulkhead.in_flight(SRC) == 1
    assert env.probe_calls == 0 and env.view_calls == 0, "pinned dispatch ignores pool state and portability"
    assert env.outcomes == ["dispatched_pinned"]
    # The stale pin is touched in the background, never on the request path.
    assert env.service.cleanup_actions == ["model_source_pin_touch"]
    assert env.pins.calls == [(thread_pin_key(_thread_key()), True)]
    result.claims.release_if_unowned()


@pytest.mark.asyncio
async def test_fresh_pin_is_not_touched(env: _Env) -> None:
    env.pins.live(_pin_record(thread_pin_key(_thread_key()), now=env.clock.now()))
    result = await env.resolve()
    assert isinstance(result, OverflowDispatch)
    assert env.service.cleanup_actions == []
    result.claims.release_if_unowned()


@pytest.mark.asyncio
async def test_pinned_model_switch_to_an_unlisted_model_is_permanently_unservable(env: _Env) -> None:
    env.pins.live(_pin_record(thread_pin_key(_thread_key()), now=env.clock.now()))
    env.unservable_cause = "model_unlisted"
    payload = _payload(
        model="gpt-5.4", input=[_user("hi"), {"type": "reasoning", "id": "rs_1", "encrypted_content": "x"}]
    )
    result = await env.resolve(payload=payload)
    assert isinstance(result, JSONResponse) and result.status_code == 400
    assert result.body and b'"code":"subscription_overflow_source_unavailable"' in result.body
    assert env.outcomes == ["pinned_unservable_model_unlisted"]
    assert env.bulkhead.in_flight(SRC) == 0


def _body(response: JSONResponse) -> dict[str, Any]:
    import json

    return cast(dict[str, Any], json.loads(bytes(response.body)))


def _first_item(payload: ResponsesRequest) -> dict[str, JsonValue]:
    assert isinstance(payload.input, list)
    first = payload.input[0]
    assert isinstance(first, dict)
    return first


def _message(content: Mapping[str, JsonValue]) -> str:
    error = content["error"]
    assert isinstance(error, dict)
    message = error["message"]
    assert isinstance(message, str)
    return message


# --- direct source routing meets a pin or anchor (I7, design §7.2) -------------------------------

DIRECT_MODEL = "direct-model"


def _direct_source(model: str = DIRECT_MODEL) -> ModelSource:
    """The source direct routing selected for ``model``; never the designated one."""

    return _source("src_direct", model=model)


@pytest.mark.asyncio
async def test_direct_source_without_evidence_returns_none_after_the_pin_lookup_alone(env: _Env) -> None:
    """A model a source serves directly is that source's request: one bounded pin read, then nothing."""

    result = await env.resolve(payload=_payload(model=DIRECT_MODEL), direct_source=_direct_source())
    assert result is None
    assert env.pins.calls == [(thread_pin_key(_thread_key()), True)]
    assert env.probe_calls == 0 and env.select_calls == [] and env.view_calls == 0 and env.dump_calls == 0
    assert env.outcomes == [], "direct routing is not an exhaustion event: no decline is counted"
    _assert_nothing_claimed(env)


@pytest.mark.asyncio
async def test_direct_source_with_an_unanchored_previous_response_id_counts_nothing(env: _Env) -> None:
    request = _request(SDK)
    result = await env.resolve(
        SDK,
        _payload(model=DIRECT_MODEL, store=None, previous_response_id="resp_direct_9"),
        request=request,
        route=ROUTE_V1_RESPONSES,
        direct_source=_direct_source(),
    )
    assert result is None
    assert env.pins.calls == [(anchor_pin_key(None, "resp_direct_9"), True)]
    assert env.outcomes == [] and env.probe_calls == 0 and env.select_calls == []
    assert getattr(request.state, HINT_STATE_ATTRIBUTE, None) is None


@pytest.mark.asyncio
async def test_live_pin_beats_a_direct_source_when_the_pinned_source_serves_the_model(env: _Env) -> None:
    """Mutant: the direct source dispatches first. The pinned source lists the model, so the turn stays pinned."""

    shared = "shared-model"
    env.sources[SRC] = _source(model=shared)
    env.pins.live(_pin_record(thread_pin_key(_thread_key()), now=env.clock.now()))
    result = await env.resolve(payload=_payload(model=shared), direct_source=_direct_source(shared))
    assert isinstance(result, OverflowDispatch)
    assert result.kind == DISPATCH_KIND_PINNED and result.source is env.sources[SRC] and result.model == shared
    assert result.request_log_source == REQUEST_LOG_SOURCE_PINNED
    assert env.outcomes == ["dispatched_pinned"]
    assert env.probe_calls == 0
    result.claims.release_if_unowned()


@pytest.mark.asyncio
async def test_live_pin_with_a_directly_owned_unlisted_model_follows_the_unservable_rules(env: _Env) -> None:
    """The pinned source does not serve the model another source owns: ciphertext -> 400 and the pin is kept;
    a source-free transcript -> neutral release, after which the route's direct dispatch serves the id-stripped body."""

    record = _pin_record(thread_pin_key(_thread_key()), now=env.clock.now())
    env.pins.live(record)
    env.unservable_cause = "model_unlisted"
    ciphertext = _payload(
        model=DIRECT_MODEL,
        input=[{**_user("hi"), "id": "msg_src_1"}, {"type": "reasoning", "id": "rs_1", "encrypted_content": "x"}],
    )
    refused = await env.resolve(payload=ciphertext, direct_source=_direct_source())
    assert isinstance(refused, JSONResponse) and refused.status_code == 400
    assert _body(refused)["error"]["code"] == SOURCE_UNAVAILABLE_CODE
    assert env.executor.deleted == []
    assert _first_item(ciphertext)["id"] == "msg_src_1", "a refused body is untouched"
    assert env.outcomes == ["pinned_unservable_model_unlisted"]

    source_free = _payload(model=DIRECT_MODEL, input=[{**_user("hi"), "id": "msg_src_1"}])
    released = await env.resolve(payload=source_free, direct_source=_direct_source())
    assert released is None
    assert env.executor.deleted == [(record.pin_key, True)], "deleted durably before the direct source serves it"
    assert "id" not in _first_item(source_free)
    assert env.outcomes == ["pinned_unservable_model_unlisted", "pinned_released_neutral"]
    assert env.probe_calls == 0
    _assert_nothing_claimed(env)


@pytest.mark.asyncio
async def test_live_anchor_beats_a_direct_source(env: _Env) -> None:
    api_key = _api_key()
    env.pins.live(
        _pin_record(
            anchor_pin_key("key_1", "resp_src_1"), now=env.clock.now(), kind=PIN_KIND_ANCHOR, api_key_id="key_1"
        )
    )
    listed = await env.resolve(
        SDK,
        _payload(store=None, previous_response_id="resp_src_1"),
        api_key,
        route=ROUTE_V1_RESPONSES,
        direct_source=_direct_source(MODEL),
    )
    assert isinstance(listed, OverflowDispatch)
    assert listed.kind == DISPATCH_KIND_ANCHOR and listed.source is env.sources[SRC]
    listed.claims.release_if_unowned()

    env.unservable_cause = "model_unlisted"
    unlisted = await env.resolve(
        SDK,
        _payload(model=DIRECT_MODEL, store=None, previous_response_id="resp_src_1"),
        api_key,
        route=ROUTE_V1_RESPONSES,
        direct_source=_direct_source(),
    )
    assert isinstance(unlisted, JSONResponse) and unlisted.status_code == 400
    assert _body(unlisted)["error"]["code"] == SOURCE_UNAVAILABLE_CODE
    assert env.outcomes == ["dispatched_anchor", "pinned_unservable_model_unlisted"]
    _assert_nothing_claimed(env)


@pytest.mark.asyncio
async def test_pinned_source_disabled_source_free_transcript_is_released_neutrally(env: _Env, caplog) -> None:
    """CP-6: durable delete first (re-read, never the positive cache), then subscription with ids stripped."""

    record = _pin_record(thread_pin_key(_thread_key()), now=env.clock.now())
    env.pins.live(record)
    env.sources.clear()
    env.unservable_cause = "source_disabled"
    payload = _payload(
        input=[
            {**_user("hello"), "id": "msg_source_1"},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "hi"}],
                "id": "msg_source_2",
            },
            {**_user("more"), "id": "msg_source_3"},
        ]
    )
    caplog.set_level(logging.WARNING, logger=LOGGER)

    result = await env.resolve(payload=payload)

    assert result is None
    assert env.executor.deleted == [(record.pin_key, True)], "delete_durably invalidates the positive cache"
    assert env.pins.calls == [(record.pin_key, True), (record.pin_key, False)], "the release re-reads without the cache"
    assert isinstance(payload.input, list)
    assert all(isinstance(item, dict) and "id" not in item for item in payload.input)
    assert env.outcomes == ["pinned_released_neutral"]
    assert "subscription_overflow_pinned_released_neutral cause=source_disabled" in caplog.text
    assert env.bulkhead.in_flight(SRC) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_type", ["shell", "apply_patch", "local_shell", "tool_search"])
async def test_pinned_source_disabled_release_admits_stateless_codex_tool_declarations(
    env: _Env, tool_type: str
) -> None:
    """CP-6 regression: a transcript that only DECLARES a stateless Codex tool type is still source-free.

    The fresh path admitted exactly these declarations when it pinned the thread, and the release
    target is a subscription account for which they are native, so they must not turn the release
    into a hard 400 (mutant: ``transcript_is_source_free(view)`` with the empty default vocabulary).
    """

    record = _pin_record(thread_pin_key(_thread_key()), now=env.clock.now())
    env.pins.live(record)
    env.sources.clear()
    env.unservable_cause = "source_disabled"
    payload = _payload(
        input=[{**_user("hello"), "id": "msg_source_1"}, {**_user("more"), "id": "msg_source_3"}],
        tools=[{"type": tool_type}, {"type": "function", "name": "lookup", "parameters": {"type": "object"}}],
    )

    result = await env.resolve(payload=payload)

    assert result is None, "declaring a stateless Codex tool must not block the neutral release"
    assert env.executor.deleted == [(record.pin_key, True)]
    assert isinstance(payload.input, list)
    assert all(isinstance(item, dict) and "id" not in item for item in payload.input)
    assert env.outcomes == ["pinned_released_neutral"]


@pytest.mark.asyncio
async def test_neutral_release_never_serves_before_the_delete_verifies(env: _Env) -> None:
    """Mutant: serve before the delete commits."""

    record = _pin_record(thread_pin_key(_thread_key()), now=env.clock.now())
    env.pins.live(record)
    env.sources.clear()
    env.executor.delete_outcome = "unknown"
    payload = _payload(input=[{**_user("hello"), "id": "msg_1"}])
    result = await env.resolve(payload=payload)
    assert isinstance(result, JSONResponse) and result.status_code == 503
    assert _body(result)["error"]["code"] == MODEL_SOURCE_UNAVAILABLE_CODE
    assert result.headers["retry-after"] == "2"
    assert _first_item(payload)["id"] == "msg_1", "the body is untouched"
    assert env.outcomes == ["pinned_release_failed"]


@pytest.mark.asyncio
async def test_neutral_release_trusts_the_re_read_not_the_positive_cache(env: _Env) -> None:
    """Mutant: neutral release trusts the positive cache. The row was released elsewhere: fall through unpinned."""

    record = _pin_record(thread_pin_key(_thread_key()), now=env.clock.now())
    env.pins.live(record)
    env.pins.uncached[record.pin_key] = PinLookupResult("none", None)
    env.sources.clear()
    result = await env.resolve()
    assert result is None
    assert env.executor.deleted == []
    assert env.outcomes == []


@pytest.mark.asyncio
async def test_pinned_source_disabled_with_reasoning_is_permanently_unservable(env: _Env) -> None:
    env.pins.live(_pin_record(thread_pin_key(_thread_key()), now=env.clock.now()))
    env.sources.clear()
    env.unservable_cause = "source_deleted"
    payload = _payload(input=[_user("hi"), {"type": "reasoning", "id": "rs_1", "encrypted_content": "cipher"}])
    result = await env.resolve(payload=payload)
    assert isinstance(result, JSONResponse) and result.status_code == 400
    body = _body(result)
    assert body["error"]["code"] == SOURCE_UNAVAILABLE_CODE and body["error"]["type"] == "invalid_request_error"
    assert env.executor.deleted == []
    assert env.outcomes == ["pinned_unservable_source_deleted"]


@pytest.mark.asyncio
async def test_tombstone_with_reasoning_answers_expired(env: _Env) -> None:
    env.pins.expired(_pin_record(thread_pin_key(_thread_key()), now=env.clock.now(), expired=True))
    payload = _payload(input=[_user("hi"), {"type": "reasoning", "id": "rs_1", "encrypted_content": "cipher"}])
    result = await env.resolve(payload=payload)
    assert isinstance(result, JSONResponse) and result.status_code == 400
    assert _body(result)["error"]["code"] == UNSUPPORTED_INPUT_CODE
    assert env.outcomes == ["pinned_unservable_tombstone"]
    assert env.select_calls == [], "a tombstone never resolves the source"


@pytest.mark.asyncio
async def test_tombstone_with_a_source_free_transcript_is_released(env: _Env) -> None:
    record = _pin_record(thread_pin_key(_thread_key()), now=env.clock.now(), expired=True)
    env.pins.expired(record)
    assert await env.resolve() is None
    assert env.executor.deleted == [(record.pin_key, True)]
    assert env.outcomes == ["pinned_released_neutral"]


@pytest.mark.asyncio
async def test_pinned_with_excluded_input_is_refused(env: _Env) -> None:
    env.pins.live(_pin_record(thread_pin_key(_thread_key()), now=env.clock.now()))
    result = await env.resolve(source_route_excluded=True)
    assert isinstance(result, JSONResponse) and result.status_code == 400
    assert _body(result)["error"]["code"] == UNSUPPORTED_INPUT_CODE
    assert env.outcomes == ["pinned_unsupported_input"]
    assert env.bulkhead.in_flight(SRC) == 0


def _image_message(text: str = "what is this") -> dict[str, JsonValue]:
    return {
        "type": "message",
        "role": "user",
        "content": [
            {"type": "input_text", "text": text},
            {"type": "input_image", "image_url": "data:image/png;base64,iVBORw0KGgo=", "detail": "auto"},
        ],
    }


@pytest.mark.asyncio
async def test_pinned_input_image_without_vision_is_refused_and_the_pin_kept(env: _Env) -> None:
    """§7.2 P16: a pin overrides portability except for what the source model cannot see."""

    env.pins.live(_pin_record(thread_pin_key(_thread_key()), now=env.clock.now()))
    result = await env.resolve(payload=_payload(input=[_user("hi"), _image_message()]))
    assert isinstance(result, JSONResponse) and result.status_code == 400
    body = _body(result)
    assert body["error"]["code"] == UNSUPPORTED_INPUT_CODE
    assert body["error"]["type"] == "invalid_request_error"
    assert "images" in _message(body)
    assert env.outcomes == ["pinned_unsupported_input"]
    assert env.executor.deleted == [], "the pin is kept"
    assert env.service.cleanup_actions == [], "nothing is touched or dispatched"
    assert env.bulkhead.in_flight(SRC) == 0
    assert env.view_calls == 0, "the pin still overrides the rest of the portability verdict"


@pytest.mark.asyncio
async def test_pinned_input_image_with_vision_is_dispatched(env: _Env) -> None:
    env.sources[SRC].models[0].supports_vision = True
    env.pins.live(_pin_record(thread_pin_key(_thread_key()), now=env.clock.now()))
    result = await env.resolve(payload=_payload(input=[_user("hi"), _image_message()]))
    assert isinstance(result, OverflowDispatch) and result.kind == DISPATCH_KIND_PINNED
    assert env.outcomes == ["dispatched_pinned"]
    result.claims.release_if_unowned()


@pytest.mark.asyncio
async def test_anchored_input_image_without_vision_is_refused(env: _Env) -> None:
    api_key = _api_key()
    env.pins.live(
        _pin_record(
            anchor_pin_key("key_1", "resp_src_1"), now=env.clock.now(), kind=PIN_KIND_ANCHOR, api_key_id="key_1"
        )
    )
    result = await env.resolve(
        SDK,
        _payload(store=None, previous_response_id="resp_src_1", input=[_image_message()]),
        api_key,
        route=ROUTE_V1_RESPONSES,
    )
    assert isinstance(result, JSONResponse) and result.status_code == 400
    assert _body(result)["error"]["code"] == UNSUPPORTED_INPUT_CODE
    assert env.outcomes == ["pinned_unsupported_input"]
    assert env.executor.deleted == []
    assert env.bulkhead.in_flight(SRC) == 0


@pytest.mark.asyncio
async def test_pinned_fails_fast_while_the_breaker_is_open(env: _Env) -> None:
    """Mutant: pinned ignores the open breaker. Transient => 503, never 429 and never 400."""

    env.pins.live(_pin_record(thread_pin_key(_thread_key()), now=env.clock.now()))
    env.open_breaker()
    result = await env.resolve()
    assert isinstance(result, JSONResponse) and result.status_code == 503
    body = _body(result)
    assert body["error"]["code"] == MODEL_SOURCE_UNAVAILABLE_CODE and body["error"]["type"] == "upstream_error"
    assert result.headers["retry-after"] == "2"
    assert env.outcomes == ["pinned_breaker_open"]
    assert env.bulkhead.in_flight(SRC) == 0


@pytest.mark.asyncio
async def test_pinned_answers_busy_when_the_bulkhead_is_saturated(env: _Env) -> None:
    env.pins.live(_pin_record(thread_pin_key(_thread_key()), now=env.clock.now()))
    env.sources[SRC] = _source(max_concurrency=1)
    occupant = env.bulkhead.try_acquire(SRC, 1)
    assert occupant is not None
    result = await env.resolve()
    assert isinstance(result, JSONResponse) and result.status_code == 503
    assert _body(result)["error"]["code"] == MODEL_SOURCE_BUSY_CODE
    assert result.headers["retry-after"] == "1"
    assert env.outcomes == ["pinned_busy"]
    env.bulkhead.release(occupant)


@pytest.mark.asyncio
async def test_pinned_scoped_key_outside_the_source_scope_is_unservable(env: _Env) -> None:
    env.pins.live(_pin_record(thread_pin_key(_thread_key()), now=env.clock.now()))
    api_key = _api_key(source_assignment_scope_enabled=True, assigned_source_ids=["src_other"])
    payload = _payload(input=[_user("hi"), {"type": "reasoning", "id": "rs_1", "encrypted_content": "cipher"}])
    result = await env.resolve(api_key=api_key, payload=payload)
    assert isinstance(result, JSONResponse) and result.status_code == 400
    assert env.outcomes == ["pinned_unservable_model_unlisted"]
    assert env.select_calls == []


@pytest.mark.asyncio
async def test_pinned_scoped_key_inside_the_source_scope_is_served(env: _Env) -> None:
    env.pins.live(_pin_record(thread_pin_key(_thread_key()), now=env.clock.now()))
    api_key = _api_key(source_assignment_scope_enabled=True, assigned_source_ids=[SRC])
    result = await env.resolve(api_key=api_key)
    assert isinstance(result, OverflowDispatch) and result.kind == DISPATCH_KIND_PINNED
    result.claims.release_if_unowned()


# --- anchors -----------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_anchor_answers_expired_without_a_neutral_release(env: _Env) -> None:
    record = _pin_record(anchor_pin_key(None, "resp_src_1"), now=env.clock.now(), kind=PIN_KIND_ANCHOR, expired=True)
    env.pins.expired(record)
    result = await env.resolve(SDK, _payload(store=None, previous_response_id="resp_src_1"), route=ROUTE_V1_RESPONSES)
    assert isinstance(result, JSONResponse) and result.status_code == 400
    assert _body(result)["error"]["code"] == UNSUPPORTED_INPUT_CODE
    assert env.executor.deleted == [] and env.pins.calls == [(record.pin_key, True)]
    assert env.outcomes == ["pinned_unservable_tombstone"]


@pytest.mark.asyncio
async def test_anchored_source_disabled_answers_source_unavailable(env: _Env) -> None:
    env.pins.live(_pin_record(anchor_pin_key(None, "resp_src_1"), now=env.clock.now(), kind=PIN_KIND_ANCHOR))
    env.sources.clear()
    result = await env.resolve(SDK, _payload(store=None, previous_response_id="resp_src_1"), route=ROUTE_V1_RESPONSES)
    assert isinstance(result, JSONResponse) and result.status_code == 400
    assert _body(result)["error"]["code"] == SOURCE_UNAVAILABLE_CODE
    assert env.executor.deleted == []
    assert env.outcomes == ["pinned_unservable_source_disabled"]


# --- fail-closed rules (§8.1) ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lookup_timeout_with_a_thread_key_fails_closed(env: _Env, caplog) -> None:
    env.pins.error = PinLookupTimeout("slow")
    caplog.set_level(logging.WARNING, logger=LOGGER)
    result = await env.resolve()
    assert isinstance(result, JSONResponse) and result.status_code == 503
    assert _body(result)["error"]["code"] == MODEL_SOURCE_UNAVAILABLE_CODE
    assert result.headers["retry-after"] == "2"
    assert env.outcomes == ["pinned_lookup_timeout"]
    assert "cause=lookup_timeout" in caplog.text
    assert env.probe_calls == 0


@pytest.mark.asyncio
async def test_lookup_failure_with_a_thread_key_fails_closed(env: _Env, caplog) -> None:
    env.pins.error = RuntimeError("db down")
    caplog.set_level(logging.WARNING, logger=LOGGER)
    result = await env.resolve()
    assert isinstance(result, JSONResponse) and result.status_code == 503
    assert env.outcomes == ["decision_error"]
    assert "subscription_overflow_decision_error stage=pin_lookup" in caplog.text
    assert "cause=decision_error" in caplog.text


@pytest.mark.asyncio
async def test_anchor_lookup_failure_fails_closed(env: _Env) -> None:
    env.pins.error = RuntimeError("db down")
    result = await env.resolve(SDK, _payload(store=None, previous_response_id="resp_x"), route=ROUTE_V1_RESPONSES)
    assert isinstance(result, JSONResponse) and result.status_code == 503
    assert env.outcomes == ["decision_error"]


@pytest.mark.asyncio
async def test_fresh_path_failure_falls_through_to_subscription(
    env: _Env, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    async def broken_probe(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("selector down")

    monkeypatch.setattr(overflow_module, "probe_pool_usage_exhaustion", broken_probe)
    caplog.set_level(logging.WARNING, logger=LOGGER)
    assert await env.resolve() is None
    assert env.outcomes == ["decision_error"]
    assert "stage=fresh" in caplog.text
    _assert_nothing_claimed(env)


@pytest.mark.asyncio
async def test_pinned_dispatch_failure_fails_closed(env: _Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutant: a pinned decision error falls through (it would hand source ciphertext to a subscription account)."""

    env.pins.live(_pin_record(thread_pin_key(_thread_key()), now=env.clock.now()))

    async def broken_select(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("catalog down")

    monkeypatch.setattr(overflow_module, "select_overflow_model_source", broken_select)
    result = await env.resolve()
    assert isinstance(result, JSONResponse) and result.status_code == 503
    assert env.outcomes == ["decision_error"]
    assert env.bulkhead.in_flight(SRC) == 0


# --- compact and handshake pin checks ---------------------------------------------------------------


def _compact() -> ResponsesCompactRequest:
    return ResponsesCompactRequest.model_validate({"model": MODEL, "instructions": "x", "input": [_user("hi")]})


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["live", "expired"])
async def test_compact_on_a_pinned_conversation_is_refused(env: _Env, state: str) -> None:
    record = _pin_record(thread_pin_key(_thread_key()), now=env.clock.now(), expired=state == "expired")
    env.pins.answers[record.pin_key] = PinLookupResult(cast(Any, state), record)
    result = await compact_pin_denial(_request(NATIVE), _compact(), context=env.context)
    assert isinstance(result, JSONResponse) and result.status_code == 400
    body = _body(result)
    assert body["error"]["code"] == UNSUPPORTED_INPUT_CODE and body["error"]["message"] == COMPACT_DENIAL_MESSAGE
    assert env.metrics.outcomes == [(ROUTE_COMPACT, "pinned_unsupported_input")]


@pytest.mark.asyncio
async def test_compact_without_a_pin_or_thread_passes(env: _Env) -> None:
    assert await compact_pin_denial(_request(NATIVE), _compact(), context=env.context) is None
    assert await compact_pin_denial(_request(SDK), _compact(), context=env.context) is None
    assert env.pins.calls == [(thread_pin_key(_thread_key()), True)]
    assert env.metrics.outcomes == []


@pytest.mark.asyncio
async def test_compact_lookup_failures_fail_closed(env: _Env) -> None:
    env.pins.error = PinLookupTimeout("slow")
    result = await compact_pin_denial(_request(NATIVE), _compact(), context=env.context)
    assert isinstance(result, JSONResponse) and result.status_code == 503
    env.pins.error = RuntimeError("db down")
    result = await compact_pin_denial(_request(NATIVE), _compact(), context=env.context)
    assert isinstance(result, JSONResponse) and result.status_code == 503
    assert env.metrics.outcomes == [(ROUTE_COMPACT, "pinned_lookup_timeout"), (ROUTE_COMPACT, "decision_error")]


@pytest.mark.asyncio
@pytest.mark.parametrize("evidence", ["live", "expired", "bounce"])
async def test_handshake_is_denied_with_426_on_evidence(env: _Env, evidence: str) -> None:
    if evidence == "bounce":
        record = _pin_record(bounce_pin_key(_thread_key()), now=env.clock.now(), kind=PIN_KIND_BOUNCE)
    else:
        record = _pin_record(thread_pin_key(_thread_key()), now=env.clock.now(), expired=evidence == "expired")
    env.pins.answers[record.pin_key] = PinLookupResult(cast(Any, evidence), record)
    result = await handshake_denial(NATIVE, context=env.context)
    assert isinstance(result, JSONResponse) and result.status_code == 426
    body = _body(result)
    assert body["error"]["code"] == HANDSHAKE_DENIAL_CODE and body["error"]["type"] == "server_error"
    assert env.metrics.outcomes == [(ROUTE_WEBSOCKET_HANDSHAKE, "bounced_ws_handshake")]
    assert sorted(key for key, _ in env.pins.calls) == sorted(
        [thread_pin_key(_thread_key()), bounce_pin_key(_thread_key())]
    )


@pytest.mark.asyncio
async def test_handshake_without_evidence_is_accepted_and_never_probes(env: _Env) -> None:
    """CP-8 / mutant: 426 without evidence. The pool is exhausted here and that alone is not evidence."""

    assert await handshake_denial(NATIVE, context=env.context) is None
    assert await handshake_denial(NATIVE_NO_THREAD, context=env.context) is None
    assert env.probe_calls == 0 and env.service.admission_calls == []
    assert env.metrics.outcomes == []


@pytest.mark.asyncio
async def test_handshake_lookup_failures_deny_toward_http(env: _Env) -> None:
    env.pins.error = PinLookupTimeout("slow")
    result = await handshake_denial(NATIVE, context=env.context)
    assert isinstance(result, JSONResponse) and result.status_code == 426
    env.pins.error = RuntimeError("db down")
    result = await handshake_denial(NATIVE, context=env.context)
    assert isinstance(result, JSONResponse) and result.status_code == 426
    assert env.metrics.outcomes == [
        (ROUTE_WEBSOCKET_HANDSHAKE, "pinned_lookup_timeout"),
        (ROUTE_WEBSOCKET_HANDSHAKE, "decision_error"),
    ]


# --- hint ------------------------------------------------------------------------------------------


def test_usage_limit_hint_is_a_no_op_without_the_state_attribute() -> None:
    request = _request(NATIVE)
    content: dict[str, JsonValue] = {"error": {"code": "usage_limit_reached", "message": "Usage limit reached"}}
    headers = {"x-codex-primary-used-percent": "100"}
    hinted_content, hinted_headers = apply_usage_limit_hint(request, content, headers)
    assert hinted_content is content and hinted_headers is headers


def test_usage_limit_hint_is_a_header_for_native_codex_and_a_sentence_for_sdks() -> None:
    content: dict[str, JsonValue] = {"error": {"code": "usage_limit_reached", "message": "Usage limit reached."}}

    native = _request(NATIVE)
    setattr(native.state, HINT_STATE_ATTRIBUTE, "not_portable_history")
    hinted_content, hinted_headers = apply_usage_limit_hint(native, content, {})
    assert hinted_content is content
    assert hinted_headers == {HINT_HEADER: HINT_NATIVE_TEXT}

    sdk = _request(SDK)
    setattr(sdk.state, HINT_STATE_ATTRIBUTE, "not_portable_history")
    hinted_content, hinted_headers = apply_usage_limit_hint(sdk, content, {})
    assert hinted_headers == {} and HINT_HEADER not in hinted_headers
    assert hinted_content == {
        "error": {"code": "usage_limit_reached", "message": f"Usage limit reached. {HINT_SDK_SENTENCE}"}
    }
    assert _message(content) == "Usage limit reached.", "the original envelope is not mutated"

    empty = _request(SDK)
    setattr(empty.state, HINT_STATE_ATTRIBUTE, "not_portable_history")
    hinted_content, _ = apply_usage_limit_hint(empty, {"error": {"code": "usage_limit_reached", "message": ""}}, {})
    assert _message(hinted_content) == HINT_SDK_SENTENCE


def test_usage_limit_hint_ignores_other_state_values() -> None:
    request = _request(NATIVE)
    setattr(request.state, HINT_STATE_ATTRIBUTE, "not_portable_input")
    content: dict[str, JsonValue] = {"error": {"code": "usage_limit_reached", "message": "x"}}
    assert apply_usage_limit_hint(request, content, {}) == (content, {})


# --- pure helpers ----------------------------------------------------------------------------------


def test_background_job_allowlist_and_memgen_refusal() -> None:
    """CP-9 / mutant: allowlist -> denylist (an unknown label such as ``guardian`` must be refused)."""

    assert background_job_allowed({}) is True
    assert background_job_allowed({"X-OpenAI-Subagent": ""}) is True
    for label in ("review", "compact", "collab_spawn"):
        assert background_job_allowed({"x-openai-subagent": label}) is True
    for label in ("memory_consolidation", "guardian", "Review", "something_new"):
        assert background_job_allowed({"x-openai-subagent": label}) is False
    assert background_job_allowed({"x-openai-memgen-request": "true"}) is False
    assert background_job_allowed({"X-OpenAI-Memgen-Request": "true", "x-openai-subagent": "review"}) is False


def test_overflow_thread_key_is_thread_only_and_session_independent() -> None:
    key = overflow_thread_key({"thread-id": THREAD_ID, "session-id": "sess-1"})
    assert (
        key
        == overflow_thread_key({"Thread-Id": THREAD_ID})
        == overflow_thread_key({"thread-id": THREAD_ID, "session_id": "other"})
    )
    assert key == _thread_key()
    assert ":thread_header:thread_only:" in key
    assert overflow_thread_key({"session-id": "sess-1"}) is None
    assert overflow_thread_key({"thread-id": "   "}) is None
    thread_pin_key(key)  # accepted by the pin key builders
    bounce_pin_key(key)


@pytest.mark.parametrize(
    ("pin_outcome", "expected"),
    [("not_written", "pin_commit_failed"), ("unknown", "pin_commit_unverified")],
)
def test_finished_hook_counts_the_pin_commit_outcome_once_under_the_route(
    monkeypatch: pytest.MonkeyPatch, pin_outcome: str, expected: str
) -> None:
    """Spec: a pin commit that is not ``written`` counts ``pin_commit_failed`` / ``pin_commit_unverified``. Mutant
    (the hook records only the transport decision): both outcomes exist only in the enum and the canary drills
    watching ``codex_lb_subscription_overflow_total`` never see a pin failure."""

    import app.modules.proxy._service.observability as observability

    metrics = _Metrics()
    monkeypatch.setattr(overflow_module, "subscription_overflow_total", metrics)
    transport_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        observability, "_record_upstream_transport_decision", lambda **kwargs: transport_calls.append(kwargs)
    )
    hook = OverflowFinishedHook(ROUTE_V1_RESPONSES)
    owner = SimpleNamespace(dispatch_kind="pinned", pin_outcome=pin_outcome)

    hook(cast(Any, owner), "error")

    assert metrics.outcomes == [(ROUTE_V1_RESPONSES, expected)]
    assert [(call["policy"], call["sticky"], call["status"]) for call in transport_calls] == [
        ("subscription_overflow", True, "error")
    ]
    assert hook == OverflowFinishedHook(ROUTE_V1_RESPONSES) and hook != OverflowFinishedHook(ROUTE_CODEX_RESPONSES)


@pytest.mark.parametrize("pin_outcome", [None, "written"])
def test_finished_hook_counts_nothing_without_a_pin_failure(
    monkeypatch: pytest.MonkeyPatch, pin_outcome: str | None
) -> None:
    import app.modules.proxy._service.observability as observability

    metrics = _Metrics()
    monkeypatch.setattr(overflow_module, "subscription_overflow_total", metrics)
    monkeypatch.setattr(observability, "_record_upstream_transport_decision", lambda **kwargs: None)
    OverflowFinishedHook(ROUTE_CODEX_RESPONSES)(
        cast(Any, SimpleNamespace(dispatch_kind="fresh", pin_outcome=pin_outcome)), "success"
    )
    assert metrics.outcomes == []


def test_record_overflow_transport_decision_reports_policy_and_stickiness(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.modules.proxy._service.observability as observability

    calls: list[dict[str, object]] = []
    monkeypatch.setattr(observability, "_record_upstream_transport_decision", lambda **kwargs: calls.append(kwargs))
    record_overflow_transport_decision(cast(Any, SimpleNamespace(dispatch_kind="pinned")), "success")
    record_overflow_transport_decision(cast(Any, SimpleNamespace(dispatch_kind="fresh")), "error")
    assert calls == [
        {
            "downstream_transport": "http",
            "upstream_transport": "openai_compatible_http",
            "policy": "subscription_overflow",
            "sticky": True,
            "status": "success",
        },
        {
            "downstream_transport": "http",
            "upstream_transport": "openai_compatible_http",
            "policy": "subscription_overflow",
            "sticky": False,
            "status": "error",
        },
    ]


def test_outcome_enum_is_closed_and_unknown_outcomes_are_never_recorded(
    monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    metrics = _Metrics()
    monkeypatch.setattr(overflow_module, "subscription_overflow_total", metrics)
    caplog.set_level(logging.ERROR, logger=LOGGER)
    record_overflow_outcome(ROUTE_CODEX_RESPONSES, "declined_made_up")
    assert metrics.outcomes == []
    assert "subscription_overflow_metric_outcome_unknown" in caplog.text
    record_overflow_outcome(ROUTE_CODEX_RESPONSES, "dispatched_fresh")
    assert metrics.outcomes == [(ROUTE_CODEX_RESPONSES, "dispatched_fresh")]
    from typing import get_args

    assert {f"declined_{reason}" for reason in get_args(DeclineReason)} <= OVERFLOW_OUTCOMES
    assert {
        "dispatched_fresh",
        "dispatched_pinned",
        "dispatched_anchor",
        "bounced_ws_handshake",
        "bounced_ws_event",
    } <= OVERFLOW_OUTCOMES
