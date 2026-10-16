from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Literal, TypedDict, Unpack

import pytest

from app.core.clock import clock_for
from app.modules.proxy._service.http_bridge import quarantine
from tests.integration.test_http_responses_bridge import _collect_sse_events, _promotion_history

if TYPE_CHECKING:
    from httpx import AsyncClient

    from app.core.types import JsonValue
    from app.modules.proxy._service.support import _HTTPBridgeSession
    from app.modules.proxy.service import ProxyService


class _CircuitKey(TypedDict):
    session_key_kind: str
    session_key_value: str
    api_key_id: str | None


@pytest.fixture(autouse=True)
def quarantine_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(quarantine, "clock_for", lambda _: SimpleNamespace(monotonic=lambda: 100.0))


async def complete(
    async_client: AsyncClient,
    *,
    session_id: str | None = None,
    history: list[dict[str, JsonValue]] | None = None,
) -> list[dict[str, JsonValue]]:
    body = {
        "model": "gpt-5.4",
        "instructions": "test",
        "stream": True,
        "input": _promotion_history() if history is None else history,
    }
    events = await _collect_sse_events(
        async_client,
        "/v1/responses",
        json_body=body,
        headers={"session_id": session_id} if session_id else None,
    )
    assert events[-1]["type"] == "response.completed"
    return events


async def seed_durable_poison(service: ProxyService, *, cooldown: float = 900.0, **key: Unpack[_CircuitKey]) -> None:
    now = clock_for(service).time()
    await service._durable_bridge.persist_retry_circuit(
        **key,
        consecutive_failures=2,
        cooldown_until_epoch=now + cooldown,
        last_detail="stream_incomplete",
        updated_at_epoch=now,
    )


def arm(
    service: ProxyService,
    session: _HTTPBridgeSession,
    evidence: Literal["poison", "weaker", "first-strike"],
) -> float:
    if evidence == "first-strike":
        quarantine._record_http_bridge_quarantine_eventless_timeout(service, session)
    else:
        quarantine._quarantine_http_bridge_session(
            service,
            session,
            reason="retry_circuit_poisoned_anchor" if evidence == "poison" else "reattach_missing_response_created",
        )
    return quarantine._http_bridge_quarantine_registry(service)[session.key].quarantined_until
