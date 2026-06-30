import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { createElement, type PropsWithChildren } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { get } from "@/lib/api-client";
import { useReports } from "./use-reports";

vi.mock("@/lib/api-client", () => ({
  get: vi.fn().mockResolvedValue({
    summary: {
      totalCostUsd: 0,
      totalInputTokens: 0,
      totalOutputTokens: 0,
      totalCachedTokens: 0,
      totalRequests: 0,
      totalErrors: 0,
      activeAccounts: 0,
      avgCostPerDay: 0,
      avgRequestsPerDay: 0,
    },
    comparison: {
      canCompare: false,
      previous: {
        totalCostUsd: 0,
        totalTokens: 0,
        totalRequests: 0,
      },
    },
    daily: [],
    byModel: [],
    byUseragent: [],
    byAccount: [],
  }),
}));

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

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: PropsWithChildren) {
    return createElement(QueryClientProvider, { client: queryClient }, children);
  };
}

const getMock = vi.mocked(get);

function getRequestedSearchParams(): URLSearchParams {
  const [url] = getMock.mock.calls[0] ?? [];
  expect(typeof url).toBe("string");
  return new URL(url, "http://localhost").searchParams;
}

describe("useReports", () => {
  beforeEach(() => {
    getMock.mockClear();
  });

  it("includes the provided timezone in reports requests when available", async () => {
    const queryClient = createTestQueryClient();

    const { result } = renderHook(
      () =>
        useReports(
          {
            startDate: "2030-01-09",
            endDate: "2030-01-15",
            accountId: ["acct_123"],
            model: "gpt-5.1",
            useragent: "claude-code",
          },
          "America/Los_Angeles",
        ),
      { wrapper: createWrapper(queryClient) },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(getMock).toHaveBeenCalledTimes(1);
    const searchParams = getRequestedSearchParams();
    expect(searchParams.get("start_date")).toBe("2030-01-09");
    expect(searchParams.get("end_date")).toBe("2030-01-15");
    expect(searchParams.get("model")).toBe("gpt-5.1");
    expect(searchParams.get("useragent_group")).toBe("claude-code");
    expect(searchParams.getAll("account_id")).toEqual(["acct_123"]);
    expect(searchParams.get("timezone")).toBe("America/Los_Angeles");
  });

  it("keeps query key and request timezone aligned across rerenders", async () => {
    const queryClient = createTestQueryClient();
    const filters = {
      startDate: "2030-01-09",
      endDate: "2030-01-15",
      accountId: ["acct_123"],
      model: "gpt-5.1",
      useragent: "claude-code",
    };

    const { result, rerender } = renderHook(
      ({ timeZone }) => useReports(filters, timeZone),
      {
        wrapper: createWrapper(queryClient),
        initialProps: { timeZone: "America/Los_Angeles" as string | undefined },
      },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    rerender({ timeZone: "America/New_York" });

    await waitFor(() => expect(getMock).toHaveBeenCalledTimes(2));

    const [firstUrl] = getMock.mock.calls[0] ?? [];
    const [secondUrl] = getMock.mock.calls[1] ?? [];
    expect(
      new URL(String(firstUrl), "http://localhost").searchParams.get(
        "timezone",
      ),
    ).toBe("America/Los_Angeles");
    expect(
      new URL(String(secondUrl), "http://localhost").searchParams.get(
        "timezone",
      ),
    ).toBe("America/New_York");
  });

  it("reuses the same cache entry when refetching with the same timezone", async () => {
    const queryClient = createTestQueryClient();

    const filters = {
      startDate: "2030-01-09",
      endDate: "2030-01-15",
      accountId: ["acct_123"],
      model: "gpt-5.1",
      useragent: "claude-code",
    };

    const { result } = renderHook(() => useReports(filters, "America/Los_Angeles"), {
      wrapper: createWrapper(queryClient),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(getMock).toHaveBeenCalledTimes(1);

    await result.current.refetch();

    await waitFor(() => expect(getMock).toHaveBeenCalledTimes(2));

    const [firstUrl] = getMock.mock.calls[0] ?? [];
    const [secondUrl] = getMock.mock.calls[1] ?? [];
    expect(
      new URL(String(firstUrl), "http://localhost").searchParams.get(
        "timezone",
      ),
    ).toBe("America/Los_Angeles");
    expect(
      new URL(String(secondUrl), "http://localhost").searchParams.get(
        "timezone",
      ),
    ).toBe("America/Los_Angeles");
  });

  it("omits the timezone query parameter when the provided timezone is unavailable", async () => {
    const queryClient = createTestQueryClient();

    const { result } = renderHook(
      () =>
        useReports(
          {
            startDate: "2030-01-09",
            endDate: "2030-01-15",
            accountId: ["acct_123"],
            model: "gpt-5.1",
            useragent: "claude-code",
          },
          undefined,
        ),
      { wrapper: createWrapper(queryClient) },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(getMock).toHaveBeenCalledTimes(1);
    const searchParams = getRequestedSearchParams();
    expect(searchParams.get("start_date")).toBe("2030-01-09");
    expect(searchParams.get("end_date")).toBe("2030-01-15");
    expect(searchParams.get("model")).toBe("gpt-5.1");
    expect(searchParams.get("useragent_group")).toBe("claude-code");
    expect(searchParams.getAll("account_id")).toEqual(["acct_123"]);
    expect(searchParams.has("timezone")).toBe(false);
  });

  it("refetches when the useragent filter changes", async () => {
    const queryClient = createTestQueryClient();

    const { result, rerender } = renderHook(
      ({ useragent }) =>
        useReports(
          {
            startDate: "2030-01-09",
            endDate: "2030-01-15",
            accountId: ["acct_123"],
            model: "gpt-5.1",
            useragent,
          },
          "America/Los_Angeles",
        ),
      {
        wrapper: createWrapper(queryClient),
        initialProps: { useragent: "claude-code" },
      },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    rerender({ useragent: "chatgpt-app" });

    await waitFor(() => expect(getMock).toHaveBeenCalledTimes(2));

    const [firstUrl] = getMock.mock.calls[0] ?? [];
    const [secondUrl] = getMock.mock.calls[1] ?? [];
    expect(
      new URL(String(firstUrl), "http://localhost").searchParams.get(
        "useragent_group",
      ),
    ).toBe("claude-code");
    expect(
      new URL(String(secondUrl), "http://localhost").searchParams.get(
        "useragent_group",
      ),
    ).toBe("chatgpt-app");
  });
});
