import { useQuery } from "@tanstack/react-query";
import { getReports } from "../api";

type ReportsFilterState = {
  startDate: string | undefined;
  endDate: string | undefined;
  accountId: string[];
  model: string | undefined;
  useragent?: string | undefined;
};

export function useReports(
  filters: ReportsFilterState,
  timeZone: string | undefined,
) {
  return useQuery({
    queryKey: ["reports", filters, timeZone],
    queryFn: () =>
      getReports({
        startDate: filters.startDate,
        endDate: filters.endDate,
        accountId: filters.accountId.length > 0 ? filters.accountId : undefined,
        model: filters.model || undefined,
        useragent: filters.useragent || undefined,
        timezone: timeZone,
      }),
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
  });
}
