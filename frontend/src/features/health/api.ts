import { get } from "@/lib/api-client";

import { HealthCheckResponseSchema } from "@/features/health/schemas";

const READINESS_PATH = "/health/ready";

export function getServiceReadiness() {
  return get(READINESS_PATH, HealthCheckResponseSchema);
}
