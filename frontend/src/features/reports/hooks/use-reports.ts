import { useQuery } from "@tanstack/react-query";
import { getReports, getReportsOptions } from "../api";
import { isReportDateRangeValid } from "../date";

type ReportsFilterState = {
  startDate: string | undefined;
  endDate: string | undefined;
  accountId: string[];
  apiKeyId?: string[];
  model: string | undefined;
  useragent?: string | undefined;
};

export function useReports(
  filters: ReportsFilterState,
  timeZone: string | undefined,
) {
  return useQuery({
    enabled: isReportDateRangeValid(filters.startDate, filters.endDate),
    queryKey: ["reports", filters, timeZone],
    queryFn: () =>
      getReports({
        startDate: filters.startDate,
        endDate: filters.endDate,
        accountId: filters.accountId.length > 0 ? filters.accountId : undefined,
        apiKeyId:
          filters.apiKeyId && filters.apiKeyId.length > 0
            ? filters.apiKeyId
            : undefined,
        model: filters.model || undefined,
        useragent: filters.useragent || undefined,
        timezone: timeZone,
      }),
    staleTime: 5 * 60_000,
    refetchInterval: false,
    refetchOnWindowFocus: false,
    retry: false,
  });
}

export function useReportsOptions(filters: ReportsFilterState, timeZone: string | undefined) {
  const scope = {
    startDate: filters.startDate,
    endDate: filters.endDate,
    accountId: [...filters.accountId].sort(),
    apiKeyId: [...(filters.apiKeyId ?? [])].sort(),
    timezone: timeZone,
  };
  return useQuery({
    queryKey: ["reports-options", scope],
    enabled: isReportDateRangeValid(filters.startDate, filters.endDate),
    queryFn: () => getReportsOptions(scope),
    staleTime: 5 * 60_000,
    refetchInterval: false,
    refetchOnWindowFocus: false,
    retry: false,
  });
}
