from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import usage as usage_core
from app.core.balancer import ACCOUNT_USAGE_LIMIT_REACHED_ERROR_CODE
from app.core.clients.proxy import stream_responses
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.openai.parsing import parse_sse_event
from app.core.openai.requests import ResponsesRequest
from app.core.usage.types import UsageWindowRow
from app.core.utils.shared_future import _await_cleanup_deferring_cancellation, _await_result_deferring_cancellation
from app.core.utils.time import naive_utc_to_epoch, utcnow
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
from app.modules.usage.authorization import OwnerAuthorization, OwnerAuthorizationKind, load_owner_authorization
from app.modules.usage.repository import UsageRepository
from app.modules.usage.updater import UsageUpdater

from .logic import SHORT_WINDOW_MAX_MINUTES, PlannerSettings
from .repository import (
    WARMUP_EXECUTION_CLAIM_TTL_SECONDS,
    QuotaPlannerRepository,
    warmup_claim_is_expired,
)

WARMUP_REQUEST_KIND = "warmup"
# Rows written by the same upstream fetch land within milliseconds of each
# other; a sibling row only proves a later fetch when it is newer by more
# than this margin.
_SIBLING_FETCH_MARGIN_SECONDS = 5.0
WARMUP_DEFAULT_INPUT_BUDGET = 32
WARMUP_DEFAULT_OUTPUT_BUDGET = 8
ACCOUNT_USAGE_LIMIT_AUTHORIZATION_FAILED_REASON = "account_usage_limit_authorization_failed"
ACCOUNT_USAGE_LIMIT_AUTHORIZATION_CANCELLED_REASON = "account_usage_limit_authorization_cancelled"

logger = logging.getLogger(__name__)


def _warmup_request_id(decision_id: str) -> str:
    return f"quota-warmup-{decision_id}"


def _warmup_claim_ttl_seconds() -> float:
    settings = get_settings()
    budget_seconds = float(getattr(settings, "http_responses_stream_request_budget_seconds", 0.0) or 0.0)
    return max(WARMUP_EXECUTION_CLAIM_TTL_SECONDS, budget_seconds)


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


@dataclass(frozen=True, slots=True)
class _FreshStandardUsage:
    account: Account | None
    decision: OwnerAuthorization
    primary: UsageWindowRow | None = None
    secondary: UsageWindowRow | None = None
    monthly: UsageWindowRow | None = None


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
        decision = await self._planner.get_decision(decision_id) if decision_id is not None else None
        if decision is not None and decision.status not in {"planned", "executing"}:
            return WarmupExecutionResult(
                decision_id=decision.id,
                status=decision.status,
                reason=decision.reason or f"decision_{decision.status}",
                executed_at=decision.executed_at,
            )

        settings = await self._planner.get_settings()
        account = await self._accounts.get_by_id(account_id)
        resolved_model = (model or settings.warmup_model_preference or "gpt-5.4-mini").strip()
        scheduled_at = utcnow()
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
        elif decision.status == "executing" and not warmup_claim_is_expired(decision):
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
            if decision.status == "executing" and warmup_claim_is_expired(decision):
                row = await self._planner.skip_stale_warmup_claim(decision.id, reason=reason)
            else:
                row = await self._planner.update_decision_status(
                    decision.id,
                    status="skipped",
                    reason=reason,
                    expected_status="planned",
                )
            return await self._result_from_update_or_current(
                decision_id=decision.id,
                row=row,
                fallback_status="skipped",
                fallback_reason=reason,
            )
        assert account is not None

        claimed = await self._planner.claim_warmup_decision(
            decision.id,
            since=_local_midnight(),
            max_warmups=settings.max_warmups_per_day,
            max_credits=settings.max_warmup_credits_per_day,
            claim_ttl_seconds=_warmup_claim_ttl_seconds(),
        )
        if claimed is None:
            return await self._resolve_refused_claim(decision_id=decision.id, settings=settings)
        claim_executed_at = claimed.executed_at
        claim_lease_expires_at = claimed.lease_expires_at
        assert claim_executed_at is not None
        assert claim_lease_expires_at is not None

        request_id = _warmup_request_id(decision.id)
        replay_result = await self._reconcile_existing_warmup_request(
            decision_id=decision.id,
            request_id=request_id,
            claim_executed_at=claim_executed_at,
            claim_lease_expires_at=claim_lease_expires_at,
        )
        if replay_result is not None:
            return replay_result

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
                # ``None`` means no configured limit applies to the warmup
                # probe; there is nothing to finalize afterwards.
                reservation_id = reservation.reservation_id if reservation is not None else None
            except ApiKeyNotFoundError:
                return await self._skip_claimed_warmup_deferring_cancellation(
                    decision_id=decision.id,
                    reason="api_key_not_found",
                    reservation_id=None,
                    claim_executed_at=claim_executed_at,
                    claim_lease_expires_at=claim_lease_expires_at,
                )
            except ApiKeyInvalidError:
                return await self._skip_claimed_warmup_deferring_cancellation(
                    decision_id=decision.id,
                    reason="api_key_invalid",
                    reservation_id=None,
                    claim_executed_at=claim_executed_at,
                    claim_lease_expires_at=claim_lease_expires_at,
                )
            except ApiKeyRateLimitExceededError as exc:
                reason = f"api_key_rate_limit_exceeded:{exc.reset_at.isoformat()}Z"
                return await self._skip_claimed_warmup_deferring_cancellation(
                    decision_id=decision.id,
                    reason=reason,
                    reservation_id=None,
                    claim_executed_at=claim_executed_at,
                    claim_lease_expires_at=claim_lease_expires_at,
                )

        try:
            authorization = await self._load_fresh_standard_usage(account.id)
        except asyncio.CancelledError:
            await self._skip_claimed_warmup_deferring_cancellation(
                decision_id=decision.id,
                reason=ACCOUNT_USAGE_LIMIT_AUTHORIZATION_CANCELLED_REASON,
                reservation_id=reservation_id,
                claim_executed_at=claim_executed_at,
                claim_lease_expires_at=claim_lease_expires_at,
            )
            raise
        except Exception:
            logger.warning(
                "Final quota warmup usage authorization failed closed",
                extra={"account_id": account.id, "decision_id": decision.id},
                exc_info=True,
            )
            return await self._skip_claimed_warmup_deferring_cancellation(
                decision_id=decision.id,
                reason=ACCOUNT_USAGE_LIMIT_AUTHORIZATION_FAILED_REASON,
                reservation_id=reservation_id,
                claim_executed_at=claim_executed_at,
                claim_lease_expires_at=claim_lease_expires_at,
            )
        authorization_reason: str | None = None
        if authorization.decision.kind is OwnerAuthorizationKind.AUTHORIZATION_FAILED:
            authorization_reason = ACCOUNT_USAGE_LIMIT_AUTHORIZATION_FAILED_REASON
        elif authorization.account is None:
            authorization_reason = "account_not_found"
        elif authorization.decision.kind is OwnerAuthorizationKind.OWNER_UNAVAILABLE:
            authorization_reason = f"account_status_{authorization.account.status.value}"
        elif authorization.decision.kind is OwnerAuthorizationKind.USAGE_POLICY_BLOCKED:
            authorization_reason = ACCOUNT_USAGE_LIMIT_REACHED_ERROR_CODE
        if authorization_reason is not None:
            return await self._skip_claimed_warmup_deferring_cancellation(
                decision_id=decision.id,
                reason=authorization_reason,
                reservation_id=reservation_id,
                claim_executed_at=claim_executed_at,
                claim_lease_expires_at=claim_lease_expires_at,
            )
        assert authorization.account is not None
        account = authorization.account
        started = time.monotonic()
        reservation_finalized = False
        try:
            usage = await self._send_warmup_probe(
                account=account,
                model=resolved_model,
                request_id=request_id,
            )
            if reservation_id is not None:
                settlement_cancellation = await _await_cleanup_deferring_cancellation(
                    self._api_keys.finalize_usage_reservation(
                        reservation_id,
                        model=resolved_model,
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        cached_input_tokens=usage.cached_input_tokens,
                    )
                )
                reservation_finalized = True
                if settlement_cancellation is not None:
                    raise settlement_cancellation
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
                expected_executed_at=claim_executed_at,
                expected_lease_expires_at=claim_lease_expires_at,
            )
            return await self._result_from_update_or_current(
                decision_id=decision.id,
                row=row,
                fallback_status="executed",
                fallback_reason="warmup_executed",
                request_id=request_id,
            )
        except asyncio.CancelledError:
            if reservation_id is not None and not reservation_finalized:
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
                expected_executed_at=claim_executed_at,
                expected_lease_expires_at=claim_lease_expires_at,
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
        if current.status not in {"planned", "executing"}:
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
        if current.status == "planned":
            row = await self._planner.update_decision_status(
                decision_id,
                status="skipped",
                reason=reason,
                expected_status="planned",
            )
        elif warmup_claim_is_expired(current):
            row = await self._planner.skip_stale_warmup_claim(
                decision_id,
                reason=reason,
                executed_at=utcnow(),
            )
        else:
            row = current
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

    async def _reconcile_existing_warmup_request(
        self,
        *,
        decision_id: str,
        request_id: str,
        claim_executed_at: datetime,
        claim_lease_expires_at: datetime,
    ) -> WarmupExecutionResult | None:
        existing = await self._request_logs.latest_successful_log_for_request_id(
            request_id,
            request_kind=WARMUP_REQUEST_KIND,
        )
        if existing is not None:
            row = await self._planner.update_decision_status(
                decision_id,
                status="executed",
                reason="warmup_executed",
                executed_at=existing.requested_at,
                expected_status="executing",
                expected_executed_at=claim_executed_at,
                expected_lease_expires_at=claim_lease_expires_at,
            )
            return await self._result_from_update_or_current(
                decision_id=decision_id,
                row=row,
                fallback_status="executed",
                fallback_reason="warmup_executed",
                request_id=request_id,
            )
        return None

    async def _skip_claimed_warmup(
        self,
        *,
        decision_id: str,
        reason: str,
        reservation_id: str | None,
        claim_executed_at: datetime,
        claim_lease_expires_at: datetime,
    ) -> WarmupExecutionResult:
        # Claim and reservation writes have committed before authorization.
        # A cancelled driver call or a PostgreSQL statement error leaves the
        # read transaction unusable. End it before releasing durable resources.
        await self._session.rollback()
        row: QuotaPlannerDecision | None = None
        try:
            if reservation_id is not None:
                await self._api_keys.release_usage_reservation(reservation_id)
        finally:
            row = await self._planner.update_decision_status(
                decision_id,
                status="skipped",
                reason=reason,
                executed_at=utcnow(),
                expected_status="executing",
                expected_executed_at=claim_executed_at,
                expected_lease_expires_at=claim_lease_expires_at,
            )
        return await self._result_from_update_or_current(
            decision_id=decision_id,
            row=row,
            fallback_status="skipped",
            fallback_reason=reason,
        )

    async def _skip_claimed_warmup_deferring_cancellation(
        self,
        *,
        decision_id: str,
        reason: str,
        reservation_id: str | None,
        claim_executed_at: datetime,
        claim_lease_expires_at: datetime,
    ) -> WarmupExecutionResult:
        async def settle() -> WarmupExecutionResult | Exception:
            # Return a cleanup error as data until the shared waiter returns its
            # cancellation marker. Otherwise a failed cleanup could replace the
            # caller's original cancellation before that marker is delivered.
            try:
                return await self._skip_claimed_warmup(
                    decision_id=decision_id,
                    reason=reason,
                    reservation_id=reservation_id,
                    claim_executed_at=claim_executed_at,
                    claim_lease_expires_at=claim_lease_expires_at,
                )
            except Exception as exc:
                return exc

        result, cancellation = await _await_result_deferring_cancellation(settle())
        if cancellation is not None:
            if isinstance(result, Exception):
                logger.warning("Warmup cleanup failed during cancellation", exc_info=result)
            raise cancellation
        if isinstance(result, Exception):
            raise result
        return result

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

        standard_usage = await self._load_fresh_standard_usage(account.id)
        if standard_usage.decision.kind is OwnerAuthorizationKind.AUTHORIZATION_FAILED:
            return False, ACCOUNT_USAGE_LIMIT_AUTHORIZATION_FAILED_REASON
        account = standard_usage.account
        if account is None:
            return False, "account_not_found"
        if account.status != AccountStatus.ACTIVE:
            return False, f"account_status_{account.status.value}"

        if standard_usage.decision.kind is OwnerAuthorizationKind.USAGE_POLICY_BLOCKED:
            return False, ACCOUNT_USAGE_LIMIT_REACHED_ERROR_CODE
        latest = standard_usage.primary
        if _sample_blocks_short_window_planning(latest):
            return False, "no_short_window"
        if self._short_window_superseded(
            account,
            latest,
            secondary=standard_usage.secondary,
            monthly=standard_usage.monthly,
        ):
            return False, "no_short_window"
        if latest is not None and latest.reset_at is not None and latest.reset_at > naive_utc_to_epoch(utcnow()):
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

    async def _load_fresh_standard_usage(self, account_id: str) -> _FreshStandardUsage:
        account = await self._accounts.get_by_id_fresh(account_id)
        if account is None or account.status != AccountStatus.ACTIVE:
            return _FreshStandardUsage(
                account=account,
                decision=OwnerAuthorization(
                    OwnerAuthorizationKind.OWNER_UNAVAILABLE,
                    owner_status=account.status if account is not None else None,
                ),
            )
        decision = await load_owner_authorization(
            self._usage,
            account_id,
            refresh_interval_seconds=get_settings().usage_refresh_interval_seconds,
            require_active=True,
        )
        snapshot = decision.snapshot
        if snapshot is None:
            return _FreshStandardUsage(account=None, decision=decision)
        account.status = snapshot.status
        account.plan_type = snapshot.plan_type
        account.usage_limit_enabled = snapshot.enabled
        account.usage_limit_percent = snapshot.limit_percent
        return _FreshStandardUsage(
            account=account,
            decision=decision,
            primary=snapshot.primary,
            secondary=snapshot.secondary,
            monthly=snapshot.monthly,
        )

    @staticmethod
    def _short_window_superseded(
        account: Account,
        latest: UsageWindowRow | None,
        *,
        secondary: UsageWindowRow | None,
        monthly: UsageWindowRow | None,
    ) -> bool:
        # A strictly newer long-window row proves a later refresh no longer
        # reported the short window: the stale short primary sample is not
        # evidence of a current short window, so warm-up traffic would open
        # nothing. Same-fetch rows land within milliseconds and stay inside
        # the margin. Monthly rows only count for plans with monthly
        # capacity — lingering rows from a former plan are not applicable.
        if latest is None:
            return False
        latest_window_minutes = getattr(latest, "window_minutes", None)
        if latest_window_minutes is None or int(latest_window_minutes) > SHORT_WINDOW_MAX_MINUTES:
            # Only samples that positively report a short duration are
            # eligible for supersession rejection; metadata-less samples
            # keep the legacy bootstrap behavior.
            return False
        latest_recorded_at = getattr(latest, "recorded_at", None)
        if latest_recorded_at is None:
            return False
        siblings = (
            (secondary, monthly)
            if usage_core.capacity_for_plan(account.plan_type, "monthly") is not None
            else (secondary,)
        )
        for sibling in siblings:
            if sibling is None:
                continue
            if (sibling.recorded_at - latest_recorded_at).total_seconds() > _SIBLING_FETCH_MARGIN_SECONDS:
                return True
        return False

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
            # The probe's own live signals must not pre-write fresh usage
            # rows: _record_warmup_effect measures the post-probe window via
            # its controlled refresh, which would otherwise skip as fresh
            # and downgrade the observation to unknown.
            suppress_live_usage=True,
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
        observed_after = (
            latest_after
            if _usage_history_is_fresh(latest_before, latest_after)
            and not _sample_blocks_short_window_planning(latest_after)
            else None
        )
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


def _sample_blocks_short_window_planning(entry: object | None) -> bool:
    # Phase planning only applies to short rolling windows. Only positive
    # evidence of a long (weekly or monthly) window in the primary slot
    # blocks warm-up execution — weekly-only plans surface the weekly window
    # there. Absent samples or samples without duration metadata keep the
    # legacy bootstrap behavior.
    if entry is None:
        return False
    window_minutes = getattr(entry, "window_minutes", None)
    if window_minutes is None:
        return False
    return int(window_minutes) > SHORT_WINDOW_MAX_MINUTES


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
