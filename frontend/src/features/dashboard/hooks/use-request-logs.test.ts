import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { createElement, type PropsWithChildren } from "react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { useRequestLogs } from "@/features/dashboard/hooks/use-request-logs";

function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
      },
    },
  });
}

function createWrapper(queryClient: QueryClient, initialEntry = "/dashboard") {
  return function Wrapper({ children }: PropsWithChildren) {
    return createElement(
      QueryClientProvider,
      { client: queryClient },
      createElement(MemoryRouter, { initialEntries: [initialEntry] }, children),
    );
  };
}

describe("useRequestLogs", () => {
  it("maps URL params into filter state and query key", async () => {
    const queryClient = createTestQueryClient();
    const wrapper = createWrapper(
      queryClient,
      "/dashboard?search=rate&timeframe=24h&accountId=acc_primary&modelOption=gpt-5.1:::high&status=rate_limit&limit=10&offset=20",
    );

    const { result } = renderHook(() => useRequestLogs(), { wrapper });

    await waitFor(() => expect(result.current.logsQuery.isSuccess).toBe(true));

    expect(result.current.filters).toMatchObject({
      search: "rate",
      timeframe: "24h",
      accountIds: ["acc_primary"],
      modelOptions: ["gpt-5.1:::high"],
      statuses: ["rate_limit"],
      limit: 10,
      offset: 20,
    });

    const query = queryClient.getQueryCache().findAll({
      queryKey: ["dashboard", "request-logs"],
    })[0];
    const key = query?.queryKey as
      | [string, string, { search: string; limit: number; offset: number }, string | undefined]
      | undefined;
    expect(key?.[2].search).toBe("rate");
    expect(key?.[2].limit).toBe(10);
    expect(key?.[2].offset).toBe(20);
  });

  it("supports pagination updates with total/hasMore response", async () => {
    const queryClient = createTestQueryClient();
    const wrapper = createWrapper(queryClient, "/dashboard?limit=1&offset=0");
    const { result } = renderHook(() => useRequestLogs(), { wrapper });

    await waitFor(() => expect(result.current.logsQuery.isSuccess).toBe(true));
    const firstTotal = result.current.logsQuery.data?.total ?? 0;
    expect(typeof result.current.logsQuery.data?.hasMore).toBe("boolean");

    act(() => {
      result.current.updateFilters({ offset: 1 });
    });

    await waitFor(() => {
      expect(result.current.filters.offset).toBe(1);
      expect(result.current.logsQuery.isSuccess).toBe(true);
    });

    expect(result.current.logsQuery.data?.total).toBe(firstTotal);
  });
});
