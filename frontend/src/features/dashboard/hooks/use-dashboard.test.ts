import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { createElement, type PropsWithChildren } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  useDashboard,
  useDashboardProjections,
  useDashboardRequestActivity,
} from "@/features/dashboard/hooks/use-dashboard";
import { server } from "@/test/mocks/server";
import { useDashboardPreferencesStore } from "@/hooks/use-dashboard-preferences";
import { getBrowserReportsTimeZone } from "@/features/reports/date";

vi.mock("@/features/reports/date", () => ({
  getBrowserReportsTimeZone: vi.fn(() => "America/Los_Angeles"),
}));

const getBrowserReportsTimeZoneMock = vi.mocked(getBrowserReportsTimeZone);

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
  beforeEach(() => {
    getBrowserReportsTimeZoneMock.mockReset();
    getBrowserReportsTimeZoneMock.mockReturnValue("America/Los_Angeles");
  });

  it("loads dashboard overview via MSW and configures the selected refetch cadence", async () => {
    useDashboardPreferencesStore.setState({ refreshSeconds: 15 });
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
    expect(refetchInterval).toBe(15_000);
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

  it("applies the selected cadence to projection polling", () => {
    useDashboardPreferencesStore.setState({ refreshSeconds: 5 });
    const queryClient = createTestQueryClient();

    renderHook(() => useDashboardProjections(false), {
      wrapper: createWrapper(queryClient),
    });

    const query = queryClient.getQueryCache().find({ queryKey: ["dashboard", "projections"] });
    const refetchInterval = (query?.options as { refetchInterval?: unknown } | undefined)
      ?.refetchInterval;
    expect(refetchInterval).toBe(5_000);
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

  it("keeps request activity lazy until heatmap mode is enabled", async () => {
    let requestCount = 0;
    server.use(
      http.get("/api/dashboard/request-activity", () => {
        requestCount += 1;
        return HttpResponse.json({ days: [] });
      }),
    );

    const queryClient = createTestQueryClient();
    const { result, rerender } = renderHook(
      ({ enabled }: { enabled: boolean }) => useDashboardRequestActivity(enabled),
      {
        initialProps: { enabled: false },
        wrapper: createWrapper(queryClient),
      },
    );

    expect(result.current.isPending).toBe(true);
    expect(requestCount).toBe(0);

    rerender({ enabled: true });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(requestCount).toBe(1);
  });

  it("forwards the browser timezone, keys its cache by timezone, and polls every 60 seconds", async () => {
    const requestedTimezones: Array<string | null> = [];
    server.use(
      http.get("/api/dashboard/request-activity", ({ request }) => {
        requestedTimezones.push(new URL(request.url).searchParams.get("timezone"));
        return HttpResponse.json({ days: [] });
      }),
    );

    const queryClient = createTestQueryClient();
    const { result, rerender } = renderHook(
      ({ enabled }: { enabled: boolean }) => useDashboardRequestActivity(enabled),
      {
        initialProps: { enabled: true },
        wrapper: createWrapper(queryClient),
      },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const firstQuery = queryClient.getQueryCache().find({
      queryKey: ["dashboard", "request-activity", "America/Los_Angeles"],
    });
    expect(firstQuery).toBeDefined();
    expect((firstQuery?.options as { refetchInterval?: unknown }).refetchInterval).toBe(60_000);
    expect(requestedTimezones).toEqual(["America/Los_Angeles"]);

    getBrowserReportsTimeZoneMock.mockReturnValue("America/New_York");
    rerender({ enabled: true });

    await waitFor(() => expect(requestedTimezones).toEqual([
      "America/Los_Angeles",
      "America/New_York",
    ]));
    expect(
      queryClient.getQueryCache().find({
        queryKey: ["dashboard", "request-activity", "America/New_York"],
      }),
    ).toBeDefined();
  });

  it("uses UTC consistently when browser timezone detection is unavailable", async () => {
    const requestedTimezones: Array<string | null> = [];
    getBrowserReportsTimeZoneMock.mockReturnValue(undefined);
    server.use(
      http.get("/api/dashboard/request-activity", ({ request }) => {
        requestedTimezones.push(new URL(request.url).searchParams.get("timezone"));
        return HttpResponse.json({ days: [] });
      }),
    );

    const queryClient = createTestQueryClient();
    const { result } = renderHook(() => useDashboardRequestActivity(true), {
      wrapper: createWrapper(queryClient),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(requestedTimezones).toEqual(["UTC"]);
    expect(
      queryClient.getQueryCache().find({
        queryKey: ["dashboard", "request-activity", "UTC"],
      }),
    ).toBeDefined();
  });
});
