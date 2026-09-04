import { CalendarClock, Gauge, KeyRound, ShieldCheck } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { useDateDisplayFormatStore } from "@/hooks/use-date-format";
import type {
  KeyDashboardProfile,
  KeyUsageLimit,
} from "@/features/key-dashboard/schemas";
import {
  formatCompactNumber,
  formatCurrency,
  formatDateTimeInline,
  formatSlug,
} from "@/utils/formatters";

type KeyProfileCardProps = {
  profile: KeyDashboardProfile;
  limits: KeyUsageLimit[];
};

function formatLimitValue(limit: KeyUsageLimit, value: number): string {
  return limit.limit_type === "cost_usd"
    ? formatCurrency(value / 1_000_000)
    : formatCompactNumber(value);
}

function policyList(values: string[] | null, fallback: string): string {
  return values && values.length > 0 ? values.join(", ") : fallback;
}

export function KeyProfileCard({ profile, limits }: KeyProfileCardProps) {
  const { t } = useTranslation();
  const dateDisplayFormat = useDateDisplayFormatStore((state) => state.dateDisplayFormat);
  const allModels = t("keyDashboard.profile.allModels");
  const allEfforts = t("keyDashboard.profile.allReasoningEfforts");
  const inherited = t("keyDashboard.profile.inherited");

  const details = [
    {
      label: t("keyDashboard.profile.createdAt"),
      value: formatDateTimeInline(profile.createdAt, dateDisplayFormat),
    },
    {
      label: t("keyDashboard.profile.expiresAt"),
      value: profile.expiresAt
        ? formatDateTimeInline(profile.expiresAt, dateDisplayFormat)
        : t("keyDashboard.profile.never"),
    },
    {
      label: t("keyDashboard.profile.lastUsedAt"),
      value: profile.lastUsedAt
        ? formatDateTimeInline(profile.lastUsedAt, dateDisplayFormat)
        : t("keyDashboard.profile.notUsedYet"),
    },
  ];

  const policies = [
    {
      label: t("keyDashboard.profile.models"),
      value: profile.enforcedModel
        ? t("keyDashboard.profile.enforcedValue", { value: profile.enforcedModel })
        : policyList(profile.allowedModels, allModels),
    },
    {
      label: t("keyDashboard.profile.reasoning"),
      value: profile.enforcedReasoningEffort
        ? t("keyDashboard.profile.enforcedValue", { value: profile.enforcedReasoningEffort })
        : policyList(profile.allowedReasoningEfforts, allEfforts),
    },
    {
      label: t("keyDashboard.profile.serviceTier"),
      value: profile.enforcedServiceTier ?? inherited,
    },
    {
      label: t("keyDashboard.profile.trafficClass"),
      value: formatSlug(profile.trafficClass),
    },
    {
      label: t("keyDashboard.profile.transportPolicy"),
      value: profile.transportPolicyOverride
        ? formatSlug(profile.transportPolicyOverride)
        : inherited,
    },
  ];

  return (
    <section className="overflow-hidden rounded-xl border bg-card" aria-labelledby="key-profile-heading">
      <div className="flex flex-col gap-4 border-b bg-muted/20 p-5 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-sky-500/10 text-sky-700 dark:text-sky-400">
            <KeyRound className="h-5 w-5" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <h2 id="key-profile-heading" className="truncate text-lg font-semibold">
              {profile.name}
            </h2>
            <p className="font-mono text-xs text-muted-foreground">{profile.keyPrefix}</p>
          </div>
        </div>
        <Badge
          variant="outline"
          className="w-fit border-emerald-500/20 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
        >
          <ShieldCheck className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
          {profile.isActive
            ? t("keyDashboard.profile.active")
            : t("keyDashboard.profile.inactive")}
        </Badge>
      </div>

      <div className="grid gap-6 p-5 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.4fr)]">
        <div className="space-y-3">
          <div className="flex items-center gap-2 text-sm font-medium">
            <CalendarClock className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
            {t("keyDashboard.profile.lifecycle")}
          </div>
          <dl className="grid gap-3 sm:grid-cols-3 lg:grid-cols-1">
            {details.map((detail) => (
              <div key={detail.label} className="rounded-lg bg-muted/35 px-3 py-2.5">
                <dt className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                  {detail.label}
                </dt>
                <dd className="mt-1 text-sm">{detail.value}</dd>
              </div>
            ))}
          </dl>
        </div>

        <div className="space-y-3">
          <div className="flex items-center gap-2 text-sm font-medium">
            <ShieldCheck className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
            {t("keyDashboard.profile.policies")}
          </div>
          <dl className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {policies.map((policy) => (
              <div key={policy.label} className="min-w-0 rounded-lg border px-3 py-2.5">
                <dt className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                  {policy.label}
                </dt>
                <dd className="mt-1 truncate text-sm" title={policy.value}>{policy.value}</dd>
              </div>
            ))}
          </dl>
        </div>
      </div>

      <div className="border-t p-5">
        <div className="mb-3 flex items-center gap-2 text-sm font-medium">
          <Gauge className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
          {t("keyDashboard.profile.limits")}
        </div>
        {limits.length === 0 ? (
          <p className="rounded-lg bg-muted/35 px-3 py-3 text-sm text-muted-foreground">
            {t("keyDashboard.profile.noLimits")}
          </p>
        ) : (
          <div className="grid gap-3 lg:grid-cols-2">
            {limits.map((limit) => {
              const usedPercent = limit.max_value > 0
                ? Math.min(100, Math.max(0, (limit.current_value / limit.max_value) * 100))
                : 0;
              return (
                <div
                  key={`${limit.limit_type}:${limit.limit_window}:${limit.model_filter ?? "all"}`}
                  className="rounded-lg border p-3"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="text-sm font-medium">
                        {formatSlug(limit.limit_type)} · {formatSlug(limit.limit_window)}
                      </div>
                      <div className="mt-0.5 text-xs text-muted-foreground">
                        {limit.model_filter ?? allModels}
                      </div>
                    </div>
                    <div className="text-right text-sm font-medium tabular-nums">
                      {formatLimitValue(limit, limit.current_value)} / {formatLimitValue(limit, limit.max_value)}
                    </div>
                  </div>
                  <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full rounded-full bg-sky-500 transition-[width]"
                      style={{ width: `${usedPercent}%` }}
                    />
                  </div>
                  <div className="mt-2 flex flex-wrap justify-between gap-2 text-xs text-muted-foreground">
                    <span>
                      {t("keyDashboard.profile.remaining", {
                        value: formatLimitValue(limit, limit.remaining_value),
                      })}
                    </span>
                    <span>
                      {t("keyDashboard.profile.resets", {
                        value: formatDateTimeInline(limit.reset_at, dateDisplayFormat),
                      })}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </section>
  );
}
