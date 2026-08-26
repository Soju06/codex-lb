from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

from app.core.openai.model_registry import ModelRegistry, canonical_service_tier_value
from app.core.plan_types import account_plan_matches_allowed, normalize_account_plan_type
from app.db.models import Account, AdditionalUsageHistory
from app.modules.proxy.additional_model_limits import get_additional_quota_key_for_model_id
from app.modules.usage.additional_quota_keys import (
    canonicalize_additional_quota_key,
    get_additional_quota_definition,
)

_ADDITIONAL_QUOTA_EXEMPT_PLAN_TYPES = frozenset({"free", "plus", "edu"})


@dataclass(frozen=True, slots=True)
class CatalogOmissionQuotaAdmission:
    normalized_model: str
    canonical_quota_key: str
    normalized_effective_service_tier: str | None

    def matches(self, *, requested_model: str, service_tier: str | None) -> bool:
        return (
            self.normalized_model == _normalize_model_id(requested_model)
            and self.canonical_quota_key == _gated_limit_name_for_model(requested_model)
            and self.normalized_effective_service_tier == _effective_model_service_tier(service_tier)
        )


@dataclass(frozen=True, slots=True)
class _ModelAccountFilterResult:
    accounts: list[Account]
    general_model_account_ids: frozenset[str] | None
    # Tier actually applied to the filter, after dropping tiers the model does
    # not advertise. Set only when the tier narrowed the pool, so an empty
    # result can say the tier excluded the accounts rather than the model.
    applied_service_tier: str | None = None


def _filter_accounts_for_model_with_catalog_evidence(
    accounts: list[Account],
    model: str,
    *,
    registry: ModelRegistry,
    service_tier: str | None = None,
    additional_quota_can_override_account_catalog: bool = False,
) -> _ModelAccountFilterResult:
    account_indexes_cover_selection = True
    get_snapshot = getattr(registry, "get_snapshot", None)
    if callable(get_snapshot):
        snapshot = get_snapshot()
        account_indexes_cover_selection = snapshot is not None and all(
            account.id in snapshot.account_plans for account in accounts
        )
    account_ids_for_model = getattr(registry, "account_ids_for_model", None)
    general_model_account_ids = (
        account_ids_for_model(model) if callable(account_ids_for_model) and account_indexes_cover_selection else None
    )
    if general_model_account_ids is None or additional_quota_can_override_account_catalog:
        model_accounts = accounts
    else:
        model_accounts = [account for account in accounts if account.id in general_model_account_ids]

    normalized_service_tier = service_tier.strip().lower() if service_tier is not None else None
    effective_service_tier = None if normalized_service_tier in {"auto", "default"} else service_tier
    if effective_service_tier is not None:
        allowed_account_ids = (
            registry.account_ids_for_model_service_tier(model, effective_service_tier)
            if account_indexes_cover_selection
            else None
        )
        if allowed_account_ids is not None:
            if additional_quota_can_override_account_catalog and general_model_account_ids is not None:
                allowed_plans = registry.plan_types_for_model_service_tier(model, effective_service_tier)
                tier_filtered_accounts: list[Account] = []
                for account in accounts:
                    if account.id in general_model_account_ids:
                        if account.id in allowed_account_ids:
                            tier_filtered_accounts.append(account)
                    elif allowed_plans is None or account_plan_matches_allowed(account.plan_type, allowed_plans):
                        tier_filtered_accounts.append(account)
                model_accounts = tier_filtered_accounts
            else:
                model_accounts = [account for account in model_accounts if account.id in allowed_account_ids]
            return _ModelAccountFilterResult(
                accounts=model_accounts,
                general_model_account_ids=general_model_account_ids,
                applied_service_tier=effective_service_tier,
            )
        allowed_plans = registry.plan_types_for_model_service_tier(model, effective_service_tier)
    else:
        allowed_plans = registry.plan_types_for_model(model)
    if allowed_plans is not None:
        model_accounts = [
            account for account in model_accounts if account_plan_matches_allowed(account.plan_type, allowed_plans)
        ]
    return _ModelAccountFilterResult(
        accounts=model_accounts,
        general_model_account_ids=general_model_account_ids,
        applied_service_tier=effective_service_tier,
    )


def _filter_accounts_for_model(
    accounts: list[Account],
    model: str,
    *,
    registry: ModelRegistry,
    service_tier: str | None = None,
) -> list[Account]:
    return _filter_accounts_for_model_with_catalog_evidence(
        accounts,
        model,
        registry=registry,
        service_tier=service_tier,
    ).accounts


def _gated_limit_name_for_model(model: str | None) -> str | None:
    return get_additional_quota_key_for_model_id(model)


def _normalize_model_id(model: str) -> str:
    return model.strip().lower()


def _effective_model_service_tier(service_tier: str | None) -> str | None:
    if service_tier is None:
        return None
    normalized_service_tier = canonical_service_tier_value(service_tier)
    return None if normalized_service_tier in {"", "auto", "default"} else normalized_service_tier


def _catalog_omission_quota_admission(
    *,
    account_id: str,
    model: str | None,
    service_tier: str | None,
    additional_limit_name: str | None,
    quota_admitted_catalog_omission_account_ids: frozenset[str],
) -> CatalogOmissionQuotaAdmission | None:
    if (
        model is None
        or additional_limit_name is not None
        or account_id not in quota_admitted_catalog_omission_account_ids
    ):
        return None
    quota_key = _gated_limit_name_for_model(model)
    if quota_key is None:
        return None
    return CatalogOmissionQuotaAdmission(
        normalized_model=_normalize_model_id(model),
        canonical_quota_key=quota_key,
        normalized_effective_service_tier=_effective_model_service_tier(service_tier),
    )


def _mapped_model_has_registry_entry(model: str | None, *, registry: ModelRegistry) -> bool:
    if model is None:
        return False
    plan_types_for_model = getattr(registry, "plan_types_for_model", None)
    if not callable(plan_types_for_model):
        return False
    if plan_types_for_model(model):
        return True
    is_suppressed_model = getattr(registry, "is_suppressed_model", None)
    return callable(is_suppressed_model) and is_suppressed_model(model)


async def _latest_additional_by_key(
    additional_usage_repo,
    quota_key: str,
    window: str,
    *,
    account_ids: list[str] | None = None,
    since: datetime | None = None,
) -> dict[str, AdditionalUsageHistory]:
    resolved_quota_key = canonicalize_additional_quota_key(
        quota_key=quota_key,
        limit_name=quota_key,
    )
    if resolved_quota_key is None:
        return {}
    return await additional_usage_repo.latest_by_quota_key(
        resolved_quota_key,
        window,
        account_ids=account_ids,
        since=since,
    )


def _additional_quota_eligibility(
    *,
    account_id: str,
    account_plan_type: str | None,
    quota_key: str | None,
    explicit_limit: bool = False,
    require_fresh_evidence: bool = False,
    latest_primary: dict[str, AdditionalUsageHistory],
    latest_secondary: dict[str, AdditionalUsageHistory],
    fresh_primary: dict[str, AdditionalUsageHistory],
    fresh_secondary: dict[str, AdditionalUsageHistory],
) -> str:
    latest_primary_entry = latest_primary.get(account_id)
    latest_secondary_entry = latest_secondary.get(account_id)
    primary_entry = fresh_primary.get(account_id)
    secondary_entry = fresh_secondary.get(account_id)

    if (
        not require_fresh_evidence
        and not explicit_limit
        and not _additional_quota_applies_to_plan(quota_key=quota_key, plan_type=account_plan_type)
    ):
        return "eligible"

    if latest_primary_entry is None and latest_secondary_entry is None:
        return "data_unavailable"
    if latest_primary_entry is not None and primary_entry is None:
        return "data_unavailable"
    if latest_secondary_entry is not None and secondary_entry is None:
        return "data_unavailable"

    if primary_entry is not None and _additional_usage_is_exhausted(primary_entry):
        return "quota_exhausted"
    if secondary_entry is not None and _additional_usage_is_exhausted(secondary_entry):
        return "quota_exhausted"
    return "eligible"


def _additional_quota_applies_to_plan(*, quota_key: str | None, plan_type: str | None) -> bool:
    definition = get_additional_quota_definition(quota_key)
    if definition is None or definition.applies_to_plans is None:
        return True
    normalized_plan = normalize_account_plan_type(plan_type)
    if normalized_plan is None:
        return True
    if normalized_plan in definition.applies_to_plans:
        return True
    return normalized_plan not in _ADDITIONAL_QUOTA_EXEMPT_PLAN_TYPES


def _additional_usage_is_exhausted(entry: AdditionalUsageHistory) -> bool:
    if entry.used_percent is None:
        return False
    if entry.reset_at is not None and int(entry.reset_at) <= int(time.time()):
        return False
    return float(entry.used_percent) >= 100.0
