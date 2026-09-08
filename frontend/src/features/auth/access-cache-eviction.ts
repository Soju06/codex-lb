import type { QueryClient } from "@tanstack/react-query";

import { useAuthStore } from "@/features/auth/hooks/use-auth";
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

/** Installs the write-access watcher; returns the unsubscribe function. */
export function installAccessCacheEviction(client: QueryClient = defaultQueryClient): () => void {
  return useAuthStore.subscribe((state, previous) => {
    if (previous.canWrite && !state.canWrite) {
      evictWriteOnlyQueries(client);
    }
  });
}
