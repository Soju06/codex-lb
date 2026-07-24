import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { createElement, type PropsWithChildren, useEffect } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { useConversations } from "@/features/dashboard/hooks/use-conversations";
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

describe("useConversations", () => {
  it("maps prefixed URL params into filter state and query key", async () => {
    const queryClient = createTestQueryClient();
    const wrapper = createWrapper(
      queryClient,
      "/dashboard?view=conversations&conversationSearch=opencode&conversationLimit=10&conversationOffset=20",
    );

    const { result } = renderHook(() => useConversations({ enabled: true }), {
      wrapper,
    });

    await waitFor(() => expect(result.current.conversationsQuery.isSuccess).toBe(true));

    expect(result.current.filters).toMatchObject({
      search: "opencode",
      limit: 10,
      offset: 20,
    });

    const query = queryClient.getQueryCache().findAll({
      queryKey: ["dashboard", "conversations"],
    })[0];
    const key = query?.queryKey as
      | [string, string, { search: string; limit: number; offset: number }]
      | undefined;
    expect(key?.[2].search).toBe("opencode");
    expect(key?.[2].limit).toBe(10);
    expect(key?.[2].offset).toBe(20);
  });

  it("does not read request-log-only keys (search/limit/offset)", async () => {
    const queryClient = createTestQueryClient();
    const wrapper = createWrapper(
      queryClient,
      "/dashboard?search=requestlog&limit=5&offset=9&conversationSearch=convonly",
    );

    const { result } = renderHook(() => useConversations({ enabled: true }), {
      wrapper,
    });

    await waitFor(() => expect(result.current.conversationsQuery.isSuccess).toBe(true));
    expect(result.current.filters.search).toBe("convonly");
    expect(result.current.filters.limit).toBe(25);
    expect(result.current.filters.offset).toBe(0);
  });

  it("preserves unrelated and request-log params when conversation filters change", async () => {
    const queryClient = createTestQueryClient();
    let locationSearch = "";
    const wrapper = createWrapper(
      queryClient,
      "/dashboard?overviewTimeframe=30d&search=requestlog&limit=5",
      (search) => {
        locationSearch = search;
      },
    );
    const { result } = renderHook(() => useConversations({ enabled: true }), {
      wrapper,
    });

    await waitFor(() => expect(result.current.conversationsQuery.isSuccess).toBe(true));

    act(() => {
      result.current.updateFilters({ search: "opencode", offset: 0 });
    });

    await waitFor(() => expect(result.current.filters.search).toBe("opencode"));
    expect(locationSearch).toContain("overviewTimeframe=30d");
    // Request-log params must be preserved untouched
    expect(locationSearch).toContain("search=requestlog");
    expect(locationSearch).toContain("limit=5");
    // Conversation params written under prefixed keys
    expect(locationSearch).toContain("conversationSearch=opencode");
    // Bare `search=` still refers to the request-log value, not opencode
    expect(locationSearch).not.toContain("search=opencode");
  });

  it("resets conversation offset when search changes", async () => {
    const queryClient = createTestQueryClient();
    const wrapper = createWrapper(
      queryClient,
      "/dashboard?view=conversations&conversationSearch=old&conversationLimit=25&conversationOffset=30",
    );
    const { result } = renderHook(() => useConversations({ enabled: true }), {
      wrapper,
    });

    await waitFor(() => expect(result.current.conversationsQuery.isSuccess).toBe(true));
    expect(result.current.filters.offset).toBe(30);

    act(() => {
      result.current.updateFilters({ search: "new", offset: 0 });
    });

    await waitFor(() => {
      expect(result.current.filters.search).toBe("new");
      expect(result.current.filters.offset).toBe(0);
    });
  });

  it("does not reset request-log offset when conversation search changes", async () => {
    const queryClient = createTestQueryClient();
    let locationSearch = "";
    const wrapper = createWrapper(
      queryClient,
      "/dashboard?view=conversations&conversationOffset=10&offset=40",
      (search) => {
        locationSearch = search;
      },
    );
    const { result } = renderHook(() => useConversations({ enabled: true }), {
      wrapper,
    });

    await waitFor(() => expect(result.current.conversationsQuery.isSuccess).toBe(true));

    act(() => {
      result.current.updateFilters({ search: "opencode", offset: 0 });
    });

    await waitFor(() => expect(result.current.filters.search).toBe("opencode"));
    // request-log offset must be untouched
    expect(locationSearch).toContain("offset=40");
    expect(locationSearch).toContain("conversationOffset=0");
  });

  it("supports pagination updates with total/hasMore response", async () => {
    const queryClient = createTestQueryClient();
    const wrapper = createWrapper(
      queryClient,
      "/dashboard?view=conversations&conversationLimit=1&conversationOffset=0",
    );
    const { result } = renderHook(() => useConversations({ enabled: true }), {
      wrapper,
    });

    await waitFor(() => expect(result.current.conversationsQuery.isSuccess).toBe(true));
    expect(typeof result.current.conversationsQuery.data?.hasMore).toBe("boolean");

    act(() => {
      result.current.updateFilters({ offset: 1 });
    });

    await waitFor(() => {
      expect(result.current.filters.offset).toBe(1);
      expect(result.current.conversationsQuery.isSuccess).toBe(true);
    });
  });

  it("does not fire the query when disabled", async () => {
    const listCalls: string[] = [];
    server.use(
      http.get("/api/conversations", ({ request }) => {
        listCalls.push(new URL(request.url).searchParams.get("search") ?? "");
        return HttpResponse.json({ conversations: [], total: 0, hasMore: false });
      }),
    );

    const queryClient = createTestQueryClient();
    const wrapper = createWrapper(queryClient, "/dashboard?view=conversations");
    renderHook(() => useConversations({ enabled: false }), { wrapper });

    // Give any potential query a moment to fire
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(listCalls).toHaveLength(0);
  });

  it("maps search to the API search parameter", async () => {
    const apiSearches: string[] = [];
    server.use(
      http.get("/api/conversations", ({ request }) => {
        apiSearches.push(new URL(request.url).searchParams.get("search") ?? "-missing-");
        return HttpResponse.json({ conversations: [], total: 0, hasMore: false });
      }),
    );

    const queryClient = createTestQueryClient();
    const wrapper = createWrapper(
      queryClient,
      "/dashboard?view=conversations&conversationSearch=hello",
    );
    const { result } = renderHook(() => useConversations({ enabled: true }), {
      wrapper,
    });

    await waitFor(() => expect(result.current.conversationsQuery.isSuccess).toBe(true));
    expect(apiSearches.some((value) => value === "hello")).toBe(true);
  });
});
