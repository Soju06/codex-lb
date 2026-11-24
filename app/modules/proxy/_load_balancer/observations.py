"""Read-only operator observations of replica-local routing state."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.modules.proxy._load_balancer.types import AccountRoutingObservation, RuntimeState

if TYPE_CHECKING:
    from app.modules.proxy.load_balancer import LoadBalancer


def _utc_timestamp(value: float | None) -> datetime | None:
    return datetime.fromtimestamp(value, tz=timezone.utc) if value is not None else None


async def account_routing_observation(balancer: LoadBalancer, account_id: str) -> AccountRoutingObservation:
    async with balancer._runtime_lock:
        snapshot = balancer._detached_runtime_snapshot(routing_tunables=balancer.current_routing_tunables())
        runtime = snapshot.get(account_id, RuntimeState())
    return AccountRoutingObservation(
        account_id=account_id,
        health_tier=runtime.health_tier,
        inflight_streams=runtime.inflight_streams,
        inflight_response_creates=runtime.inflight_response_creates,
        recent_errors=runtime.error_count,
        last_selected_at=_utc_timestamp(runtime.last_selected_at),
        cooldown_until=_utc_timestamp(runtime.cooldown_until),
        overload_backoff_until=_utc_timestamp(runtime.overload_backoff_until),
        burst_backoff_until=_utc_timestamp(runtime.burst_backoff_until),
    )
