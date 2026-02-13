import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { createElement, type PropsWithChildren } from "react";
import { describe, expect, it, vi } from "vitest";

import { useApiKeys } from "@/features/api-keys/hooks/use-api-keys";

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

describe("useApiKeys", () => {
  it("runs CRUD mutations and returns plain key on create", async () => {
    const queryClient = createTestQueryClient();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    const { result } = renderHook(() => useApiKeys(), {
      wrapper: createWrapper(queryClient),
    });

    await waitFor(() => expect(result.current.apiKeysQuery.isSuccess).toBe(true));
    const existingKeyId = result.current.apiKeysQuery.data?.[0]?.id;
    expect(existingKeyId).toBeTruthy();

    const created = await result.current.createMutation.mutateAsync({
      name: "Test Key",
      allowedModels: ["gpt-5.1"],
      weeklyTokenLimit: 1000,
      expiresAt: null,
    });
    expect(created.key).toContain("sk-test");

    await result.current.updateMutation.mutateAsync({
      keyId: existingKeyId as string,
      payload: {
        name: "Updated Name",
      },
    });

    await result.current.regenerateMutation.mutateAsync(existingKeyId as string);
    await result.current.deleteMutation.mutateAsync(created.id);

    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["api-keys", "list"] });
    });
  });
});
