import type { QueryClient } from "@tanstack/react-query";

import { hasPermission, useAuthStore } from "@/features/auth/hooks/use-auth";
import { queryClient as defaultQueryClient } from "@/lib/query-client";

/**
 * Query keys whose data the backend serves only to principals with the coarse
 * `write` permission. Pages already stop fetching and rendering them without
 * write access, but `enabled: false` leaves earlier admin responses in the
 * cache; these are removed the moment write access is lost (logout into a
 * passwordless guest session, guest login within gcTime, session downgrade).
 */
export const WRITE_ONLY_QUERY_KEYS: readonly (readonly string[])[] = [
  ["settings", "upstream-proxy"],
  ["api-keys"],
  ["sticky-sessions"],
];

export function evictWriteOnlyQueries(client: QueryClient = defaultQueryClient): void {
  for (const queryKey of WRITE_ONLY_QUERY_KEYS) {
    client.removeQueries({ queryKey: [...queryKey] });
  }
}

/**
 * Query keys served only to `users:manage` holders (the people/roles lists).
 * Removed when that permission is lost or when a different account signs in,
 * so one account never sees a list fetched by another.
 */
export const USERS_MANAGE_QUERY_KEYS: readonly (readonly string[])[] = [["dashboard-users"], ["dashboard-roles"]];

export function evictUsersManageQueries(client: QueryClient = defaultQueryClient): void {
  for (const queryKey of USERS_MANAGE_QUERY_KEYS) {
    client.removeQueries({ queryKey: [...queryKey] });
  }
}

/** Installs the access watchers; returns the unsubscribe function. */
export function installAccessCacheEviction(client: QueryClient = defaultQueryClient): () => void {
  return useAuthStore.subscribe((state, previous) => {
    if (previous.canWrite && !state.canWrite) {
      evictWriteOnlyQueries(client);
    }
    const lostUsersManage =
      hasPermission(previous.permissions, "users:manage") && !hasPermission(state.permissions, "users:manage");
    const accountChanged = (previous.user?.id ?? null) !== (state.user?.id ?? null);
    if (lostUsersManage || accountChanged) {
      evictUsersManageQueries(client);
    }
  });
}
