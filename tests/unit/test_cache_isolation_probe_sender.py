"""The probe's routed upstream call: what it reports, and what it cleans up.

The sender is the only place the probe touches the network, so it owns two
contracts the orchestrator cannot check: a routed-transport failure becomes a
typed failed row rather than an exception, and the upstream stream is closed
before the next sequential call starts.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from app.core.clients.codex import CodexTransportError
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.modules.accounts.repository import AccountsRepository
from app.modules.cache_isolation_probe import sender as sender_module
from app.modules.cache_isolation_probe.sender import CacheProbeSender

pytestmark = pytest.mark.unit


def _account() -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id="probe-account",
        chatgpt_account_id="workspace",
        email="probe@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


def _completed_event(*, input_tokens: int, cached_tokens: int) -> str:
    payload = {
        "type": "response.completed",
        "response": {
            "id": "resp-1",
            "status": "completed",
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": 1,
                "total_tokens": input_tokens + 1,
                "input_tokens_details": {"cached_tokens": cached_tokens},
            },
        },
    }
    return f"data: {json.dumps(payload)}\n\n"


@asynccontextmanager
async def _unused_accounts_repo() -> AsyncIterator[AccountsRepository]:
    """Every test below patches the two methods that would open a repository."""

    raise AssertionError("the probe sender must not open an accounts repository here")
    yield  # pragma: no cover - unreachable, keeps this an async generator


def _build(monkeypatch, stream_factory) -> CacheProbeSender:
    probe_sender = CacheProbeSender(_unused_accounts_repo)
    account = _account()

    async def _refreshed(_account_id: str) -> Account:
        return account

    async def _route(_account: Account) -> None:
        return None

    monkeypatch.setattr(probe_sender, "_refreshed_account", _refreshed)
    monkeypatch.setattr(probe_sender, "_resolve_route", _route)
    monkeypatch.setattr(sender_module, "stream_responses", stream_factory)
    return probe_sender


async def test_a_completed_stream_reports_the_cached_token_counts(monkeypatch) -> None:
    async def _stream(*args, **kwargs) -> AsyncIterator[str]:
        del args, kwargs
        yield _completed_event(input_tokens=28_168, cached_tokens=28_032)

    probe_sender = _build(monkeypatch, _stream)

    result = await probe_sender.send("probe-account", model="gpt-probe", prefix="inert")

    assert result.ok is True
    assert result.input_tokens == 28_168
    assert result.cached_tokens == 28_032


async def test_the_upstream_stream_is_closed_before_send_returns(monkeypatch) -> None:
    """A mid-stream return would otherwise leave the upstream response to
    async-generator finalization, overlapping the next sequential call."""

    closed: list[bool] = []

    async def _stream(*args, **kwargs) -> AsyncIterator[str]:
        del args, kwargs
        try:
            yield _completed_event(input_tokens=28_168, cached_tokens=0)
            yield "data: never-consumed\n\n"
        finally:
            closed.append(True)

    probe_sender = _build(monkeypatch, _stream)

    result = await probe_sender.send("probe-account", model="gpt-probe", prefix="inert")

    assert result.ok is True
    assert closed == [True]


async def test_a_routed_transport_failure_becomes_a_typed_failed_row(monkeypatch) -> None:
    async def _stream(*args, **kwargs) -> AsyncIterator[str]:
        del args, kwargs
        raise CodexTransportError("upstream refused the connection", error_code="upstream_unreachable")
        yield ""  # pragma: no cover - unreachable, keeps this an async generator

    probe_sender = _build(monkeypatch, _stream)

    result = await probe_sender.send("probe-account", model="gpt-probe", prefix="inert")

    assert result.ok is False
    assert result.error_code == "upstream_unreachable"


async def test_a_stream_that_never_terminates_is_reported_not_raised(monkeypatch) -> None:
    async def _stream(*args, **kwargs) -> AsyncIterator[str]:
        del args, kwargs
        yield 'data: {"type": "response.in_progress"}\n\n'

    probe_sender = _build(monkeypatch, _stream)

    result = await probe_sender.send("probe-account", model="gpt-probe", prefix="inert")

    assert result.ok is False
    assert result.error_code == "stream_incomplete"
