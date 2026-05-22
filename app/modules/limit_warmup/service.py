from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from app.core import usage as usage_core
from app.core.auth.refresh import RefreshError
from app.core.clients.proxy import override_stream_timeouts, stream_responses
from app.core.crypto import TokenEncryptor
from app.core.openai.model_registry import get_model_registry
from app.core.openai.models import OpenAIError, ResponseUsage
from app.core.openai.parsing import parse_sse_event
from app.core.openai.requests import ResponsesRequest
from app.core.plan_types import account_plan_matches_allowed
from app.core.usage.pricing import get_pricing_for_model
from app.core.utils.time import utcnow
from app.db.models import Account, AccountLimitWarmup, AccountStatus, DashboardSettings, UsageHistory
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.repository import AccountsRepository
from app.modules.usage.mappers import usage_history_to_window_row

logger = logging.getLogger(__name__)

LIMIT_WARMUP_SOURCE = "limit_warmup"
LIMIT_WARMUP_HEADER = "x-codex-lb-limit-warmup"
_DEFAULT_WARMUP_INSTRUCTIONS = "Reply with OK only."
_TERMINAL_ERROR_EVENTS = {"response.failed", "response.incomplete", "error"}
_QUOTA_ERROR_CODES = {"insufficient_quota", "quota_exceeded", "rate_limit_exceeded", "usage_limit_reached"}


@dataclass(frozen=True, slots=True)
class LimitWarmupSendResult:
    request_id: str
    success: bool
    latency_ms: int
    usage: ResponseUsage | None = None
    error_code: str | None = None
    error_message: str | None = None


class LimitWarmupSender(Protocol):
    async def send(self, account: Account, *, model: str, prompt: str) -> LimitWarmupSendResult: ...


class LimitWarmupAttemptsRepository(Protocol):
    async def latest_by_account(self, account_ids: list[str]) -> dict[str, AccountLimitWarmup]: ...

    async def try_create_attempt(
        self,
        *,
        account_id: str,
        window: str,
        reset_at: int,
        model: str,
        attempted_at,
        status: str = "pending",
    ) -> AccountLimitWarmup | None: ...

    async def complete_attempt(
        self,
        attempt_id: int,
        *,
        status: str,
        completed_at,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> AccountLimitWarmup | None: ...


class LimitWarmupRequestLogRepository(Protocol):
    async def add_log(
        self,
        account_id: str | None,
        request_id: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        latency_ms: int | None,
        status: str,
        error_code: str | None,
        latency_first_token_ms: int | None = None,
        error_message: str | None = None,
        requested_at: datetime | None = None,
        cached_input_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        reasoning_effort: str | None = None,
        service_tier: str | None = None,
        requested_service_tier: str | None = None,
        actual_service_tier: str | None = None,
        transport: str | None = None,
        api_key_id: str | None = None,
        session_id: str | None = None,
        plan_type: str | None = None,
        source: str | None = None,
    ) -> object: ...


class StreamingLimitWarmupSender:
    def __init__(self, accounts_repo: AccountsRepository) -> None:
        self._accounts_repo = accounts_repo
        self._auth_manager = AuthManager(accounts_repo)
        self._encryptor = TokenEncryptor()

    async def send(self, account: Account, *, model: str, prompt: str) -> LimitWarmupSendResult:
        request_id = f"limit-warmup-{uuid.uuid4().hex}"
        started = time.monotonic()
        try:
            fresh_account = await self._auth_manager.ensure_fresh(account)
        except RefreshError as exc:
            return LimitWarmupSendResult(
                request_id=request_id,
                success=False,
                latency_ms=_elapsed_ms(started),
                error_code=f"auth_refresh_{exc.code}",
                error_message=exc.message,
            )

        if fresh_account.status != AccountStatus.ACTIVE:
            return LimitWarmupSendResult(
                request_id=request_id,
                success=False,
                latency_ms=_elapsed_ms(started),
                error_code="account_not_active",
                error_message=f"Account status is {fresh_account.status.value}",
            )

        payload = ResponsesRequest.model_validate(
            {
                "model": model,
                "instructions": _DEFAULT_WARMUP_INSTRUCTIONS,
                "input": prompt,
                "tools": [],
                "parallel_tool_calls": False,
                "stream": True,
                "store": False,
                "max_output_tokens": 4,
            }
        )
        headers = {
            "x-request-id": request_id,
            LIMIT_WARMUP_HEADER: "1",
            "user-agent": "codex-lb-limit-warmup",
        }
        access_token = self._encryptor.decrypt(fresh_account.access_token_encrypted)
        usage: ResponseUsage | None = None
        with override_stream_timeouts(
            connect_timeout_seconds=5.0,
            idle_timeout_seconds=10.0,
            total_timeout_seconds=30.0,
        ):
            async for event_block in stream_responses(
                payload,
                headers,
                access_token,
                fresh_account.chatgpt_account_id,
                upstream_stream_transport_override="http",
            ):
                event = parse_sse_event(event_block)
                if event is None:
                    continue
                if event.response is not None and event.response.usage is not None:
                    usage = event.response.usage
                if event.type == "response.completed":
                    return LimitWarmupSendResult(
                        request_id=request_id,
                        success=True,
                        latency_ms=_elapsed_ms(started),
                        usage=usage,
                    )
                if event.type in _TERMINAL_ERROR_EVENTS:
                    error = _event_error(event.error, event.response.error if event.response is not None else None)
                    return LimitWarmupSendResult(
                        request_id=request_id,
                        success=False,
                        latency_ms=_elapsed_ms(started),
                        usage=usage,
                        error_code=error.code or event.type,
                        error_message=error.message or event.type,
                    )

        return LimitWarmupSendResult(
            request_id=request_id,
            success=False,
            latency_ms=_elapsed_ms(started),
            usage=usage,
            error_code="stream_incomplete",
            error_message="Warm-up stream ended without a terminal event",
        )


class LimitWarmupService:
    def __init__(
        self,
        warmup_repo: LimitWarmupAttemptsRepository,
        request_logs_repo: LimitWarmupRequestLogRepository,
        *,
        sender: LimitWarmupSender | None = None,
    ) -> None:
        self._warmup_repo = warmup_repo
        self._request_logs_repo = request_logs_repo
        self._sender = sender

    async def run_after_usage_refresh(
        self,
        *,
        accounts: list[Account],
        settings: DashboardSettings,
        before_primary: dict[str, UsageHistory],
        before_secondary: dict[str, UsageHistory],
        after_primary: dict[str, UsageHistory],
        after_secondary: dict[str, UsageHistory],
    ) -> None:
        if not settings.limit_warmup_enabled:
            return
        selected_windows = _selected_windows(settings.limit_warmup_windows)
        if not selected_windows:
            return

        account_ids = [account.id for account in accounts]
        latest_attempts = await self._warmup_repo.latest_by_account(account_ids)
        sender = self._sender
        if sender is None:
            raise RuntimeError("LimitWarmupService requires a sender")

        for account in accounts:
            if not _account_is_safe_candidate(account):
                continue
            if not account.limit_warmup_enabled:
                continue
            latest_attempt = latest_attempts.get(account.id)
            if _in_cooldown(
                latest_attempt,
                cooldown_seconds=settings.limit_warmup_cooldown_seconds,
            ):
                continue

            for window in selected_windows:
                candidate = _build_candidate(
                    account=account,
                    window=window,
                    before_primary=before_primary,
                    before_secondary=before_secondary,
                    after_primary=after_primary,
                    after_secondary=after_secondary,
                    min_available_percent=settings.limit_warmup_min_available_percent,
                )
                if candidate is None:
                    continue

                model = self._resolve_model(settings.limit_warmup_model, account)
                if model is None:
                    skipped = await self._warmup_repo.try_create_attempt(
                        account_id=account.id,
                        window=window,
                        reset_at=candidate.reset_at,
                        model="auto",
                        attempted_at=utcnow(),
                    )
                    if skipped is not None:
                        completed = await self._warmup_repo.complete_attempt(
                            skipped.id,
                            status="skipped",
                            completed_at=utcnow(),
                            error_code="model_unavailable",
                            error_message="No eligible priced text model was available for warm-up",
                        )
                        latest_attempts[account.id] = completed or skipped
                    continue

                attempt = await self._warmup_repo.try_create_attempt(
                    account_id=account.id,
                    window=window,
                    reset_at=candidate.reset_at,
                    model=model,
                    attempted_at=utcnow(),
                )
                if attempt is None:
                    continue

                completed = await self._send_and_complete(
                    attempt,
                    account=account,
                    model=model,
                    prompt=settings.limit_warmup_prompt,
                    sender=sender,
                )
                latest_attempts[account.id] = completed or attempt
                break

    def _resolve_model(self, configured_model: str, account: Account) -> str | None:
        normalized = configured_model.strip()
        if normalized and normalized.lower() != "auto":
            return normalized

        candidates: list[tuple[float, str]] = []
        for model in get_model_registry().get_models_with_fallback().values():
            if not model.supported_in_api:
                continue
            if model.input_modalities and "text" not in {modality.lower() for modality in model.input_modalities}:
                continue
            if model.available_in_plans and not account_plan_matches_allowed(
                account.plan_type, model.available_in_plans
            ):
                continue
            resolved_price = get_pricing_for_model(model.slug)
            if resolved_price is None:
                continue
            _, price = resolved_price
            candidates.append((price.input_per_1m + price.output_per_1m, model.slug))
        if not candidates:
            return None
        return min(candidates, key=lambda item: (item[0], item[1]))[1]

    async def _send_and_complete(
        self,
        attempt: AccountLimitWarmup,
        *,
        account: Account,
        model: str,
        prompt: str,
        sender: LimitWarmupSender,
    ) -> AccountLimitWarmup | None:
        try:
            result = await sender.send(account, model=model, prompt=prompt)
        except Exception as exc:
            logger.warning(
                "Limit warm-up send failed account_id=%s window=%s", account.id, attempt.window, exc_info=True
            )
            completed_at = utcnow()
            return await self._warmup_repo.complete_attempt(
                attempt.id,
                status="failed",
                completed_at=completed_at,
                error_code="warmup_send_failed",
                error_message=_truncate(str(exc)),
            )

        await self._record_request_log(
            account=account,
            model=model,
            result=result,
        )
        status = "succeeded" if result.success else "failed"
        error_code = result.error_code
        if error_code in _QUOTA_ERROR_CODES:
            error_code = "quota_still_exhausted"
        return await self._warmup_repo.complete_attempt(
            attempt.id,
            status=status,
            completed_at=utcnow(),
            error_code=error_code,
            error_message=_truncate(result.error_message),
        )

    async def _record_request_log(
        self,
        *,
        account: Account,
        model: str,
        result: LimitWarmupSendResult,
    ) -> None:
        usage = result.usage
        input_tokens = usage.input_tokens if usage is not None else None
        output_tokens = usage.output_tokens if usage is not None else None
        cached_input_tokens = (
            usage.input_tokens_details.cached_tokens
            if usage is not None and usage.input_tokens_details is not None
            else None
        )
        reasoning_tokens = (
            usage.output_tokens_details.reasoning_tokens
            if usage is not None and usage.output_tokens_details is not None
            else None
        )
        await self._request_logs_repo.add_log(
            account_id=account.id,
            request_id=result.request_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            reasoning_tokens=reasoning_tokens,
            latency_ms=result.latency_ms,
            status="success" if result.success else "error",
            error_code=result.error_code,
            error_message=_truncate(result.error_message),
            transport="http",
            plan_type=account.plan_type,
            source=LIMIT_WARMUP_SOURCE,
        )


@dataclass(frozen=True, slots=True)
class _WarmupCandidate:
    reset_at: int


def _selected_windows(value: str) -> tuple[str, ...]:
    normalized = value.strip().lower()
    if normalized == "both":
        return ("primary", "secondary")
    if normalized in {"primary", "secondary"}:
        return (normalized,)
    return ()


def _account_is_safe_candidate(account: Account) -> bool:
    return account.status == AccountStatus.ACTIVE


def _in_cooldown(attempt: AccountLimitWarmup | None, *, cooldown_seconds: int) -> bool:
    if attempt is None:
        return False
    return utcnow() - attempt.attempted_at < timedelta(seconds=cooldown_seconds)


def _build_candidate(
    *,
    account: Account,
    window: str,
    before_primary: dict[str, UsageHistory],
    before_secondary: dict[str, UsageHistory],
    after_primary: dict[str, UsageHistory],
    after_secondary: dict[str, UsageHistory],
    min_available_percent: float,
) -> _WarmupCandidate | None:
    before = _effective_usage_entry(
        account.id,
        window=window,
        primary=before_primary,
        secondary=before_secondary,
    )
    after = _effective_usage_entry(
        account.id,
        window=window,
        primary=after_primary,
        secondary=after_secondary,
    )
    if before is None or after is None:
        return None
    if before.reset_at is None or after.reset_at is None:
        return None
    if before.used_percent < 100.0:
        return None
    available_percent = max(0.0, 100.0 - after.used_percent)
    if available_percent < min_available_percent:
        return None
    if after.reset_at <= before.reset_at:
        return None
    return _WarmupCandidate(reset_at=after.reset_at)


def _effective_usage_entry(
    account_id: str,
    *,
    window: str,
    primary: dict[str, UsageHistory],
    secondary: dict[str, UsageHistory],
) -> UsageHistory | None:
    if window == "primary":
        primary_entry = primary.get(account_id)
        if primary_entry is None or usage_core.is_weekly_window_minutes(primary_entry.window_minutes):
            return None
        return primary_entry

    primary_entry = primary.get(account_id)
    secondary_entry = secondary.get(account_id)
    if primary_entry is not None and usage_core.is_weekly_window_minutes(primary_entry.window_minutes):
        if secondary_entry is None:
            return primary_entry
        if usage_core.should_use_weekly_primary(
            usage_history_to_window_row(primary_entry),
            usage_history_to_window_row(secondary_entry),
        ):
            return primary_entry
    return secondary_entry


def _event_error(*errors: OpenAIError | None) -> OpenAIError:
    for error in errors:
        if error is not None:
            return error
    return OpenAIError(message=None, code=None)


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _truncate(value: str | None, limit: int = 1000) -> str | None:
    if value is None:
        return None
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "..."
