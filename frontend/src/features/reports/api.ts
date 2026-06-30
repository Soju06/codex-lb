import { get } from "@/lib/api-client";
import { ReportsResponseSchema } from "./schemas";

export type ReportsParams = {
  startDate?: string;
  endDate?: string;
  accountId?: string[];
  model?: string;
  useragent?: string;
  timezone?: string;
};

export function getReports(params: ReportsParams = {}) {
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
  const suffix = query.size > 0 ? `?${query.toString()}` : "";
  return get(`/api/reports${suffix}`, ReportsResponseSchema);
}
