from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.core.config.settings import get_settings
from app.core.resilience.circuit_breaker import CircuitState, get_circuit_breaker
from app.core.resilience.degradation import get_status, is_degraded
from app.db.session import SessionLocal, get_session
from app.modules.health.schemas import BridgeRingInfo, HealthCheckResponse, HealthResponse
from app.modules.proxy.ring_membership import RingMembershipService

router = APIRouter(tags=["health"])


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

                degradation_status = get_status() if is_degraded() else None
                if degradation_status is not None:
                    checks["upstream"] = "degraded"
                    checks["upstream_reason"] = degradation_status["reason"] or "unknown"
                    status = "degraded"
                else:
                    settings = get_settings()
                    circuit_breaker = get_circuit_breaker(settings) if settings.circuit_breaker_enabled else None
                    if circuit_breaker is not None and circuit_breaker.state == CircuitState.OPEN:
                        raise HTTPException(status_code=503, detail="Circuit breaker open — upstream unavailable")

                # Add bridge ring consistency info (best-effort, informational only)
                bridge_ring = await _get_bridge_ring_info()

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


async def _get_bridge_ring_info() -> BridgeRingInfo:
    """Get bridge ring consistency info. Best-effort; returns error field if unavailable."""
    settings = get_settings()
    instance_id = settings.http_responses_session_bridge_instance_id

    try:
        ring_service = RingMembershipService(SessionLocal)
        active_members = await ring_service.list_active()
        fingerprint = await ring_service.ring_fingerprint()
        is_member = instance_id in active_members

        return BridgeRingInfo(
            ring_fingerprint=fingerprint,
            ring_size=len(active_members),
            instance_id=instance_id,
            is_member=is_member,
        )
    except Exception as e:
        # DB unavailable or other error; return informational error
        return BridgeRingInfo(
            ring_fingerprint=None,
            ring_size=0,
            instance_id=instance_id,
            is_member=False,
            error=f"unavailable: {type(e).__name__}",
        )


@router.get("/health/startup", response_model=HealthCheckResponse)
async def health_startup() -> HealthCheckResponse:
    import app.core.startup as startup_module

    if startup_module._startup_complete:
        return HealthCheckResponse(status="ok")
    else:
        raise HTTPException(status_code=503, detail="Service is starting")
