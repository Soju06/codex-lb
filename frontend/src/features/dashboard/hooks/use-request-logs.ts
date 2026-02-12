import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";

import { getRequestLogOptions, getRequestLogs } from "@/features/dashboard/api";
import { FilterStateSchema, type FilterState } from "@/features/dashboard/schemas";

const DEFAULT_FILTER_STATE: FilterState = {
  search: "",
  timeframe: "all",
  accountIds: [],
  modelOptions: [],
  statuses: [],
  limit: 25,
  offset: 0,
};

function parseNumber(value: string | null, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function parseFilterState(params: URLSearchParams): FilterState {
  const candidate = {
    search: params.get("search") ?? "",
    timeframe: params.get("timeframe") ?? "all",
    accountIds: params.getAll("accountId"),
    modelOptions: params.getAll("modelOption"),
    statuses: params.getAll("status"),
    limit: parseNumber(params.get("limit"), DEFAULT_FILTER_STATE.limit),
    offset: parseNumber(params.get("offset"), DEFAULT_FILTER_STATE.offset),
  };
  const parsed = FilterStateSchema.safeParse(candidate);
  if (parsed.success) {
    return parsed.data;
  }
  return DEFAULT_FILTER_STATE;
}

function writeFilterState(state: FilterState): URLSearchParams {
  const params = new URLSearchParams();
  if (state.search) {
    params.set("search", state.search);
  }
  if (state.timeframe !== "all") {
    params.set("timeframe", state.timeframe);
  }
  for (const value of state.accountIds) {
    params.append("accountId", value);
  }
  for (const value of state.modelOptions) {
    params.append("modelOption", value);
  }
  for (const value of state.statuses) {
    params.append("status", value);
  }
  params.set("limit", String(state.limit));
  params.set("offset", String(state.offset));
  return params;
}

function timeframeToSinceIso(timeframe: FilterState["timeframe"]): string | undefined {
  if (timeframe === "all") {
    return undefined;
  }
  const now = Date.now();
  const lookup: Record<Exclude<FilterState["timeframe"], "all">, number> = {
    "1h": 60 * 60 * 1000,
    "24h": 24 * 60 * 60 * 1000,
    "7d": 7 * 24 * 60 * 60 * 1000,
  };
  return new Date(now - lookup[timeframe]).toISOString();
}

export function useRequestLogs() {
  const [searchParams, setSearchParams] = useSearchParams();

  const filters = useMemo(() => parseFilterState(searchParams), [searchParams]);
  const since = useMemo(() => timeframeToSinceIso(filters.timeframe), [filters.timeframe]);

  const logsQuery = useQuery({
    queryKey: ["dashboard", "request-logs", filters, since],
    queryFn: () =>
      getRequestLogs({
        search: filters.search || undefined,
        limit: filters.limit,
        offset: filters.offset,
        accountIds: filters.accountIds,
        statuses: filters.statuses,
        modelOptions: filters.modelOptions,
        since,
      }),
    refetchInterval: 30_000,
  });

  const optionsQuery = useQuery({
    queryKey: ["dashboard", "request-log-options", filters.timeframe, since],
    queryFn: () =>
      getRequestLogOptions({
        since,
        statuses: filters.statuses,
      }),
  });

  const updateFilters = (patch: Partial<FilterState>) => {
    const nextState: FilterState = {
      ...filters,
      ...patch,
    };
    setSearchParams(writeFilterState(nextState));
  };

  return {
    filters,
    logsQuery,
    optionsQuery,
    updateFilters,
  };
}
