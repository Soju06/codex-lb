import { useQuery } from "@tanstack/react-query";
import { getReports, getReportsOptions, getThreadIdentity } from "../api";
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

// Raw request-log scans, so the query only runs while the card is visible and
// its result is held far longer than the cost report's.
const THREAD_IDENTITY_STALE_TIME_MS = 5 * 60_000;

export function useThreadIdentity(
  filters: Pick<ReportsFilterState, "startDate" | "endDate">,
  timeZone: string | undefined,
  enabled: boolean,
) {
  const scope = {
    startDate: filters.startDate,
    endDate: filters.endDate,
    timezone: timeZone,
  };
  return useQuery({
    enabled: enabled && isReportDateRangeValid(filters.startDate, filters.endDate),
    queryKey: ["reports-thread-identity", scope],
    queryFn: () => getThreadIdentity(scope),
    staleTime: THREAD_IDENTITY_STALE_TIME_MS,
    refetchInterval: false,
    refetchOnWindowFocus: false,
    retry: false,
  });
}
