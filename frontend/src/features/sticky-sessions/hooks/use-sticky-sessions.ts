import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useDeferredValue, useMemo, useState } from "react";
import { toast } from "sonner";

import {
  deleteFilteredStickySessions,
  deleteStickySessions,
  listStickySessions,
  purgeStickySessions,
} from "@/features/sticky-sessions/api";
import type {
  StickySessionIdentifier,
  StickySessionProviderKind,
  StickySessionSortBy,
  StickySessionSortDir,
  StickySessionsDeleteFilteredResponse,
  StickySessionsDeleteResponse,
  StickySessionsListParams,
} from "@/features/sticky-sessions/schemas";

const DEFAULT_STICKY_SESSIONS_LIMIT = 10;

export function useStickySessions() {
  const queryClient = useQueryClient();
  const [params, setParams] = useState<StickySessionsListParams>({
    staleOnly: false,
    providerKind: null,
    accountQuery: "",
    keyQuery: "",
    sortBy: "updated_at",
    sortDir: "desc",
    offset: 0,
    limit: DEFAULT_STICKY_SESSIONS_LIMIT,
  });
  const deferredAccountQuery = useDeferredValue(params.accountQuery);
  const deferredKeyQuery = useDeferredValue(params.keyQuery);
  const queryParams = useMemo(
    () => ({
      ...params,
      accountQuery: deferredAccountQuery,
      keyQuery: deferredKeyQuery,
    }),
    [deferredAccountQuery, deferredKeyQuery, params],
  );

  const stickySessionsQuery = useQuery({
    queryKey: ["sticky-sessions", "list", queryParams],
    queryFn: () => listStickySessions(queryParams),
    placeholderData: (previousData) => previousData,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["sticky-sessions", "list"] });
  };

  const setOffset = (offset: number) => {
    setParams((current) => ({ ...current, offset }));
  };

  const setLimit = (limit: number) => {
    setParams((current) => ({ ...current, limit, offset: 0 }));
  };

  const setAccountQuery = (accountQuery: string) => {
    setParams((current) => ({ ...current, accountQuery, offset: 0 }));
  };

  const setKeyQuery = (keyQuery: string) => {
    setParams((current) => ({ ...current, keyQuery, offset: 0 }));
  };

  const setProviderKind = (providerKind: StickySessionProviderKind | null) => {
    setParams((current) => ({ ...current, providerKind, offset: 0 }));
  };

  const setSort = (sortBy: StickySessionSortBy, sortDir: StickySessionSortDir) => {
    setParams((current) => ({ ...current, sortBy, sortDir, offset: 0 }));
  };

  const deleteMutation = useMutation({
    mutationFn: (targets: StickySessionIdentifier[]) => deleteStickySessions({ sessions: targets }),
    onSuccess: async (response: StickySessionsDeleteResponse) => {
      if (response.deletedCount > 0 && response.failed.length === 0) {
        toast.success(response.deletedCount === 1 ? "Sticky session deleted" : `Deleted ${response.deletedCount} sessions`);
      } else if (response.deletedCount > 0) {
        toast.warning(
          `Deleted ${response.deletedCount} sessions. ${response.failed.length} could not be deleted.`,
        );
      } else {
        toast.error("No selected sessions could be deleted");
      }
      await invalidate();
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to delete sticky sessions");
    },
  });

  const deleteFilteredMutation = useMutation({
    mutationFn: () =>
      deleteFilteredStickySessions({
        staleOnly: queryParams.staleOnly,
        providerKind: queryParams.providerKind,
        accountQuery: queryParams.accountQuery,
        keyQuery: queryParams.keyQuery,
      }),
    onSuccess: async (response: StickySessionsDeleteFilteredResponse) => {
      if (response.deletedCount > 0) {
        toast.success(
          response.deletedCount === 1 ? "Filtered sticky session deleted" : `Deleted ${response.deletedCount} filtered sessions`,
        );
      } else {
        toast.error("No filtered sessions could be deleted");
      }
      await invalidate();
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to delete filtered sticky sessions");
    },
  });

  const purgeMutation = useMutation({
    mutationFn: (staleOnly: boolean) => purgeStickySessions({ staleOnly }),
    onSuccess: (response) => {
      toast.success(`Purged ${response.deletedCount} sticky sessions`);
      invalidate();
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to purge sticky sessions");
    },
  });

  return {
    params,
    setAccountQuery,
    setKeyQuery,
    setProviderKind,
    setSort,
    setOffset,
    setLimit,
    stickySessionsQuery,
    deleteMutation,
    deleteFilteredMutation,
    purgeMutation,
  };
}
