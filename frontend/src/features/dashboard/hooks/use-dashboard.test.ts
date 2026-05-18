import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { createElement, type PropsWithChildren } from "react";
import { describe, expect, it } from "vitest";

import { useDashboard } from "@/features/dashboard/hooks/use-dashboard";
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

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: PropsWithChildren) {
    return createElement(QueryClientProvider, { client: queryClient }, children);
  };
}

describe("useDashboard", () => {
  it("loads dashboard overview via MSW and configures 30s refetch", async () => {
    const queryClient = createTestQueryClient();
    const { result } = renderHook(() => useDashboard("30d"), {
      wrapper: createWrapper(queryClient),
    });

    expect(result.current.isLoading || result.current.isPending).toBe(true);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.accounts.length).toBeGreaterThan(0);

    const query = queryClient.getQueryCache().find({ queryKey: ["dashboard", "overview", "30d"] });
    const refetchInterval = (query?.options as { refetchInterval?: unknown } | undefined)
      ?.refetchInterval;
    expect(refetchInterval).toBe(30_000);
  });

  it("passes timeframe to the overview endpoint", async () => {
    let requestedTimeframe: string | null = null;
    server.use(
      http.get("/api/dashboard/overview", ({ request }) => {
        requestedTimeframe = new URL(request.url).searchParams.get("timeframe");
        return HttpResponse.json({
          lastSyncAt: "2026-01-01T00:00:00Z",
          timeframe: { key: "1d", windowMinutes: 1440, bucketSeconds: 3600, bucketCount: 24 },
          accounts: [],
          summary: {
            primaryWindow: {
              remainingPercent: 80,
              capacityCredits: 100,
              remainingCredits: 80,
              resetAt: "2026-01-01T00:00:00Z",
              windowMinutes: 300,
            },
            secondaryWindow: null,
            cost: { currency: "USD", totalUsd: 0 },
            metrics: null,
          },
          windows: {
            primary: { windowKey: "primary", windowMinutes: 300, accounts: [] },
            secondary: null,
          },
          trends: { requests: [], tokens: [], cost: [], errorRate: [] },
        });
      }),
    );

    const queryClient = createTestQueryClient();
    const { result } = renderHook(() => useDashboard("1d"), {
      wrapper: createWrapper(queryClient),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(requestedTimeframe).toBe("1d");
  });

  it("exposes error state on request failure", async () => {
    server.use(
      http.get("/api/dashboard/overview", () =>
        HttpResponse.json(
          {
            error: {
              code: "overview_failed",
              message: "overview failed",
            },
          },
          { status: 500 },
        ),
      ),
    );

    const queryClient = createTestQueryClient();
    const { result } = renderHook(() => useDashboard("7d"), {
      wrapper: createWrapper(queryClient),
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
