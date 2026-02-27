import { useEffect, useState } from "react";
import { Activity, ArrowRightLeft } from "lucide-react";
import { useQuery } from "@tanstack/react-query";

import { getDashboardOverview } from "@/features/dashboard/api";
import { getSettings } from "@/features/settings/api";
import { formatTimeLong } from "@/utils/formatters";

function getRoutingLabel(strategy: "usage_weighted" | "round_robin", sticky: boolean, preferEarlier: boolean): string {
  if (strategy === "round_robin") {
    return sticky ? "Round robin + Sticky threads" : "Round robin";
  }
  if (sticky && preferEarlier) return "Sticky + Early reset";
  if (sticky) return "Sticky threads";
  if (preferEarlier) return "Early reset preferred";
  return "Usage weighted";
}

export function StatusBar() {
  const { data: lastSyncAt = null } = useQuery({
    queryKey: ["dashboard", "overview"],
    queryFn: getDashboardOverview,
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
    ? getRoutingLabel(settings.routingStrategy, settings.stickyThreadsEnabled, settings.preferEarlierResetAccounts)
    : "—";

  return (
    <footer className="fixed bottom-0 left-0 right-0 z-50 border-t border-white/[0.08] bg-background/50 px-4 py-2 shadow-[0_-1px_12px_rgba(0,0,0,0.06)] backdrop-blur-xl backdrop-saturate-[1.8] supports-[backdrop-filter]:bg-background/40 dark:shadow-[0_-1px_12px_rgba(0,0,0,0.25)]">
      <div className="mx-auto flex w-full max-w-7xl flex-wrap items-center gap-x-5 gap-y-1 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1.5">
          {isLive ? (
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" aria-label="Live" />
          ) : (
            <Activity className="h-3 w-3" aria-hidden="true" />
          )}
          <span className="font-medium">Last sync:</span> {lastSync.time}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <ArrowRightLeft className="h-3 w-3" aria-hidden="true" />
          <span className="font-medium">Routing:</span> {routingLabel}
        </span>
      </div>
    </footer>
  );
}
