import { useTranslation } from "react-i18next";

import type { ThreadIdentityFacet, ThreadIdentityResponse } from "../schemas";

export type ThreadIdentityCardProps = {
  data: ThreadIdentityResponse;
};

function formatPercent(ratio: number): string {
  const percent = ratio * 100;
  if (percent > 0 && percent < 0.1) {
    return "<0.1%";
  }
  return `${percent.toFixed(1)}%`;
}

function formatFactor(value: number): string {
  return value.toFixed(2);
}

function formatCount(value: number): string {
  return value.toLocaleString();
}

type MetricRow = {
  id: string;
  label: string;
  hint: string;
  render: (facet: ThreadIdentityFacet) => string;
  sub: (facet: ThreadIdentityFacet) => string | null;
};

export function ThreadIdentityCard({ data }: ThreadIdentityCardProps) {
  const { t } = useTranslation();

  if (!data.available) {
    return (
      <div className="rounded-xl border bg-card p-5" data-testid="thread-identity-card">
        <div className="text-sm font-semibold text-foreground">{t("reports.threadIdentity.title")}</div>
        <p className="mt-3 text-sm text-muted-foreground">
          {t("reports.threadIdentity.unavailable", { days: data.maxDays })}
        </p>
      </div>
    );
  }

  const rows: MetricRow[] = [
    {
      id: "factor",
      label: t("reports.threadIdentity.metrics.accountsPerConversation"),
      hint: t("reports.threadIdentity.hints.accountsPerConversation", {
        requests: data.conversationMinRequests,
      }),
      render: (facet) => formatFactor(facet.meanAccountsPerConversation),
      sub: (facet) =>
        t("reports.threadIdentity.subs.conversations", {
          conversations: formatCount(facet.conversations),
        }),
    },
    {
      id: "single-account",
      label: t("reports.threadIdentity.metrics.singleAccountShare"),
      hint: t("reports.threadIdentity.hints.singleAccountShare"),
      render: (facet) => formatPercent(facet.singleAccountConversationShare),
      sub: () => null,
    },
    {
      id: "switch-rate",
      label: t("reports.threadIdentity.metrics.switchRate"),
      hint: t("reports.threadIdentity.hints.switchRate", {
        minutes: Math.round(data.switchMaxGapSeconds / 60),
      }),
      render: (facet) => formatPercent(facet.accountSwitchRate),
      sub: (facet) => t("reports.threadIdentity.subs.turns", { turns: formatCount(facet.turns) }),
    },
    {
      id: "cache-hit",
      label: t("reports.threadIdentity.metrics.cacheHitRatio"),
      hint: t("reports.threadIdentity.hints.cacheHitRatio", {
        tokens: formatCount(data.cacheMinInputTokens),
      }),
      render: (facet) => formatPercent(facet.cacheHitRatio),
      sub: (facet) =>
        t("reports.threadIdentity.subs.cacheSample", {
          tokens: formatCount(facet.cacheSampleInputTokens),
        }),
    },
    {
      id: "requests",
      label: t("reports.threadIdentity.metrics.requestShare"),
      hint: t("reports.threadIdentity.hints.requestShare"),
      render: (facet) => formatPercent(facet.requestShare),
      sub: (facet) => t("reports.threadIdentity.subs.requests", { requests: formatCount(facet.requests) }),
    },
  ];

  // Account deletion detaches history, which drags the account-spread
  // figures toward one; disclose it rather than let the factor quietly decay.
  const unattributedShare =
    data.totalRequests > 0
      ? (data.keyed.unattributedRequestShare * data.keyed.requests +
          data.unkeyed.unattributedRequestShare * data.unkeyed.requests) /
        data.totalRequests
      : 0;

  const facets: { id: string; label: string; facet: ThreadIdentityFacet }[] = [
    { id: "keyed", label: t("reports.threadIdentity.keyed"), facet: data.keyed },
    { id: "unkeyed", label: t("reports.threadIdentity.unkeyed"), facet: data.unkeyed },
  ];

  return (
    <div className="rounded-xl border bg-card p-5" data-testid="thread-identity-card">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="text-sm font-semibold text-foreground">{t("reports.threadIdentity.title")}</div>
        <div className="text-xs text-muted-foreground">
          {t("reports.threadIdentity.unkeyedShare", {
            share: formatPercent(data.unkeyedRequestShare),
            requests: formatCount(data.totalRequests),
          })}
        </div>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">{t("reports.threadIdentity.subtitle")}</p>

      <div className="mt-4 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
              <th scope="col" className="py-2 pr-4 text-left font-medium">
                {t("reports.threadIdentity.metricColumn")}
              </th>
              {facets.map((column) => (
                <th key={column.id} scope="col" className="py-2 pl-4 text-right font-medium">
                  {column.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id} className="border-b last:border-b-0 align-top">
                <th scope="row" className="py-3 pr-4 text-left font-normal">
                  <div className="text-foreground">{row.label}</div>
                  <div className="text-xs text-muted-foreground">{row.hint}</div>
                </th>
                {facets.map((column) => {
                  const sub = row.sub(column.facet);
                  return (
                    <td
                      key={column.id}
                      className="py-3 pl-4 text-right tabular-nums"
                      data-testid={`thread-identity-${column.id}-${row.id}`}
                    >
                      <div className="font-semibold text-foreground">{row.render(column.facet)}</div>
                      {sub ? <div className="text-xs text-muted-foreground">{sub}</div> : null}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {data.unkeyed.threadGroupingApproximate ? (
        <p className="mt-3 text-xs text-muted-foreground">{t("reports.threadIdentity.approximate")}</p>
      ) : null}
      {unattributedShare > 0 ? (
        <p className="mt-1 text-xs text-muted-foreground">
          {t("reports.threadIdentity.unattributed", { share: formatPercent(unattributedShare) })}
        </p>
      ) : null}
    </div>
  );
}
