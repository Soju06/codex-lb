"""Operator endpoints for the cross-account prompt cache isolation probe.

Both routes sit behind ``ops:write`` -- the same gate as the other
operational actions that change what the fleet does -- because running the
probe bills the account pool. ``GET`` is the cost preview the operator reads
before deciding; ``POST`` is the spend, and it refuses to act without an
explicit confirmation, a shared rate-limit budget, and a pool that is not
already under pressure.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request

from app.core.audit.service import AuditActor, AuditService, AuditTarget
from app.core.auth.dashboard_access import DashboardPrincipal, Permission
from app.core.auth.dependencies import (
    require_dashboard_permission,
    set_dashboard_error_format,
    validate_dashboard_session,
)
from app.core.exceptions import DashboardBadRequestError, DashboardConflictError
from app.core.rate_limiter.db_rate_limiter import DatabaseRateLimiter
from app.db.session import get_background_session
from app.modules.cache_isolation_probe.schemas import (
    CacheProbeAccountResponse,
    CacheProbeCallResponse,
    CacheProbePlanResponse,
    CacheProbePressureResponse,
    CacheProbeRunRequest,
    CacheProbeRunResponse,
)
from app.modules.cache_isolation_probe.service import (
    CacheIsolationProbeService,
    CacheProbeRefused,
    ProbeAccount,
    ProbePlan,
    ProbeResult,
)

router = APIRouter(
    prefix="/api/diagnostics/cache-isolation-probe",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)

#: Deliberately a *shared* budget, not a per-principal one: the resource being
#: protected is the account pool's quota, which two operators drain just as
#: fast as one. Three runs an hour leaves room for a before/after comparison
#: plus a retry without turning the diagnostic into a traffic source.
_RATE_LIMIT_KEY = "cache-isolation-probe"
_probe_rate_limiter = DatabaseRateLimiter(max_attempts=3, window_seconds=3600, type="cache_isolation_probe")

_service = CacheIsolationProbeService()


def get_cache_isolation_probe_service() -> CacheIsolationProbeService:
    return _service


def get_cache_isolation_probe_rate_limiter() -> DatabaseRateLimiter:
    return _probe_rate_limiter


def _account_response(account: ProbeAccount) -> CacheProbeAccountResponse:
    return CacheProbeAccountResponse(account_id=account.account_id, label=account.label)


def _plan_response(plan: ProbePlan) -> CacheProbePlanResponse:
    return CacheProbePlanResponse(
        model=plan.model,
        seed_account=_account_response(plan.seed_account) if plan.seed_account is not None else None,
        available_other_accounts=[_account_response(account) for account in plan.available_other_accounts],
        seed_repetitions=plan.seed_repetitions,
        total_calls=plan.total_calls,
        estimated_input_tokens_per_call=plan.estimated_input_tokens_per_call,
        estimated_total_input_tokens=plan.estimated_total_input_tokens,
        max_seed_repetitions=plan.max_seed_repetitions,
        max_other_accounts=plan.max_other_accounts,
        pressure=CacheProbePressureResponse(
            under_pressure=plan.pressure.under_pressure,
            reason=plan.pressure.reason,
            detail=plan.pressure.detail,
            selectable_account_count=plan.pressure.selectable_account_count,
            eligible_account_count=plan.pressure.eligible_account_count,
            pressured_account_count=plan.pressure.pressured_account_count,
        ),
    )


def _run_response(result: ProbeResult) -> CacheProbeRunResponse:
    return CacheProbeRunResponse(
        run_id=result.run_id,
        model=result.model,
        started_at=result.started_at,
        completed_at=result.completed_at,
        seed_account=_account_response(result.seed_account),
        seed_repetitions=result.seed_repetitions,
        calls=[
            CacheProbeCallResponse(
                sequence=call.sequence,
                account_id=call.account_id,
                account_label=call.account_label,
                role=call.role,
                status=call.status,
                cache_hit=call.cache_hit,
                input_tokens=call.input_tokens,
                cached_tokens=call.cached_tokens,
                latency_ms=call.latency_ms,
                error_code=call.error_code,
            )
            for call in result.calls
        ],
        seed_hit_count=result.seed_hit_count,
        seed_call_count=result.seed_call_count,
        other_hit_count=result.other_hit_count,
        other_call_count=result.other_call_count,
        cross_account_hit=result.cross_account_hit,
        verdict=result.verdict,
    )


@router.get(
    "",
    response_model=CacheProbePlanResponse,
    dependencies=[Depends(require_dashboard_permission(Permission.OPS_WRITE))],
)
async def get_cache_isolation_probe_plan(
    service: CacheIsolationProbeService = Depends(get_cache_isolation_probe_service),
) -> CacheProbePlanResponse:
    return _plan_response(await service.plan())


@router.post(
    "/run",
    response_model=CacheProbeRunResponse,
    dependencies=[Depends(require_dashboard_permission(Permission.OPS_WRITE))],
)
async def run_cache_isolation_probe(
    request: Request,
    payload: CacheProbeRunRequest = Body(default_factory=CacheProbeRunRequest),
    principal: DashboardPrincipal = Depends(validate_dashboard_session),
    service: CacheIsolationProbeService = Depends(get_cache_isolation_probe_service),
) -> CacheProbeRunResponse:
    if payload.confirm is not True:
        raise DashboardBadRequestError(
            "Running the cache isolation probe spends real account quota and must be confirmed explicitly.",
            code="cache_probe_confirmation_required",
        )

    # Charged before the run, in its own short-lived session, so the shared
    # budget is spent even if the run then fails -- and so no DB session is
    # held open across minutes of upstream calls.
    async with get_background_session() as session:
        await get_cache_isolation_probe_rate_limiter().check_and_increment(_RATE_LIMIT_KEY, session)

    try:
        result = await service.run(
            model=payload.model,
            seed_repetitions=payload.seed_repetitions,
            other_account_count=payload.other_account_count,
        )
    except CacheProbeRefused as exc:
        raise DashboardConflictError(exc.message, code=exc.code) from exc

    # Numeric results only: the generated prefix, the run nonce and every byte
    # of upstream response content are never persisted anywhere.
    AuditService.log_async(
        "cache_isolation_probe_run",
        actor_ip=request.client.host if request.client else None,
        actor=AuditActor.from_principal(principal),
        target=AuditTarget("account", result.seed_account.account_id),
        details={
            "run_id": result.run_id,
            "model": result.model,
            "verdict": result.verdict,
            "seed_account_id": result.seed_account.account_id,
            "seed_repetitions": result.seed_repetitions,
            "seed_hit_count": result.seed_hit_count,
            "seed_call_count": result.seed_call_count,
            "other_hit_count": result.other_hit_count,
            "other_call_count": result.other_call_count,
            "cross_account_hit": result.cross_account_hit,
            "total_input_tokens": sum(call.input_tokens or 0 for call in result.calls),
            "total_cached_tokens": sum(call.cached_tokens or 0 for call in result.calls),
        },
    )
    return _run_response(result)
