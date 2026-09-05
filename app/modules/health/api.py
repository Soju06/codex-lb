from __future__ import annotations

import math
from datetime import timedelta
from hashlib import sha256
from ipaddress import ip_address
from typing import cast

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import or_ as sa_or
from sqlalchemy import select as sa_select
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.settings import get_settings
from app.core.shutdown import DRAIN_DEADLINE_HEADER
from app.core.utils.time import to_utc_naive, utcnow
from app.db.models import BridgeRingMember
from app.db.session import get_session
from app.modules.health.schemas import BridgeRingInfo, HealthCheckResponse, HealthResponse
from app.modules.proxy.ring_membership import RING_STALE_THRESHOLD_SECONDS

router = APIRouter(tags=["health"])


def _is_internal_client_host(client_host: str | None) -> bool:
    if client_host in {"localhost"}:
        return True
    if client_host is None:
        return False
    try:
        address = ip_address(client_host)
    except ValueError:
        return False
    return address.is_loopback


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/health/live", response_model=HealthCheckResponse)
async def health_live() -> HealthCheckResponse:
    return HealthCheckResponse(status="ok")


@router.get("/health/ready", response_model=HealthCheckResponse)
async def health_ready() -> HealthCheckResponse:
    draining = False
    try:
        import app.core.draining as draining_module

        draining = getattr(draining_module, "_draining", False)
    except (ImportError, AttributeError):
        pass

    if draining:
        raise HTTPException(status_code=503, detail="Service is draining")

    try:
        async for session in get_session():
            try:
                await session.execute(text("SELECT 1"))
                checks = {"database": "ok"}
                status = "ok"

                # Upstream health (degradation flag, circuit breaker) is NOT
                # checked here — only infrastructure readiness matters.
                # Mixing upstream state into readiness causes permanent
                # pod eviction after transient upstream failures.

                bridge_ring = await _get_bridge_ring_info(session)
                failure_detail = _bridge_readiness_failure_detail(bridge_ring)
                if failure_detail in {
                    "Service is not an active bridge ring member",
                    "Service bridge ring metadata is unavailable",
                }:
                    payload = HealthCheckResponse(
                        status="unavailable",
                        checks=checks,
                        bridge_ring=bridge_ring,
                    ).model_dump(mode="json")
                    payload["detail"] = failure_detail
                    return cast(HealthCheckResponse, JSONResponse(status_code=503, content=payload))
                if failure_detail is not None:
                    raise HTTPException(status_code=503, detail=failure_detail)

                return HealthCheckResponse(status=status, checks=checks, bridge_ring=bridge_ring)
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(
                    status_code=503,
                    detail="Service unavailable",
                )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Service unavailable",
        )

    raise HTTPException(status_code=503, detail="Service unavailable")


@router.post("/internal/drain/start", include_in_schema=False)
async def start_internal_drain(request: Request) -> HealthCheckResponse:
    client_host = request.client.host if request.client is not None else None
    if not _is_internal_client_host(client_host):
        raise HTTPException(status_code=403, detail="Internal access required")

    import app.core.shutdown as shutdown_state

    deadline_header = getattr(request, "headers", {}).get(DRAIN_DEADLINE_HEADER)
    deadline_monotonic: float | None = None
    if deadline_header is not None:
        try:
            deadline_monotonic = float(deadline_header)
            if not math.isfinite(deadline_monotonic):
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Invalid drain deadline") from exc

    timeout_seconds = get_settings().shutdown_drain_timeout_seconds
    if deadline_monotonic is None:
        effective_deadline = shutdown_state.begin_drain(timeout_seconds=timeout_seconds)
    else:
        effective_deadline = shutdown_state.commit_shutdown(
            timeout_seconds=timeout_seconds,
            deadline_monotonic=deadline_monotonic,
        )

    proxy_service = getattr(request.app.state, "proxy_service", None)
    if proxy_service is not None and hasattr(proxy_service, "mark_http_bridge_draining"):
        await proxy_service.mark_http_bridge_draining()

    return HealthCheckResponse(
        status="ok",
        checks={
            "draining": str(shutdown_state.is_draining()).lower(),
            "shutdown_committed": str(shutdown_state.is_shutdown_committed()).lower(),
            "deadline_monotonic": format(effective_deadline, ".17g"),
        },
    )


@router.post("/internal/drain/stop", include_in_schema=False)
async def stop_internal_drain(request: Request) -> HealthCheckResponse:
    client_host = request.client.host if request.client is not None else None
    if not _is_internal_client_host(client_host):
        raise HTTPException(status_code=403, detail="Internal access required")

    import app.core.shutdown as shutdown_state

    if not shutdown_state.stop_drain():
        raise HTTPException(status_code=409, detail="Process shutdown is already committed")

    return HealthCheckResponse(status="ok", checks={"draining": "false"})


@router.get("/internal/drain/status", include_in_schema=False)
async def internal_drain_status(request: Request) -> HealthCheckResponse:
    client_host = request.client.host if request.client is not None else None
    if not _is_internal_client_host(client_host):
        raise HTTPException(status_code=403, detail="Internal access required")

    import app.core.shutdown as shutdown_state

    checks = {
        "draining": str(shutdown_state.is_draining()).lower(),
        "bridge_drain_active": str(shutdown_state.is_bridge_drain_active()).lower(),
        "in_flight": str(shutdown_state.get_in_flight()),
    }

    app = getattr(request, "app", None)
    app_state = getattr(app, "state", None)
    proxy_service = getattr(app_state, "proxy_service", None)
    if proxy_service is not None and hasattr(proxy_service, "http_bridge_activity_snapshot_nowait"):
        try:
            bridge_activity = proxy_service.http_bridge_activity_snapshot_nowait()
            checks.update(
                {
                    key: str(value).lower() if isinstance(value, bool) else str(value)
                    for key, value in bridge_activity.items()
                }
            )
        except Exception as exc:
            checks["http_bridge_activity_error"] = type(exc).__name__

    return HealthCheckResponse(status="ok", checks=checks)


def _bridge_readiness_failure_detail(bridge_ring: BridgeRingInfo) -> str | None:
    import app.core.startup as startup_module

    settings = get_settings()
    if not getattr(settings, "http_responses_session_bridge_enabled", True):
        return None
    if not startup_module._bridge_durable_schema_ready:
        return "Service bridge durable schema is not ready"
    if not startup_module._bridge_registration_complete:
        return "Service bridge registration is not complete"
    if bridge_ring.error is not None:
        return "Service bridge ring metadata is unavailable"
    if bridge_ring.is_member:
        return None
    return "Service is not an active bridge ring member"


async def _get_bridge_ring_info(session: AsyncSession) -> BridgeRingInfo:
    try:
        settings = get_settings()
        instance_id = getattr(settings, "http_responses_session_bridge_instance_id", None)

        now = utcnow()
        cutoff = now - timedelta(seconds=RING_STALE_THRESHOLD_SECONDS)
        statement = sa_select(
            BridgeRingMember.instance_id,
            BridgeRingMember.last_heartbeat_at,
        )
        if instance_id:
            statement = statement.where(
                sa_or(
                    BridgeRingMember.last_heartbeat_at >= cutoff,
                    BridgeRingMember.instance_id == instance_id,
                )
            )
        else:
            statement = statement.where(BridgeRingMember.last_heartbeat_at >= cutoff)
        result = await session.execute(statement.order_by(BridgeRingMember.instance_id))
        rows = [(member_id, to_utc_naive(heartbeat_at)) for member_id, heartbeat_at in result.all()]
        active_members = [member_id for member_id, heartbeat_at in rows if heartbeat_at >= cutoff]
        local_heartbeat_at = next(
            (heartbeat_at for member_id, heartbeat_at in rows if member_id == instance_id),
            None,
        )
        heartbeat_age_seconds = (
            max((now - local_heartbeat_at).total_seconds(), 0.0) if local_heartbeat_at is not None else None
        )
        data = ",".join(active_members)
        fingerprint = sha256(data.encode()).hexdigest()
        is_member = instance_id in active_members if instance_id else False

        return BridgeRingInfo(
            ring_fingerprint=fingerprint,
            ring_size=len(active_members),
            instance_id=instance_id,
            is_member=is_member,
            heartbeat_age_seconds=heartbeat_age_seconds,
        )
    except Exception as e:
        return BridgeRingInfo(
            ring_fingerprint=None,
            ring_size=0,
            instance_id=None,
            is_member=False,
            error=f"unavailable: {type(e).__name__}",
        )


@router.get("/health/startup", response_model=HealthCheckResponse)
async def health_startup() -> HealthCheckResponse:
    import app.core.startup as startup_module

    if startup_module._startup_complete:
        return HealthCheckResponse(status="ok")
    raise HTTPException(status_code=503, detail="Service is starting")
