import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { createElement, type PropsWithChildren } from "react";
import { describe, expect, it, vi } from "vitest";

import { useSettings, useTelemetryConsent, useTelemetryPreview } from "@/features/settings/hooks/use-settings";

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

describe("useSettings", () => {
  it("loads settings and invalidates cache on update", async () => {
    const queryClient = createTestQueryClient();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    const { result } = renderHook(() => useSettings(), {
      wrapper: createWrapper(queryClient),
    });

    await waitFor(() => expect(result.current.settingsQuery.isSuccess).toBe(true));
    expect(result.current.settingsQuery.data?.stickyThreadsEnabled).toBeTypeOf("boolean");
    expect(result.current.settingsQuery.data?.openaiCacheAffinityMaxAgeSeconds).toBeTypeOf("number");
    expect(result.current.settingsQuery.data?.dashboardSessionTtlSeconds).toBeTypeOf("number");

    await result.current.updateSettingsMutation.mutateAsync({
      stickyThreadsEnabled: false,
      preferEarlierResetAccounts: true,
      preferEarlierResetWindow: "secondary",
      openaiCacheAffinityMaxAgeSeconds: 180,
      dashboardSessionTtlSeconds: 31536000,
      stickyReallocationBudgetThresholdPct: 95,
      stickyReallocationPrimaryBudgetThresholdPct: 95,
      stickyReallocationSecondaryBudgetThresholdPct: 100,
      importWithoutOverwrite: true,
      totpRequiredOnLogin: false,
      apiKeyAuthEnabled: true,
    });

    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["settings", "detail"] });
    });
  });
});

describe("useTelemetryConsent", () => {
  it("loads consent and invalidates cache on decision", async () => {
    const queryClient = createTestQueryClient();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    const { result } = renderHook(() => useTelemetryConsent(), {
      wrapper: createWrapper(queryClient),
    });

    await waitFor(() => expect(result.current.telemetryConsentQuery.isSuccess).toBe(true));
    expect(result.current.telemetryConsentQuery.data?.state).toBe("enabled");
    expect(result.current.telemetryConsentQuery.data?.active).toBe(true);
    // A persisted decision skips the expensive snapshot build entirely.
    expect(result.current.telemetryConsentQuery.data?.preview).toBeNull();

    await result.current.updateTelemetryConsentMutation.mutateAsync({ enabled: false });

    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["settings", "telemetry"] });
    });
  });
});

describe("useTelemetryPreview", () => {
  it("stays idle until enabled, then loads the preview envelope", async () => {
    const queryClient = createTestQueryClient();

    const { result, rerender } = renderHook(({ enabled }) => useTelemetryPreview(enabled), {
      wrapper: createWrapper(queryClient),
      initialProps: { enabled: false },
    });

    expect(result.current.telemetryPreviewQuery.isFetching).toBe(false);
    expect(result.current.telemetryPreviewQuery.data).toBeUndefined();

    rerender({ enabled: true });

    await waitFor(() => expect(result.current.telemetryPreviewQuery.isSuccess).toBe(true));
    const preview = result.current.telemetryPreviewQuery.data?.preview;
    expect(preview?.metrics.schema_version).toBe(1);
    expect(preview?.instance_id).toBe("00000000-0000-4000-8000-000000000000");
    expect(preview?.timestamp).toBe("2026-08-06T00:00:00Z");
  });
});
