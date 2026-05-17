import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { createElement, type PropsWithChildren } from "react";
import { describe, expect, it, vi } from "vitest";

import { useAutomations } from "@/features/automations/hooks/use-automations";

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

describe("useAutomations", () => {
  it("loads, creates, updates, runs and deletes automation entries", async () => {
    const queryClient = createTestQueryClient();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    const initialProps: { selectedAutomationId: string | null } = { selectedAutomationId: null };

    const { result, rerender } = renderHook(
      ({ selectedAutomationId }: { selectedAutomationId: string | null }) =>
        useAutomations(selectedAutomationId),
      {
        initialProps,
        wrapper: createWrapper(queryClient),
      },
    );

    await waitFor(() => expect(result.current.automationsQuery.isSuccess).toBe(true));

    const created = await result.current.createMutation.mutateAsync({
      name: "Daily ping",
      enabled: true,
      schedule: {
        type: "daily",
        time: "05:00",
        timezone: "UTC",
        thresholdMinutes: 0,
        days: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
      },
      model: "gpt-5.1",
      prompt: "ping",
      accountIds: ["acc_primary"],
    });

    await result.current.updateMutation.mutateAsync({
      automationId: created.id,
      payload: {
        enabled: false,
        prompt: "health-check",
      },
    });

    rerender({ selectedAutomationId: created.id });
    await waitFor(() => expect(result.current.runsQuery.isSuccess).toBe(true));

    await result.current.runNowMutation.mutateAsync(created.id);
    await result.current.deleteMutation.mutateAsync(created.id);

    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["automations"] });
    });
  });
});
