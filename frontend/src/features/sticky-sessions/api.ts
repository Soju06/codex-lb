import { del, get, post } from "@/lib/api-client";

import {
  StickySessionDeleteResponseSchema,
  StickySessionIdentifierSchema,
  StickySessionsListResponseSchema,
  StickySessionsPurgeRequestSchema,
  StickySessionsPurgeResponseSchema,
} from "@/features/sticky-sessions/schemas";

const STICKY_SESSIONS_PATH = "/api/sticky-sessions";

export function listStickySessions() {
  return get(STICKY_SESSIONS_PATH, StickySessionsListResponseSchema);
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
