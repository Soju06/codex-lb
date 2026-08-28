import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { createElement, type PropsWithChildren } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createModelSource } from "@/test/mocks/factories";

const modelSourceApiMocks = vi.hoisted(() => ({
  listModelSources: vi.fn(),
  createModelSource: vi.fn(),
  updateModelSource: vi.fn(),
  deleteModelSource: vi.fn(),
}));

const toastMocks = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
}));

vi.mock("@/features/model-sources/api", () => modelSourceApiMocks);
vi.mock("sonner", () => ({ toast: toastMocks }));

function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
}

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: PropsWithChildren) {
    return createElement(QueryClientProvider, { client: queryClient }, children);
  };
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("useModelSources", () => {
  it("invalidates source, key, and model picker queries after mutations", async () => {
    const queryClient = createTestQueryClient();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    const source = createModelSource({ id: "source_1", name: "Source One" });

    modelSourceApiMocks.listModelSources.mockResolvedValue({ sources: [source] });
    modelSourceApiMocks.createModelSource.mockResolvedValue(source);
    modelSourceApiMocks.updateModelSource.mockResolvedValue(source);
    modelSourceApiMocks.deleteModelSource.mockResolvedValue(undefined);

    const { useModelSources } = await import("@/features/model-sources/hooks/use-model-sources");
    const { result } = renderHook(() => useModelSources(), {
      wrapper: createWrapper(queryClient),
    });

    await waitFor(() => expect(result.current.modelSourcesQuery.isSuccess).toBe(true));

    await result.current.createMutation.mutateAsync({
      name: "Source One",
      baseUrl: "https://source.example/v1",
      apiKey: "token",
      models: [],
    });
    await result.current.updateMutation.mutateAsync({
      sourceId: source.id,
      payload: { name: "Source One Updated" },
    });
    await result.current.deleteMutation.mutateAsync(source.id);

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["model-sources", "list"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["api-keys", "list"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["models"] });
    expect(invalidateSpy).not.toHaveBeenCalledWith({ queryKey: ["api-keys", "models"] });
  });
});
