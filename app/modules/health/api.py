from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.core.config.settings import get_settings
from app.core.resilience.circuit_breaker import CircuitState, get_circuit_breaker
from app.core.resilience.degradation import get_status, is_degraded
from app.db.session import get_session
from app.modules.health.schemas import HealthCheckResponse, HealthResponse

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
        import app.core.draining as draining_module  # type: ignore[import-not-found]

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
                        checks["upstream"] = "degraded"
                        checks["upstream_reason"] = "upstream circuit breaker is open"
                        status = "degraded"

                return HealthCheckResponse(status=status, checks=checks)
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


@router.get("/health/startup", response_model=HealthCheckResponse)
async def health_startup() -> HealthCheckResponse:
    import app.core.startup as startup_module

    if startup_module._startup_complete:
        return HealthCheckResponse(status="ok")
    else:
        raise HTTPException(status_code=503, detail="Service is starting")
