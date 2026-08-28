import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useDeferredValue, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import {
  deleteFilteredStickySessions,
  deleteStickySessions,
  listStickySessions,
  purgeStickySessions,
} from "@/features/sticky-sessions/api";
import type {
  StickySessionIdentifier,
  StickySessionSortBy,
  StickySessionSortDir,
  StickySessionsDeleteFilteredResponse,
  StickySessionsDeleteResponse,
  StickySessionsListParams,
} from "@/features/sticky-sessions/schemas";

const DEFAULT_STICKY_SESSIONS_LIMIT = 10;

export function useStickySessions() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [params, setParams] = useState<StickySessionsListParams>({
    staleOnly: false,
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

  const { data, error, isFetching, isLoading, isPending, isSuccess, refetch } = useQuery({
    queryKey: ["sticky-sessions", "list", queryParams],
    queryFn: () => listStickySessions(queryParams),
    placeholderData: (previousData) => previousData,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
  });
  const stickySessionsQuery = { data, error, isFetching, isLoading, isPending, isSuccess, refetch };

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

  const setSort = (sortBy: StickySessionSortBy, sortDir: StickySessionSortDir) => {
    setParams((current) => ({ ...current, sortBy, sortDir, offset: 0 }));
  };

  const deleteMutation = useMutation({
    mutationFn: (targets: StickySessionIdentifier[]) => deleteStickySessions({ sessions: targets }),
    onSuccess: async (response: StickySessionsDeleteResponse) => {
      if (response.deletedCount > 0 && response.failed.length === 0) {
        toast.success(
          response.deletedCount === 1
            ? t("stickySessions.toasts.deletedOne")
            : t("stickySessions.toasts.deletedMany", { count: response.deletedCount }),
        );
      } else if (response.deletedCount > 0) {
        toast.warning(
          t("stickySessions.toasts.deletedPartial", { deleted: response.deletedCount, failed: response.failed.length }),
        );
      } else {
        toast.error(t("stickySessions.toasts.noneSelectedDeleted"));
      }
      await queryClient.invalidateQueries({ queryKey: ["sticky-sessions", "list"] });
    },
    onError: (error: Error) => {
      toast.error(error.message || t("stickySessions.toasts.deleteFailed"));
    },
  });

  const deleteFilteredMutation = useMutation({
    mutationFn: () =>
      deleteFilteredStickySessions({
        staleOnly: queryParams.staleOnly,
        accountQuery: queryParams.accountQuery,
        keyQuery: queryParams.keyQuery,
      }),
    onSuccess: async (response: StickySessionsDeleteFilteredResponse) => {
      if (response.deletedCount > 0) {
        toast.success(
          response.deletedCount === 1
            ? t("stickySessions.toasts.filteredDeletedOne")
            : t("stickySessions.toasts.filteredDeletedMany", { count: response.deletedCount }),
        );
      } else {
        toast.error(t("stickySessions.toasts.noneFilteredDeleted"));
      }
      await queryClient.invalidateQueries({ queryKey: ["sticky-sessions", "list"] });
    },
    onError: (error: Error) => {
      toast.error(error.message || t("stickySessions.toasts.deleteFilteredFailed"));
    },
  });

  const purgeMutation = useMutation({
    mutationFn: (staleOnly: boolean) => purgeStickySessions({ staleOnly }),
    onSuccess: (response) => {
      toast.success(t("stickySessions.toasts.purged", { count: response.deletedCount }));
      void queryClient.invalidateQueries({ queryKey: ["sticky-sessions", "list"] });
    },
    onError: (error: Error) => {
      toast.error(error.message || t("stickySessions.toasts.purgeFailed"));
    },
  });

  return {
    params,
    setAccountQuery,
    setKeyQuery,
    setSort,
    setOffset,
    setLimit,
    stickySessionsQuery,
    deleteMutation,
    deleteFilteredMutation,
    purgeMutation,
  };
}
