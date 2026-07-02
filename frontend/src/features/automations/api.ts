import { del, get, patch, post } from "@/lib/api-client";

import {
  AutomationCreateRequestSchema,
  AutomationDeleteResponseSchema,
  AutomationJobFilterOptionsSchema,
  AutomationRunDetailsSchema,
  AutomationJobSchema,
  AutomationRunFilterOptionsSchema,
  AutomationRunSchema,
  AutomationRunsListResponseSchema,
  AutomationUpdateRequestSchema,
  AutomationsListResponseSchema,
} from "@/features/automations/schemas";

const AUTOMATIONS_PATH = "/api/automations";

export type AutomationJobsListFilters = {
  limit?: number;
  offset?: number;
  search?: string;
  accountIds?: string[];
  models?: string[];
  statuses?: string[];
  scheduleTypes?: string[];
};

export type AutomationRunsListFilters = {
  limit?: number;
  offset?: number;
  search?: string;
  accountIds?: string[];
  models?: string[];
  statuses?: string[];
  triggers?: string[];
  automationIds?: string[];
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

function withQuery(path: string, query: URLSearchParams): string {
  return query.size > 0 ? `${path}?${query.toString()}` : path;
}

export function listAutomations(params: AutomationJobsListFilters = {}) {
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
  appendMany(query, "model", params.models);
  appendMany(query, "status", params.statuses);
  appendMany(query, "scheduleType", params.scheduleTypes);
  return get(withQuery(AUTOMATIONS_PATH, query), AutomationsListResponseSchema);
}

export function getAutomationJobOptions(params: Omit<AutomationJobsListFilters, "limit" | "offset"> = {}) {
  const query = new URLSearchParams();
  if (params.search) {
    query.set("search", params.search);
  }
  appendMany(query, "accountId", params.accountIds);
  appendMany(query, "model", params.models);
  appendMany(query, "status", params.statuses);
  appendMany(query, "scheduleType", params.scheduleTypes);
  return get(withQuery(`${AUTOMATIONS_PATH}/options`, query), AutomationJobFilterOptionsSchema);
}

export function listAutomationRunsPage(params: AutomationRunsListFilters = {}) {
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
  appendMany(query, "model", params.models);
  appendMany(query, "status", params.statuses);
  appendMany(query, "trigger", params.triggers);
  appendMany(query, "automationId", params.automationIds);
  return get(withQuery(`${AUTOMATIONS_PATH}/runs`, query), AutomationRunsListResponseSchema);
}

export function getAutomationRunOptions(params: Omit<AutomationRunsListFilters, "limit" | "offset"> = {}) {
  const query = new URLSearchParams();
  if (params.search) {
    query.set("search", params.search);
  }
  appendMany(query, "accountId", params.accountIds);
  appendMany(query, "model", params.models);
  appendMany(query, "status", params.statuses);
  appendMany(query, "trigger", params.triggers);
  appendMany(query, "automationId", params.automationIds);
  return get(withQuery(`${AUTOMATIONS_PATH}/runs/options`, query), AutomationRunFilterOptionsSchema);
}

export function getAutomationRunDetails(runId: string) {
  return get(`${AUTOMATIONS_PATH}/runs/${runId}/details`, AutomationRunDetailsSchema);
}

export function createAutomation(payload: unknown) {
  const validated = AutomationCreateRequestSchema.parse(payload);
  return post(AUTOMATIONS_PATH, AutomationJobSchema, { body: validated });
}

export function updateAutomation(automationId: string, payload: unknown) {
  const validated = AutomationUpdateRequestSchema.parse(payload);
  return patch(`${AUTOMATIONS_PATH}/${automationId}`, AutomationJobSchema, { body: validated });
}

export function deleteAutomation(automationId: string) {
  return del(`${AUTOMATIONS_PATH}/${automationId}`, AutomationDeleteResponseSchema);
}

export function runAutomationNow(automationId: string) {
  return post(`${AUTOMATIONS_PATH}/${automationId}/run-now`, AutomationRunSchema);
}

export function listAutomationRuns(automationId: string, limit = 20) {
  return get(`${AUTOMATIONS_PATH}/${automationId}/runs?limit=${limit}`, AutomationRunsListResponseSchema);
}
