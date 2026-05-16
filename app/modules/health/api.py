from __future__ import annotations

from datetime import timedelta
from hashlib import sha256
from ipaddress import ip_address

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select as sa_select
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.settings import get_settings
from app.core.utils.time import utcnow
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

    shutdown_state.set_bridge_drain_active(True)
    shutdown_state.set_draining(True)

    proxy_service = getattr(request.app.state, "proxy_service", None)
    if proxy_service is not None and hasattr(proxy_service, "mark_http_bridge_draining"):
        await proxy_service.mark_http_bridge_draining()

    return HealthCheckResponse(status="ok", checks={"draining": "ok"})


@router.get("/internal/drain/status", include_in_schema=False)
async def internal_drain_status(request: Request) -> HealthCheckResponse:
    client_host = request.client.host if request.client is not None else None
    if not _is_internal_client_host(client_host):
        raise HTTPException(status_code=403, detail="Internal access required")

    import app.core.shutdown as shutdown_state

    return HealthCheckResponse(
        status="ok",
        checks={
            "draining": str(shutdown_state.is_draining()).lower(),
            "bridge_drain_active": str(shutdown_state.is_bridge_drain_active()).lower(),
            "in_flight": str(shutdown_state.get_in_flight()),
        },
    )


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
    if bridge_ring.ring_size == 0:
        return None
    if bridge_ring.is_member:
        return None
    return "Service is not an active bridge ring member"


async def _get_bridge_ring_info(session: AsyncSession) -> BridgeRingInfo:
    try:
        settings = get_settings()
        instance_id = getattr(settings, "http_responses_session_bridge_instance_id", None)

        cutoff = utcnow() - timedelta(seconds=RING_STALE_THRESHOLD_SECONDS)
        result = await session.execute(
            sa_select(BridgeRingMember.instance_id)
            .where(BridgeRingMember.last_heartbeat_at >= cutoff)
            .order_by(BridgeRingMember.instance_id)
        )
        active_members = list(result.scalars().all())
        data = ",".join(sorted(active_members))
        fingerprint = sha256(data.encode()).hexdigest()
        is_member = instance_id in active_members if instance_id else False

        return BridgeRingInfo(
            ring_fingerprint=fingerprint,
            ring_size=len(active_members),
            instance_id=instance_id,
            is_member=is_member,
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
