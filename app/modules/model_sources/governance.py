"""Company admission derived from durable request logs; no inference probes."""

from datetime import datetime, timedelta

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.time import utcnow
from app.db.models import ModelSource, RequestLog
from app.modules.model_sources.schemas import CompanyModelHealthState, CompanySourceStatus

COMPANY_KINDS = frozenset({"llmbox", "trae", "codebase_llm"})
COMPANY_HEALTH_CHECK_SOURCE = "company_model_health_check"
COMPANY_HEALTH_CHECK_FRESH_SECONDS = 3 * 60 * 60
SOURCE_FAULTS = (
    "invalid_upstream_response",
    "model_source_timeout",
    "model_source_idle_timeout",
    "model_source_error",
    "model_source_credentials_error",
    "model_source_stream_error",
    "model_source_stream_truncated",
    "model_source_response_failed",
    "model_source_response_invalid",
)


async def operational_status(session: AsyncSession, source: ModelSource) -> CompanySourceStatus:
    now = utcnow()
    scope = (RequestLog.model_source_id == source.id, RequestLog.requested_at >= now - timedelta(hours=24))
    stats = (
        await session.execute(
            select(
                func.sum(case((RequestLog.status == "success", 1), else_=0)),
                func.sum(case((RequestLog.status == "error", 1), else_=0)),
                func.sum(case((RequestLog.upstream_status_code == 429, 1), else_=0)),
                func.sum(case((RequestLog.upstream_status_code >= 500, 1), else_=0)),
                func.avg(RequestLog.latency_ms),
                func.sum(func.coalesce(RequestLog.input_tokens, 0) + func.coalesce(RequestLog.output_tokens, 0)),
            ).where(*scope)
        )
    ).one()
    status = CompanySourceStatus(
        credential_cache="unavailable",
        successes=stats[0] or 0,
        failures=stats[1] or 0,
        rate_limits=stats[2] or 0,
        server_errors=stats[3] or 0,
        average_latency_ms=stats[4],
        budget_used=stats[5] or 0,
    )
    status.budget_exhausted = source.local_token_budget is not None and status.budget_used >= source.local_token_budget
    fault = and_(
        RequestLog.status == "error",
        or_(
            RequestLog.upstream_status_code.in_((401, 403, 429)),
            RequestLog.upstream_status_code >= 500,
            and_(
                or_(RequestLog.upstream_status_code.is_(None), RequestLog.upstream_status_code < 400),
                RequestLog.error_code.in_(SOURCE_FAULTS),
            ),
        ),
    )
    recent = list(
        (
            await session.execute(
                select(RequestLog)
                .where(
                    *scope,
                    or_(RequestLog.status == "success", fault),
                )
                .order_by(RequestLog.requested_at.desc(), RequestLog.id.desc())
                .limit(3)
            )
        ).scalars()
    )
    if not recent:
        return status
    latest = recent[0]
    if latest.status == "success":
        status.health = "healthy"
        return status
    status.health = "degraded"
    immediate = latest.upstream_status_code in (401, 403, 429) or latest.error_code == "model_source_credentials_error"
    if immediate or (len(recent) == 3 and all(row.status == "error" for row in recent)):
        until = latest.requested_at + timedelta(seconds=60)
        if until > now:
            status.health = "unavailable"
            status.cooldown_until = until
    return status


async def catalog_eligible_models(session: AsyncSession, sources: list[ModelSource]) -> set[tuple[str, str]]:
    """Models whose latest scheduled health check is fresh, fast and successful."""
    from app.modules.model_sources import codebase_llm, llmbox, trae

    company = [source for source in sources if source.kind in COMPANY_KINDS]
    if not company:
        return set()
    bindings = {"trae": trae, "llmbox": llmbox, "codebase_llm": codebase_llm}
    admitted: set[str] = set()
    for source in company:
        state = await operational_status(session, source)
        if bindings[source.kind].cache_state() == "present" and not state.budget_exhausted:
            admitted.add(source.id)
    if not admitted:
        return set()
    checks = await latest_health_checks(session, list(admitted))
    now = utcnow()
    return {key for key, check in checks.items() if health_check_state(check, now=now) == "healthy"}


async def latest_health_checks(session: AsyncSession, source_ids: list[str]) -> dict[tuple[str, str], RequestLog]:
    """One completed scheduled probe per source/model, including expired results."""
    if not source_ids:
        return {}
    ranked = (
        select(
            RequestLog.id,
            func.row_number()
            .over(
                partition_by=(RequestLog.model_source_id, RequestLog.model),
                order_by=(RequestLog.requested_at.desc(), RequestLog.id.desc()),
            )
            .label("rank"),
        )
        .where(
            RequestLog.model_source_id.in_(source_ids),
            RequestLog.source == COMPANY_HEALTH_CHECK_SOURCE,
        )
        .subquery()
    )
    rows = (
        await session.execute(select(RequestLog).join(ranked, RequestLog.id == ranked.c.id).where(ranked.c.rank == 1))
    ).scalars()
    return {(row.model_source_id, row.model): row for row in rows if row.model_source_id is not None}


def health_check_state(check: RequestLog | None, *, now: datetime) -> CompanyModelHealthState:
    if check is None:
        return "unknown"
    if check.requested_at < now - timedelta(seconds=COMPANY_HEALTH_CHECK_FRESH_SECONDS):
        return "stale"
    if check.status != "success":
        return "unhealthy"
    if check.latency_ms is None or not 0 <= check.latency_ms <= 30_000:
        return "slow"
    if check.latency_first_token_ms is not None and not 0 <= check.latency_first_token_ms <= 30_000:
        return "slow"
    return "healthy"
