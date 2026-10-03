import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { DonutChart } from "@/components/donut-chart";
import type { AccountSummary } from "@/features/accounts/schemas";
import type { DashboardAccountStatus } from "@/utils/account-status";

const STATUS_GROUPS: Record<string, { label: DashboardAccountStatus; color: string }> = {
  active: { label: "active", color: "#10b981" },
  paused: { label: "paused", color: "#f59e0b" },
  rate_limited: { label: "limited", color: "#f97316" },
  quota_exceeded: { label: "exceeded", color: "#ef4444" },
  reauth_required: { label: "reauth", color: "#0ea5e9" },
  deactivated: { label: "deactivated", color: "#71717a" },
};

export function AccountDistributionCharts({ accounts }: { accounts: AccountSummary[] }) {
  const { t } = useTranslation();
  const { plans, statuses } = useMemo(() => {
    const plans = new Map<string, number>();
    const statuses = new Map<string, number>();
    for (const account of accounts) {
      const plan = account.planType.trim().toLowerCase();
      const status = Object.hasOwn(STATUS_GROUPS, account.status) ? account.status : "unknown";
      plans.set(plan, (plans.get(plan) ?? 0) + 1);
      statuses.set(status, (statuses.get(status) ?? 0) + 1);
    }
    return {
      plans: Array.from(plans).sort(([a], [b]) => a.localeCompare(b)),
      statuses: [...Object.keys(STATUS_GROUPS), "unknown"]
        .map((status) => ({ status, count: statuses.get(status) ?? 0 }))
        .filter(({ count }) => count > 0),
    };
  }, [accounts]);

  return (
    <div className="grid min-w-0 grid-cols-1 gap-4 lg:grid-cols-2" data-testid="accounts-distribution-charts">
      <DonutChart
        title={t("accounts.distribution.byPlan")}
        subtitle={t("accounts.distribution.description")}
        variant="distribution"
        total={accounts.length}
        items={plans.map(([plan, count]) => ({
          id: `plan:${plan}`,
          label: plan || t("accounts.distribution.unknown"),
          value: count,
        }))}
      />
      <DonutChart
        title={t("accounts.distribution.byStatus")}
        subtitle={t("accounts.distribution.description")}
        variant="distribution"
        total={accounts.length}
        items={statuses.map(({ status, count }) => ({
          id: status,
          label: status === "unknown"
            ? t("accounts.distribution.unknown")
            : t(`common.status.${STATUS_GROUPS[status].label}`),
          color: STATUS_GROUPS[status]?.color ?? "#a1a1aa",
          value: count,
        }))}
      />
    </div>
  );
}
