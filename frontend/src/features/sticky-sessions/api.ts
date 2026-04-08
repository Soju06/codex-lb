import { del, get, post } from "@/lib/api-client";

import {
  StickySessionIdentifierSchema,
  StickySessionsDeleteFilteredRequestSchema,
  StickySessionsDeleteFilteredResponseSchema,
  StickySessionsDeleteRequestSchema,
  StickySessionsDeleteResponseSchema,
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
  if (validated.accountQuery) {
    searchParams.set("accountQuery", validated.accountQuery);
  }
  if (validated.keyQuery) {
    searchParams.set("keyQuery", validated.keyQuery);
  }
  if (validated.providerKind) {
    searchParams.set("providerKind", validated.providerKind);
  }
  searchParams.set("sortBy", validated.sortBy);
  searchParams.set("sortDir", validated.sortDir);
  return get(`${STICKY_SESSIONS_PATH}?${searchParams.toString()}`, StickySessionsListResponseSchema);
}

export function deleteStickySession(payload: unknown) {
  const validated = StickySessionIdentifierSchema.parse(payload);
  const searchParams = new URLSearchParams({
    providerKind: validated.providerKind,
  });
  return del(`${STICKY_SESSIONS_PATH}/${validated.kind}/${encodeURIComponent(validated.key)}?${searchParams.toString()}`);
}

export function deleteStickySessions(payload: unknown) {
  const validated = StickySessionsDeleteRequestSchema.parse(payload);
  return post(`${STICKY_SESSIONS_PATH}/delete`, StickySessionsDeleteResponseSchema, {
    body: validated,
  });
}

export function deleteFilteredStickySessions(payload: unknown) {
  const validated = StickySessionsDeleteFilteredRequestSchema.parse(payload);
  return post(`${STICKY_SESSIONS_PATH}/delete-filtered`, StickySessionsDeleteFilteredResponseSchema, {
    body: validated,
  });
}

export function purgeStickySessions(payload: unknown) {
  const validated = StickySessionsPurgeRequestSchema.parse(payload);
  return post(`${STICKY_SESSIONS_PATH}/purge`, StickySessionsPurgeResponseSchema, {
    body: validated,
  });
}
