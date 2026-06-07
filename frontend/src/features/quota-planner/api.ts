import { get, post, put } from "@/lib/api-client";

import {
  QuotaPlannerDecisionSchema,
  QuotaPlannerForecastSchema,
  QuotaPlannerSettingsSchema,
  QuotaPlannerSettingsUpdateRequestSchema,
  QuotaPlannerWarmNowRequestSchema,
  QuotaPlannerWarmupActionResponseSchema,
} from "@/features/quota-planner/schemas";
import { z } from "zod";

const QUOTA_PLANNER_PATH = "/api/quota-planner";

export function getQuotaPlannerSettings() {
  return get(`${QUOTA_PLANNER_PATH}/settings`, QuotaPlannerSettingsSchema);
}

export function updateQuotaPlannerSettings(payload: unknown) {
  const validated = QuotaPlannerSettingsUpdateRequestSchema.parse(payload);
  return put(`${QUOTA_PLANNER_PATH}/settings`, QuotaPlannerSettingsSchema, { body: validated });
}

export function listQuotaPlannerDecisions(limit = 20) {
  const searchParams = new URLSearchParams({ limit: String(limit) });
  return get(`${QUOTA_PLANNER_PATH}/decisions?${searchParams.toString()}`, z.array(QuotaPlannerDecisionSchema));
}

export function getQuotaPlannerForecast(horizonHours = 36) {
  const searchParams = new URLSearchParams({ horizonHours: String(horizonHours) });
  return get(`${QUOTA_PLANNER_PATH}/forecast?${searchParams.toString()}`, QuotaPlannerForecastSchema);
}

export function warmQuotaPlannerAccount(payload: unknown) {
  const validated = QuotaPlannerWarmNowRequestSchema.parse(payload);
  return post(`${QUOTA_PLANNER_PATH}/warm-now`, QuotaPlannerWarmupActionResponseSchema, { body: validated });
}

export function cancelQuotaPlannerDecision(decisionId: string) {
  return post(`${QUOTA_PLANNER_PATH}/decisions/${encodeURIComponent(decisionId)}/cancel`, QuotaPlannerWarmupActionResponseSchema);
}
