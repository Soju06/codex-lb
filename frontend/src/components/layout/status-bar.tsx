import { useEffect, useState } from "react";
import { Activity, ArrowRightLeft, Tag } from "lucide-react";
import { useQuery } from "@tanstack/react-query";

import { getDashboardOverview } from "@/features/dashboard/api";
import { DEFAULT_OVERVIEW_TIMEFRAME } from "@/features/dashboard/schemas";
import { getSettings } from "@/features/settings/api";
import { formatTimeLong } from "@/utils/formatters";

const GITHUB_REPOSITORY_URL = "https://github.com/soju06/codex-lb";

function getRoutingLabel(
  strategy: "usage_weighted" | "round_robin" | "capacity_weighted",
  sticky: boolean,
  preferEarlier: boolean,
  preferEarlierWindow: "primary" | "secondary",
): string {
  const earlyResetLabel = preferEarlierWindow === "secondary" ? "Early weekly reset" : "Early 5h reset";
  if (strategy === "round_robin") {
    return sticky ? "Round robin + Sticky threads" : "Round robin";
  }
  if (strategy === "capacity_weighted") {
    if (sticky && preferEarlier) return `Capacity weighted + Sticky + ${earlyResetLabel}`;
    if (sticky) return "Capacity weighted + Sticky threads";
    if (preferEarlier) return `Capacity weighted + ${earlyResetLabel}`;
    return "Capacity weighted";
  }
  if (sticky && preferEarlier) return `Sticky + ${earlyResetLabel}`;
  if (sticky) return "Sticky threads";
  if (preferEarlier) return `${earlyResetLabel} preferred`;
  return "Usage weighted";
}

export function StatusBar() {
  const { data: lastSyncAt = null } = useQuery({
    queryKey: ["dashboard", "overview", DEFAULT_OVERVIEW_TIMEFRAME],
    queryFn: () => getDashboardOverview({ timeframe: DEFAULT_OVERVIEW_TIMEFRAME }),
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
    select: (data) => data.lastSyncAt,
  });

  const { data: settings } = useQuery({
    queryKey: ["settings", "detail"],
    queryFn: getSettings,
  });
  const lastSync = formatTimeLong(lastSyncAt);
  const [isLive, setIsLive] = useState(false);
  useEffect(() => {
    function check() {
      setIsLive(lastSyncAt ? Date.now() - new Date(lastSyncAt).getTime() < 60_000 : false);
    }
    check();
    const id = setInterval(check, 10_000);
    return () => clearInterval(id);
  }, [lastSyncAt]);

  const routingLabel = settings
    ? getRoutingLabel(
        settings.routingStrategy,
        settings.stickyThreadsEnabled,
        settings.preferEarlierResetAccounts,
        settings.preferEarlierResetWindow,
      )
    : "—";

  return (
    <footer className="fixed bottom-0 left-0 right-0 z-50 border-t border-white/[0.08] bg-background/50 px-4 py-2 shadow-[0_-1px_12px_rgba(0,0,0,0.06)] backdrop-blur-xl backdrop-saturate-[1.8] supports-[backdrop-filter]:bg-background/40 dark:shadow-[0_-1px_12px_rgba(0,0,0,0.25)]">
      <div className="mx-auto flex w-full max-w-[1500px] items-center gap-4 text-xs text-muted-foreground">
        <div className="flex min-w-0 flex-wrap items-center gap-x-5 gap-y-1">
          <span className="inline-flex items-center gap-1.5">
            {isLive ? (
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" title="Live" />
            ) : (
              <Activity className="h-3 w-3" aria-hidden="true" />
            )}
            <span className="font-medium">Last sync:</span> {lastSync.time}
          </span>
          <span className="inline-flex items-center gap-1.5">
            <ArrowRightLeft className="h-3 w-3" aria-hidden="true" />
            <span className="font-medium">Routing:</span> {routingLabel}
          </span>
          <span className="inline-flex items-center gap-1.5">
            <Tag className="h-3 w-3" aria-hidden="true" />
            <span className="font-medium">Version:</span> {__APP_VERSION__}
          </span>
        </div>
        <a
          aria-label="Open official GitHub repository"
          className="ml-auto inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-border/70 bg-background/70 text-muted-foreground transition-colors hover:bg-muted/70 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          href={GITHUB_REPOSITORY_URL}
          rel="noreferrer"
          target="_blank"
          title="GitHub"
        >
          <svg className="h-3.5 w-3.5" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
            <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82A7.63 7.63 0 0 1 8 3.86c.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
          </svg>
        </a>
      </div>
    </footer>
  );
}
