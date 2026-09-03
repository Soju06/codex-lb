import { Activity, Coins, Database, DollarSign, KeyRound, LogOut, RefreshCw } from "lucide-react";
import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner, SpinnerBlock } from "@/components/ui/spinner";
import { RecentRequestsTable } from "@/features/dashboard/components/recent-requests-table";
import { StatsGrid } from "@/features/dashboard/components/stats-grid";
import type { RequestLogColumnWidths } from "@/features/dashboard/request-log-columns";
import type { DashboardStat } from "@/features/dashboard/utils";
import { getKeyDashboardData } from "@/features/key-dashboard/api";
import { KeyProfileCard } from "@/features/key-dashboard/components/key-profile-card";
import {
  toDashboardRequestLog,
  type KeyDashboardProfile,
  type KeyDashboardRequestLogsResponse,
  type KeyUsage,
} from "@/features/key-dashboard/schemas";
import {
  forgetRememberedApiKey,
  getRememberedApiKey,
  rememberApiKey,
} from "@/features/key-dashboard/storage";
import { ApiError } from "@/lib/api-client";
import { formatCompactNumber, formatCurrency } from "@/utils/formatters";

const DEFAULT_LIMIT = 25;
const SAFE_COLUMNS = ["time", "model", "transport", "status", "ttft", "tps", "tokens", "cost", "details"] as const;
const SAFE_COLUMN_WIDTHS: RequestLogColumnWidths = {
  time: 136,
  model: 232,
  transport: 144,
  status: 112,
  ttft: 96,
  tps: 96,
  tokens: 112,
  cost: 96,
  details: 360,
};

export function KeyDashboardPage() {
  const { t } = useTranslation();
  const apiKeyRef = useRef("");
  const requestGenerationRef = useRef(0);
  const [draftKey, setDraftKey] = useState("");
  const [initialRememberedKey] = useState(getRememberedApiKey);
  const [rememberKey, setRememberKey] = useState(initialRememberedKey !== null);
  const [profile, setProfile] = useState<KeyDashboardProfile | null>(null);
  const [usage, setUsage] = useState<KeyUsage | null>(null);
  const [logs, setLogs] = useState<KeyDashboardRequestLogsResponse | null>(null);
  const [limit, setLimit] = useState(DEFAULT_LIMIT);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const connected = profile !== null && usage !== null && logs !== null;
  const requests = useMemo(
    () => logs?.requests.map(toDashboardRequestLog) ?? [],
    [logs],
  );
  const stats = useMemo<DashboardStat[]>(
    () => usage ? [
      {
        label: t("keyDashboard.stats.requests"),
        value: formatCompactNumber(usage.request_count),
        icon: Activity,
        trend: [],
        trendColor: "#0ea5e9",
        accentClassName: "bg-sky-500/10 text-sky-700 dark:text-sky-400",
      },
      {
        label: t("keyDashboard.stats.totalTokens"),
        value: formatCompactNumber(usage.total_tokens),
        icon: Coins,
        trend: [],
        trendColor: "#8b5cf6",
        accentClassName: "bg-violet-500/10 text-violet-700 dark:text-violet-400",
      },
      {
        label: t("keyDashboard.stats.cachedTokens"),
        value: formatCompactNumber(usage.cached_input_tokens),
        icon: Database,
        trend: [],
        trendColor: "#10b981",
        accentClassName: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
      },
      {
        label: t("keyDashboard.stats.cost"),
        value: formatCurrency(usage.total_cost_usd),
        icon: DollarSign,
        trend: [],
        trendColor: "#f59e0b",
        accentClassName: "bg-amber-500/10 text-amber-700 dark:text-amber-400",
      },
    ] : [],
    [t, usage],
  );

  const clearConnection = useCallback(() => {
    requestGenerationRef.current += 1;
    apiKeyRef.current = "";
    forgetRememberedApiKey();
    setDraftKey("");
    setRememberKey(false);
    setProfile(null);
    setUsage(null);
    setLogs(null);
    setLimit(DEFAULT_LIMIT);
    setOffset(0);
    setIsLoading(false);
  }, []);

  const load = useCallback(async (
    apiKey: string,
    nextLimit: number,
    nextOffset: number,
    rememberAfterSuccess = false,
  ) => {
    const generation = ++requestGenerationRef.current;
    setIsLoading(true);
    setError(null);
    try {
      const data = await getKeyDashboardData(apiKey, nextLimit, nextOffset);
      if (generation !== requestGenerationRef.current) return;
      apiKeyRef.current = apiKey;
      if (rememberAfterSuccess) rememberApiKey(apiKey);
      setProfile(data.profile);
      setUsage(data.usage);
      setLogs(data.logs);
      setLimit(nextLimit);
      setOffset(nextOffset);
    } catch (caught) {
      if (generation !== requestGenerationRef.current) return;
      if (caught instanceof ApiError && caught.status === 401) {
        clearConnection();
        setError(t("keyDashboard.errors.invalidKey"));
      } else {
        setError(caught instanceof Error ? caught.message : t("keyDashboard.errors.loadFailed"));
      }
    } finally {
      if (generation === requestGenerationRef.current) setIsLoading(false);
    }
  }, [clearConnection, t]);

  useEffect(() => {
    if (!initialRememberedKey) return;
    const restoreTimer = window.setTimeout(() => {
      void load(initialRememberedKey, DEFAULT_LIMIT, 0);
    }, 0);
    return () => window.clearTimeout(restoreTimer);
  }, [initialRememberedKey, load]);

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const key = draftKey.trim();
    if (!key) {
      setError(t("keyDashboard.errors.keyRequired"));
      return;
    }
    setDraftKey("");
    void load(key, DEFAULT_LIMIT, 0, rememberKey);
  };

  const handlePageChange = (nextLimit: number, nextOffset: number) => {
    if (!apiKeyRef.current) return;
    void load(apiKeyRef.current, nextLimit, nextOffset);
  };

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b bg-card/80 backdrop-blur">
        <div className="mx-auto flex h-16 w-full max-w-[1500px] items-center justify-between px-4 sm:px-6">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <KeyRound className="h-4 w-4" aria-hidden="true" />
            </div>
            <div>
              <div className="font-semibold">codex-lb</div>
              <div className="text-xs text-muted-foreground">{t("keyDashboard.title")}</div>
            </div>
          </div>
          {connected ? (
            <div className="flex gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={isLoading}
                onClick={() => handlePageChange(limit, offset)}
              >
                {isLoading ? <Spinner size="sm" /> : <RefreshCw className="h-4 w-4" aria-hidden="true" />}
                {t("keyDashboard.refresh")}
              </Button>
              <Button type="button" variant="ghost" size="sm" onClick={clearConnection}>
                <LogOut className="h-4 w-4" aria-hidden="true" />
                {t("keyDashboard.disconnect")}
              </Button>
            </div>
          ) : null}
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1500px] px-4 py-8 sm:px-6">
        {!connected ? (
          <div className="mx-auto mt-[10vh] max-w-md rounded-xl border bg-card p-6 shadow-sm">
            <div className="mb-6 space-y-2 text-center">
              <h1 className="text-2xl font-semibold tracking-tight">{t("keyDashboard.welcomeTitle")}</h1>
              <p className="text-sm text-muted-foreground">{t("keyDashboard.welcomeDescription")}</p>
            </div>
            <form className="space-y-4" onSubmit={handleSubmit}>
              <div className="space-y-2">
                <Label htmlFor="key-dashboard-api-key">{t("keyDashboard.apiKeyLabel")}</Label>
                <Input
                  id="key-dashboard-api-key"
                  type="password"
                  autoComplete="off"
                  spellCheck={false}
                  value={draftKey}
                  disabled={isLoading}
                  placeholder="sk-clb-…"
                  onChange={(event) => setDraftKey(event.target.value)}
                />
              </div>
              <div className="flex items-start gap-2">
                <Checkbox
                  id="key-dashboard-remember-key"
                  checked={rememberKey}
                  disabled={isLoading}
                  onCheckedChange={(checked) => setRememberKey(checked === true)}
                />
                <Label htmlFor="key-dashboard-remember-key" className="space-y-0.5 leading-tight">
                  <span className="block text-sm">{t("keyDashboard.rememberKey")}</span>
                  <span className="block text-xs font-normal text-muted-foreground">
                    {t("keyDashboard.rememberKeyDescription")}
                  </span>
                </Label>
              </div>
              {error ? <AlertMessage variant="error">{error}</AlertMessage> : null}
              <Button className="w-full" type="submit" disabled={isLoading}>
                {isLoading ? <Spinner size="sm" /> : <KeyRound className="h-4 w-4" aria-hidden="true" />}
                {t("keyDashboard.connect")}
              </Button>
              <p className="text-center text-xs text-muted-foreground">{t("keyDashboard.securityNotice")}</p>
            </form>
          </div>
        ) : (
          <div className="space-y-8">
            <div>
              <h1 className="text-2xl font-semibold tracking-tight">{t("keyDashboard.title")}</h1>
              <p className="mt-1 text-sm text-muted-foreground">{t("keyDashboard.description")}</p>
            </div>
            {error ? <AlertMessage variant="error">{error}</AlertMessage> : null}
            <KeyProfileCard profile={profile} limits={usage.limits} />
            <StatsGrid stats={stats} />
            <section className="space-y-4" aria-labelledby="key-dashboard-recent-requests">
              <div className="flex items-center justify-between">
                <div>
                  <h2 id="key-dashboard-recent-requests" className="text-lg font-semibold">
                    {t("keyDashboard.recentRequests")}
                  </h2>
                  <p className="text-sm text-muted-foreground">{t("keyDashboard.recentRequestsDescription")}</p>
                </div>
              </div>
              {isLoading && !logs ? <SpinnerBlock /> : (
                <RecentRequestsTable
                  requests={requests}
                  accounts={[]}
                  total={logs.total}
                  limit={limit}
                  offset={offset}
                  hasMore={logs.hasMore}
                  visibleColumns={SAFE_COLUMNS}
                  columnWidths={SAFE_COLUMN_WIDTHS}
                  allowSensitiveDetails={false}
                  onLimitChange={(nextLimit) => handlePageChange(nextLimit, 0)}
                  onOffsetChange={(nextOffset) => handlePageChange(limit, nextOffset)}
                />
              )}
            </section>
          </div>
        )}
      </main>
    </div>
  );
}
