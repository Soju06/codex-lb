import { get } from "@/lib/api-client";
import {
  KeyDashboardProfileSchema,
  KeyDashboardRequestLogsResponseSchema,
  KeyUsageSchema,
  type KeyDashboardProfile,
  type KeyDashboardRequestLogsResponse,
  type KeyUsage,
} from "@/features/key-dashboard/schemas";

function keyRequestOptions(apiKey: string) {
  return {
    headers: { Authorization: `Bearer ${apiKey}` },
    credentials: "omit" as const,
    cache: "no-store" as const,
    suppressUnauthorizedHandler: true,
  };
}

export function getKeyUsage(apiKey: string): Promise<KeyUsage> {
  return get("/v1/usage", KeyUsageSchema, keyRequestOptions(apiKey));
}

export function getKeyDashboardProfile(apiKey: string): Promise<KeyDashboardProfile> {
  return get(
    "/api/key-dashboard/profile",
    KeyDashboardProfileSchema,
    keyRequestOptions(apiKey),
  );
}

export function getKeyDashboardRequestLogs(
  apiKey: string,
  limit: number,
  offset: number,
): Promise<KeyDashboardRequestLogsResponse> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  return get(
    `/api/key-dashboard/request-logs?${params.toString()}`,
    KeyDashboardRequestLogsResponseSchema,
    keyRequestOptions(apiKey),
  );
}

export async function getKeyDashboardData(
  apiKey: string,
  limit: number,
  offset: number,
): Promise<{
  profile: KeyDashboardProfile;
  usage: KeyUsage;
  logs: KeyDashboardRequestLogsResponse;
}> {
  const [profile, usage, logs] = await Promise.all([
    getKeyDashboardProfile(apiKey),
    getKeyUsage(apiKey),
    getKeyDashboardRequestLogs(apiKey, limit, offset),
  ]);
  return { profile, usage, logs };
}
