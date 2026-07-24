import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";

import { getConversations, type ConversationListFilters } from "@/features/dashboard/api";
import {
  ConversationFilterStateSchema,
  type ConversationFilterState,
} from "@/features/dashboard/schemas";

const DEFAULT_CONVERSATION_FILTER_STATE: ConversationFilterState = {
  search: "",
  limit: 25,
  offset: 0,
};

const CONVERSATION_PARAM_KEYS = [
  "conversationSearch",
  "conversationLimit",
  "conversationOffset",
] as const;

function parseNumber(value: string | null, fallback: number): number {
  if (value === null) {
    return fallback;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function parseConversationFilterState(params: URLSearchParams): ConversationFilterState {
  const candidate = {
    search: params.get("conversationSearch") ?? "",
    limit: parseNumber(
      params.get("conversationLimit"),
      DEFAULT_CONVERSATION_FILTER_STATE.limit,
    ),
    offset: parseNumber(
      params.get("conversationOffset"),
      DEFAULT_CONVERSATION_FILTER_STATE.offset,
    ),
  };
  const parsed = ConversationFilterStateSchema.safeParse(candidate);
  if (parsed.success) {
    return parsed.data;
  }
  return DEFAULT_CONVERSATION_FILTER_STATE;
}

function writeConversationFilterState(
  state: ConversationFilterState,
  base?: URLSearchParams,
): URLSearchParams {
  const params = new URLSearchParams(base);
  for (const key of CONVERSATION_PARAM_KEYS) {
    params.delete(key);
  }
  if (state.search) {
    params.set("conversationSearch", state.search);
  }
  params.set("conversationLimit", String(state.limit));
  params.set("conversationOffset", String(state.offset));
  return params;
}

export type UseConversationsOptions = {
  enabled?: boolean;
};

export function useConversations(options: UseConversationsOptions = {}) {
  const enabled = options.enabled ?? true;
  const [searchParams, setSearchParams] = useSearchParams();

  const filters = useMemo(
    () => parseConversationFilterState(searchParams),
    [searchParams],
  );

  const listFilters = useMemo<ConversationListFilters>(
    () => ({
      search: filters.search || undefined,
      limit: filters.limit,
      offset: filters.offset,
    }),
    [filters],
  );

  const {
    data,
    error,
    isFetching,
    isLoading,
    isPending,
    isSuccess,
    refetch,
  } = useQuery({
    queryKey: ["dashboard", "conversations", listFilters],
    queryFn: () => getConversations(listFilters),
    enabled,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
    placeholderData: keepPreviousData,
  });

  const conversationsQuery = {
    data,
    error,
    isFetching,
    isLoading,
    isPending,
    isSuccess,
    refetch,
  };

  const updateFilters = (patch: Partial<ConversationFilterState>) => {
    const nextState: ConversationFilterState = {
      ...filters,
      ...patch,
    };
    setSearchParams(writeConversationFilterState(nextState, searchParams));
  };

  return {
    filters,
    listFilters,
    conversationsQuery,
    updateFilters,
  };
}

export type UseConversationsResult = ReturnType<typeof useConversations>;
