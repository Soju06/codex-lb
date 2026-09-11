"""Persisted quota eligibility feeds owner-only probe cleanup."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import timezone
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from app.core.clients.proxy_websocket import UpstreamWebSocketMessage
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.http_bridge import mixin as bridge_mixin
from app.modules.proxy._service.support import _HTTPBridgeResponseCreateAttempt
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.usage.repository import UsageRepository
from tests.integration.test_load_balancer_integration import _repo_factory
from tests.unit.test_http_bridge_accepted_retirement import _configure
from tests.unit.test_proxy_http_bridge import (
    _activate_half_open_probe,
    _make_bridge_session,
    _make_eventless_http_bridge_owner,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_persisted_exhausted_owner_returns_exact_probe_without_fallback(db_setup, monkeypatch):
    now = utcnow()
    epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    reset = epoch + 5 * 24 * 3600
    owner = Account(
        id="proof-exhausted-owner",
        email="owner@example.invalid",
        plan_type="plus",
        access_token_encrypted=b"unused-test-access",
        refresh_token_encrypted=b"unused-test-refresh",
        id_token_encrypted=b"unused-test-id",
        last_refresh=now,
        status=AccountStatus.QUOTA_EXCEEDED,
        reset_at=epoch - 1,
        blocked_at=epoch - 3601,
    )
    alternate = Account(
        id="proof-healthy-alternate",
        email="alternate@example.invalid",
        plan_type="plus",
        access_token_encrypted=b"unused-test-access",
        refresh_token_encrypted=b"unused-test-refresh",
        id_token_encrypted=b"unused-test-id",
        last_refresh=now,
        status=AccountStatus.ACTIVE,
    )
    async with SessionLocal() as db:
        accounts, usage = AccountsRepository(db), UsageRepository(db)
        await accounts.upsert(owner)
        await accounts.upsert(alternate)
        for account, primary, secondary in ((owner, 15.0, 100.0), (alternate, 20.0, 50.0)):
            await usage.add_entry(
                account_id=account.id,
                used_percent=primary,
                window="primary",
                reset_at=epoch + 300,
                window_minutes=300,
                recorded_at=now,
                credits_has=False,
                credits_unlimited=False,
                credits_balance=0.0,
            )
            await usage.add_entry(
                account_id=account.id,
                used_percent=secondary,
                window="secondary",
                reset_at=reset,
                window_minutes=10080,
                recorded_at=now,
            )
    get_account_selection_cache().invalidate()

    service = proxy_service.ProxyService(_repo_factory)
    _configure(monkeypatch, service)
    session = _make_bridge_session(key_value="persisted-exhausted-owner")
    session.account = owner
    request = _make_eventless_http_bridge_owner(sent_at=time.monotonic())
    request.started_at = time.monotonic()
    request.skip_request_log = True
    request.request_text = json.dumps({"type": "response.create", "model": request.model, "input": []})
    request.file_required_preferred_account = True
    request.preferred_account_id = owner.id
    attempt = _HTTPBridgeResponseCreateAttempt(ordinal=1)
    request.response_create_attempt = attempt
    session.pending_requests.append(request)
    session.queued_request_count = 1
    service._http_bridge_sessions[session.key] = session
    old_lease = await service._load_balancer.acquire_account_lease(owner.id, kind="stream")
    assert old_lease is not None
    session.account_lease = old_lease
    circuit = _activate_half_open_probe(service, session)
    circuit.half_open_until = 0.0
    circuit.half_open_owner_session = None
    circuit.cooldown_until = time.monotonic() - 1.0
    upstream = cast(Any, session.upstream)
    upstream.receive = AsyncMock(return_value=UpstreamWebSocketMessage(kind="close", close_code=1011))
    upstream.send_text = AsyncMock()
    open_socket = AsyncMock(side_effect=AssertionError("unavailable owner must not dispatch any socket"))
    monkeypatch.setattr(service, "_open_upstream_websocket_with_budget", open_socket)
    monkeypatch.setattr(service, "_ensure_fresh_with_budget", AsyncMock(side_effect=lambda account, **kwargs: account))
    recovery = AsyncMock(wraps=bridge_mixin._sleep_for_account_selection_recovery)
    monkeypatch.setattr(bridge_mixin, "_sleep_for_account_selection_recovery", recovery)
    selections = []
    actual_select = service._select_account_with_budget_for_stream

    async def observe_selection(*args: Any, **kwargs: Any):
        result = await actual_select(*args, **kwargs)
        selections.append((kwargs, result))
        return result

    monkeypatch.setattr(service, "_select_account_with_budget_for_stream", observe_selection)
    actual_admit = service._http_bridge_precreated_retry_allowed
    claims = []

    async def observe_admission(*args: Any, **kwargs: Any):
        result = await actual_admit(*args, **kwargs)
        claims.extend(kwargs["claimed_lease_out"])
        return result

    monkeypatch.setattr(service, "_http_bridge_precreated_retry_allowed", observe_admission)
    actual_return = service._release_http_bridge_retry_circuit_half_open
    returns = []

    async def observe_return(target: Any, **kwargs: Any):
        owned = (circuit.half_open_until, circuit.half_open_owner_token, circuit.half_open_lease_generation)
        result = await actual_return(target, **kwargs)
        if result:
            assert target is session and kwargs["probe_owner"] is request
            assert session.key not in service._http_bridge_sessions and attempt.disarmed
            returns.append(owned)
        return result

    monkeypatch.setattr(service, "_release_http_bridge_retry_circuit_half_open", observe_return)
    release = AsyncMock(wraps=service._load_balancer.release_account_lease)
    acquire = AsyncMock(wraps=service._load_balancer.acquire_account_lease)
    monkeypatch.setattr(service._load_balancer, "release_account_lease", release)
    monkeypatch.setattr(service._load_balancer, "acquire_account_lease", acquire)
    reader = asyncio.create_task(service._relay_http_bridge_upstream_messages(session))
    session.upstream_reader = reader
    try:
        await asyncio.wait_for(asyncio.shield(reader), timeout=5.0)
    finally:
        if not reader.done():
            reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)

    assert not reader.cancelled()
    recovery.assert_not_awaited()
    assert len(selections) == 1
    kwargs, selection = selections[0]
    assert kwargs["preferred_account_id"] == owner.id
    assert kwargs["fallback_on_preferred_account_unavailable"] is False
    assert selection.account is None and selection.error_code == "continuity_owner_unavailable"
    assert len(claims) == 1 and claims[0][1] is request and claims[0][2] > 0
    assert returns == claims
    assert circuit.half_open_until == 0.0 and circuit.half_open_owner_token is None
    assert circuit.consecutive_failures == 2
    open_socket.assert_not_awaited()
    upstream.send_text.assert_not_awaited()
    acquire.assert_not_awaited()
    assert [call.args[0] for call in release.await_args_list if call.args[0] is not None] == [old_lease]
    upstream.close.assert_awaited_once()
    assert service._load_balancer._runtime[owner.id].inflight_streams == 0
    assert session.account_lease is None and session.pending_account_lease_releases == []
    assert session.upstream_reader is None and not session.pending_requests
    assert not session.handoff_in_progress and session.handoff_future is None
    assert session.key not in service._http_bridge_inflight_sessions
    assert session.key not in service._http_bridge_sessions
    assert id(session) not in service._http_bridge_detached_sessions
    assert not session.response_create_gate.locked()
    assert request.event_queue is not None
    terminal = request.event_queue.get_nowait()
    assert terminal is not None and "previous_response_owner_unavailable" in terminal
    assert request.event_queue.get_nowait() is None and request.event_queue.empty()
    service._handle_stream_error.assert_not_awaited()

    async with SessionLocal() as db:
        persisted = await db.get(Account, owner.id)
        assert persisted is not None and persisted.status == AccountStatus.QUOTA_EXCEEDED
        assert persisted.reset_at == reset
    # A healthy alternate was available, but the owner-bound reconnect never dispatched it.
    ordinary = await service._load_balancer.select_account()
    assert ordinary.account is not None and ordinary.account.id == alternate.id
