from __future__ import annotations

from dataclasses import dataclass, field

from app.db.models import Account, AdditionalUsageHistory, UsageHistory
from app.modules.proxy._load_balancer.sticky_selection import SelectionInputsProtocol
from app.modules.quota_planner.logic import PlannerSettings


@dataclass(frozen=True, slots=True)
class SelectionInputs(SelectionInputsProtocol):
    accounts: list[Account]
    latest_primary: dict[str, UsageHistory | AdditionalUsageHistory]
    latest_secondary: dict[str, UsageHistory | AdditionalUsageHistory]
    latest_monthly: dict[str, UsageHistory]
    # Ownership ambiguity is resolved before transient additional-quota,
    # exclusion, runtime-health, budget, and account-cap filters. Keep that
    # stronger candidate pool alongside the effective routing pool.
    continuity_owner_candidates: list[Account] | None = None
    # Sticky-row mutation is authorized by account assignment and security
    # policy, before model/service-tier eligibility. Keep this separate from
    # continuity ambiguity: a model-ineligible account can still own the raw
    # row that this authenticated request is allowed to retire.
    sticky_mutation_authority_account_ids: frozenset[str] | None = None
    standard_latest_primary: dict[str, UsageHistory] = field(default_factory=dict)
    standard_latest_secondary: dict[str, UsageHistory] = field(default_factory=dict)
    quota_planner_settings: PlannerSettings = PlannerSettings()
    runtime_accounts: list[Account] | None = None
    error_message: str | None = None
    error_code: str | None = None
    ignore_standard_quota_account_ids: frozenset[str] = frozenset()
    ignore_standard_quota_status: bool = False
    persist_standard_quota_status: bool = True
    routing_policy_override: str | None = None
    quota_admitted_catalog_omission_account_ids: frozenset[str] = frozenset()

    @property
    def effective_continuity_owner_candidates(self) -> list[Account]:
        if self.continuity_owner_candidates is None:
            return self.accounts
        return self.continuity_owner_candidates

    @property
    def effective_sticky_mutation_authority_account_ids(self) -> frozenset[str]:
        if self.sticky_mutation_authority_account_ids is None:
            return frozenset(account.id for account in self.effective_continuity_owner_candidates)
        return self.sticky_mutation_authority_account_ids
