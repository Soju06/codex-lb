"""Dashboard projection of completed probes; never schedules inference."""

from datetime import datetime, timedelta

from app.db.models import RequestLog
from app.modules.model_sources.governance import COMPANY_HEALTH_CHECK_FRESH_SECONDS, health_check_state
from app.modules.model_sources.health_checks import HEALTH_CHECK_INTERVAL_SECONDS, HEALTH_CHECK_TIMEOUT_SECONDS
from app.modules.model_sources.schemas import CompanyHealthCheckSummary, CompanyModelHealthCheck, ModelSourceResponse


def attach_health_checks(
    source: ModelSourceResponse, checks: dict[tuple[str, str], RequestLog], *, now: datetime
) -> None:
    status = source.company_status
    if status is None:
        return
    admitted = source.is_enabled and status.credential_cache == "present" and not status.budget_exhausted
    fresh = visible = 0
    for model in source.models:
        check = checks.get((source.id, model.model))
        state = health_check_state(check, now=now)
        catalog_visible = admitted and model.is_enabled and state == "healthy"
        fresh += int(model.is_enabled and state not in ("unknown", "stale"))
        visible += int(catalog_visible)
        model.health_check = CompanyModelHealthCheck(
            state=state,
            checked_at=check.requested_at if check else None,
            expires_at=(check.requested_at + timedelta(seconds=COMPANY_HEALTH_CHECK_FRESH_SECONDS)) if check else None,
            next_due_at=(
                check.requested_at + timedelta(seconds=HEALTH_CHECK_INTERVAL_SECONDS)
                if check and source.is_enabled and model.is_enabled
                else None
            ),
            latency_ms=check.latency_ms if check else None,
            first_token_ms=check.latency_first_token_ms if check else None,
            error_code=check.error_code if check else None,
            input_tokens=check.input_tokens if check else None,
            output_tokens=check.output_tokens if check else None,
            catalog_visible=catalog_visible,
        )
    status.health_check_summary = CompanyHealthCheckSummary(
        enabled_models=sum(model.is_enabled for model in source.models),
        fresh_checks=fresh,
        visible_models=visible,
        interval_seconds=HEALTH_CHECK_INTERVAL_SECONDS,
        timeout_seconds=HEALTH_CHECK_TIMEOUT_SECONDS,
        freshness_seconds=COMPANY_HEALTH_CHECK_FRESH_SECONDS,
    )
