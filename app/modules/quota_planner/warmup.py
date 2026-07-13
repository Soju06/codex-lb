from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clients.proxy import stream_responses
from app.core.crypto import TokenEncryptor
from app.core.openai.parsing import parse_sse_event
from app.core.openai.requests import ResponsesRequest
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, QuotaPlannerDecision
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import (
    ApiKeyInvalidError,
    ApiKeyNotFoundError,
    ApiKeyRateLimitExceededError,
    ApiKeyRequestUsageBudget,
    ApiKeysService,
)
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import UsageRepository
from app.modules.usage.updater import UsageUpdater

from .logic import PlannerSettings
from .repository import QuotaPlannerRepository

WARMUP_REQUEST_KIND = "warmup"
WARMUP_DEFAULT_INPUT_BUDGET = 32
WARMUP_DEFAULT_OUTPUT_BUDGET = 8

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WarmupExecutionResult:
    decision_id: str
    status: str
    reason: str
    request_id: str | None = None
    executed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class WarmupUsage:
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    reasoning_tokens: int | None


class QuotaWarmupService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._planner = QuotaPlannerRepository(session)
        self._accounts = AccountsRepository(session)
        self._usage = UsageRepository(session)
        self._request_logs = RequestLogsRepository(session)
        self._api_keys = ApiKeysService(ApiKeysRepository(session))
        self._encryptor = TokenEncryptor()

    async def warm_now(
        self,
        *,
        account_id: str,
        model: str | None = None,
        api_key_id: str | None = None,
        force_probe: bool = False,
        decision_id: str | None = None,
    ) -> WarmupExecutionResult:
        settings = await self._planner.get_settings()
        account = await self._accounts.get_by_id(account_id)
        resolved_model = (model or settings.warmup_model_preference or "gpt-5.4-mini").strip()
        scheduled_at = utcnow()
        decision = await self._planner.get_decision(decision_id) if decision_id is not None else None
        if decision is None:
            decision = await self._planner.log_decision(
                mode=settings.mode,
                action="warmup",
                account_id=account.id if account is not None else None,
                scheduled_at=scheduled_at,
                score=0.0,
                reason="manual_warm_now_requested",
                status="planned",
                idempotency_key=f"manual:{scheduled_at:%Y%m%d%H%M%S}:{account_id}:{uuid4().hex}",
            )
        elif decision.status != "planned":
            return WarmupExecutionResult(
                decision_id=decision.id,
                status=decision.status,
                reason=decision.reason or f"decision_{decision.status}",
                executed_at=decision.executed_at,
            )
        allowed, reason = await self._execution_gate(
            settings=settings,
            account=account,
            model=resolved_model,
            force_probe=force_probe,
        )
        if not allowed:
            row = await self._planner.update_decision_status(
                decision.id,
                status="skipped",
                reason=reason,
                expected_status="planned",
            )
            if row is None:
                current = await self._planner.get_decision(decision.id)
                if current is not None:
                    return WarmupExecutionResult(
                        decision_id=current.id,
                        status=current.status,
                        reason=current.reason or f"decision_{current.status}",
                        executed_at=current.executed_at,
                    )
            return WarmupExecutionResult(
                decision_id=decision.id,
                status=row.status if row else "skipped",
                reason=reason,
            )
        assert account is not None

        claimed = await self._planner.claim_warmup_decision(
            decision.id,
            since=_local_midnight(),
            max_warmups=settings.max_warmups_per_day,
            max_credits=settings.max_warmup_credits_per_day,
        )
        if claimed is None:
            return await self._resolve_refused_claim(decision_id=decision.id, settings=settings)

        reservation_id: str | None = None
        if api_key_id is not None:
            try:
                reservation = await self._api_keys.enforce_limits_for_request(
                    api_key_id,
                    request_model=resolved_model,
                    request_usage_budget=ApiKeyRequestUsageBudget(
                        input_tokens=WARMUP_DEFAULT_INPUT_BUDGET,
                        output_tokens=WARMUP_DEFAULT_OUTPUT_BUDGET,
                    ),
                )
                reservation_id = reservation.reservation_id
            except ApiKeyNotFoundError:
                row = await self._planner.update_decision_status(
                    decision.id,
                    status="skipped",
                    reason="api_key_not_found",
                    expected_status="executing",
                )
                return await self._result_from_update_or_current(
                    decision_id=decision.id,
                    row=row,
                    fallback_status="skipped",
                    fallback_reason="api_key_not_found",
                )
            except ApiKeyInvalidError:
                row = await self._planner.update_decision_status(
                    decision.id,
                    status="skipped",
                    reason="api_key_invalid",
                    expected_status="executing",
                )
                return await self._result_from_update_or_current(
                    decision_id=decision.id,
                    row=row,
                    fallback_status="skipped",
                    fallback_reason="api_key_invalid",
                )
            except ApiKeyRateLimitExceededError as exc:
                reason = f"api_key_rate_limit_exceeded:{exc.reset_at.isoformat()}Z"
                row = await self._planner.update_decision_status(
                    decision.id,
                    status="skipped",
                    reason=reason,
                    expected_status="executing",
                )
                return await self._result_from_update_or_current(
                    decision_id=decision.id,
                    row=row,
                    fallback_status="skipped",
                    fallback_reason=reason,
                )

        request_id = f"quota-warmup-{uuid4().hex}"
        started = time.monotonic()
        try:
            usage = await self._send_warmup_probe(
                account=account,
                model=resolved_model,
                request_id=request_id,
            )
            if reservation_id is not None:
                await self._api_keys.finalize_usage_reservation(
                    reservation_id,
                    model=resolved_model,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cached_input_tokens=usage.cached_input_tokens,
                )
            await self._request_logs.add_log(
                account_id=account_id,
                api_key_id=api_key_id,
                request_id=request_id,
                model=resolved_model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cached_input_tokens=usage.cached_input_tokens,
                reasoning_tokens=usage.reasoning_tokens,
                latency_ms=int((time.monotonic() - started) * 1000),
                status="success",
                error_code=None,
                transport="quota_planner",
                request_kind=WARMUP_REQUEST_KIND,
            )
            await self._try_record_warmup_effect(
                account,
                resolved_model,
                source="warmup_probe",
                confidence="observed",
            )
            row = await self._planner.update_decision_status(
                decision.id,
                status="executed",
                reason="warmup_executed",
                executed_at=utcnow(),
                expected_status="executing",
            )
            return await self._result_from_update_or_current(
                decision_id=decision.id,
                row=row,
                fallback_status="executed",
                fallback_reason="warmup_executed",
                request_id=request_id,
            )
        except asyncio.CancelledError:
            if reservation_id is not None:
                await self._api_keys.fail_usage_reservation(
                    reservation_id,
                    model=resolved_model,
                    input_tokens=0,
                    output_tokens=0,
                    cached_input_tokens=0,
                )
            raise
        except Exception as exc:
            if reservation_id is not None:
                await self._api_keys.fail_usage_reservation(
                    reservation_id,
                    model=resolved_model,
                    input_tokens=0,
                    output_tokens=0,
                    cached_input_tokens=0,
                )
            await self._request_logs.add_log(
                account_id=account_id,
                api_key_id=api_key_id,
                request_id=request_id,
                model=resolved_model,
                input_tokens=0,
                output_tokens=0,
                latency_ms=int((time.monotonic() - started) * 1000),
                status="error",
                error_code="warmup_failed",
                error_message=str(exc),
                transport="quota_planner",
                request_kind=WARMUP_REQUEST_KIND,
            )
            await self._try_record_warmup_effect(
                account,
                resolved_model,
                source="warmup_probe",
                confidence="failed",
            )
            row = await self._planner.update_decision_status(
                decision.id,
                status="failed",
                reason=f"warmup_failed:{type(exc).__name__}",
                executed_at=utcnow(),
                expected_status="executing",
            )
            return await self._result_from_update_or_current(
                decision_id=decision.id,
                row=row,
                fallback_status="failed",
                fallback_reason=f"warmup_failed:{type(exc).__name__}",
                request_id=request_id,
            )

    async def _try_record_warmup_effect(
        self,
        account: Account,
        model: str,
        *,
        source: str,
        confidence: str,
    ) -> None:
        try:
            await self._record_warmup_effect(account, model, source=source, confidence=confidence)
        except Exception:
            logger.exception("Failed to record quota warmup effect", extra={"account_id": account.id, "model": model})

    async def _resolve_refused_claim(
        self,
        *,
        decision_id: str,
        settings: PlannerSettings,
    ) -> WarmupExecutionResult:
        """Map a refused planned -> executing claim to a decision outcome.

        The claim UPDATE returns no row either because a concurrent worker
        already moved the decision out of ``planned`` or because a daily budget
        guard failed. Re-read the decision fresh (the identity map may hold a
        stale snapshot) to distinguish the two, then re-run the budget counts to
        pick the matching budget-exhausted reason.
        """
        current = await self._planner.get_decision_fresh(decision_id)
        if current is None:
            return WarmupExecutionResult(decision_id=decision_id, status="skipped", reason="decision_missing")
        if current.status != "planned":
            return WarmupExecutionResult(
                decision_id=current.id,
                status=current.status,
                reason=current.reason or f"decision_{current.status}",
                executed_at=current.executed_at,
            )
        today = _local_midnight()
        active_today = await self._planner.count_active_warmups_since(today)
        if active_today >= settings.max_warmups_per_day:
            reason = "daily_warmup_count_budget_exhausted"
        else:
            reason = "daily_warmup_credit_budget_exhausted"
        row = await self._planner.update_decision_status(
            decision_id,
            status="skipped",
            reason=reason,
            expected_status="planned",
        )
        return await self._result_from_update_or_current(
            decision_id=decision_id,
            row=row,
            fallback_status="skipped",
            fallback_reason=reason,
        )

    async def _result_from_update_or_current(
        self,
        *,
        decision_id: str,
        row: QuotaPlannerDecision | None,
        fallback_status: str,
        fallback_reason: str,
        request_id: str | None = None,
    ) -> WarmupExecutionResult:
        if row is None:
            row = await self._planner.get_decision(decision_id)
        if row is None:
            return WarmupExecutionResult(decision_id=decision_id, status=fallback_status, reason=fallback_reason)
        return WarmupExecutionResult(
            decision_id=row.id,
            status=row.status,
            reason=row.reason or fallback_reason,
            request_id=request_id,
            executed_at=row.executed_at,
        )

    async def cancel_decision(self, decision_id: str) -> WarmupExecutionResult | None:
        row = await self._planner.get_decision(decision_id)
        if row is None:
            return None
        if row.status not in {"planned", "skipped"}:
            return WarmupExecutionResult(decision_id=row.id, status=row.status, reason="not_cancelable")
        updated = await self._planner.update_decision_status(
            decision_id,
            status="canceled",
            reason="admin_canceled",
            expected_status={"planned", "skipped"},
        )
        if updated is None:
            current = await self._planner.get_decision(decision_id)
            if current is None:
                return None
            return WarmupExecutionResult(decision_id=current.id, status=current.status, reason="not_cancelable")
        return WarmupExecutionResult(decision_id=updated.id, status=updated.status, reason=updated.reason or "")

    async def _execution_gate(
        self,
        *,
        settings: PlannerSettings,
        account: Account | None,
        model: str,
        force_probe: bool,
    ) -> tuple[bool, str]:
        if account is None:
            return False, "account_not_found"
        if settings.mode == "off":
            return False, "planner_off"
        if settings.mode == "shadow":
            return False, "shadow_mode_records_only"
        if not settings.allow_synthetic_traffic:
            return False, "synthetic_traffic_disabled"
        if settings.dry_run:
            return False, "dry_run_enabled"
        if account.status != AccountStatus.ACTIVE:
            return False, f"account_status_{account.status.value}"

        latest = (await self._usage.latest_by_account()).get(account.id)
        if latest is not None and latest.reset_at is not None and latest.reset_at > int(utcnow().timestamp()):
            return False, "account_window_already_active"

        # Advisory pre-check only: the authoritative budget enforcement happens
        # inside the atomic planned -> executing claim (claim_warmup_decision).
        today = _local_midnight()
        active_today = await self._planner.count_active_warmups_since(today)
        if active_today >= settings.max_warmups_per_day:
            return False, "daily_warmup_count_budget_exhausted"
        spent_today = await self._planner.warmup_cost_since(today)
        if settings.max_warmup_credits_per_day <= 0 or spent_today >= settings.max_warmup_credits_per_day:
            return False, "daily_warmup_credit_budget_exhausted"

        effect = await self._planner.latest_warmup_effect_observation(account_id=account.id, model=model)
        if not force_probe and (effect is None or effect.confidence not in {"observed", "known", "high"}):
            return False, "warmup_effect_unknown"
        return True, "ready"

    async def _send_warmup_probe(self, *, account: Account, model: str, request_id: str) -> WarmupUsage:
        payload = ResponsesRequest.model_validate(
            {
                "model": model,
                "instructions": "Reply with OK.",
                "input": "quota planner warmup",
                "stream": True,
                "store": False,
                "generate": False,
            }
        )
        headers = {"x-request-id": request_id, "user-agent": "codex-lb-quota-planner"}
        access_token = self._encryptor.decrypt(account.access_token_encrypted)
        upstream_account_id = account.chatgpt_account_id
        usage = WarmupUsage(input_tokens=0, output_tokens=0, cached_input_tokens=0, reasoning_tokens=None)
        async for event_block in stream_responses(
            payload,
            headers,
            access_token,
            upstream_account_id,
            raise_for_status=True,
        ):
            event = parse_sse_event(event_block)
            if event is None or event.response is None or event.response.usage is None:
                continue
            raw_usage = event.response.usage
            usage = WarmupUsage(
                input_tokens=raw_usage.input_tokens or 0,
                output_tokens=raw_usage.output_tokens or 0,
                cached_input_tokens=(
                    raw_usage.input_tokens_details.cached_tokens if raw_usage.input_tokens_details else 0
                )
                or 0,
                reasoning_tokens=(
                    raw_usage.output_tokens_details.reasoning_tokens if raw_usage.output_tokens_details else None
                ),
            )
        return usage

    async def _record_warmup_effect(
        self,
        account: Account,
        model: str,
        *,
        source: str,
        confidence: str,
    ) -> None:
        latest_before = (await self._usage.latest_by_account()).get(account.id)
        latest_before_by_account = {account.id: latest_before} if latest_before else {}
        await UsageUpdater(self._usage, self._accounts).refresh_accounts([account], latest_before_by_account)
        latest_after = (await self._usage.latest_by_account()).get(account.id)
        observed_after = latest_after if _usage_history_is_fresh(latest_before, latest_after) else None
        effective_confidence = confidence if observed_after is not None else "unknown"
        await self._planner.add_window_observation(
            account_id=account.id,
            model=model,
            source=source,
            primary_remaining_percent=(100.0 - observed_after.used_percent) if observed_after else None,
            primary_reset_at=observed_after.reset_at if observed_after else None,
            confidence=effective_confidence,
        )


def _local_midnight() -> datetime:
    return utcnow().replace(hour=0, minute=0, second=0, microsecond=0)


def _usage_history_is_fresh(before: object | None, after: object | None) -> bool:
    if after is None:
        return False
    if before is None:
        return True
    before_id = getattr(before, "id", None)
    after_id = getattr(after, "id", None)
    if before_id is not None and after_id is not None:
        return after_id != before_id
    return getattr(after, "recorded_at", None) != getattr(before, "recorded_at", None)
