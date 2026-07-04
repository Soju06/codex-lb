from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from typing import cast
from uuid import uuid4

import aiohttp
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from app.core.auth import (
    DEFAULT_EMAIL,
    DEFAULT_PLAN,
    claims_from_auth,
    generate_unique_account_id,
    parse_auth_json,
    token_expiry_epoch_ms,
)
from app.core.auth.api_key_cache import get_api_key_cache
from app.core.auth.refresh import RefreshError
from app.core.cache.invalidation import NAMESPACE_API_KEY, get_cache_invalidation_poller
from app.core.clients.http import lease_http_session
from app.core.clients.usage import (
    ConsumeRateLimitResetCreditResponse,
    UsageFetchError,
    consume_rate_limit_reset_credit,
    fetch_usage,
)
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.plan_types import coerce_account_plan_type
from app.core.upstream_proxy import ResolvedUpstreamRoute, UpstreamProxyRouteError, resolve_upstream_route
from app.core.upstream_proxy.resolver import _is_missing_upstream_proxy_schema
from app.core.usage.models import UsagePayload
from app.core.utils.time import naive_utc_to_epoch, to_utc_naive, utcnow
from app.db.models import Account, AccountStatus, DashboardSettings
from app.db.session import get_background_session
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.mappers import build_account_summaries, build_account_usage_trends
from app.modules.accounts.repository import AccountsRepository
from app.modules.accounts.schemas import (
    AccountAdditionalQuota,
    AccountAdditionalWindow,
    AccountAuthExportResponse,
    AccountAuthExportTokens,
    AccountExportResponse,
    AccountImportResponse,
    AccountOpenCodeAuthExportAccount,
    AccountOpenCodeAuthExportResponse,
    AccountProbeResponse,
    AccountRequestUsage,
    AccountSummary,
    AccountTrendsResponse,
    AccountUsageResetConsumeResponse,
    AccountUsageResetCredits,
    AccountUsageResetCreditsResponse,
    CodexAuthJson,
    CodexAuthTokens,
    OpenCodeAuthJson,
    OpenCodeOAuthAuth,
)
from app.modules.limit_warmup.repository import LimitWarmupRepository
from app.modules.proxy.account_cache import (
    clear_account_routing_unavailable,
    get_account_selection_cache,
    mark_account_routing_unavailable,
)
from app.modules.rate_limit_reset_credits.store import get_rate_limit_reset_credits_store
from app.modules.usage.additional_quota_keys import (
    get_additional_display_label_for_quota_key,
    get_additional_quota_routing_policy,
)
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository
from app.modules.usage.updater import AdditionalUsageRepositoryPort, UsageUpdater

logger = logging.getLogger(__name__)

_SPARKLINE_DAYS = 7
_DETAIL_BUCKET_SECONDS = 3600  # 1h → 168 points

DEFAULT_PROBE_MODEL = "gpt-5.5"
PROBE_REQUEST_TIMEOUT_SECONDS = 30.0
PROBE_CONNECT_TIMEOUT_SECONDS = 10.0
# Network/upstream failure sentinel for ``probe_status_code`` — kept as ``0`` so
# the value is distinguishable from any real HTTP status the upstream might
# return.
PROBE_NETWORK_FAILURE_STATUS = 0
IMPORT_PROXY_REQUIRED_PAUSE_REASON = "upstream_proxy_required_on_import"


class InvalidAuthJsonError(Exception):
    pass


class AccountNotProbableError(Exception):
    """Raised when an account is in a status that disallows probing."""


class AccountStateTransitionError(Exception):
    """Raised when an operator action is not valid for the account state."""


class AccountUsageResetCreditsUnavailableError(Exception):
    """Raised when a dashboard account cannot read upstream reset credits."""


class AccountUsageResetConsumeUnavailableError(Exception):
    """Raised when a dashboard account cannot consume upstream reset credits."""


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

    async def list_accounts(self) -> list[AccountSummary]:
        accounts = await self._repo.list_accounts()
        if not accounts:
            return []
        account_ids = [account.id for account in accounts]
        account_id_set = set(account_ids)
        primary_usage = await self._usage_repo.latest_by_account(window="primary") if self._usage_repo else {}
        secondary_usage = await self._usage_repo.latest_by_account(window="secondary") if self._usage_repo else {}
        monthly_usage = await self._usage_repo.latest_by_account(window="monthly") if self._usage_repo else {}
        request_usage_rows = await self._repo.list_request_usage_summary_by_account(account_ids)
        limit_warmups_by_account = (
            await self._limit_warmup_repo.latest_by_account(account_ids) if self._limit_warmup_repo else {}
        )
        request_usage_by_account = {
            account_id: AccountRequestUsage(
                request_count=row.request_count,
                total_tokens=row.total_tokens,
                cached_input_tokens=row.cached_input_tokens,
                total_cost_usd=row.total_cost_usd,
            )
            for account_id, row in request_usage_rows.items()
        }
        additional_quotas_by_account: dict[str, list[AccountAdditionalQuota]] = {}
        additional_usage_repo = cast(AdditionalUsageRepository | None, self._additional_usage_repo)
        if additional_usage_repo:
            additional_quota_routing_overrides = await self._repo.additional_quota_routing_policy_overrides()
            quota_keys = await additional_usage_repo.list_quota_keys(account_ids=account_ids)
            for quota_key in quota_keys:
                primary_entries = await additional_usage_repo.latest_by_account(quota_key, "primary")
                secondary_entries = await additional_usage_repo.latest_by_account(quota_key, "secondary")
                for account_id in (set(primary_entries) | set(secondary_entries)) & account_id_set:
                    primary_entry = primary_entries.get(account_id)
                    secondary_entry = secondary_entries.get(account_id)
                    reference_entry = primary_entry or secondary_entry
                    if reference_entry is None:
                        continue
                    additional_quotas_by_account.setdefault(account_id, []).append(
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
                            primary_window=AccountAdditionalWindow(
                                used_percent=primary_entry.used_percent,
                                reset_at=primary_entry.reset_at,
                                window_minutes=primary_entry.window_minutes,
                            )
                            if primary_entry is not None
                            else None,
                            secondary_window=AccountAdditionalWindow(
                                used_percent=secondary_entry.used_percent,
                                reset_at=secondary_entry.reset_at,
                                window_minutes=secondary_entry.window_minutes,
                            )
                            if secondary_entry is not None
                            else None,
                        )
                    )
        for account_quota_list in additional_quotas_by_account.values():
            account_quota_list.sort(key=lambda quota: quota.display_label or quota.quota_key or quota.limit_name)

        return build_account_summaries(
            accounts=accounts,
            primary_usage=primary_usage,
            secondary_usage=secondary_usage,
            monthly_usage=monthly_usage,
            request_usage_by_account=request_usage_by_account,
            additional_quotas_by_account=additional_quotas_by_account,
            limit_warmups_by_account=limit_warmups_by_account,
            encryptor=self._encryptor,
        )

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

    async def get_usage_reset_credits(self, account_id: str) -> AccountUsageResetCreditsResponse | None:
        account = await self._repo.get_by_id(account_id)
        if account is None:
            return None
        if account.status in (AccountStatus.PAUSED, AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED):
            raise AccountUsageResetCreditsUnavailableError(
                f"Account is {account.status.value} and cannot fetch usage reset credits",
            )
        if self._auth_manager is not None:
            try:
                account = await self._auth_manager.ensure_fresh(account)
            except RefreshError as exc:
                raise AccountUsageResetCreditsUnavailableError(
                    f"Account credentials could not be refreshed: {exc.message}",
                ) from exc
        if not account.chatgpt_account_id:
            raise AccountUsageResetCreditsUnavailableError("Account is missing ChatGPT account identity")

        payload = await self._fetch_usage_payload_for_reset_credits(account)
        reset_credits = payload.rate_limit_reset_credits
        available_count = reset_credits.available_count if reset_credits is not None else None
        return AccountUsageResetCreditsResponse(
            account_id=account.id,
            rate_limit_reset_credits=AccountUsageResetCredits(
                available_count=max(0, int(available_count or 0)),
            ),
        )

    async def _fetch_usage_payload_for_reset_credits(self, account: Account) -> UsagePayload:
        access_token = self._encryptor.decrypt(account.access_token_encrypted)
        route = await self._resolve_usage_reset_credit_route(account, operation="usage_reset_credits_read")
        try:
            return await fetch_usage(
                access_token=access_token,
                account_id=account.chatgpt_account_id,
                route=route,
                allow_direct_egress=route is None,
            )
        except UsageFetchError as exc:
            if exc.status_code != 401 or self._auth_manager is None:
                raise
            try:
                account = await self._auth_manager.ensure_fresh(account, force=True)
            except RefreshError as refresh_exc:
                raise AccountUsageResetCreditsUnavailableError(
                    f"Account credentials could not be refreshed: {refresh_exc.message}",
                ) from refresh_exc
            if not account.chatgpt_account_id:
                raise AccountUsageResetCreditsUnavailableError("Account is missing ChatGPT account identity") from exc
            access_token = self._encryptor.decrypt(account.access_token_encrypted)
            retry_route = await self._resolve_usage_reset_credit_route(account, operation="usage_reset_credits_read")
            return await fetch_usage(
                access_token=access_token,
                account_id=account.chatgpt_account_id,
                route=retry_route,
                allow_direct_egress=retry_route is None,
            )

    async def consume_usage_reset_credit(
        self,
        account_id: str,
        *,
        redeem_request_id: str | None = None,
    ) -> AccountUsageResetConsumeResponse | None:
        account = await self._repo.get_by_id(account_id)
        if account is None:
            return None
        if account.status in (AccountStatus.PAUSED, AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED):
            raise AccountUsageResetConsumeUnavailableError(
                f"Account is {account.status.value} and cannot consume usage reset credits",
            )
        if self._auth_manager is not None:
            try:
                account = await self._auth_manager.ensure_fresh(account)
            except RefreshError as exc:
                raise AccountUsageResetConsumeUnavailableError(
                    f"Account credentials could not be refreshed: {exc.message}",
                ) from exc
        if not account.chatgpt_account_id:
            raise AccountUsageResetConsumeUnavailableError("Account is missing ChatGPT account identity")

        primary_before, secondary_before = await self._latest_usage_percents(account_id)
        status_before = account.status.value
        upstream_response, account = await self._consume_usage_reset_credit(
            account, redeem_request_id=redeem_request_id
        )
        if upstream_response.code in ("reset", "already_redeemed", "no_credit", "nothing_to_reset"):
            await get_rate_limit_reset_credits_store().invalidate(account_id)

        usage_written = False
        if upstream_response.code in ("reset", "already_redeemed") and self._usage_repo and self._usage_updater:
            usage_written = await self._usage_updater.force_refresh(account, ignore_refresh_disabled=True)
            get_account_selection_cache().invalidate()

        refreshed = await self._repo.get_by_id(account_id) or account
        primary_after, secondary_after = await self._latest_usage_percents(account_id)

        return AccountUsageResetConsumeResponse(
            status="reset",
            account_id=account_id,
            code=upstream_response.code,
            windows_reset=upstream_response.windows_reset,
            usage_written=usage_written,
            primary_used_percent_before=primary_before,
            primary_used_percent_after=primary_after,
            secondary_used_percent_before=secondary_before,
            secondary_used_percent_after=secondary_after,
            account_status_before=status_before,
            account_status_after=refreshed.status.value,
        )

    async def _consume_usage_reset_credit(
        self,
        account: Account,
        *,
        redeem_request_id: str | None = None,
    ) -> tuple[ConsumeRateLimitResetCreditResponse, Account]:
        chatgpt_account_id = account.chatgpt_account_id
        if not chatgpt_account_id:
            raise AccountUsageResetConsumeUnavailableError("Account is missing ChatGPT account identity")
        effective_redeem_request_id = redeem_request_id or str(uuid4())
        access_token = self._encryptor.decrypt(account.access_token_encrypted)
        route = await self._resolve_usage_reset_credit_route(account, operation="usage_reset_credits_consume")
        try:
            response = await consume_rate_limit_reset_credit(
                access_token=access_token,
                account_id=chatgpt_account_id,
                redeem_request_id=effective_redeem_request_id,
                route=route,
                allow_direct_egress=route is None,
            )
            return response, account
        except UsageFetchError as exc:
            if exc.status_code != 401 or self._auth_manager is None:
                raise
            try:
                account = await self._auth_manager.ensure_fresh(account, force=True)
            except RefreshError as refresh_exc:
                raise AccountUsageResetConsumeUnavailableError(
                    f"Account credentials could not be refreshed: {refresh_exc.message}",
                ) from refresh_exc
            if not account.chatgpt_account_id:
                raise AccountUsageResetConsumeUnavailableError("Account is missing ChatGPT account identity") from exc
            access_token = self._encryptor.decrypt(account.access_token_encrypted)
            retry_route = await self._resolve_usage_reset_credit_route(account, operation="usage_reset_credits_consume")
            response = await consume_rate_limit_reset_credit(
                access_token=access_token,
                account_id=account.chatgpt_account_id,
                redeem_request_id=effective_redeem_request_id,
                route=retry_route,
                allow_direct_egress=retry_route is None,
            )
            return response, account

    async def _resolve_usage_reset_credit_route(
        self,
        account: Account,
        *,
        operation: str,
    ) -> ResolvedUpstreamRoute | None:
        async with get_background_session() as session:
            return await resolve_upstream_route(
                session,
                account_id=account.id,
                operation=operation,
                scope="account",
                encryptor=self._encryptor,
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
                email=account.email,  # ty:ignore[invalid-argument-type]
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
        id_token = self._encryptor.decrypt(account.id_token_encrypted)
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
                email=account.email,  # ty:ignore[invalid-argument-type]
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
        claims = claims_from_auth(auth)

        email = claims.email or DEFAULT_EMAIL
        raw_account_id = claims.account_id
        account_id = generate_unique_account_id(raw_account_id, email, claims.workspace_id, claims.workspace_label)
        plan_type = coerce_account_plan_type(claims.plan_type, DEFAULT_PLAN)
        last_refresh = to_utc_naive(auth.last_refresh_at) if auth.last_refresh_at else utcnow()

        account = Account(
            id=account_id,
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
        import_usage_refresh_allowed = await self._import_usage_refresh_allowed(saved)
        if not import_usage_refresh_allowed:
            await self._repo.update_status(
                saved.id,
                AccountStatus.PAUSED,
                IMPORT_PROXY_REQUIRED_PAUSE_REASON,
                None,
                blocked_at=None,
            )
            mark_account_routing_unavailable(saved.id)
            saved = await self._repo.get_by_id(saved.id) or saved
        if import_usage_refresh_allowed and self._usage_repo and self._usage_updater:
            latest_usage = await self._usage_repo.latest_by_account(window="primary")
            await self._usage_updater.refresh_accounts([saved], latest_usage)
        if saved.status == AccountStatus.ACTIVE:
            clear_account_routing_unavailable(saved.id)
        get_account_selection_cache().invalidate()
        return AccountImportResponse(
            account_id=saved.id,
            email=saved.email,  # ty:ignore[invalid-argument-type]
            workspace_id=saved.workspace_id,
            workspace_label=saved.workspace_label,
            seat_type=saved.seat_type,
            plan_type=saved.plan_type,
            status=saved.status,
        )

    async def _import_usage_refresh_allowed(self, account: Account) -> bool:
        try:
            route = await resolve_upstream_route(
                self._repo.session,
                account_id=account.id,
                operation="usage_refresh",
                scope="account",
                encryptor=self._encryptor,
            )
        except UpstreamProxyRouteError as exc:
            logger.info(
                "Pausing imported account until upstream proxy binding is available account_id=%s reason=%s",
                account.id,
                exc.reason,
            )
            return False
        if route is not None:
            return True

        try:
            settings = await self._repo.session.get(DashboardSettings, 1)
        except OperationalError as exc:
            if not _is_missing_upstream_proxy_schema(exc):
                raise
            return True
        if settings is not None and settings.upstream_proxy_routing_enabled:
            logger.info(
                "Pausing imported account until upstream proxy default pool is configured account_id=%s",
                account.id,
            )
            return False
        return True

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
            clear_account_routing_unavailable(account_id)
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
            mark_account_routing_unavailable(account_id)
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

    async def delete_account(self, account_id: str, *, delete_history: bool = False) -> bool:
        result = await self._repo.delete(account_id, delete_history=delete_history)
        if result:
            mark_account_routing_unavailable(account_id)
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
        id_token = self._encryptor.decrypt(account.id_token_encrypted)
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
            email=account.email,  # ty:ignore[invalid-argument-type]
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
        probe_status = await self._send_probe_request(
            access_token=access_token,
            chatgpt_account_id=probe_account.chatgpt_account_id,
            model=probe_model,
        )

        if self._usage_repo and self._usage_updater:
            await self._usage_updater.force_refresh(probe_account, ignore_refresh_disabled=True)
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
        settings = get_settings()
        base = settings.upstream_base_url.rstrip("/")
        if "/backend-api" not in base:
            base = f"{base}/backend-api"
        url = f"{base}/codex/responses"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }
        if chatgpt_account_id and not chatgpt_account_id.startswith(("email_", "local_")):
            headers["chatgpt-account-id"] = chatgpt_account_id
        body = {
            "model": model,
            "instructions": "Respond with a single dot.",
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "."}],
                }
            ],
            "max_output_tokens": 1,
            "stream": True,
            "store": False,
        }
        timeout = aiohttp.ClientTimeout(
            total=PROBE_REQUEST_TIMEOUT_SECONDS,
            sock_connect=PROBE_CONNECT_TIMEOUT_SECONDS,
        )
        try:
            async with lease_http_session() as session:
                async with session.post(url, headers=headers, json=body, timeout=timeout) as resp:
                    # Initiating the request is enough to wake the upstream
                    # rate-limiter; we do not consume the SSE body.
                    return resp.status
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning(
                "Probe upstream request failed account=%s error=%s",
                chatgpt_account_id,
                exc,
            )
            return PROBE_NETWORK_FAILURE_STATUS


def _opencode_auth_export_filename(account: Account) -> str:
    source = account.email or account.id
    safe = "".join(char if char.isalnum() or char in "._-" else "-" for char in source).strip("-._")
    return f"opencode-auth-{safe or account.id}.json"
