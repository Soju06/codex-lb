import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { createElement, type PropsWithChildren } from "react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { useRequestLogs } from "@/features/dashboard/hooks/use-request-logs";
import { server } from "@/test/mocks/server";

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
      | [string, string, { search: string; limit: number; offset: number }]
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

  it("uses facet filters for options query without status self-filter", async () => {
    const calls: Array<{
      statuses: string[];
      accountIds: string[];
      modelOptions: string[];
      since: string | null;
    }> = [];
    server.use(
      http.get("/api/request-logs/options", ({ request }) => {
        const url = new URL(request.url);
        calls.push({
          statuses: url.searchParams.getAll("status"),
          accountIds: url.searchParams.getAll("accountId"),
          modelOptions: url.searchParams.getAll("modelOption"),
          since: url.searchParams.get("since"),
        });
        return HttpResponse.json({
          accountIds: [],
          modelOptions: [],
          statuses: ["ok", "rate_limit", "quota", "error"],
        });
      }),
    );

    const queryClient = createTestQueryClient();
    const wrapper = createWrapper(
      queryClient,
      "/dashboard?timeframe=24h&accountId=acc_primary&modelOption=gpt-5.1:::high&status=ok",
    );

    const { result } = renderHook(() => useRequestLogs(), { wrapper });
    await waitFor(() => expect(result.current.optionsQuery.isSuccess).toBe(true));
    await waitFor(() => expect(result.current.filters.accountIds).toEqual(["acc_primary"]));
    await waitFor(() => expect(result.current.filters.modelOptions).toEqual(["gpt-5.1:::high"]));

    const matchingCall = calls.find(
      (call) =>
        call.accountIds.includes("acc_primary") &&
        call.modelOptions.includes("gpt-5.1:::high"),
    );
    expect(matchingCall).toBeDefined();
    expect(matchingCall?.statuses).toEqual([]);
    expect(matchingCall?.since).toMatch(/T/);
  });

  it("removes stale status from request parameters immediately after unselect", async () => {
    const statusesPerCall: string[][] = [];
    server.use(
      http.get("/api/request-logs", ({ request }) => {
        const url = new URL(request.url);
        statusesPerCall.push(url.searchParams.getAll("status"));
        return HttpResponse.json({
          requests: [],
          total: 0,
          hasMore: false,
        });
      }),
    );

    const queryClient = createTestQueryClient();
    const wrapper = createWrapper(queryClient, "/dashboard?status=ok&status=stale_status");
    const { result } = renderHook(() => useRequestLogs(), { wrapper });

    await waitFor(() => expect(result.current.logsQuery.isSuccess).toBe(true));
    await waitFor(() => expect(result.current.filters.statuses).toEqual(["ok", "stale_status"]));
    await waitFor(() =>
      expect(
        statusesPerCall.some(
          (statuses) => statuses.includes("ok") && statuses.includes("stale_status"),
        ),
      ).toBe(true),
    );

    act(() => {
      result.current.updateFilters({ statuses: ["ok"], offset: 0 });
    });

    await waitFor(() => expect(result.current.filters.statuses).toEqual(["ok"]));
    await waitFor(() => expect(statusesPerCall[statusesPerCall.length - 1]).toEqual(["ok"]));
  });
});
