import { useQuery } from "@tanstack/react-query";

import { getDashboardOverview, getDashboardProjections } from "@/features/dashboard/api";
import {
  DEFAULT_OVERVIEW_TIMEFRAME,
  type OverviewTimeframe,
} from "@/features/dashboard/schemas";

export function useDashboard(timeframe: OverviewTimeframe = DEFAULT_OVERVIEW_TIMEFRAME) {
  return useQuery({
    queryKey: ["dashboard", "overview", timeframe],
    queryFn: () => getDashboardOverview({ timeframe }),
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
  });
}

export function useDashboardProjections(enabled = true) {
  return useQuery({
    queryKey: ["dashboard", "projections"],
    queryFn: getDashboardProjections,
    enabled,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
  });
}
