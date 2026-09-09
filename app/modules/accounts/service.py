from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from typing import cast

from pydantic import ValidationError

from app.core.auth import (
    DEFAULT_EMAIL,
    DEFAULT_PLAN,
    claims_from_auth,
    generate_unique_account_id,
    parse_auth_json,
    token_expiry_epoch_ms,
)
from app.core.auth.api_key_cache import get_api_key_cache
from app.core.cache.invalidation import NAMESPACE_API_KEY, get_cache_invalidation_poller
from app.core.clients import rate_limit_resets
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.plan_types import coerce_account_plan_type
from app.core.providers import (
    ANTHROPIC_PROVIDER_NAME,
    GLM_DEFAULT_PLAN,
    GLM_PROVIDER_NAME,
    OPENAI_PROVIDER_NAME,
    get_provider,
    normalize_provider_name,
)
from app.core.utils.time import naive_utc_to_epoch, to_utc_naive, utcnow
from app.db.models import Account, AccountStatus, AdditionalUsageHistory
from app.modules.accounts import probes, reset_credit_cache
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.mappers import build_account_summaries, build_account_usage_trends
from app.modules.accounts.repository import AccountsRepository
from app.modules.accounts.reset_credit_attempts import ResetCreditAttemptsRepository
from app.modules.accounts.reset_credit_recovery import refresh_standard_capacity
from app.modules.accounts.schemas import (
    AccountAdditionalQuota,
    AccountAdditionalWindow,
    AccountApiKeyImportRequest,
    AccountAuthExportResponse,
    AccountAuthExportTokens,
    AccountExportResponse,
    AccountImportResponse,
    AccountOpenCodeAuthExportAccount,
    AccountOpenCodeAuthExportResponse,
    AccountProbeResponse,
    AccountRequestUsage,
    AccountResetCredit,
    AccountResetCreditConsumeResponse,
    AccountResetCreditsResponse,
    AccountSubscriptionCheckResponse,
    AccountSubscriptionLedger,
    AccountSummary,
    AccountTrendsResponse,
    CodexAuthJson,
    CodexAuthTokens,
    OpenCodeAuthJson,
    OpenCodeOAuthAuth,
)
from app.modules.accounts.subscription_status import (
    CANCELED_SUBSCRIPTION_STATUS,
    is_subscription_usable,
    normalize_subscription_status,
)
from app.modules.limit_warmup.repository import LimitWarmupRepository
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.usage.additional_quota_keys import (
    get_additional_display_label_for_quota_key,
    get_additional_quota_routing_policy,
    list_additional_quota_definitions,
)
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository
from app.modules.usage.updater import AdditionalUsageRepositoryPort, UsageUpdater

logger = logging.getLogger(__name__)

_SPARKLINE_DAYS = 7
_DETAIL_BUCKET_SECONDS = 3600  # 1h → 168 points

# Probe senders and defaults live in app.modules.accounts.probes so background
# schedulers can reuse them; re-exported here for the existing import surface.
DEFAULT_PROBE_MODEL = probes.DEFAULT_PROBE_MODEL
DEFAULT_ANTHROPIC_SUBSCRIPTION_CHECK_MODEL = probes.DEFAULT_ANTHROPIC_SUBSCRIPTION_CHECK_MODEL
DEFAULT_GLM_PROBE_MODEL = probes.DEFAULT_GLM_PROBE_MODEL
PROBE_REQUEST_TIMEOUT_SECONDS = probes.PROBE_REQUEST_TIMEOUT_SECONDS
PROBE_CONNECT_TIMEOUT_SECONDS = probes.PROBE_CONNECT_TIMEOUT_SECONDS
PROBE_NETWORK_FAILURE_STATUS = probes.PROBE_NETWORK_FAILURE_STATUS

# Short-TTL cache for the expensive per-account request-usage aggregation (a dedup
# window over the full request_logs history, ~2s on a large DB). request-usage is a
# cumulative, non-real-time token/cost tally, so brief staleness is acceptable; only
# the dashboard (GET /api/accounts?fresh=1) computes it, and this keeps repeated
# dashboard polls from re-running the scan. Keyed by the account-id set.
_REQUEST_USAGE_CACHE_TTL_SECONDS = 20.0
_request_usage_cache: dict[frozenset[str], tuple[float, dict[str, AccountRequestUsage]]] = {}

# Short-TTL cache for the per-account additional-quota windows. Assembling these is
# ~14 serial DB round-trips (a single AsyncSession can't run them concurrently), and
# the data is background-refreshed by the usage scheduler, so a few seconds of
# staleness is fine and keeps repeated cc/menubar startup calls cheap.
_ADDITIONAL_QUOTAS_CACHE_TTL_SECONDS = 12.0
_additional_quotas_cache: dict[frozenset[str], tuple[float, dict[str, list[AccountAdditionalQuota]]]] = {}

# Mirrored in app/modules/proxy/anthropic_service.py and app/modules/usage/updater.py
# — all three must agree on the quota_key/window identifying Anthropic's
# dedicated Fable-scoped weekly limit marker.
_ANTHROPIC_FABLE_SCOPED_WEEKLY_QUOTA_KEY = "anthropic_fable_scoped_weekly"
_ANTHROPIC_FABLE_SCOPED_WEEKLY_WINDOW = "primary"


def clear_account_caches() -> None:
    """Clear the in-process accounts read caches (used by tests for isolation)."""
    _request_usage_cache.clear()
    _additional_quotas_cache.clear()


class InvalidAuthJsonError(Exception):
    pass


class AccountNotProbableError(Exception):
    """Raised when an account is in a status that disallows probing."""


class AccountResetCreditsUnavailableError(Exception):
    """Raised when an account cannot list or redeem rate-limit reset credits."""


class AccountStateTransitionError(Exception):
    """Raised when an operator action is not valid for the account state."""


def _additional_quota_window(
    entry: AdditionalUsageHistory | None,
    *,
    now_epoch: int,
) -> AccountAdditionalWindow | None:
    if entry is None:
        return None
    # An exhausted window (used_percent >= 100) with no bounded reset horizon —
    # either already elapsed or persisted as None — is a stale block that must
    # not display as permanently 100% consumed. Present it as re-admitted.
    if float(entry.used_percent) >= 100.0 and (entry.reset_at is None or entry.reset_at <= now_epoch):
        return AccountAdditionalWindow(
            used_percent=0.0,
            reset_at=None,
            window_minutes=None,
        )
    return AccountAdditionalWindow(
        used_percent=entry.used_percent,
        reset_at=entry.reset_at,
        window_minutes=entry.window_minutes,
    )


class AccountsService:
    def __init__(
        self,
        repo: AccountsRepository,
        usage_repo: UsageRepository | None = None,
        additional_usage_repo: AdditionalUsageRepository | AdditionalUsageRepositoryPort | None = None,
        limit_warmup_repo: LimitWarmupRepository | None = None,
        auth_manager: AuthManager | None = None,
    ) -> None:
        self._repo = repo
        self._usage_repo = usage_repo
        self._additional_usage_repo = additional_usage_repo
        self._limit_warmup_repo = limit_warmup_repo
        self._usage_updater = UsageUpdater(usage_repo, repo, additional_usage_repo) if usage_repo else None
        self._encryptor = TokenEncryptor()
        self._auth_manager = auth_manager
        self._reset_attempts: ResetCreditAttemptsRepository | None = None

    async def list_accounts(self, *, include_request_usage: bool = False) -> list[AccountSummary]:
        accounts = await self._repo.list_accounts()
        if not accounts:
            return []
        account_ids = [account.id for account in accounts]
        account_id_set = set(account_ids)
        primary_usage = await self._usage_repo.latest_by_account(window="primary") if self._usage_repo else {}
        secondary_usage = await self._usage_repo.latest_by_account(window="secondary") if self._usage_repo else {}
        monthly_usage = await self._usage_repo.latest_by_account(window="monthly") if self._usage_repo else {}
        limit_warmups_by_account = (
            await self._limit_warmup_repo.latest_by_account(account_ids) if self._limit_warmup_repo else {}
        )
        # request-usage is an expensive dedup aggregation over the full request_logs
        # history and is consumed only by the dashboard token/cost columns — not the cc
        # banner or menubar. Keep it off the hot path: compute (cached) only when asked
        # for via GET /api/accounts?fresh=1.
        request_usage_by_account: dict[str, AccountRequestUsage] = {}
        if include_request_usage:
            request_usage_by_account = await self._request_usage_by_account(account_ids)
        additional_quotas_by_account = await self._additional_quotas_by_account(account_ids, account_id_set)
        fable_scoped_weekly_by_account = await self._fable_scoped_weekly_by_account(account_ids)

        return build_account_summaries(
            accounts=accounts,
            primary_usage=primary_usage,
            secondary_usage=secondary_usage,
            monthly_usage=monthly_usage,
            request_usage_by_account=request_usage_by_account,
            additional_quotas_by_account=additional_quotas_by_account,
            limit_warmups_by_account=limit_warmups_by_account,
            fable_scoped_weekly_by_account=fable_scoped_weekly_by_account,
            encryptor=self._encryptor,
        )

    async def _fable_scoped_weekly_by_account(self, account_ids: list[str]) -> dict[str, AdditionalUsageHistory]:
        """Single batched lookup mirroring the routing path's eligibility read
        (app/modules/proxy/anthropic_service.py) — one query, no N+1."""
        if not self._additional_usage_repo:
            return {}
        additional_usage_repo = cast(AdditionalUsageRepository, self._additional_usage_repo)
        return await additional_usage_repo.latest_by_account(
            _ANTHROPIC_FABLE_SCOPED_WEEKLY_QUOTA_KEY,
            _ANTHROPIC_FABLE_SCOPED_WEEKLY_WINDOW,
            account_ids=account_ids,
        )

    async def _request_usage_by_account(self, account_ids: list[str]) -> dict[str, AccountRequestUsage]:
        """Per-account cumulative request-usage, short-TTL cached.

        The underlying query is a dedup window over the full request_logs history
        (~2s on a large DB). It backs the dashboard token/cost columns only, so a few
        seconds of staleness is fine and keeps repeated dashboard polls cheap.
        """
        key = frozenset(account_ids)
        now = time.monotonic()
        cached = _request_usage_cache.get(key)
        if cached is not None and (now - cached[0]) < _REQUEST_USAGE_CACHE_TTL_SECONDS:
            return cached[1]
        rows = await self._repo.list_request_usage_summary_by_account(account_ids)
        result = {
            account_id: AccountRequestUsage(
                request_count=row.request_count,
                total_tokens=row.total_tokens,
                cached_input_tokens=row.cached_input_tokens,
                cache_creation_tokens=row.cache_creation_tokens,
                cache_read_tokens=row.cache_read_tokens,
                total_cost_usd=row.total_cost_usd,
            )
            for account_id, row in rows.items()
        }
        _request_usage_cache[key] = (now, result)
        return result

    async def _additional_quotas_by_account(
        self, account_ids: list[str], account_id_set: set[str]
    ) -> dict[str, list[AccountAdditionalQuota]]:
        """Per-account additional-quota windows, short-TTL cached (see module cache)."""
        key = frozenset(account_ids)
        now = time.monotonic()
        cached = _additional_quotas_cache.get(key)
        if cached is not None and (now - cached[0]) < _ADDITIONAL_QUOTAS_CACHE_TTL_SECONDS:
            return cached[1]
        result: dict[str, list[AccountAdditionalQuota]] = {}
        additional_usage_repo = cast(AdditionalUsageRepository | None, self._additional_usage_repo)
        if additional_usage_repo:
            additional_quota_routing_overrides = await self._repo.additional_quota_routing_policy_overrides()
            quota_keys = [definition.quota_key for definition in list_additional_quota_definitions()]
            # The Fable scoped-weekly marker is written outside the registry
            # (see app/modules/usage/updater.py); clients read its percent from
            # additionalQuotas, so enumerate it explicitly.
            if _ANTHROPIC_FABLE_SCOPED_WEEKLY_QUOTA_KEY not in quota_keys:
                quota_keys.append(_ANTHROPIC_FABLE_SCOPED_WEEKLY_QUOTA_KEY)
            now_epoch = int(time.time())
            for quota_key in quota_keys:
                primary_entries = await additional_usage_repo.latest_by_account(
                    quota_key, "primary", account_ids=account_ids
                )
                secondary_entries = await additional_usage_repo.latest_by_account(
                    quota_key, "secondary", account_ids=account_ids
                )
                for account_id in (set(primary_entries) | set(secondary_entries)) & account_id_set:
                    primary_entry = primary_entries.get(account_id)
                    secondary_entry = secondary_entries.get(account_id)
                    reference_entry = primary_entry or secondary_entry
                    if reference_entry is None:
                        continue
                    result.setdefault(account_id, []).append(
                        AccountAdditionalQuota(
                            quota_key=quota_key,
                            limit_name=reference_entry.limit_name,
                            metered_feature=reference_entry.metered_feature,
                            display_label=get_additional_display_label_for_quota_key(quota_key)
                            or reference_entry.limit_name,
                            routing_policy=get_additional_quota_routing_policy(
                                quota_key,
                                overrides=additional_quota_routing_overrides,
                            ),
                            primary_window=_additional_quota_window(primary_entry, now_epoch=now_epoch),
                            secondary_window=_additional_quota_window(secondary_entry, now_epoch=now_epoch),
                        )
                    )
        for account_quota_list in result.values():
            account_quota_list.sort(key=lambda quota: quota.display_label or quota.quota_key or quota.limit_name)
        _additional_quotas_cache[key] = (now, result)
        return result

    async def get_account_trends(self, account_id: str) -> AccountTrendsResponse | None:
        account = await self._repo.get_by_id(account_id)
        if not account or not self._usage_repo:
            return None
        now = utcnow()
        since = now - timedelta(days=_SPARKLINE_DAYS)
        since_epoch = naive_utc_to_epoch(since)
        bucket_count = (_SPARKLINE_DAYS * 24 * 3600) // _DETAIL_BUCKET_SECONDS
        buckets = await self._usage_repo.trends_by_bucket(
            since=since,
            bucket_seconds=_DETAIL_BUCKET_SECONDS,
            account_id=account_id,
        )
        trends = build_account_usage_trends(buckets, since_epoch, _DETAIL_BUCKET_SECONDS, bucket_count)
        trend = trends.get(account_id)
        return AccountTrendsResponse(
            account_id=account_id,
            primary=trend.primary if trend else [],
            secondary=trend.secondary if trend else [],
            secondary_scheduled=trend.secondary_scheduled if trend else [],
        )

    async def export_opencode_auth(self, account_id: str) -> AccountOpenCodeAuthExportResponse | None:
        account = await self._repo.get_by_id(account_id)
        if account is None:
            return None

        access_token = self._encryptor.decrypt(account.access_token_encrypted)
        refresh_token = self._encryptor.decrypt(account.refresh_token_encrypted)
        expires = token_expiry_epoch_ms(access_token) or 0
        return AccountOpenCodeAuthExportResponse(
            filename=_opencode_auth_export_filename(account),
            account=AccountOpenCodeAuthExportAccount(
                account_id=account.id,
                chatgpt_account_id=account.chatgpt_account_id,
                email=account.email,
            ),
            auth_json=OpenCodeAuthJson(
                openai=OpenCodeOAuthAuth(
                    refresh=refresh_token,
                    access=access_token,
                    expires=expires,
                    account_id=account.chatgpt_account_id,
                ),
            ),
        )

    async def export_auth(self, account_id: str) -> AccountAuthExportResponse | None:
        account = await self._repo.get_by_id(account_id)
        if account is None:
            return None

        access_token = self._encryptor.decrypt(account.access_token_encrypted)
        refresh_token = self._encryptor.decrypt(account.refresh_token_encrypted)
        # Anthropic OAuth issues no id_token; the column is NULL for those rows.
        id_token = self._encryptor.decrypt(account.id_token_encrypted) if account.id_token_encrypted else ""
        expires = token_expiry_epoch_ms(access_token) or 0

        tokens = AccountAuthExportTokens(
            id_token=id_token,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at_ms=expires,
        )

        codex_auth_json = CodexAuthJson(
            auth_mode="chatgpt",
            openai_api_key=None,
            tokens=CodexAuthTokens(
                id_token=id_token,
                access_token=access_token,
                refresh_token=refresh_token,
                account_id=account.chatgpt_account_id,
            ),
            last_refresh=account.last_refresh.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
        )

        opencode_auth_json = OpenCodeAuthJson(
            openai=OpenCodeOAuthAuth(
                refresh=refresh_token,
                access=access_token,
                expires=expires,
                account_id=account.chatgpt_account_id,
            ),
        )

        return AccountAuthExportResponse(
            filename=_opencode_auth_export_filename(account),
            account=AccountOpenCodeAuthExportAccount(
                account_id=account.id,
                chatgpt_account_id=account.chatgpt_account_id,
                email=account.email,
            ),
            tokens=tokens,
            codex_auth_json=codex_auth_json,
            opencode_auth_json=opencode_auth_json,
        )

    async def import_account(self, raw: bytes) -> AccountImportResponse:
        try:
            auth = parse_auth_json(raw)
        except (json.JSONDecodeError, ValidationError, UnicodeDecodeError, TypeError) as exc:
            raise InvalidAuthJsonError("Invalid auth.json payload") from exc
        provider = get_provider(OPENAI_PROVIDER_NAME)
        metadata = provider.account_metadata_from_id_token(auth.tokens.id_token)
        claims = claims_from_auth(auth)

        email = claims.email or metadata.email or DEFAULT_EMAIL
        raw_account_id = claims.account_id or metadata.account_id
        account_id = generate_unique_account_id(raw_account_id, email, claims.workspace_id)
        plan_type = coerce_account_plan_type(claims.plan_type or metadata.plan_type, DEFAULT_PLAN)
        last_refresh = to_utc_naive(auth.last_refresh_at) if auth.last_refresh_at else utcnow()

        account = Account(
            id=account_id,
            provider=provider.name,
            chatgpt_account_id=raw_account_id,
            email=email,
            workspace_id=claims.workspace_id,
            workspace_label=claims.workspace_label,
            seat_type=claims.seat_type,
            plan_type=plan_type,
            access_token_encrypted=self._encryptor.encrypt(auth.tokens.access_token),
            refresh_token_encrypted=self._encryptor.encrypt(auth.tokens.refresh_token),
            id_token_encrypted=self._encryptor.encrypt(auth.tokens.id_token),
            last_refresh=last_refresh,
            status=AccountStatus.ACTIVE,
            deactivation_reason=None,
        )

        saved = await self._repo.upsert_account_slot(account)
        if self._usage_repo and self._usage_updater:
            latest_usage = await self._usage_repo.latest_by_account(window="primary")
            await self._usage_updater.refresh_accounts([saved], latest_usage)
        get_account_selection_cache().invalidate()
        return AccountImportResponse(
            account_id=saved.id,
            email=saved.email,
            workspace_id=saved.workspace_id,
            workspace_label=saved.workspace_label,
            seat_type=saved.seat_type,
            plan_type=saved.plan_type,
            status=saved.status,
        )

    async def import_api_key_account(self, payload: AccountApiKeyImportRequest) -> AccountImportResponse:
        provider_name = normalize_provider_name(payload.provider)
        if provider_name != GLM_PROVIDER_NAME:
            raise ValueError(f"Provider {provider_name} does not support API-key account import")
        provider = get_provider(provider_name)
        api_key = payload.api_key.get_secret_value().strip()
        if not api_key:
            raise ValueError("apiKey is required")
        email = payload.email.strip().lower()
        raw_account_id = (payload.account_id or "zai_glm_coding").strip()
        account_id = generate_unique_account_id(raw_account_id, email)
        plan_type = coerce_account_plan_type(payload.plan_type, GLM_DEFAULT_PLAN)

        account = Account(
            id=account_id,
            provider=provider.name,
            chatgpt_account_id=raw_account_id,
            email=email,
            workspace_id=None,
            workspace_label=None,
            seat_type=None,
            plan_type=plan_type,
            access_token_encrypted=self._encryptor.encrypt(api_key),
            refresh_token_encrypted=self._encryptor.encrypt(api_key),
            id_token_encrypted=None,
            last_refresh=utcnow(),
            status=AccountStatus.ACTIVE,
            deactivation_reason=None,
        )

        saved = await self._repo.upsert_account_slot(account, preserve_unknown_workspace_duplicates=False)
        if payload.alias is not None:
            alias = payload.alias.strip() or None
            await self._repo.update_alias(saved.id, alias)
            saved.alias = alias
        get_account_selection_cache().invalidate()
        return AccountImportResponse(
            account_id=saved.id,
            email=saved.email,
            workspace_id=saved.workspace_id,
            workspace_label=saved.workspace_label,
            seat_type=saved.seat_type,
            plan_type=saved.plan_type,
            status=saved.status,
        )

    async def reactivate_account(self, account_id: str) -> bool:
        account = await self._repo.get_by_id(account_id)
        if account is None:
            return False
        if account.status == AccountStatus.REAUTH_REQUIRED:
            raise AccountStateTransitionError("Account requires re-authentication and cannot be reactivated directly")
        result = await self._repo.update_status_if_current(
            account_id,
            AccountStatus.ACTIVE,
            None,
            None,
            blocked_at=None,
            expected_status=account.status,
            expected_deactivation_reason=account.deactivation_reason,
            expected_reset_at=account.reset_at,
            expected_blocked_at=account.blocked_at,
        )
        if not result:
            raise AccountStateTransitionError("Account state changed; retry the operation")
        if result:
            get_account_selection_cache().invalidate()
        return result

    async def pause_account(self, account_id: str) -> bool:
        account = await self._repo.get_by_id(account_id)
        if account is None:
            return False
        if account.status in (AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED):
            raise AccountStateTransitionError(f"Account is {account.status.value} and cannot be paused")
        result = await self._repo.update_status_if_current(
            account_id,
            AccountStatus.PAUSED,
            None,
            None,
            blocked_at=None,
            expected_status=account.status,
            expected_deactivation_reason=account.deactivation_reason,
            expected_reset_at=account.reset_at,
            expected_blocked_at=account.blocked_at,
        )
        if not result:
            raise AccountStateTransitionError("Account state changed; retry the operation")
        if result:
            get_account_selection_cache().invalidate()
        return result

    async def update_account(self, account_id: str, *, security_work_authorized: bool | None = None) -> bool:
        result = False
        if security_work_authorized is not None:
            result = await self._repo.update_security_work_authorized(account_id, security_work_authorized)
        if result:
            get_account_selection_cache().invalidate()
        return result

    async def set_limit_warmup_enabled(self, account_id: str, enabled: bool) -> bool:
        result = await self._repo.update_limit_warmup_enabled(account_id, enabled)
        if result:
            get_account_selection_cache().invalidate()
        return result

    async def set_routing_policy(self, account_id: str, routing_policy: str) -> bool:
        result = await self._repo.update_routing_policy(account_id, routing_policy)
        if result:
            get_account_selection_cache().invalidate()
        return result

    async def set_subscription_ledger(
        self,
        account_id: str,
        payload: AccountSubscriptionLedger,
    ) -> AccountSubscriptionLedger | None:
        currency = payload.currency.upper() if payload.currency else None
        notes = payload.notes.strip() if payload.notes else None
        if notes == "":
            notes = None
        next_charge_at = to_utc_naive(payload.next_charge_at) if payload.next_charge_at else None
        current_period_end_at = to_utc_naive(payload.current_period_end_at) if payload.current_period_end_at else None
        last_verified_at = to_utc_naive(payload.last_verified_at) if payload.last_verified_at else None
        updated = await self._repo.update_subscription_ledger(
            account_id,
            status=payload.status,
            next_charge_at=next_charge_at,
            current_period_end_at=current_period_end_at,
            amount=payload.amount,
            currency=currency,
            last_verified_at=last_verified_at,
            notes=notes,
        )
        if not updated:
            return None
        get_account_selection_cache().invalidate()
        return AccountSubscriptionLedger(
            status=payload.status,
            next_charge_at=next_charge_at,
            current_period_end_at=current_period_end_at,
            amount=payload.amount,
            currency=currency,
            last_verified_at=last_verified_at,
            notes=notes,
        )

    async def check_subscription(self, account_id: str) -> AccountSubscriptionCheckResponse | None:
        account = await self._repo.get_by_id(account_id)
        if account is None:
            return None
        if normalize_subscription_status(account.subscription_status) != CANCELED_SUBSCRIPTION_STATUS:
            raise AccountStateTransitionError("Subscription checks are only available for canceled accounts")

        check_account = account
        if self._auth_manager is not None:
            check_account = await self._auth_manager.ensure_fresh(account, force=False)

        access_token = self._encryptor.decrypt(check_account.access_token_encrypted)
        provider = normalize_provider_name(check_account.provider)
        message: str | None = None
        if provider == ANTHROPIC_PROVIDER_NAME:
            probe_status, message = await self._send_anthropic_subscription_check_request(access_token=access_token)
        elif provider == OPENAI_PROVIDER_NAME:
            probe_status = await self._send_probe_request(
                access_token=access_token,
                chatgpt_account_id=check_account.chatgpt_account_id,
                model=DEFAULT_PROBE_MODEL,
            )
        else:
            raise AccountStateTransitionError(f"Provider {provider} cannot be subscription-checked")

        working = 200 <= probe_status < 300
        now = utcnow()
        notes = _subscription_check_notes(
            working=working,
            probe_status=probe_status,
            checked_at=now,
            message=message,
        )
        subscription = await self.set_subscription_ledger(
            account_id,
            AccountSubscriptionLedger(
                status="active" if working else "canceled",
                next_charge_at=account.subscription_next_charge_at,
                current_period_end_at=account.subscription_current_period_end_at,
                amount=account.subscription_amount,
                currency=account.subscription_currency,
                last_verified_at=now,
                notes=notes,
            ),
        )
        return AccountSubscriptionCheckResponse(
            status="checked",
            account_id=account_id,
            working=working,
            probe_status_code=probe_status,
            subscription=subscription,
            message=message,
        )

    async def delete_account(self, account_id: str, *, delete_history: bool = False) -> bool:
        result = await self._repo.delete(account_id, delete_history=delete_history)
        if result:
            get_account_selection_cache().invalidate()
            get_api_key_cache().clear()
            poller = get_cache_invalidation_poller()
            if poller is not None:
                await poller.bump(NAMESPACE_API_KEY)
        return result

    async def set_account_alias(self, account_id: str, alias: str | None) -> bool:
        normalized = alias.strip() if isinstance(alias, str) else None
        if normalized == "":
            normalized = None
        return await self._repo.update_alias(account_id, normalized)

    async def export_account(self, account_id: str) -> AccountExportResponse | None:
        account = await self._repo.get_by_id(account_id)
        if not account:
            return None
        access_token = self._encryptor.decrypt(account.access_token_encrypted)
        refresh_token = self._encryptor.decrypt(account.refresh_token_encrypted)
        id_token = self._encryptor.decrypt(account.id_token_encrypted) if account.id_token_encrypted else ""
        auth_json = {
            "auth_mode": "chatgpt",
            "OPENAI_API_KEY": None,
            "tokens": {
                "id_token": id_token,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "account_id": account.chatgpt_account_id,
            },
            "last_refresh": account.last_refresh.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
        }
        return AccountExportResponse(
            account_id=account.id,
            email=account.email,
            workspace_id=account.workspace_id,
            workspace_label=account.workspace_label,
            seat_type=account.seat_type,
            plan_type=account.plan_type,
            status=account.status.value,
            auth_json=json.dumps(auth_json, indent=2),
        )

    async def probe_account(
        self,
        account_id: str,
        model: str | None = None,
    ) -> AccountProbeResponse | None:
        """Send a minimal upstream ``responses.create`` pinned to one account.

        Bypasses load-balancer scoring so an operator can wake the upstream
        rate-limiter for a stuck account (see upstream issues #676 / #677).
        Triggers an immediate usage refresh after the probe and returns the
        before/after snapshot so the operator can see whether the upstream
        state changed.
        """
        account = await self._repo.get_by_id(account_id)
        if account is None:
            return None
        if account.status in (AccountStatus.PAUSED, AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED):
            raise AccountNotProbableError(f"Account is {account.status.value} and cannot be probed")

        primary_before, secondary_before = await self._latest_usage_percents(account_id)
        status_before = account.status.value

        probe_account = account
        if self._auth_manager is not None:
            probe_account = await self._auth_manager.ensure_fresh(account, force=False)

        access_token = self._encryptor.decrypt(probe_account.access_token_encrypted)
        probe_model = model or DEFAULT_PROBE_MODEL
        provider = normalize_provider_name(probe_account.provider)
        if provider == GLM_PROVIDER_NAME:
            probe_status, _ = await self._send_messages_probe_request(
                access_token=access_token,
                base_url=get_settings().glm_anthropic_upstream_base_url,
                model=model or DEFAULT_GLM_PROBE_MODEL,
            )
        elif provider == ANTHROPIC_PROVIDER_NAME:
            probe_status, _ = await self._send_messages_probe_request(
                access_token=access_token,
                base_url=get_settings().anthropic_upstream_base_url,
                model=model or DEFAULT_ANTHROPIC_SUBSCRIPTION_CHECK_MODEL,
            )
        else:
            probe_status = await self._send_probe_request(
                access_token=access_token,
                chatgpt_account_id=probe_account.chatgpt_account_id,
                model=probe_model,
            )

        if self._usage_repo and self._usage_updater and provider != GLM_PROVIDER_NAME:
            await self._usage_updater.force_refresh(probe_account)
            get_account_selection_cache().invalidate()

        refreshed = await self._repo.get_by_id(account_id) or account
        primary_after, secondary_after = await self._latest_usage_percents(account_id)

        return AccountProbeResponse(
            status="probed",
            account_id=account_id,
            probe_status_code=probe_status,
            primary_used_percent_before=primary_before,
            primary_used_percent_after=primary_after,
            secondary_used_percent_before=secondary_before,
            secondary_used_percent_after=secondary_after,
            account_status_before=status_before,
            account_status_after=refreshed.status.value,
        )

    async def list_rate_limit_reset_credits(self, account_id: str) -> AccountResetCreditsResponse | None:
        account = await self._repo.get_by_id(account_id)
        if account is None:
            return None
        credit_account = await self._reset_credit_account(account)
        access_token = self._encryptor.decrypt(credit_account.access_token_encrypted)
        payload = await rate_limit_resets.fetch_reset_credits(
            access_token=access_token,
            chatgpt_account_id=credit_account.chatgpt_account_id,
        )
        reset_credit_cache.record_count(
            account_id,
            sum(credit.status == "available" for credit in payload.credits),
        )
        return AccountResetCreditsResponse(
            account_id=account_id,
            available_count=payload.available_count,
            credits=[AccountResetCredit(**credit.model_dump()) for credit in payload.credits],
        )

    async def redeem_rate_limit_reset_credit(
        self,
        account_id: str,
        credit_id: str | None = None,
        *,
        trigger: str = "manual",
    ) -> AccountResetCreditConsumeResponse | None:
        """Consume one banked upstream reset credit and refresh usage state.

        ``already_redeemed`` is idempotent success per the Codex app-server
        contract, so both it and ``reset`` trigger the post-redeem refresh.
        """
        account = await self._repo.get_by_id(account_id)
        if account is None:
            return None
        credit_account = await self._reset_credit_account(account)

        primary_before, secondary_before = await self._latest_usage_percents(account_id)
        access_token = self._encryptor.decrypt(credit_account.access_token_encrypted)
        attempts = self._reset_attempts or ResetCreditAttemptsRepository(self._repo.session)
        attempt = await attempts.active()
        retrying = attempt is not None
        if attempt is not None:
            if attempt.account_id != account_id or (credit_id is not None and attempt.credit_id != credit_id):
                raise AccountResetCreditsUnavailableError("Another reset attempt requires reconciliation")
            if not await attempts.acquire(attempt):
                raise AccountResetCreditsUnavailableError("Reset recovery is already running or cooling down")
        else:
            inventory = await rate_limit_resets.fetch_reset_credits(
                access_token=access_token, chatgpt_account_id=credit_account.chatgpt_account_id,
            )
            available = [item for item in inventory.credits if item.status == "available"]
            reset_credit_cache.record_count(account_id, len(available))
            if inventory.available_count <= 0 or not available or (
                credit_id is not None and not any(item.id == credit_id for item in available)
            ):
                return AccountResetCreditConsumeResponse(
                    status="not_redeemed", account_id=account_id, code="no_credit", windows_reset=0,
                    primary_used_percent_before=primary_before, secondary_used_percent_before=secondary_before,
                    primary_used_percent_after=primary_before, secondary_used_percent_after=secondary_before,
                )
            if credit_id is None:
                credit_id = min(available, key=lambda item: item.expires_at or "9999").id
            attempt = await attempts.create(account_id, credit_id, trigger)
            if attempt is None:
                raise AccountResetCreditsUnavailableError("Another reset attempt is already running")

        if attempt.state == "applied":
            payload = rate_limit_resets.ConsumeResetCreditPayload(
                code="already_redeemed", windows_reset=attempt.windows_reset,
            )
        else:
            # Inventory and usage must be reconciled before re-sending a
            # pending attempt. The same committed ID protects uncertain calls.
            inventory = await rate_limit_resets.fetch_reset_credits(
                access_token=access_token, chatgpt_account_id=credit_account.chatgpt_account_id,
            )
            available = [item for item in inventory.credits if item.status == "available"]
            reset_credit_cache.record_count(account_id, len(available))
            credit = next((item for item in inventory.credits if item.id == attempt.credit_id), None)
            if credit is not None and credit.status == "redeemed":
                payload = rate_limit_resets.ConsumeResetCreditPayload(code="already_redeemed")
            elif inventory.available_count <= 0 and attempt.credit_id is None:
                payload = rate_limit_resets.ConsumeResetCreditPayload(code="no_credit")
            elif credit is not None and credit.status not in {"available", "redeemed"}:
                raise AccountResetCreditsUnavailableError("Reset credit status is unresolved; outcome is unknown")
            elif attempt.credit_id is not None and credit is None:
                raise AccountResetCreditsUnavailableError("Reset credit is absent from inventory; outcome is unknown")
            else:
                if self._usage_updater and self._usage_repo:
                    refreshed = await self._usage_updater.force_refresh(credit_account)
                    if retrying and not refreshed:
                        raise AccountResetCreditsUnavailableError("Pending reset requires fresh usage reconciliation")
                if inventory.available_count <= 0:
                    raise AccountResetCreditsUnavailableError("Reset inventory has no available credits")
                payload = await rate_limit_resets.consume_reset_credit(
                    access_token=access_token,
                    chatgpt_account_id=credit_account.chatgpt_account_id,
                    redeem_request_id=attempt.id,
                    credit_id=attempt.credit_id,
                )

            if payload.code in ("reset", "already_redeemed"):
                # Never allow a later refresh failure to hide consumed credit.
                await attempts.applied(attempt, payload.code, payload.windows_reset)
            else:
                await attempts.settle(attempt, payload.code)

        if payload.code in ("reset", "already_redeemed"):
            try:
                refreshed_credits = await rate_limit_resets.fetch_reset_credits(
                    access_token=access_token,
                    chatgpt_account_id=credit_account.chatgpt_account_id,
                )
                reset_credit_cache.record_count(
                    account_id,
                    sum(credit.status == "available" for credit in refreshed_credits.credits),
                )
            except Exception:
                reset_credit_cache.clear(account_id)
                logger.warning(
                    "Post-redemption reset-credit listing failed account=%s",
                    account_id,
                    exc_info=True,
                )

            if self._usage_repo and self._usage_updater:
                recovered = await refresh_standard_capacity(credit_account, self._usage_updater, self._usage_repo)
                get_account_selection_cache().invalidate()
                if recovered is not True:
                    # Upstream usage can remain stale until one real request
                    # wakes its limiter. Applied attempts cannot consume again.
                    await self.probe_account(account_id)
                    recovered = await refresh_standard_capacity(credit_account, self._usage_updater, self._usage_repo)
                if recovered is True:
                    await attempts.settle(attempt, payload.code)

        primary_after, secondary_after = await self._latest_usage_percents(account_id)
        return AccountResetCreditConsumeResponse(
            status="redeemed" if payload.code in ("reset", "already_redeemed") else "not_redeemed",
            account_id=account_id,
            code=payload.code,
            windows_reset=payload.windows_reset,
            primary_used_percent_before=primary_before,
            primary_used_percent_after=primary_after,
            secondary_used_percent_before=secondary_before,
            secondary_used_percent_after=secondary_after,
        )

    async def _reset_credit_account(self, account: Account) -> Account:
        provider = normalize_provider_name(account.provider)
        if provider != OPENAI_PROVIDER_NAME:
            raise AccountResetCreditsUnavailableError(
                f"Provider {provider} does not support rate-limit reset credits"
            )
        if account.status in (AccountStatus.PAUSED, AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED):
            raise AccountResetCreditsUnavailableError(
                f"Account is {account.status.value} and cannot use reset credits"
            )
        if not is_subscription_usable(account):
            raise AccountResetCreditsUnavailableError("Account subscription cannot use reset credits")
        if self._auth_manager is not None:
            return await self._auth_manager.ensure_fresh(account, force=False)
        return account

    async def _latest_usage_percents(self, account_id: str) -> tuple[float | None, float | None]:
        if self._usage_repo is None:
            return None, None
        primary_entry = await self._usage_repo.latest_entry_for_account(account_id, window="primary")
        secondary_entry = await self._usage_repo.latest_entry_for_account(account_id, window="secondary")
        return (
            primary_entry.used_percent if primary_entry is not None else None,
            secondary_entry.used_percent if secondary_entry is not None else None,
        )

    async def _send_probe_request(
        self,
        *,
        access_token: str,
        chatgpt_account_id: str | None,
        model: str,
    ) -> int:
        return await probes.send_openai_probe(
            access_token=access_token,
            chatgpt_account_id=chatgpt_account_id,
            model=model,
        )

    async def _send_messages_probe_request(
        self,
        *,
        access_token: str,
        base_url: str,
        model: str,
    ) -> tuple[int, str | None]:
        return await probes.send_messages_probe(
            access_token=access_token,
            base_url=base_url,
            model=model,
        )

    async def _send_anthropic_subscription_check_request(
        self,
        *,
        access_token: str,
    ) -> tuple[int, str | None]:
        return await self._send_messages_probe_request(
            access_token=access_token,
            base_url=get_settings().anthropic_upstream_base_url,
            model=DEFAULT_ANTHROPIC_SUBSCRIPTION_CHECK_MODEL,
        )


def _opencode_auth_export_filename(account: Account) -> str:
    source = account.email or account.id
    safe = "".join(char if char.isalnum() or char in "._-" else "-" for char in source).strip("-._")
    return f"opencode-auth-{safe or account.id}.json"


def _subscription_check_notes(
    *,
    working: bool,
    probe_status: int,
    checked_at: datetime,
    message: str | None,
) -> str:
    date = checked_at.date().isoformat()
    if working:
        return f"Subscription check succeeded on {date}; account is locally active."
    suffix = f" {message}" if message else ""
    return (
        f"Subscription check returned HTTP {probe_status} on {date}; "
        f"keeping account canceled until reactivated.{suffix}"
    )
