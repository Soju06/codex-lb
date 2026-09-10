import { lazy, Suspense, useEffect, useState } from "react";
import { Activity, Coins, Database, DollarSign, Users } from "lucide-react";
import { useTranslation } from "react-i18next";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import { SpinnerBlock } from "@/components/ui/spinner";
import { StatsGrid } from "@/features/dashboard/components/stats-grid";
import { getKeyDashboardGroup } from "@/features/key-dashboard/api";
import type { KeyDashboardGroup } from "@/features/key-dashboard/schemas";
import { ApiError } from "@/lib/api-client";
import { formatCompactNumber, formatCurrency } from "@/utils/formatters";

const KeyGroupDailyChart = lazy(() => import("./key-group-daily-chart").then((module) => ({
  default: module.KeyGroupDailyChart,
})));

type Props = {
  apiKey: string;
  onUnauthorized: () => void;
};

export function KeyGroupPanel({ apiKey, onUnauthorized }: Props) {
  const { t, i18n } = useTranslation();
  const [data, setData] = useState<KeyDashboardGroup | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retryVersion, setRetryVersion] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    void getKeyDashboardGroup(apiKey, controller.signal).then((group) => {
      if (!controller.signal.aborted) {
        setError(null);
        setData(group);
      }
    }).catch((caught: unknown) => {
      if (controller.signal.aborted) return;
      if (caught instanceof ApiError && caught.status === 401) {
        onUnauthorized();
      } else {
        setError(caught instanceof Error ? caught.message : t("keyDashboard.errors.loadFailed"));
      }
    });
    return () => controller.abort();
  }, [apiKey, retryVersion, onUnauthorized, t]);

  if (error) return (
    <div className="space-y-3">
      <AlertMessage variant="error">{error}</AlertMessage>
      <Button variant="outline" onClick={() => {
        setData(null);
        setError(null);
        setRetryVersion((value) => value + 1);
      }}>{t("keyDashboard.group.retry")}</Button>
    </div>
  );
  if (!data) return <SpinnerBlock />;
  if (data.groupName === null) return (
    <div className="rounded-xl border border-dashed bg-card px-6 py-14 text-center">
      <Users className="mx-auto mb-4 size-8 text-muted-foreground" aria-hidden="true" />
      <h2 className="text-lg font-semibold">{t("keyDashboard.group.emptyTitle")}</h2>
      <p className="mx-auto mt-2 max-w-md text-sm text-muted-foreground">{t("keyDashboard.group.emptyDescription")}</p>
    </div>
  );

  const total = data.members.reduce((sum, member) => ({
    requests: sum.requests + member.requestCount,
    tokens: sum.tokens + member.totalTokens,
    cached: sum.cached + member.cachedInputTokens,
    cost: sum.cost + member.totalCostUsd,
  }), { requests: 0, tokens: 0, cached: 0, cost: 0 });
  const stats = [
    { label: t("keyDashboard.group.requests"), value: formatCompactNumber(total.requests), icon: Activity, trend: [], trendColor: "#0ea5e9", accentClassName: "bg-sky-500/10 text-sky-700 dark:text-sky-400" },
    { label: t("keyDashboard.group.tokens"), value: formatCompactNumber(total.tokens), icon: Coins, trend: [], trendColor: "#8b5cf6", accentClassName: "bg-violet-500/10 text-violet-700 dark:text-violet-400" },
    { label: t("keyDashboard.group.cached"), value: formatCompactNumber(total.cached), icon: Database, trend: [], trendColor: "#10b981", accentClassName: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" },
    { label: t("keyDashboard.group.cost"), value: formatCurrency(total.cost), icon: DollarSign, trend: [], trendColor: "#f59e0b", accentClassName: "bg-amber-500/10 text-amber-700 dark:text-amber-400" },
  ];
  const date = (value: string) => new Date(value).toLocaleString(i18n.language);
  const number = new Intl.NumberFormat(i18n.language);

  return (
    <section className="min-w-0 space-y-6" aria-labelledby="key-group-title">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 id="key-group-title" className="break-words text-xl font-semibold">{data.groupName}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{t("keyDashboard.group.description")}</p>
          <p className="mt-1 text-xs text-muted-foreground">{date(data.from)} – {date(data.until)}</p>
        </div>
        <span className="rounded-full border bg-muted px-3 py-1 text-xs font-medium">{t("keyDashboard.group.period")}</span>
      </div>
      <StatsGrid stats={stats} />
      <Suspense fallback={<SpinnerBlock />}>
        <KeyGroupDailyChart members={data.members} />
      </Suspense>
      <div className="overflow-x-auto rounded-xl border bg-card">
        <table className="w-full min-w-[680px] text-sm">
          <caption className="sr-only">{t("keyDashboard.group.members")}</caption>
          <thead className="border-b bg-muted/40 text-xs text-muted-foreground">
            <tr>
              <th scope="col" className="px-4 py-3 text-left font-medium">{t("keyDashboard.group.member")}</th>
              {(["requests", "tokens", "cached", "cost"] as const).map((metric) => (
                <th key={metric} scope="col" className="px-4 py-3 text-right font-medium">{t(`keyDashboard.group.${metric}`)}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y">
            {data.members.map((member, index) => (
              <tr key={index} className={member.isCurrentKey ? "bg-primary/5" : undefined}>
                <th scope="row" className="max-w-64 px-4 py-4 text-left font-normal">
                  <div className="flex items-center gap-2">
                    <span className="break-words font-medium">{member.name}</span>
                    {member.isCurrentKey ? <span className="shrink-0 rounded bg-primary/10 px-1.5 py-0.5 text-xs text-primary">{t("keyDashboard.group.you")}</span> : null}
                  </div>
                  <div className="mt-1 font-mono text-xs text-muted-foreground">{member.keyPrefix}</div>
                </th>
                <td className="px-4 py-4 text-right tabular-nums">{number.format(member.requestCount)}</td>
                <td className="px-4 py-4 text-right tabular-nums">{number.format(member.totalTokens)}</td>
                <td className="px-4 py-4 text-right tabular-nums">{number.format(member.cachedInputTokens)}</td>
                <td className="px-4 py-4 text-right tabular-nums">{formatCurrency(member.totalCostUsd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
