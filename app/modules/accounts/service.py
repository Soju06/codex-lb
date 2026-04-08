from __future__ import annotations

import json
from datetime import timedelta
from typing import cast

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.core.auth import (
    DEFAULT_EMAIL,
    DEFAULT_PLAN,
    claims_from_auth,
    generate_unique_account_id,
    parse_auth_json,
)
from app.core.auth.api_key_cache import get_api_key_cache
from app.core.cache.invalidation import NAMESPACE_API_KEY, get_cache_invalidation_poller
from app.core.clients.openai_platform import OpenAIPlatformError
from app.core.crypto import TokenEncryptor
from app.core.plan_types import coerce_account_plan_type
from app.core.utils.time import naive_utc_to_epoch, to_utc_naive, utcnow
from app.db.models import Account, AccountStatus
from app.modules.accounts.mappers import build_account_summaries, build_account_usage_trends
from app.modules.accounts.repository import AccountRequestUsageSummary, AccountsRepository
from app.modules.accounts.schemas import (
    AccountAdditionalQuota,
    AccountAdditionalWindow,
    AccountImportResponse,
    AccountRequestUsage,
    AccountSummary,
    AccountTrendsResponse,
    PlatformIdentityCreateRequest,
    PlatformIdentityUpdateRequest,
)
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.proxy.provider_adapters import OpenAIPlatformProviderAdapter
from app.modules.upstream_identities.repository import (
    OpenAIPlatformIdentityConflictError as UpstreamIdentityConflictError,
)
from app.modules.upstream_identities.schemas import (
    PlatformIdentityCreateRequest as UpstreamPlatformIdentityCreateRequest,
)
from app.modules.upstream_identities.schemas import (
    PlatformIdentitySummary,
    PlatformIdentityValidationResult,
)
from app.modules.upstream_identities.schemas import (
    PlatformIdentityUpdateRequest as UpstreamPlatformIdentityUpdateRequest,
)
from app.modules.upstream_identities.service import OpenAIPlatformIdentitiesService
from app.modules.usage.additional_quota_keys import get_additional_display_label_for_quota_key
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository
from app.modules.usage.updater import AdditionalUsageRepositoryPort, UsageUpdater

_SPARKLINE_DAYS = 7
_DETAIL_BUCKET_SECONDS = 3600  # 1h → 168 points


class InvalidAuthJsonError(Exception):
    pass


class PlatformIdentityPrerequisiteError(ValueError):
    pass


class PlatformIdentityConflictError(Exception):
    pass


class PlatformIdentityNotFoundError(Exception):
    pass


class AccountsService:
    def __init__(
        self,
        repo: AccountsRepository,
        usage_repo: UsageRepository | None = None,
        additional_usage_repo: AdditionalUsageRepository | AdditionalUsageRepositoryPort | None = None,
        *,
        platform_service: OpenAIPlatformIdentitiesService | None = None,
    ) -> None:
        self._repo = repo
        self._usage_repo = usage_repo
        self._additional_usage_repo = additional_usage_repo
        self._platform_service = platform_service
        self._usage_updater = UsageUpdater(usage_repo, repo, additional_usage_repo) if usage_repo else None
        self._encryptor = TokenEncryptor()
        self._platform_adapter = OpenAIPlatformProviderAdapter()

    async def list_accounts(self) -> list[AccountSummary]:
        accounts = await self._repo.list_accounts()
        platform_summaries = (
            await self._platform_service.list_identities() if self._platform_service is not None else []
        )
        account_ids = [account.id for account in accounts]
        routing_subject_ids = list(account_ids)
        routing_subject_ids.extend(summary.routing_subject_id for summary in platform_summaries)
        if not accounts:
            if not routing_subject_ids:
                return []
            request_usage_rows = await self._repo.list_request_usage_summary_by_subject(routing_subject_ids)
            return [
                _platform_summary_to_account_summary(
                    summary,
                    request_usage_rows.get(summary.routing_subject_id),
                )
                for summary in platform_summaries
            ]
        account_id_set = set(account_ids)
        primary_usage = await self._usage_repo.latest_by_account(window="primary") if self._usage_repo else {}
        secondary_usage = await self._usage_repo.latest_by_account(window="secondary") if self._usage_repo else {}
        request_usage_rows = await self._repo.list_request_usage_summary_by_subject(routing_subject_ids)
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

        summaries = build_account_summaries(
            accounts=accounts,
            primary_usage=primary_usage,
            secondary_usage=secondary_usage,
            request_usage_by_account=request_usage_by_account,
            additional_quotas_by_account=additional_quotas_by_account,
            encryptor=self._encryptor,
        )
        if self._platform_service is None:
            return summaries
        platform_accounts = [
            _platform_summary_to_account_summary(
                summary,
                request_usage_rows.get(summary.routing_subject_id),
            )
            for summary in platform_summaries
        ]
        return summaries + platform_accounts

    async def get_account_trends(self, account_id: str) -> AccountTrendsResponse | None:
        account = await self._repo.get_by_id(account_id)
        if account is None:
            if self._platform_service is not None and await self._platform_service.get_identity(account_id) is not None:
                return AccountTrendsResponse(account_id=account_id, primary=[], secondary=[])
            return None
        if not self._usage_repo:
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
        )

    async def import_account(self, raw: bytes) -> AccountImportResponse:
        try:
            auth = parse_auth_json(raw)
        except (json.JSONDecodeError, ValidationError, UnicodeDecodeError, TypeError) as exc:
            raise InvalidAuthJsonError("Invalid auth.json payload") from exc
        claims = claims_from_auth(auth)

        email = claims.email or DEFAULT_EMAIL
        raw_account_id = claims.account_id
        account_id = generate_unique_account_id(raw_account_id, email)
        plan_type = coerce_account_plan_type(claims.plan_type, DEFAULT_PLAN)
        last_refresh = to_utc_naive(auth.last_refresh_at) if auth.last_refresh_at else utcnow()

        account = Account(
            id=account_id,
            chatgpt_account_id=raw_account_id,
            email=email,
            plan_type=plan_type,
            access_token_encrypted=self._encryptor.encrypt(auth.tokens.access_token),
            refresh_token_encrypted=self._encryptor.encrypt(auth.tokens.refresh_token),
            id_token_encrypted=self._encryptor.encrypt(auth.tokens.id_token),
            last_refresh=last_refresh,
            status=AccountStatus.ACTIVE,
            deactivation_reason=None,
        )

        saved = await self._repo.upsert(account)
        if self._usage_repo and self._usage_updater:
            latest_usage = await self._usage_repo.latest_by_account(window="primary")
            await self._usage_updater.refresh_accounts([saved], latest_usage)
        get_account_selection_cache().invalidate()
        return AccountImportResponse(
            account_id=saved.id,
            email=saved.email,
            plan_type=saved.plan_type,
            status=saved.status,
        )

    async def create_platform_identity(self, payload: PlatformIdentityCreateRequest) -> AccountImportResponse:
        if self._platform_service is None:
            raise RuntimeError("Platform identities are not configured")
        if not await self._has_active_chatgpt_accounts():
            raise PlatformIdentityPrerequisiteError(
                "OpenAI Platform fallback requires at least one active ChatGPT-web account."
            )
        if await self._has_platform_identity():
            raise PlatformIdentityConflictError("Only one OpenAI Platform fallback key can be registered.")
        validation = await self._validate_platform_identity(payload)
        upstream_payload = UpstreamPlatformIdentityCreateRequest.model_validate(payload.model_dump())
        try:
            result = await self._platform_service.create_identity(upstream_payload, validation=validation)
        except (IntegrityError, UpstreamIdentityConflictError) as exc:
            raise PlatformIdentityConflictError(str(exc)) from exc
        get_account_selection_cache().invalidate()
        return AccountImportResponse(
            account_id=result.identity.id,
            email=result.identity.label,
            plan_type="openai_platform",
            status=result.identity.status.value,
        )

    async def update_platform_identity(
        self,
        account_id: str,
        payload: PlatformIdentityUpdateRequest,
    ) -> AccountSummary:
        if self._platform_service is None:
            raise RuntimeError("Platform identities are not configured")
        identity = await self._platform_service.get_identity(account_id)
        if identity is None:
            raise PlatformIdentityNotFoundError(f"Platform identity not found: {account_id}")

        upstream_payload = UpstreamPlatformIdentityUpdateRequest.model_validate(payload.model_dump(exclude_unset=True))
        validation = None
        if _should_revalidate_platform_identity(upstream_payload):
            api_key = (
                upstream_payload.api_key
                if upstream_payload.api_key is not None
                else self._platform_service.decrypt_api_key(identity)
            )
            organization = (
                upstream_payload.organization
                if "organization" in upstream_payload.model_fields_set
                else identity.organization_id
            )
            project = (
                upstream_payload.project if "project" in upstream_payload.model_fields_set else identity.project_id
            )
            validation = await self._validate_platform_identity_fields(
                api_key=api_key,
                organization=organization,
                project=project,
            )

        result = await self._platform_service.update_identity(
            identity,
            upstream_payload,
            validation=validation,
        )
        get_account_selection_cache().invalidate()
        request_usage_rows = await self._repo.list_request_usage_summary_by_subject([result.identity.id])
        request_usage = request_usage_rows.get(result.identity.id)
        return _platform_summary_to_account_summary(
            self._platform_service.summarize_identity(result.identity),
            request_usage,
        )

    async def reactivate_account(self, account_id: str) -> bool:
        result = await self._repo.update_status(account_id, AccountStatus.ACTIVE, None)
        if not result and self._platform_service is not None:
            result = await self._platform_service.reactivate_identity(account_id)
        if result:
            get_account_selection_cache().invalidate()
        return result

    async def pause_account(self, account_id: str) -> bool:
        result = await self._repo.update_status(account_id, AccountStatus.PAUSED, None)
        if not result and self._platform_service is not None:
            result = await self._platform_service.pause_identity(account_id)
        if result:
            get_account_selection_cache().invalidate()
        return result

    async def delete_account(self, account_id: str) -> bool:
        result = await self._repo.delete(account_id)
        if not result and self._platform_service is not None:
            result = await self._platform_service.delete_identity(account_id)
        if result:
            get_account_selection_cache().invalidate()
            get_api_key_cache().clear()
            poller = get_cache_invalidation_poller()
            if poller is not None:
                await poller.bump(NAMESPACE_API_KEY)
        return result

    async def _validate_platform_identity(
        self,
        payload: PlatformIdentityCreateRequest,
    ) -> PlatformIdentityValidationResult:
        return await self._validate_platform_identity_fields(
            api_key=payload.api_key,
            organization=payload.organization,
            project=payload.project,
        )

    async def _validate_platform_identity_fields(
        self,
        *,
        api_key: str,
        organization: str | None,
        project: str | None,
    ) -> PlatformIdentityValidationResult:
        try:
            response = await self._platform_adapter.validate_identity(
                api_key=api_key,
                organization=organization,
                project=project,
            )
        except OpenAIPlatformError as exc:
            reason = _platform_validation_reason(exc)
            return PlatformIdentityValidationResult(
                valid=False,
                last_validated_at=None,
                failure_reason=reason,
                models=[],
            )
        data = response.payload.get("data")
        models = (
            [entry.get("id") for entry in data if isinstance(entry, dict) and isinstance(entry.get("id"), str)]
            if isinstance(data, list)
            else []
        )
        return PlatformIdentityValidationResult(
            valid=True,
            last_validated_at=utcnow(),
            failure_reason=None,
            models=models,
        )

    async def _has_active_chatgpt_accounts(self) -> bool:
        accounts = await self._repo.list_accounts()
        return any(account.status not in (AccountStatus.DEACTIVATED, AccountStatus.PAUSED) for account in accounts)

    async def _has_platform_identity(self) -> bool:
        if self._platform_service is None:
            return False
        return bool(await self._platform_service.list_identities())


def _platform_validation_reason(exc: OpenAIPlatformError) -> str:
    error = exc.payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return f"platform_validation_failed:{exc.status_code}"


def _should_revalidate_platform_identity(payload: UpstreamPlatformIdentityUpdateRequest) -> bool:
    fields_set = payload.model_fields_set
    return payload.api_key is not None or "organization" in fields_set or "project" in fields_set


def _platform_summary_to_account_summary(
    summary: PlatformIdentitySummary,
    request_usage: AccountRequestUsageSummary | None,
) -> AccountSummary:
    payload = summary.model_dump()
    if request_usage is not None:
        payload["request_usage"] = AccountRequestUsage(
            request_count=request_usage.request_count,
            total_tokens=request_usage.total_tokens,
            cached_input_tokens=request_usage.cached_input_tokens,
            total_cost_usd=request_usage.total_cost_usd,
        )
    else:
        payload["request_usage"] = None
    return AccountSummary.model_validate(payload)
