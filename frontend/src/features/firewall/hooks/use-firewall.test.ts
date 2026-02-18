import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { createElement, type PropsWithChildren } from "react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";
import { toast } from "sonner";

import * as firewallApi from "@/features/firewall/api";
import { useFirewall } from "@/features/firewall/hooks/use-firewall";
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

describe("useFirewall", () => {
  it("loads firewall list and invalidates on create/delete", async () => {
    const entries: Array<{ ipAddress: string; createdAt: string }> = [];
    const queryClient = createTestQueryClient();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    server.use(
      http.get("/api/firewall/ips", () =>
        HttpResponse.json({
          mode: entries.length === 0 ? "allow_all" : "allowlist_active",
          entries,
        }),
      ),
      http.post("/api/firewall/ips", async ({ request }) => {
        const payload = (await request.json()) as { ipAddress?: string };
        const ipAddress = String(payload.ipAddress || "").trim();
        const createdAt = "2026-02-18T12:00:00Z";
        entries.push({ ipAddress, createdAt });
        return HttpResponse.json({ ipAddress, createdAt });
      }),
      http.delete("/api/firewall/ips/:ipAddress", ({ params }) => {
        const ipAddress = decodeURIComponent(String(params.ipAddress));
        const index = entries.findIndex((entry) => entry.ipAddress === ipAddress);
        if (index >= 0) {
          entries.splice(index, 1);
        }
        return HttpResponse.json({ status: "deleted" });
      }),
    );

    const { result } = renderHook(() => useFirewall(), {
      wrapper: createWrapper(queryClient),
    });

    await waitFor(() => expect(result.current.firewallQuery.isSuccess).toBe(true));
    expect(result.current.firewallQuery.data?.mode).toBe("allow_all");

    await result.current.createMutation.mutateAsync("127.0.0.1");
    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["firewall", "ips"] });
    });

    await result.current.deleteMutation.mutateAsync("127.0.0.1");
    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["firewall", "ips"] });
    });
  });

  it("uses fallback toast messages when mutation errors have empty messages", async () => {
    const queryClient = createTestQueryClient();
    const toastSpy = vi.spyOn(toast, "error").mockImplementation(() => "");
    const createSpy = vi
      .spyOn(firewallApi, "createFirewallIp")
      .mockRejectedValueOnce(new Error(""));
    const deleteSpy = vi
      .spyOn(firewallApi, "deleteFirewallIp")
      .mockRejectedValueOnce(new Error(""));

    const { result } = renderHook(() => useFirewall(), {
      wrapper: createWrapper(queryClient),
    });

    await waitFor(() => expect(result.current.firewallQuery.isSuccess).toBe(true));
    await expect(result.current.createMutation.mutateAsync("127.0.0.1")).rejects.toThrow();
    await expect(result.current.deleteMutation.mutateAsync("127.0.0.1")).rejects.toThrow();

    expect(toastSpy).toHaveBeenCalledWith("Failed to add firewall IP");
    expect(toastSpy).toHaveBeenCalledWith("Failed to remove firewall IP");

    createSpy.mockRestore();
    deleteSpy.mockRestore();
    toastSpy.mockRestore();
  });
});
