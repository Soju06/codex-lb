import { useQuery } from "@tanstack/react-query";

import { getDashboardOverviewWithParams } from "@/features/dashboard/api";

export function useDashboard(ownerUserId?: string) {
  return useQuery({
    queryKey: ["dashboard", "overview", ownerUserId ?? "all"],
    queryFn: () => getDashboardOverviewWithParams({ ownerUserId }),
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
  });
}
