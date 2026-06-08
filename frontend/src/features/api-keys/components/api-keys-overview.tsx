import type { ApiKey } from "@/features/api-keys/schemas";
import { formatCompactNumber, formatCurrency } from "@/utils/formatters";

type UsageMetric = "requests" | "tokens" | "cost";

type OverviewStatProps = {
  label: string;
  value: string;
  meta?: string | null;
};

type BreakdownRow = {
  id: string;
  label: string;
  labelSuffix: string;
  value: number;
  share: number;
};

function isExpired(apiKey: ApiKey): boolean {
  if (!apiKey.expiresAt) return false;
  return new Date(apiKey.expiresAt).getTime() < Date.now();
}

function formatSharePercent(share: number): string {
  if (!Number.isFinite(share) || share <= 0) {
    return "0%";
  }

  const percent = share * 100;
  const maximumFractionDigits = percent < 10 ? 1 : 0;
  return `${percent.toLocaleString("en-US", { maximumFractionDigits })}%`;
}

function OverviewStat({ label, value, meta }: OverviewStatProps) {
  const testId = `api-keys-overview-stat-${label.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  return (
    <div className="rounded-xl border bg-card p-4" data-testid={testId}>
      <p className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">{label}</p>
      <p className="mt-1 text-[1.55rem] font-semibold tracking-tight tabular-nums">{value}</p>
      {meta ? <p className="mt-1 text-xs text-muted-foreground">{meta}</p> : null}
    </div>
  );
}

function formatMetricValue(metric: UsageMetric, value: number): string {
  if (metric === "cost") {
    return formatCurrency(value);
  }
  return formatCompactNumber(value);
}

function buildBreakdownRows(apiKeys: ApiKey[], metric: UsageMetric): BreakdownRow[] {
  const rows = apiKeys
    .map((apiKey) => {
      const usage = apiKey.usageSummary;
      const value =
        metric === "cost"
          ? usage?.totalCostUsd ?? 0
          : metric === "tokens"
            ? usage?.totalTokens ?? 0
            : usage?.requestCount ?? 0;

      return {
        id: apiKey.id,
        label: apiKey.name,
        labelSuffix: apiKey.keyPrefix ? ` · ${apiKey.keyPrefix}` : "",
        value,
      };
    })
    .filter((row) => row.value > 0)
    .sort((a, b) => b.value - a.value);

  const total = rows.reduce((sum, row) => sum + row.value, 0);
  if (total <= 0) {
    return [];
  }

  return rows.map((row) => ({
    ...row,
    share: row.value / total,
  }));
}

function BreakdownPanel({
  title,
  subtitle,
  metric,
  apiKeys,
}: {
  title: string;
  subtitle: string;
  metric: UsageMetric;
  apiKeys: ApiKey[];
}) {
  const rows = buildBreakdownRows(apiKeys, metric);
  const hasRows = rows.length > 0;

  return (
    <div className="rounded-xl border bg-card p-4" data-testid={`api-keys-overview-${metric}-panel`}>
      <div className="mb-3">
        <h3 className="text-sm font-semibold">{title}</h3>
        <p className="mt-0.5 text-xs text-muted-foreground">{subtitle}</p>
      </div>

      {!hasRows ? (
        <div className="flex h-[12rem] items-center justify-center rounded-lg border border-dashed bg-muted/10 px-4 text-sm text-muted-foreground">
          No usage recorded yet.
        </div>
      ) : (
        <div
          className="space-y-3 overflow-y-auto pr-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
          style={{ maxHeight: "18rem" }}
        >
          {rows.map((row, index) => (
            <div
              key={row.id}
              className="animate-fade-in-up space-y-1.5"
              style={{ animationDelay: `${index * 50}ms` }}
            >
              <div className="flex items-center justify-between gap-3 text-xs">
                <span className="min-w-0 truncate font-medium">
                  {row.label}
                  <span className="text-muted-foreground">{row.labelSuffix}</span>
                </span>
                <span className="shrink-0 tabular-nums text-muted-foreground">
                  {formatMetricValue(metric, row.value)} · {formatSharePercent(row.share)}
                </span>
              </div>
              <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full rounded-full bg-primary transition-[width] duration-500 ease-out"
                  style={{ width: `${Math.max(row.share * 100, 1)}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export type ApiKeysOverviewProps = {
  apiKeys: ApiKey[];
};

export function ApiKeysOverview({ apiKeys }: ApiKeysOverviewProps) {
  const totalKeys = apiKeys.length;
  const activeKeys = apiKeys.filter((apiKey) => apiKey.isActive && !isExpired(apiKey)).length;
  const expiredKeys = apiKeys.filter((apiKey) => isExpired(apiKey)).length;
  const usedKeys = apiKeys.filter((apiKey) => (apiKey.usageSummary?.requestCount ?? 0) > 0).length;
  const totalRequests = apiKeys.reduce((sum, apiKey) => sum + (apiKey.usageSummary?.requestCount ?? 0), 0);
  const totalTokens = apiKeys.reduce((sum, apiKey) => sum + (apiKey.usageSummary?.totalTokens ?? 0), 0);
  const totalCostUsd = apiKeys.reduce((sum, apiKey) => sum + (apiKey.usageSummary?.totalCostUsd ?? 0), 0);
  const inactiveKeys = totalKeys - activeKeys;
  const idleKeys = totalKeys - usedKeys;

  return (
    <section className="space-y-4">
      <div className="flex items-center gap-3">
        <h2 className="text-[13px] font-medium uppercase tracking-wider text-muted-foreground">Overview</h2>
        <div className="h-px flex-1 bg-border" />
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <OverviewStat label="API keys" value={formatCompactNumber(totalKeys)} meta={`${formatCompactNumber(expiredKeys)} expired`} />
        <OverviewStat label="Active keys" value={formatCompactNumber(activeKeys)} meta={`${formatCompactNumber(inactiveKeys)} inactive`} />
        <OverviewStat label="Used keys" value={formatCompactNumber(usedKeys)} meta={`${formatCompactNumber(idleKeys)} idle`} />
        <OverviewStat
          label="Lifetime requests"
          value={formatCompactNumber(totalRequests)}
          meta={`${formatCompactNumber(totalTokens)} tokens`}
        />
        <OverviewStat label="Lifetime cost" value={formatCurrency(totalCostUsd)} meta="Across all keys (lifetime)" />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <BreakdownPanel
          title="Lifetime Cost by API Key"
          subtitle="Relative spend across all keys"
          metric="cost"
          apiKeys={apiKeys}
        />
        <BreakdownPanel
          title="Lifetime Tokens by API Key"
          subtitle="Relative token volume across all keys"
          metric="tokens"
          apiKeys={apiKeys}
        />
      </div>
    </section>
  );
}
