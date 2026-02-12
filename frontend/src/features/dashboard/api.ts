import { get } from "@/lib/api-client";

import {
  DashboardOverviewSchema,
  RequestLogFilterOptionsSchema,
  RequestLogsResponseSchema,
} from "@/features/dashboard/schemas";

type RequestLogsParams = {
  limit?: number;
  offset?: number;
  search?: string;
  accountIds?: string[];
  statuses?: string[];
  models?: string[];
  reasoningEfforts?: string[];
  modelOptions?: string[];
  since?: string;
  until?: string;
};

function appendMany(params: URLSearchParams, key: string, values?: string[]): void {
  if (!values || values.length === 0) {
    return;
  }
  for (const value of values) {
    if (value) {
      params.append(key, value);
    }
  }
}

export function getDashboardOverview() {
  return get("/api/dashboard/overview", DashboardOverviewSchema);
}

export function getRequestLogs(params: RequestLogsParams = {}) {
  const query = new URLSearchParams();
  if (typeof params.limit === "number") {
    query.set("limit", String(params.limit));
  }
  if (typeof params.offset === "number") {
    query.set("offset", String(params.offset));
  }
  if (params.search) {
    query.set("search", params.search);
  }
  appendMany(query, "accountId", params.accountIds);
  appendMany(query, "status", params.statuses);
  appendMany(query, "model", params.models);
  appendMany(query, "reasoningEffort", params.reasoningEfforts);
  appendMany(query, "modelOption", params.modelOptions);
  if (params.since) {
    query.set("since", params.since);
  }
  if (params.until) {
    query.set("until", params.until);
  }
  const suffix = query.size > 0 ? `?${query.toString()}` : "";
  return get(`/api/request-logs${suffix}`, RequestLogsResponseSchema);
}

export function getRequestLogOptions(params?: { since?: string; until?: string; statuses?: string[] }) {
  const query = new URLSearchParams();
  if (params?.since) {
    query.set("since", params.since);
  }
  if (params?.until) {
    query.set("until", params.until);
  }
  appendMany(query, "status", params?.statuses);
  const suffix = query.size > 0 ? `?${query.toString()}` : "";
  return get(`/api/request-logs/options${suffix}`, RequestLogFilterOptionsSchema);
}
