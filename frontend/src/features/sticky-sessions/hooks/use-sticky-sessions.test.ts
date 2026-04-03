import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
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
        displayName: "sticky-a@example.com",
        kind: "prompt_cache",
        createdAt: "2026-03-10T12:00:00Z",
        updatedAt: "2026-03-10T12:05:00Z",
        expiresAt: "2026-03-10T12:10:00Z",
        isStale: false,
      },
    ];
    const queryClient = createTestQueryClient();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    let seenUrl = "";
    let deletePayload: unknown = null;

    server.use(
      http.get("/api/sticky-sessions", ({ request }) => {
        seenUrl = request.url;
        return HttpResponse.json({
          entries,
          stalePromptCacheCount: entries.filter((entry) => entry.isStale && entry.kind === "prompt_cache").length,
          total: entries.length,
          hasMore: false,
        });
      }),
      http.post("/api/sticky-sessions/delete", async ({ request }) => {
        deletePayload = await request.json();
        const sessions =
          deletePayload && typeof deletePayload === "object" && "sessions" in deletePayload
            ? ((deletePayload as { sessions?: Array<{ key: string; kind: string }> }).sessions ?? [])
            : [];
        for (const session of sessions) {
          const index = entries.findIndex((entry) => entry.key === session.key && entry.kind === session.kind);
          if (index >= 0) {
            entries.splice(index, 1);
          }
        }
        return HttpResponse.json({ deletedCount: sessions.length, deleted: sessions, failed: [] });
      }),
      http.post("/api/sticky-sessions/purge", () => HttpResponse.json({ deletedCount: 0 })),
    );

    const { result } = renderHook(() => useStickySessions(), {
      wrapper: createWrapper(queryClient),
    });

    await waitFor(() => expect(result.current.stickySessionsQuery.isSuccess).toBe(true));
    expect(result.current.stickySessionsQuery.data?.entries).toHaveLength(1);
    expect(result.current.params.accountQuery).toBe("");
    expect(result.current.params.keyQuery).toBe("");
    expect(result.current.params.sortBy).toBe("updated_at");
    expect(result.current.params.sortDir).toBe("desc");
    expect(seenUrl).toContain("offset=0");
    expect(seenUrl).toContain("limit=10");

    await result.current.deleteMutation.mutateAsync([{ key: "thread_123", kind: "prompt_cache" }]);
    expect(deletePayload).toEqual({ sessions: [{ key: "thread_123", kind: "prompt_cache" }] });
    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["sticky-sessions", "list"] });
    });

    await result.current.purgeMutation.mutateAsync(true);
    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["sticky-sessions", "list"] });
    });

    act(() => {
      result.current.setOffset(10);
    });
    await waitFor(() => {
      expect(result.current.params.offset).toBe(10);
    });

    act(() => {
      result.current.setLimit(25);
    });
    await waitFor(() => {
      expect(result.current.params.limit).toBe(25);
      expect(result.current.params.offset).toBe(0);
    });

    act(() => {
      result.current.setAccountQuery("sticky-a");
    });
    await waitFor(() => {
      expect(result.current.params.accountQuery).toBe("sticky-a");
      expect(result.current.params.offset).toBe(0);
    });

    act(() => {
      result.current.setKeyQuery("thread_123");
    });
    await waitFor(() => {
      expect(result.current.params.keyQuery).toBe("thread_123");
      expect(result.current.params.offset).toBe(0);
    });

    await waitFor(() => {
      expect(seenUrl).toContain("accountQuery=sticky-a");
      expect(seenUrl).toContain("keyQuery=thread_123");
    });

    act(() => {
      result.current.setSort("key", "asc");
    });
    await waitFor(() => {
      expect(result.current.params.sortBy).toBe("key");
      expect(result.current.params.sortDir).toBe("asc");
      expect(result.current.params.offset).toBe(0);
    });

    await waitFor(() => {
      expect(seenUrl).toContain("sortBy=key");
      expect(seenUrl).toContain("sortDir=asc");
    });
  });

  it("reports partial bulk-delete failures", async () => {
    const queryClient = createTestQueryClient();
    const warningSpy = vi.spyOn(toast, "warning").mockImplementation(() => "");
    const deleteSpy = vi.spyOn(stickySessionsApi, "deleteStickySessions").mockResolvedValueOnce({
      deletedCount: 1,
      deleted: [{ key: "thread_123", kind: "prompt_cache" }],
      failed: [{ key: "thread_999", kind: "codex_session", reason: "not_found" }],
    });

    const { result } = renderHook(() => useStickySessions(), {
      wrapper: createWrapper(queryClient),
    });

    await waitFor(() => expect(result.current.stickySessionsQuery.isSuccess).toBe(true));
    await result.current.deleteMutation.mutateAsync([
      { key: "thread_123", kind: "prompt_cache" },
      { key: "thread_999", kind: "codex_session" },
    ]);

    expect(warningSpy).toHaveBeenCalledWith("Deleted 1 sessions. 1 could not be deleted.");

    deleteSpy.mockRestore();
    warningSpy.mockRestore();
  });

  it("uses fallback toast messages when sticky-session mutations fail", async () => {
    const queryClient = createTestQueryClient();
    const toastSpy = vi.spyOn(toast, "error").mockImplementation(() => "");
    const deleteSpy = vi
      .spyOn(stickySessionsApi, "deleteStickySessions")
      .mockRejectedValueOnce(new Error(""));
    const purgeSpy = vi
      .spyOn(stickySessionsApi, "purgeStickySessions")
      .mockRejectedValueOnce(new Error(""));

    const { result } = renderHook(() => useStickySessions(), {
      wrapper: createWrapper(queryClient),
    });

    await waitFor(() => expect(result.current.stickySessionsQuery.isSuccess).toBe(true));
    await expect(
      result.current.deleteMutation.mutateAsync([{ key: "thread_123", kind: "prompt_cache" }]),
    ).rejects.toThrow();
    await expect(result.current.purgeMutation.mutateAsync(true)).rejects.toThrow();

    expect(toastSpy).toHaveBeenCalledWith("Failed to delete sticky sessions");
    expect(toastSpy).toHaveBeenCalledWith("Failed to purge sticky sessions");

    deleteSpy.mockRestore();
    purgeSpy.mockRestore();
    toastSpy.mockRestore();
  });
});
