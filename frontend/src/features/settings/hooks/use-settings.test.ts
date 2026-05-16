import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { createElement, type PropsWithChildren } from "react";
import { describe, expect, it, vi } from "vitest";

import { useSettings } from "@/features/settings/hooks/use-settings";

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
