import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";

import {
  getAutomationJobOptions,
  getAutomationRunOptions,
  listAutomations,
  listAutomationRunsPage,
  type AutomationJobsListFilters,
  type AutomationRunsListFilters,
} from "@/features/automations/api";

export type AutomationJobsFilterState = {
  search: string;
  accountIds: string[];
  models: string[];
  statuses: string[];
  scheduleTypes: string[];
  limit: number;
  offset: number;
};

export type AutomationRunsFilterState = {
  search: string;
  accountIds: string[];
  models: string[];
  statuses: string[];
  triggers: string[];
  limit: number;
  offset: number;
};

const DEFAULT_JOBS_FILTER_STATE: AutomationJobsFilterState = {
  search: "",
  accountIds: [],
  models: [],
  statuses: [],
  scheduleTypes: [],
  limit: 25,
  offset: 0,
};

const DEFAULT_RUNS_FILTER_STATE: AutomationRunsFilterState = {
  search: "",
  accountIds: [],
  models: [],
  statuses: [],
  triggers: [],
  limit: 25,
  offset: 0,
};

const JOBS_PARAM_KEYS = [
  "jobsSearch",
  "jobsAccountId",
  "jobsModel",
  "jobsStatus",
  "jobsScheduleType",
  "jobsLimit",
  "jobsOffset",
] as const;

const RUNS_PARAM_KEYS = [
  "runsSearch",
  "runsAccountId",
  "runsModel",
  "runsStatus",
  "runsTrigger",
  "runsLimit",
  "runsOffset",
] as const;

function parseNumber(value: string | null, fallback: number): number {
  if (value === null) {
    return fallback;
  }
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.max(0, Math.trunc(parsed));
}

function parseJobsState(params: URLSearchParams): AutomationJobsFilterState {
  return {
    search: params.get("jobsSearch") ?? "",
    accountIds: params.getAll("jobsAccountId"),
    models: params.getAll("jobsModel"),
    statuses: params.getAll("jobsStatus"),
    scheduleTypes: params.getAll("jobsScheduleType"),
    limit: Math.max(1, parseNumber(params.get("jobsLimit"), DEFAULT_JOBS_FILTER_STATE.limit)),
    offset: parseNumber(params.get("jobsOffset"), DEFAULT_JOBS_FILTER_STATE.offset),
  };
}

function parseRunsState(params: URLSearchParams): AutomationRunsFilterState {
  return {
    search: params.get("runsSearch") ?? "",
    accountIds: params.getAll("runsAccountId"),
    models: params.getAll("runsModel"),
    statuses: params.getAll("runsStatus"),
    triggers: params.getAll("runsTrigger"),
    limit: Math.max(1, parseNumber(params.get("runsLimit"), DEFAULT_RUNS_FILTER_STATE.limit)),
    offset: parseNumber(params.get("runsOffset"), DEFAULT_RUNS_FILTER_STATE.offset),
  };
}

function writeJobsState(state: AutomationJobsFilterState, base: URLSearchParams): URLSearchParams {
  const params = new URLSearchParams(base);
  for (const key of JOBS_PARAM_KEYS) {
    params.delete(key);
  }
  if (state.search) {
    params.set("jobsSearch", state.search);
  }
  for (const value of state.accountIds) {
    params.append("jobsAccountId", value);
  }
  for (const value of state.models) {
    params.append("jobsModel", value);
  }
  for (const value of state.statuses) {
    params.append("jobsStatus", value);
  }
  for (const value of state.scheduleTypes) {
    params.append("jobsScheduleType", value);
  }
  params.set("jobsLimit", String(state.limit));
  params.set("jobsOffset", String(state.offset));
  return params;
}

function writeRunsState(state: AutomationRunsFilterState, base: URLSearchParams): URLSearchParams {
  const params = new URLSearchParams(base);
  for (const key of RUNS_PARAM_KEYS) {
    params.delete(key);
  }
  if (state.search) {
    params.set("runsSearch", state.search);
  }
  for (const value of state.accountIds) {
    params.append("runsAccountId", value);
  }
  for (const value of state.models) {
    params.append("runsModel", value);
  }
  for (const value of state.statuses) {
    params.append("runsStatus", value);
  }
  for (const value of state.triggers) {
    params.append("runsTrigger", value);
  }
  params.set("runsLimit", String(state.limit));
  params.set("runsOffset", String(state.offset));
  return params;
}

export function useAutomationListing() {
  const [searchParams, setSearchParams] = useSearchParams();
  const jobsFilters = useMemo(() => parseJobsState(searchParams), [searchParams]);
  const runsFilters = useMemo(() => parseRunsState(searchParams), [searchParams]);

  const jobsListFilters = useMemo<AutomationJobsListFilters>(
    () => ({
      limit: jobsFilters.limit,
      offset: jobsFilters.offset,
      search: jobsFilters.search || undefined,
      accountIds: jobsFilters.accountIds,
      models: jobsFilters.models,
      statuses: jobsFilters.statuses,
      scheduleTypes: jobsFilters.scheduleTypes,
    }),
    [jobsFilters],
  );
  const runsListFilters = useMemo<AutomationRunsListFilters>(
    () => ({
      limit: runsFilters.limit,
      offset: runsFilters.offset,
      search: runsFilters.search || undefined,
      accountIds: runsFilters.accountIds,
      models: runsFilters.models,
      statuses: runsFilters.statuses,
      triggers: runsFilters.triggers,
    }),
    [runsFilters],
  );

  const jobsQuery = useQuery({
    queryKey: ["automations", "jobs", jobsListFilters],
    queryFn: () => listAutomations(jobsListFilters),
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
    placeholderData: keepPreviousData,
  });

  const runsQuery = useQuery({
    queryKey: ["automations", "runs", runsListFilters],
    queryFn: () => listAutomationRunsPage(runsListFilters),
    refetchInterval: 15_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
    placeholderData: keepPreviousData,
  });

  const jobOptionsQuery = useQuery({
    queryKey: [
      "automations",
      "jobs-options",
      jobsFilters.search,
      jobsFilters.accountIds,
      jobsFilters.models,
      jobsFilters.statuses,
      jobsFilters.scheduleTypes,
    ],
    queryFn: () =>
      getAutomationJobOptions({
        search: jobsFilters.search || undefined,
        accountIds: jobsFilters.accountIds,
        models: jobsFilters.models,
        statuses: jobsFilters.statuses,
        scheduleTypes: jobsFilters.scheduleTypes,
      }),
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
  });

  const runOptionsQuery = useQuery({
    queryKey: [
      "automations",
      "runs-options",
      runsFilters.search,
      runsFilters.accountIds,
      runsFilters.models,
      runsFilters.statuses,
      runsFilters.triggers,
    ],
    queryFn: () =>
      getAutomationRunOptions({
        search: runsFilters.search || undefined,
        accountIds: runsFilters.accountIds,
        models: runsFilters.models,
        statuses: runsFilters.statuses,
        triggers: runsFilters.triggers,
      }),
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
  });

  const updateJobsFilters = (patch: Partial<AutomationJobsFilterState>) => {
    setSearchParams((current) => {
      const next = {
        ...parseJobsState(current),
        ...patch,
      };
      return writeJobsState(next, current);
    });
  };

  const updateRunsFilters = (patch: Partial<AutomationRunsFilterState>) => {
    setSearchParams((current) => {
      const next = {
        ...parseRunsState(current),
        ...patch,
      };
      return writeRunsState(next, current);
    });
  };

  const resetJobsFilters = () => {
    setSearchParams((current) =>
      writeJobsState(DEFAULT_JOBS_FILTER_STATE, current),
    );
  };

  const resetRunsFilters = () => {
    setSearchParams((current) =>
      writeRunsState(DEFAULT_RUNS_FILTER_STATE, current),
    );
  };

  return {
    jobsFilters,
    runsFilters,
    jobsListFilters,
    runsListFilters,
    jobsQuery,
    runsQuery,
    jobOptionsQuery,
    runOptionsQuery,
    updateJobsFilters,
    updateRunsFilters,
    resetJobsFilters,
    resetRunsFilters,
  };
}
