import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { createElement, type PropsWithChildren, useEffect } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";
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

function LocationSpy({ onChange }: { onChange?: (search: string) => void }) {
  const routeLocation = useLocation();

  useEffect(() => {
    onChange?.(routeLocation.search);
  }, [routeLocation.search, onChange]);

  return null;
}

function createWrapper(
  queryClient: QueryClient,
  initialEntry = "/dashboard",
  onLocationChange?: (search: string) => void,
) {
  return function Wrapper({ children }: PropsWithChildren) {
    return createElement(
      QueryClientProvider,
      { client: queryClient },
      createElement(
        MemoryRouter,
        { initialEntries: [initialEntry] },
        createElement(LocationSpy, { onChange: onLocationChange }),
        children,
      ),
    );
  };
}

describe("useRequestLogs", () => {
  it("maps URL params into filter state and query key", async () => {
    const queryClient = createTestQueryClient();
    const wrapper = createWrapper(
      queryClient,
      "/dashboard?overviewTimeframe=30d&search=rate&timeframe=24h&accountId=acc_primary&apiKeyId=key_1&modelOption=gpt-5.1:::high&status=rate_limit&limit=10&offset=20",
    );

    const { result } = renderHook(() => useRequestLogs(), { wrapper });

    await waitFor(() => expect(result.current.logsQuery.isSuccess).toBe(true));

    expect(result.current.filters).toMatchObject({
      search: "rate",
      timeframe: "24h",
      accountIds: ["acc_primary"],
      apiKeyIds: ["key_1"],
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

  it("preserves unrelated search params when request-log filters change", async () => {
    const queryClient = createTestQueryClient();
    let locationSearch = "";
    const wrapper = createWrapper(
      queryClient,
      "/dashboard?overviewTimeframe=30d&limit=25&offset=0",
      (search) => {
        locationSearch = search;
      },
    );
    const { result } = renderHook(() => useRequestLogs(), { wrapper });

    await waitFor(() => expect(result.current.logsQuery.isSuccess).toBe(true));

    act(() => {
      result.current.updateFilters({ search: "quota" });
    });

    await waitFor(() => expect(result.current.filters.search).toBe("quota"));
    expect(locationSearch).toContain("overviewTimeframe=30d");
    expect(locationSearch).toContain("search=quota");
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
      apiKeyIds: string[];
      modelOptions: string[];
      since: string | null;
    }> = [];
    server.use(
      http.get("/api/request-logs/options", ({ request }) => {
        const url = new URL(request.url);
        calls.push({
          statuses: url.searchParams.getAll("status"),
          accountIds: url.searchParams.getAll("accountId"),
          apiKeyIds: url.searchParams.getAll("apiKeyId"),
          modelOptions: url.searchParams.getAll("modelOption"),
          since: url.searchParams.get("since"),
        });
        return HttpResponse.json({
          accountIds: [],
          apiKeys: [],
          modelOptions: [],
          statuses: ["ok", "rate_limit", "quota", "error"],
        });
      }),
    );

    const queryClient = createTestQueryClient();
    const wrapper = createWrapper(
      queryClient,
      "/dashboard?timeframe=24h&accountId=acc_primary&apiKeyId=key_1&modelOption=gpt-5.1:::high&status=ok",
    );

    const { result } = renderHook(() => useRequestLogs(), { wrapper });
    await waitFor(() => expect(result.current.optionsQuery.isSuccess).toBe(true));
    await waitFor(() => expect(result.current.filters.accountIds).toEqual(["acc_primary"]));
    await waitFor(() => expect(result.current.filters.apiKeyIds).toEqual(["key_1"]));
    await waitFor(() => expect(result.current.filters.modelOptions).toEqual(["gpt-5.1:::high"]));

    const matchingCall = calls.find(
      (call) =>
        call.accountIds.includes("acc_primary") &&
        call.apiKeyIds.includes("key_1") &&
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

  it("preserves api key filters in the URL and request-log queries", async () => {
    const apiKeyCalls: string[][] = [];
    server.use(
      http.get("/api/request-logs", ({ request }) => {
        const url = new URL(request.url);
        apiKeyCalls.push(url.searchParams.getAll("apiKeyId"));
        return HttpResponse.json({
          requests: [],
          total: 0,
          hasMore: false,
        });
      }),
    );

    const queryClient = createTestQueryClient();
    const wrapper = createWrapper(queryClient, "/dashboard?apiKeyId=key_1");
    const { result } = renderHook(() => useRequestLogs(), { wrapper });

    await waitFor(() => expect(result.current.logsQuery.isSuccess).toBe(true));
    await waitFor(() => expect(result.current.filters.apiKeyIds).toEqual(["key_1"]));
    await waitFor(() => expect(apiKeyCalls.some((ids) => ids.includes("key_1"))).toBe(true));

    act(() => {
      result.current.updateFilters({ apiKeyIds: ["key_1", "key_2"], offset: 0 });
    });

    await waitFor(() => expect(result.current.filters.apiKeyIds).toEqual(["key_1", "key_2"]));
    await waitFor(() =>
      expect(apiKeyCalls[apiKeyCalls.length - 1]).toEqual(["key_1", "key_2"]),
    );
  });

  it("parses conversationId from URL and maps to conversation_id in API", async () => {
    const apiCallParams: string[] = [];
    server.use(
      http.get("/api/request-logs", ({ request }) => {
        const url = new URL(request.url);
        apiCallParams.push(url.searchParams.get("conversation_id") ?? "");
        return HttpResponse.json({
          requests: [],
          total: 0,
          hasMore: false,
        });
      }),
    );

    const queryClient = createTestQueryClient();
    const wrapper = createWrapper(queryClient, "/dashboard?conversationId=conv_abc123&limit=25&offset=0");
    const { result } = renderHook(() => useRequestLogs(), { wrapper });

    await waitFor(() => expect(result.current.logsQuery.isSuccess).toBe(true));
    expect(result.current.filters.conversationId).toBe("conv_abc123");
    expect(apiCallParams.some((p) => p === "conv_abc123")).toBe(true);
  });

  it("maps null/empty conversationId to no conversation_id param in API", async () => {
    const apiCallParams: string[] = [];
    server.use(
      http.get("/api/request-logs", ({ request }) => {
        const url = new URL(request.url);
        apiCallParams.push(url.searchParams.get("conversation_id") ?? "-missing-");
        return HttpResponse.json({
          requests: [],
          total: 0,
          hasMore: false,
        });
      }),
    );

    const queryClient = createTestQueryClient();
    const wrapper = createWrapper(queryClient, "/dashboard?limit=25&offset=0");
    const { result } = renderHook(() => useRequestLogs(), { wrapper });

    await waitFor(() => expect(result.current.logsQuery.isSuccess).toBe(true));
    expect(result.current.filters.conversationId).toBeNull();
    expect(apiCallParams.some((p) => p === "-missing-")).toBe(true);
  });

  it("resets offset when conversationId is set", async () => {
    const queryClient = createTestQueryClient();
    const wrapper = createWrapper(queryClient, "/dashboard?limit=25&offset=10");
    const { result } = renderHook(() => useRequestLogs(), { wrapper });

    await waitFor(() => expect(result.current.logsQuery.isSuccess).toBe(true));
    expect(result.current.filters.offset).toBe(10);

    act(() => {
      result.current.updateFilters({ conversationId: "conv_test", offset: 0 });
    });

    await waitFor(() => {
      expect(result.current.filters.conversationId).toBe("conv_test");
      expect(result.current.filters.offset).toBe(0);
    });
  });

  it("resets offset when conversationId is cleared", async () => {
    const queryClient = createTestQueryClient();
    const wrapper = createWrapper(queryClient, "/dashboard?conversationId=conv_abc123&limit=25&offset=5");
    const { result } = renderHook(() => useRequestLogs(), { wrapper });

    await waitFor(() => expect(result.current.logsQuery.isSuccess).toBe(true));
    expect(result.current.filters.offset).toBe(5);

    act(() => {
      result.current.updateFilters({ conversationId: null, offset: 0 });
    });

    await waitFor(() => {
      expect(result.current.filters.conversationId).toBeNull();
      expect(result.current.filters.offset).toBe(0);
    });
  });

  it("preserves unrelated search params when conversationId is set or cleared", async () => {
    const queryClient = createTestQueryClient();
    let locationSearch = "";
    const wrapper = createWrapper(
      queryClient,
      "/dashboard?overviewTimeframe=30d&limit=25&offset=0",
      (search) => {
        locationSearch = search;
      },
    );
    const { result } = renderHook(() => useRequestLogs(), { wrapper });

    await waitFor(() => expect(result.current.logsQuery.isSuccess).toBe(true));

    act(() => {
      result.current.updateFilters({ conversationId: "conv_preserve", offset: 0 });
    });

    await waitFor(() => expect(result.current.filters.conversationId).toBe("conv_preserve"));
    expect(locationSearch).toContain("overviewTimeframe=30d");
    expect(locationSearch).toContain("conversationId=conv_preserve");

    act(() => {
      result.current.updateFilters({ conversationId: null, offset: 0 });
    });

    await waitFor(() => expect(result.current.filters.conversationId).toBeNull());
    expect(locationSearch).toContain("overviewTimeframe=30d");
    expect(locationSearch).not.toContain("conversationId");
  });

  it("writes and clears conversationId in browser URL", async () => {
    const queryClient = createTestQueryClient();
    let locationSearch = "";
    const wrapper = createWrapper(
      queryClient,
      "/dashboard?limit=25&offset=0",
      (search) => {
        locationSearch = search;
      },
    );
    const { result } = renderHook(() => useRequestLogs(), { wrapper });

    await waitFor(() => expect(result.current.logsQuery.isSuccess).toBe(true));

    act(() => {
      result.current.updateFilters({ conversationId: "conv_write_clear", offset: 0 });
    });

    await waitFor(() => {
      expect(locationSearch).toContain("conversationId=conv_write_clear");
    });

    act(() => {
      result.current.updateFilters({ conversationId: null, offset: 0 });
    });

    await waitFor(() => {
      expect(locationSearch).not.toContain("conversationId");
    });
  });

  it("round-trips conversation IDs with special characters through URL", async () => {
    const specialIds = [
      "conv/a&b+c",
      "conv has spaces",
      "conv_utf8_\u2603",
    ];
    const queryClient = createTestQueryClient();

    for (const rawId of specialIds) {
      const encoded = encodeURIComponent(rawId);
      const wrapper = createWrapper(
        queryClient,
        `/dashboard?conversationId=${encoded}&limit=25&offset=0`,
      );
      const { result } = renderHook(() => useRequestLogs(), { wrapper });

      await waitFor(() => expect(result.current.logsQuery.isSuccess).toBe(true));
      expect(result.current.filters.conversationId).toBe(rawId);
    }
  });

  it("maps special-character conversationId to conversation_id in API", async () => {
    const apiParams: string[] = [];
    server.use(
      http.get("/api/request-logs", ({ request }) => {
        const url = new URL(request.url);
        apiParams.push(url.searchParams.get("conversation_id") ?? "missing");
        return HttpResponse.json({ requests: [], total: 0, hasMore: false });
      }),
    );

    const rawId = "conv/a&b+c";
    const queryClient = createTestQueryClient();
    const wrapper = createWrapper(
      queryClient,
      `/dashboard?conversationId=${encodeURIComponent(rawId)}&limit=25&offset=0`,
    );
    const { result } = renderHook(() => useRequestLogs(), { wrapper });

    await waitFor(() => expect(result.current.logsQuery.isSuccess).toBe(true));
    const decoded = result.current.filters.conversationId;
    expect(decoded).toBe(rawId);
    expect(apiParams.some((p) => p === rawId)).toBe(true);
  });
});
