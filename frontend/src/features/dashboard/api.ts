import { get } from "@/lib/api-client";

import {
  DashboardOverviewSchema,
  RequestLogFilterOptionsSchema,
  RequestLogsResponseSchema,
} from "@/features/dashboard/schemas";

const DASHBOARD_PATH = "/api/dashboard";
const REQUEST_LOGS_PATH = "/api/request-logs";

export type DashboardOverviewParams = {
  ownerUserId?: string;
};

export type RequestLogsListFilters = {
  limit?: number;
  offset?: number;
  search?: string;
  ownerUserId?: string;
  accountIds?: string[];
  statuses?: string[];
  modelOptions?: string[];
  since?: string;
  until?: string;
};

export type RequestLogFacetFilters = {
  ownerUserId?: string;
  since?: string;
  until?: string;
  accountIds?: string[];
  modelOptions?: string[];
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
  return getDashboardOverviewWithParams();
}

export function getDashboardOverviewWithParams(params: DashboardOverviewParams = {}) {
  const query = new URLSearchParams();
  if (params.ownerUserId) {
    query.set("ownerUserId", params.ownerUserId);
  }
  const suffix = query.size > 0 ? `?${query.toString()}` : "";
  return get(`${DASHBOARD_PATH}/overview${suffix}`, DashboardOverviewSchema);
}

export function getRequestLogs(params: RequestLogsListFilters = {}) {
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
  if (params.ownerUserId) {
    query.set("ownerUserId", params.ownerUserId);
  }
  appendMany(query, "accountId", params.accountIds);
  appendMany(query, "status", params.statuses);
  appendMany(query, "modelOption", params.modelOptions);
  if (params.since) {
    query.set("since", params.since);
  }
  if (params.until) {
    query.set("until", params.until);
  }
  const suffix = query.size > 0 ? `?${query.toString()}` : "";
  return get(`${REQUEST_LOGS_PATH}${suffix}`, RequestLogsResponseSchema);
}

export function getRequestLogOptions(params: RequestLogFacetFilters = {}) {
  const query = new URLSearchParams();
  if (params.ownerUserId) {
    query.set("ownerUserId", params.ownerUserId);
  }
  if (params.since) {
    query.set("since", params.since);
  }
  if (params.until) {
    query.set("until", params.until);
  }
  appendMany(query, "accountId", params.accountIds);
  appendMany(query, "modelOption", params.modelOptions);
  const suffix = query.size > 0 ? `?${query.toString()}` : "";
  return get(`${REQUEST_LOGS_PATH}/options${suffix}`, RequestLogFilterOptionsSchema);
}
