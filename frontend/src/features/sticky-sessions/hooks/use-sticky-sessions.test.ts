import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { createElement, type PropsWithChildren } from "react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";
import { toast } from "sonner";

import * as stickySessionsApi from "@/features/sticky-sessions/api";
import { useStickySessions } from "@/features/sticky-sessions/hooks/use-sticky-sessions";
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

describe("useStickySessions", () => {
  it("loads sticky sessions and invalidates on delete/purge", async () => {
    const entries = [
      {
        key: "thread_123",
        accountId: "acc_1",
        kind: "prompt_cache",
        createdAt: "2026-03-10T12:00:00Z",
        updatedAt: "2026-03-10T12:05:00Z",
        expiresAt: "2026-03-10T12:10:00Z",
        isStale: false,
      },
    ];
    const queryClient = createTestQueryClient();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    server.use(
      http.get("/api/sticky-sessions", () =>
        HttpResponse.json({
          entries,
          stalePromptCacheCount: entries.filter((entry) => entry.isStale && entry.kind === "prompt_cache").length,
        }),
      ),
      http.delete("/api/sticky-sessions/:kind/:key", ({ params }) => {
        const key = decodeURIComponent(String(params.key));
        const kind = String(params.kind);
        const index = entries.findIndex((entry) => entry.key === key && entry.kind === kind);
        if (index >= 0) {
          entries.splice(index, 1);
        }
        return HttpResponse.json({ status: "deleted" });
      }),
      http.post("/api/sticky-sessions/purge", () => HttpResponse.json({ deletedCount: 0 })),
    );

    const { result } = renderHook(() => useStickySessions(), {
      wrapper: createWrapper(queryClient),
    });

    await waitFor(() => expect(result.current.stickySessionsQuery.isSuccess).toBe(true));
    expect(result.current.stickySessionsQuery.data?.entries).toHaveLength(1);

    await result.current.deleteMutation.mutateAsync({ key: "thread_123", kind: "prompt_cache" });
    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["sticky-sessions", "list"] });
    });

    await result.current.purgeMutation.mutateAsync(true);
    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["sticky-sessions", "list"] });
    });
  });

  it("uses fallback toast messages when sticky-session mutations fail", async () => {
    const queryClient = createTestQueryClient();
    const toastSpy = vi.spyOn(toast, "error").mockImplementation(() => "");
    const deleteSpy = vi
      .spyOn(stickySessionsApi, "deleteStickySession")
      .mockRejectedValueOnce(new Error(""));
    const purgeSpy = vi
      .spyOn(stickySessionsApi, "purgeStickySessions")
      .mockRejectedValueOnce(new Error(""));

    const { result } = renderHook(() => useStickySessions(), {
      wrapper: createWrapper(queryClient),
    });

    await waitFor(() => expect(result.current.stickySessionsQuery.isSuccess).toBe(true));
    await expect(result.current.deleteMutation.mutateAsync({ key: "thread_123", kind: "prompt_cache" })).rejects.toThrow();
    await expect(result.current.purgeMutation.mutateAsync(true)).rejects.toThrow();

    expect(toastSpy).toHaveBeenCalledWith("Failed to remove sticky session");
    expect(toastSpy).toHaveBeenCalledWith("Failed to purge sticky sessions");

    deleteSpy.mockRestore();
    purgeSpy.mockRestore();
    toastSpy.mockRestore();
  });
});
