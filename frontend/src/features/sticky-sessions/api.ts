import { del, get, post } from "@/lib/api-client";

import {
  StickySessionDeleteResponseSchema,
  StickySessionIdentifierSchema,
  StickySessionsListParamsSchema,
  StickySessionsListResponseSchema,
  StickySessionsPurgeRequestSchema,
  StickySessionsPurgeResponseSchema,
} from "@/features/sticky-sessions/schemas";

const STICKY_SESSIONS_PATH = "/api/sticky-sessions";

export function listStickySessions(params: unknown) {
  const validated = StickySessionsListParamsSchema.parse(params);
  const searchParams = new URLSearchParams({
    staleOnly: String(validated.staleOnly),
    offset: String(validated.offset),
    limit: String(validated.limit),
  });
  return get(`${STICKY_SESSIONS_PATH}?${searchParams.toString()}`, StickySessionsListResponseSchema);
}

export function deleteStickySession(payload: unknown) {
  const validated = StickySessionIdentifierSchema.parse(payload);
  return del(
    `${STICKY_SESSIONS_PATH}/${validated.kind}/${encodeURIComponent(validated.key)}`,
    StickySessionDeleteResponseSchema,
  );
}

export function purgeStickySessions(payload: unknown) {
  const validated = StickySessionsPurgeRequestSchema.parse(payload);
  return post(`${STICKY_SESSIONS_PATH}/purge`, StickySessionsPurgeResponseSchema, {
    body: validated,
  });
}
