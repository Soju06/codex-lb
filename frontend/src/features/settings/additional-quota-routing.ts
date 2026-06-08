import type { AdditionalQuotaRoutingPolicy, DashboardSettings } from "@/features/settings/schemas";

type AdditionalQuotaRoutingPolicies = DashboardSettings["additionalQuotaRoutingPolicies"];

export type AdditionalQuotaRoutingPolicyState = {
  base: AdditionalQuotaRoutingPolicies;
  policies: AdditionalQuotaRoutingPolicies;
};

export function mergeAdditionalQuotaRoutingPolicy(
  policies: AdditionalQuotaRoutingPolicies,
  quotaKey: string,
  routingPolicy: AdditionalQuotaRoutingPolicy,
) {
  return {
    ...policies,
    [quotaKey]: routingPolicy,
  };
}

export function currentAdditionalQuotaRoutingPolicies(
  state: AdditionalQuotaRoutingPolicyState,
  settingsPolicies: AdditionalQuotaRoutingPolicies,
) {
  return state.base === settingsPolicies ? state.policies : settingsPolicies;
}
