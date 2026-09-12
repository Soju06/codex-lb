import { get } from "@/lib/api-client";
import {
  ReportsResponseSchema,
  ReportsOptionsResponseSchema,
  ThreadIdentityResponseSchema,
} from "./schemas";

export type ReportsParams = {
  startDate?: string;
  endDate?: string;
  accountId?: string[];
  apiKeyId?: string[];
  model?: string;
  useragent?: string;
  timezone?: string;
};

function reportQuery(params: ReportsParams): string {
  const query = new URLSearchParams();
  if (params.startDate) query.set("start_date", params.startDate);
  if (params.endDate) query.set("end_date", params.endDate);
  if (params.model) query.set("model", params.model);
  if (params.useragent) query.set("useragent_group", params.useragent);
  if (params.timezone) query.set("timezone", params.timezone);
  if (params.accountId) {
    for (const id of params.accountId) {
      query.append("account_id", id);
    }
  }
  if (params.apiKeyId) {
    for (const id of params.apiKeyId) {
      query.append("api_key_id", id);
    }
  }
  const suffix = query.size > 0 ? `?${query.toString()}` : "";
  return suffix;
}

export function getReports(params: ReportsParams = {}) {
  return get(`/api/reports${reportQuery(params)}`, ReportsResponseSchema);
}

export function getReportsOptions(params: Omit<ReportsParams, "model" | "useragent"> = {}) {
  return get(`/api/reports/options${reportQuery(params)}`, ReportsOptionsResponseSchema);
}

// Thread identity is deliberately scoped by the date range alone: an account
// or model filter would change what "accounts per conversation" means.
export function getThreadIdentity(
  params: Pick<ReportsParams, "startDate" | "endDate" | "timezone"> = {},
) {
  return get(`/api/reports/thread-identity${reportQuery(params)}`, ThreadIdentityResponseSchema);
}
