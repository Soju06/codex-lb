"""WebSocket parity for subscription-exhaustion overflow, end to end (#2123 WP-D folded into WP-C2).

Handshake denial (``api.py`` -> ``app.modules.proxy.overflow.handshake_denial``)
and the in-band bounce (``_service/websocket/overflow.py``) against the real
pin table, the real account selector and the real settings row:

* live pin / tombstone / bounce row -> HTTP ``426``
  ``subscription_overflow_requires_http_transport`` before the accept, no
  request-log row; a bounce row stops denying once it expires;
* an exhausted pool without evidence is accepted (no speculative 426) and the
  eligible ``response.create`` is bounced in-band with a ``503`` event plus a
  60 s bounce row, so the next handshake for the thread meets the 426;
* capability handshakes are never denied; pinned and anchored turns are bounced
  on a reused socket with the reservation released and the row finalized; the
  HTTP session bridge never enters overflow.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from starlette.testclient import WebSocketDenialResponse

import app.core.metrics.prometheus as prometheus_module
import app.modules.proxy.service as proxy_module
from app.core.clients.proxy import CODEX_LB_REQUIRED_CAPABILITY_HEADER
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, HttpBridgeSessionRecord, ModelSourcePin, RequestLog
from app.db.session import SessionLocal, sqlite_writer_section
from app.dependencies import get_proxy_service_for_app
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyCreateData, ApiKeysService
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.proxy.affinity import _codex_backend_identity
from app.modules.proxy.model_source_pins import (
    PIN_KIND_ANCHOR,
    PIN_KIND_BOUNCE,
    PIN_KIND_THREAD,
    WS_BOUNCE_TTL_SECONDS,
    ModelSourcePinRepository,
    PinWrite,
    anchor_pin_key,
    bounce_pin_key,
    thread_pin_key,
)
from app.modules.proxy.overflow import HANDSHAKE_DENIAL_CODE, WS_BOUNCE_CODE
from app.modules.usage.repository import UsageRepository

pytestmark = pytest.mark.integration

_CODEX_WS = "ws://localhost/backend-api/codex/responses"
_CODEX_V1_ALIAS_WS = "ws://localhost/backend-api/codex/v1/responses"
_MODEL = "gpt-5.4"
_NON_RETRYABLE_CODES = {"server_is_overloaded", "slow_down"}


# --- helpers -----------------------------------------------------------------------------------------------


def _mask_countdown(event_text: str) -> str:
    return re.sub(r"\d+s\b", "Ns", event_text)


def _aware(value: datetime) -> datetime:
    # SQLite hands the tz-aware pin columns back naive; PostgreSQL keeps the offset.
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _thread_key(thread_id: str) -> str:
    key = _codex_backend_identity({"thread-id": thread_id}).thread_selection_key
    assert key is not None
    return key


def _native_headers(thread_id: str, *, session_id: str = "sess_ws_overflow") -> dict[str, str]:
    return {
        "user-agent": "codex_cli_rs/0.150.0 (Ubuntu 24.04.2 LTS; x86_64) WindowsTerminal",
        "originator": "codex_cli_rs",
        "thread-id": thread_id,
        "session-id": session_id,
    }


def _response_create(text: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "response.create",
        "model": _MODEL,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": text}]}],
        "stream": True,
    }
    payload.update(extra)
    return payload


async def _upsert_pins(now: datetime, *writes: PinWrite, drain_until: datetime | None = None) -> None:
    async with SessionLocal() as session:
        async with sqlite_writer_section():
            await ModelSourcePinRepository(session).upsert(list(writes), now=now, drain_until=drain_until)
            await session.commit()


async def _pin_rows() -> list[ModelSourcePin]:
    async with SessionLocal() as session:
        return list((await session.scalars(select(ModelSourcePin))).all())


async def _request_log_error_codes() -> list[str | None]:
    async with SessionLocal() as session:
        return list((await session.scalars(select(RequestLog.error_code))).all())


async def _bridge_session_count() -> int:
    async with SessionLocal() as session:
        return int(await session.scalar(select(func.count()).select_from(HttpBridgeSessionRecord)) or 0)


async def _seed_exhausted_account(account_id: str) -> int:
    """A usage-proven exhausted pool: ``QUOTA_EXCEEDED`` at 100 % with a known reset (mirrors the HTTP golden test)."""

    now_epoch = int(time.time())
    reset_at = now_epoch + 1800
    now = utcnow()
    async with SessionLocal() as session:
        session.add(
            Account(
                id=account_id,
                chatgpt_account_id=account_id,
                email=f"{account_id}@example.com",
                plan_type="plus",
                access_token_encrypted=b"access",
                refresh_token_encrypted=b"refresh",
                id_token_encrypted=b"id",
                last_refresh=now,
                status=AccountStatus.QUOTA_EXCEEDED,
                blocked_at=now_epoch,
                reset_at=reset_at,
            )
        )
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


async def _create_api_key_authorization(name: str) -> str:
    async with SessionLocal() as session:
        created_key = await ApiKeysService(ApiKeysRepository(session)).create_key(
            ApiKeyCreateData(name=name, allowed_models=None)
        )
    return f"Bearer {created_key.key}"


async def _drain_request_logs(app: Any) -> None:
    assert await get_proxy_service_for_app(app).drain_persistence_tasks(timeout_seconds=2)


async def _designate_source(app: Any, name: str) -> str:
    """Create a Responses-capable source listing the registry slug and designate it; the WebSocket never dials it.

    Runs on the app's loop through the test client's portal; the dashboard routes
    recognise the httpx ASGI transport as a local request.
    """

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as dashboard:
        created = await dashboard.post(
            "/api/model-sources/",
            json={
                "name": name,
                "baseUrl": "http://127.0.0.1:9/v1",
                "apiKey": f"token-{name}",
                "supportsChatCompletions": True,
                "supportsResponses": True,
                "models": [{"model": _MODEL, "contextWindow": 400_000, "supportsStreaming": True}],
            },
        )
        assert created.status_code == 200, created.text
        source_id = created.json()["id"]
        designated = await dashboard.put("/api/settings", json={"subscriptionOverflowSourceId": source_id})
        assert designated.status_code == 200, designated.text
    return source_id


def _designate(client: TestClient, app: Any, name: str) -> str:
    assert client.portal is not None
    return client.portal.call(_designate_source, app, name)


def _expect_denial(client: TestClient, url: str, headers: dict[str, str]) -> WebSocketDenialResponse:
    with pytest.raises(WebSocketDenialResponse) as denial:
        with client.websocket_connect(url, headers=headers):
            pytest.fail("the handshake must be denied before it is accepted")
    return denial.value


def _expect_accept(client: TestClient, url: str, headers: dict[str, str]) -> None:
    with client.websocket_connect(url, headers=headers):
        pass


class _ObservedCounter:
    def __init__(self) -> None:
        self.samples: list[tuple[str, str]] = []

    def labels(self, **labels: str) -> SimpleNamespace:
        def inc(amount: float = 1.0) -> None:
            del amount
            self.samples.append((labels["route"], labels["outcome"]))

        return SimpleNamespace(inc=inc)


class _FakeUpstreamMessage:
    def __init__(self, text: str) -> None:
        self.kind = "text"
        self.text = text
        self.data = None
        self.close_code = None
        self.error = None
        self.error_code = None


class _FakeUpstreamWebSocket:
    def __init__(self, response_id: str) -> None:
        self.sent_text: list[str] = []
        self.closed = False
        self._messages: asyncio.Queue[_FakeUpstreamMessage] = asyncio.Queue()
        for event in (
            {"type": "response.created", "response": {"id": response_id, "status": "in_progress"}},
            {"type": "response.completed", "response": {"id": response_id, "status": "completed"}},
        ):
            self._messages.put_nowait(_FakeUpstreamMessage(json.dumps(event, separators=(",", ":"))))

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)

    async def send_bytes(self, data: bytes) -> None:
        del data

    async def receive(self) -> _FakeUpstreamMessage:
        return await self._messages.get()

    def archive_received(self, message: _FakeUpstreamMessage) -> None:
        del message

    async def close(self) -> None:
        self.closed = True


def _attach_fake_subscription_upstream(monkeypatch: pytest.MonkeyPatch, upstream: _FakeUpstreamWebSocket) -> None:
    """An accepted session whose first turn is served by a fake subscription upstream; nothing dials ChatGPT."""

    account = SimpleNamespace(id="acct_ws_overflow_subscription", security_work_authorized=False)

    async def select_account(self, *args, request_state, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        del self, args, request_state, kwargs
        return account

    async def open_attempt(self, selected_account, headers, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        del self, headers, kwargs
        return selected_account, upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_websocket_connect_account", select_account)
    monkeypatch.setattr(proxy_module.ProxyService, "_try_open_websocket_connect_attempt", open_attempt)


# --- handshake: 426 only on evidence ----------------------------------------------------------------------


@pytest.mark.parametrize("url", [_CODEX_WS, _CODEX_V1_ALIAS_WS], ids=["native", "native-v1-alias"])
def test_live_pin_denies_the_handshake_before_accept_without_a_row(app_instance, monkeypatch, url: str) -> None:
    thread_id = "thr_ws_live_pin"

    async def fail_before_selection(*_args, **_kwargs):
        pytest.fail("a denied handshake must never reach account selection")

    monkeypatch.setattr(proxy_module.ProxyService, "_select_websocket_connect_account", fail_before_selection)

    with TestClient(app_instance, client=("127.0.0.1", 50000)) as client:
        source_id = _designate(client, app_instance, "ws-overflow-live-pin")
        assert client.portal is not None
        client.portal.call(
            _upsert_pins,
            utcnow(),
            PinWrite(thread_pin_key(_thread_key(thread_id)), PIN_KIND_THREAD, source_id, None),
        )

        denial = _expect_denial(client, url, _native_headers(thread_id))

        assert denial.status_code == 426
        body = denial.json()
        assert body["error"]["code"] == HANDSHAKE_DENIAL_CODE == "subscription_overflow_requires_http_transport"
        assert body["error"]["type"] == "server_error"
        assert client.portal.call(_request_log_error_codes) == [], "a denied handshake writes no request-log row"
        assert [row.kind for row in client.portal.call(_pin_rows)] == [PIN_KIND_THREAD], "the denial writes nothing"


def test_tombstoned_pin_denies_the_handshake(app_instance, monkeypatch) -> None:
    """An expired pin (tombstone) still denies: the HTTP route answers the neutral release or the 400."""

    thread_id = "thr_ws_tombstone"

    async def fail_before_selection(*_args, **_kwargs):
        pytest.fail("a denied handshake must never reach account selection")

    monkeypatch.setattr(proxy_module.ProxyService, "_select_websocket_connect_account", fail_before_selection)

    with TestClient(app_instance, client=("127.0.0.1", 50000)) as client:
        source_id = _designate(client, app_instance, "ws-overflow-tombstone")
        assert client.portal is not None
        # Written eight days ago: past the 7 d idle TTL, well inside the 21 d tombstone grace.
        client.portal.call(
            _upsert_pins,
            utcnow() - timedelta(days=8),
            PinWrite(thread_pin_key(_thread_key(thread_id)), PIN_KIND_THREAD, source_id, None),
        )

        denial = _expect_denial(client, _CODEX_WS, _native_headers(thread_id))

        assert denial.status_code == 426
        assert denial.json()["error"]["code"] == HANDSHAKE_DENIAL_CODE


def test_bounce_row_denies_the_handshake_until_it_expires(app_instance, monkeypatch) -> None:
    """Design §3: a bounce row lives ``WS_BOUNCE_TTL_SECONDS`` (``expires_at == purge_at``); then handshakes resume."""

    thread_id = "thr_ws_bounce_row"
    selections: list[str] = []

    async def record_selection(self, *args, request_state, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        del self, args, kwargs
        selections.append(request_state.request_id)
        return None

    monkeypatch.setattr(proxy_module.ProxyService, "_select_websocket_connect_account", record_selection)

    with TestClient(app_instance, client=("127.0.0.1", 50000)) as client:
        source_id = _designate(client, app_instance, "ws-overflow-bounce-row")
        assert client.portal is not None
        bounce = PinWrite(bounce_pin_key(_thread_key(thread_id)), PIN_KIND_BOUNCE, source_id, None)
        client.portal.call(_upsert_pins, utcnow(), bounce)

        denial = _expect_denial(client, _CODEX_WS, _native_headers(thread_id))
        assert denial.status_code == 426
        assert denial.json()["error"]["code"] == HANDSHAKE_DENIAL_CODE

        (row,) = client.portal.call(_pin_rows)
        assert row.kind == PIN_KIND_BOUNCE
        assert row.expires_at == row.purge_at
        assert row.expires_at - row.last_seen_at == timedelta(seconds=WS_BOUNCE_TTL_SECONDS)

        # The same row, written one TTL plus a second ago: expired and purged in one step -> accepted again.
        client.portal.call(_upsert_pins, utcnow() - timedelta(seconds=WS_BOUNCE_TTL_SECONDS + 1), bounce)
        _expect_accept(client, _CODEX_WS, _native_headers(thread_id))

    assert selections == [], "an accepted handshake without a frame never selects an account"


def test_exhausted_pool_without_evidence_is_accepted_and_the_capability_handshake_is_never_denied(
    app_instance, monkeypatch
) -> None:
    """Mutants: a speculative 426 on exhaustion alone, and a 426 for a capability handshake despite a live pin."""

    thread_id = "thr_ws_no_evidence"
    pinned_thread_id = "thr_ws_capability_pinned"

    with TestClient(app_instance, client=("127.0.0.1", 50000)) as client:
        assert client.portal is not None
        client.portal.call(_seed_exhausted_account, "acct_ws_overflow_no_evidence")
        source_id = _designate(client, app_instance, "ws-overflow-no-evidence")
        client.portal.call(
            _upsert_pins,
            utcnow(),
            PinWrite(thread_pin_key(_thread_key(pinned_thread_id)), PIN_KIND_THREAD, source_id, None),
        )
        authorization = client.portal.call(_create_api_key_authorization, "ws overflow capability")

        # Exhausted pool, no pin/tombstone/bounce for the thread: accepted, no probe at the handshake.
        _expect_accept(client, _CODEX_WS, _native_headers(thread_id))

        # A capability handshake resolves only on this transport; a live pin must not deny it.
        capability_headers = {
            **_native_headers(pinned_thread_id),
            "Authorization": authorization,
            CODEX_LB_REQUIRED_CAPABILITY_HEADER: "trusted_cyber",
        }
        _expect_accept(client, _CODEX_WS, capability_headers)

        # ... while the same pinned thread without the capability header is denied.
        assert _expect_denial(client, _CODEX_WS, _native_headers(pinned_thread_id)).status_code == 426


# --- in-band bounce on fresh exhaustion ---------------------------------------------------------------------


def test_exhausted_eligible_turn_is_bounced_in_band_and_the_next_handshake_is_denied(app_instance, monkeypatch) -> None:
    """Design §7.3 row 1 end to end: today's 429 event while dark, the 503 bounce + bounce row once designated,
    and the re-handshake meets the 426. The HTTP session bridge is never entered."""

    thread_id = "thr_ws_fresh_exhaustion"
    counter = _ObservedCounter()
    monkeypatch.setattr(prometheus_module, "subscription_overflow_total", counter, raising=False)

    with TestClient(app_instance, client=("127.0.0.1", 50000)) as client:
        assert client.portal is not None
        reset_at = client.portal.call(_seed_exhausted_account, "acct_ws_overflow_exhausted")

        # Dark (both columns NULL): the exhausted pool answers today's 429 event.
        with client.websocket_connect(_CODEX_WS, headers=_native_headers(thread_id)) as websocket:
            websocket.send_text(json.dumps(_response_create("hello while dark")))
            dark_event = json.loads(websocket.receive_text())
        assert dark_event["type"] == "error"
        assert dark_event["status"] == 429
        assert dark_event["error"]["code"] == "usage_limit_reached"
        assert dark_event["error"]["resets_at"] == reset_at
        assert client.portal.call(_pin_rows) == [], "no bounce row while the feature is dark"

        source_id = _designate(client, app_instance, "ws-overflow-fresh")

        # Designated: the handshake is still accepted (no evidence yet) and the turn is bounced in-band.
        with client.websocket_connect(_CODEX_WS, headers=_native_headers(thread_id)) as websocket:
            websocket.send_text(json.dumps(_response_create("hello while exhausted")))
            bounce_event = json.loads(websocket.receive_text())

        assert bounce_event["type"] == "error"
        assert bounce_event["status"] == 503, "a 4xx is terminal client-side; only a 503 makes Codex re-handshake"
        assert isinstance(bounce_event["status"], int)
        assert bounce_event["error"]["code"] == WS_BOUNCE_CODE == "model_source_requires_http_transport"
        assert bounce_event["error"]["type"] == "server_error"
        assert bounce_event["error"]["code"] not in _NON_RETRYABLE_CODES
        assert "resets_at" not in bounce_event["error"]

        (row,) = client.portal.call(_pin_rows)
        assert row.pin_key == bounce_pin_key(_thread_key(thread_id))
        assert row.pin_key == "bounce\n" + _thread_key(thread_id), "keyed by the thread alone, never per API key"
        assert row.kind == PIN_KIND_BOUNCE
        assert row.source_id == source_id
        assert row.api_key_id is None
        assert row.expires_at == row.purge_at
        assert row.expires_at - row.last_seen_at == timedelta(seconds=WS_BOUNCE_TTL_SECONDS)
        assert abs((_aware(row.expires_at) - datetime.now(timezone.utc)).total_seconds() - WS_BOUNCE_TTL_SECONDS) < 30

        # The retry's handshake meets the bounce evidence; an unrelated thread is still accepted.
        denial = _expect_denial(client, _CODEX_WS, _native_headers(thread_id))
        assert denial.status_code == 426
        assert denial.json()["error"]["code"] == HANDSHAKE_DENIAL_CODE
        _expect_accept(client, _CODEX_WS, _native_headers("thr_ws_other_thread"))

        client.portal.call(_drain_request_logs, app_instance)
        error_codes = client.portal.call(_request_log_error_codes)
        assert error_codes.count("usage_limit_reached") == 1, "today's 429 row while dark"
        assert error_codes.count(WS_BOUNCE_CODE) == 1, "the bounce finalizes the turn's row"
        assert client.portal.call(_bridge_session_count) == 0, "the HTTP session bridge never enters overflow"

    assert ("websocket", "bounced_ws_event") in counter.samples


def test_exhausted_turn_with_a_reasoning_item_keeps_todays_429_event(app_instance, monkeypatch) -> None:
    """A non-portable history is declined: the 429 event is byte-identical to the dark answer (no hint over WS)."""

    thread_id = "thr_ws_not_portable"
    history = [
        {"role": "user", "content": [{"type": "input_text", "text": "earlier"}]},
        {"type": "reasoning", "id": "rs_subscription_minted", "summary": [], "encrypted_content": "opaque"},
        {"role": "user", "content": [{"type": "input_text", "text": "continue"}]},
    ]

    with TestClient(app_instance, client=("127.0.0.1", 50000)) as client:
        assert client.portal is not None
        client.portal.call(_seed_exhausted_account, "acct_ws_overflow_history")

        with client.websocket_connect(_CODEX_WS, headers=_native_headers(thread_id)) as websocket:
            websocket.send_text(json.dumps(_response_create("x", input=history)))
            dark_text = websocket.receive_text()

        _designate(client, app_instance, "ws-overflow-history")

        with client.websocket_connect(_CODEX_WS, headers=_native_headers(thread_id)) as websocket:
            websocket.send_text(json.dumps(_response_create("x", input=history)))
            designated_text = websocket.receive_text()

        # The usage-limit message counts down to ``resets_at`` ("Try again in 300s"); mask the digits so a second
        # boundary between the two turns cannot fake a difference, and compare everything else byte for byte.
        assert _mask_countdown(designated_text) == _mask_countdown(dark_text)
        assert json.loads(designated_text)["status"] == 429
        assert client.portal.call(_pin_rows) == [], "a declined turn writes no bounce row"
        _expect_accept(client, _CODEX_WS, _native_headers(thread_id))


# --- pinned / anchored turns on an accepted socket ----------------------------------------------------------


def test_reused_socket_pinned_turn_is_bounced_with_reservation_released_and_row_finalized(
    app_instance, monkeypatch
) -> None:
    """A thread pinned mid-session (over HTTP, on any replica) is bounced on the open socket; the frame never
    reaches the attached subscription upstream and the turn's row is finalized."""

    thread_id = "thr_ws_reused_pinned"
    upstream = _FakeUpstreamWebSocket("resp_ws_overflow_turn_one")
    _attach_fake_subscription_upstream(monkeypatch, upstream)
    released: list[str] = []
    real_release = proxy_module.ProxyService._release_websocket_request_state_reservation

    async def spy_release(self, request_state, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        released.append(request_state.request_id)
        return await real_release(self, request_state, *args, **kwargs)

    monkeypatch.setattr(proxy_module.ProxyService, "_release_websocket_request_state_reservation", spy_release)

    with TestClient(app_instance, client=("127.0.0.1", 50000)) as client:
        assert client.portal is not None
        source_id = _designate(client, app_instance, "ws-overflow-reused")

        with client.websocket_connect(_CODEX_WS, headers=_native_headers(thread_id)) as websocket:
            websocket.send_text(json.dumps(_response_create("first turn on the subscription")))
            created = json.loads(websocket.receive_text())
            completed = json.loads(websocket.receive_text())
            assert created["type"] == "response.created" and completed["type"] == "response.completed"

            # Another replica pins the thread to the source between the turns.
            client.portal.call(
                _upsert_pins,
                utcnow(),
                PinWrite(thread_pin_key(_thread_key(thread_id)), PIN_KIND_THREAD, source_id, None),
            )
            released_before = len(released)

            websocket.send_text(json.dumps(_response_create("second turn on the pinned thread")))
            bounce_event = json.loads(websocket.receive_text())

        assert bounce_event["type"] == "error"
        assert bounce_event["status"] == 503
        assert bounce_event["error"]["code"] == WS_BOUNCE_CODE
        assert len(upstream.sent_text) == 1, "the pinned turn must not be forwarded to the subscription upstream"
        assert len(released) > released_before, "the bounced turn releases its usage reservation"

        rows = {row.kind: row for row in client.portal.call(_pin_rows)}
        assert rows[PIN_KIND_BOUNCE].pin_key == bounce_pin_key(_thread_key(thread_id))
        assert rows[PIN_KIND_BOUNCE].source_id == source_id

        client.portal.call(_drain_request_logs, app_instance)
        assert WS_BOUNCE_CODE in client.portal.call(_request_log_error_codes), "the turn's row is finalized"

        # The client's retry re-handshakes and meets the pin.
        assert _expect_denial(client, _CODEX_WS, _native_headers(thread_id)).status_code == 426


def test_anchored_previous_response_id_is_bounced_over_websocket(app_instance, monkeypatch) -> None:
    """An SDK chain anchored on a source-minted response id returns to its source: the socket turn is bounced."""

    previous_response_id = "resp_source_minted_ws_anchor"
    owner_lookups: list[str] = []

    async def no_subscription_owner(
        self, *, previous_response_id, api_key, session_id=None, surface, request_state=None, **kwargs
    ):  # noqa: ANN001, ANN003, ANN202, E501
        del self, api_key, session_id, surface, request_state, kwargs
        owner_lookups.append(previous_response_id)
        return None

    async def fail_before_selection(*_args, **_kwargs):
        pytest.fail("an anchored continuation must not select a subscription account")

    monkeypatch.setattr(proxy_module.ProxyService, "_resolve_websocket_previous_response_owner", no_subscription_owner)
    monkeypatch.setattr(proxy_module.ProxyService, "_select_websocket_connect_account", fail_before_selection)

    with TestClient(app_instance, client=("127.0.0.1", 50000)) as client:
        assert client.portal is not None
        source_id = _designate(client, app_instance, "ws-overflow-anchor")
        client.portal.call(
            _upsert_pins,
            utcnow(),
            PinWrite(anchor_pin_key(None, previous_response_id), PIN_KIND_ANCHOR, source_id, None),
        )

        # No thread key (SDK client): the handshake has no thread evidence and is accepted; the turn is bounced.
        with client.websocket_connect(_CODEX_WS, headers={"user-agent": "openai-python/1.0"}) as websocket:
            websocket.send_text(json.dumps(_response_create("continue", previous_response_id=previous_response_id)))
            event = json.loads(websocket.receive_text())

        assert event["type"] == "error"
        assert event["status"] == 503
        assert event["error"]["code"] == WS_BOUNCE_CODE
        assert owner_lookups == [previous_response_id]
        assert [row.kind for row in client.portal.call(_pin_rows)] == [PIN_KIND_ANCHOR], (
            "no thread key -> no bounce row"
        )


def test_recorded_subscription_owner_is_never_bounced_by_an_anchor(app_instance, monkeypatch) -> None:
    """Identifier syntax is not ownership: a recorded subscription owner keeps the turn on its account."""

    previous_response_id = "resp_subscription_owned_ws"
    upstream = _FakeUpstreamWebSocket("resp_ws_owned_completed")
    _attach_fake_subscription_upstream(monkeypatch, upstream)

    async def recorded_owner(
        self, *, previous_response_id, api_key, session_id=None, surface, request_state=None, **kwargs
    ):  # noqa: ANN001, ANN003, ANN202, E501
        del self, previous_response_id, api_key, session_id, surface, request_state, kwargs
        return "acct_ws_overflow_subscription"

    monkeypatch.setattr(proxy_module.ProxyService, "_resolve_websocket_previous_response_owner", recorded_owner)

    with TestClient(app_instance, client=("127.0.0.1", 50000)) as client:
        assert client.portal is not None
        source_id = _designate(client, app_instance, "ws-overflow-owned")
        client.portal.call(
            _upsert_pins,
            utcnow(),
            PinWrite(anchor_pin_key(None, previous_response_id), PIN_KIND_ANCHOR, source_id, None),
        )

        with client.websocket_connect(_CODEX_WS, headers={"user-agent": "openai-python/1.0"}) as websocket:
            websocket.send_text(json.dumps(_response_create("continue", previous_response_id=previous_response_id)))
            created = json.loads(websocket.receive_text())
            completed = json.loads(websocket.receive_text())

    assert created["type"] == "response.created" and completed["type"] == "response.completed"
    assert json.loads(upstream.sent_text[0])["previous_response_id"] == previous_response_id
