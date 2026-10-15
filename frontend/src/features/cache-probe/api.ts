import { get, post } from "@/lib/api-client";

import {
  CacheProbePlanSchema,
  CacheProbeRunRequestSchema,
  CacheProbeRunSchema,
} from "@/features/cache-probe/schemas";

const CACHE_PROBE_PATH = "/api/diagnostics/cache-isolation-probe";

export function getCacheProbePlan() {
  return get(CACHE_PROBE_PATH, CacheProbePlanSchema);
}

export function runCacheProbe(payload: unknown) {
  // `confirm` is a literal `true` in the schema, so an unconfirmed run cannot
  // be sent by accident from here either.
  const validated = CacheProbeRunRequestSchema.parse(payload);
  return post(`${CACHE_PROBE_PATH}/run`, CacheProbeRunSchema, { body: validated });
}
