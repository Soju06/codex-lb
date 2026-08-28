import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { createElement, type PropsWithChildren } from "react";
import { describe, expect, it, vi } from "vitest";

import {
  useAccounts,
  useAccountUsageResetCredits,
} from "@/features/accounts/hooks/use-accounts";
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

describe("useAccounts", () => {
  it("loads accounts and invalidates related queries after mutations", async () => {
    const queryClient = createTestQueryClient();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    let usageResetBody: unknown;
    server.use(
      http.post("/api/accounts/:accountId/usage-reset-credits/consume", async ({ params, request }) => {
        const accountId = String(params.accountId);
        usageResetBody = await request.json();
        return HttpResponse.json({
          status: "reset",
          accountId,
          code: "reset",
          windowsReset: 2,
          usageWritten: true,
          primaryUsedPercentBefore: 99,
          primaryUsedPercentAfter: 1,
          secondaryUsedPercentBefore: 80,
          secondaryUsedPercentAfter: 1,
          accountStatusBefore: "rate_limited",
          accountStatusAfter: "active",
        });
      }),
    );
    const { result } = renderHook(() => useAccounts(), {
      wrapper: createWrapper(queryClient),
    });

    await waitFor(() => expect(result.current.accountsQuery.isSuccess).toBe(true));
    const firstAccountId = result.current.accountsQuery.data?.[0]?.accountId;
    expect(firstAccountId).toBeTruthy();

    await result.current.pauseMutation.mutateAsync(firstAccountId as string);
    await result.current.resumeMutation.mutateAsync(firstAccountId as string);
    await result.current.probeMutation.mutateAsync({
      accountId: firstAccountId as string,
    });
    await result.current.usageResetMutation.mutateAsync({
      accountId: firstAccountId as string,
    });
    expect(usageResetBody).toEqual({
      redeemRequestId: expect.any(String),
    });
    const routingPolicyResult = await result.current.routingPolicyMutation.mutateAsync({
      accountId: firstAccountId as string,
      routingPolicy: "preserve",
    });
    expect(routingPolicyResult.routingPolicy).toBe("preserve");

    const imported = await result.current.importMutation.mutateAsync(
      new File(["{}"], "auth.json", { type: "application/json" }),
    );
    await result.current.deleteMutation.mutateAsync({ accountId: imported.accountId, deleteHistory: false });

    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["accounts", "list"] });
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["accounts", "trends"] });
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["accounts", "usage-reset-credits"] });
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: ["accounts", "trends", firstAccountId],
      });
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: ["accounts", "usage-reset-credits", firstAccountId],
      });
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["dashboard", "overview"] });
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["dashboard", "projections"] });
    });
  });

  it("exports auth for an account without invalidating account queries", async () => {
    const queryClient = createTestQueryClient();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    const { result } = renderHook(() => useAccounts(), {
      wrapper: createWrapper(queryClient),
    });

    await waitFor(() => expect(result.current.accountsQuery.isSuccess).toBe(true));
    const firstAccountId = result.current.accountsQuery.data?.[0]?.accountId;
    expect(firstAccountId).toBeTruthy();

    await result.current.exportAuthMutation.mutateAsync(firstAccountId as string);

    expect(invalidateSpy).not.toHaveBeenCalledWith({ queryKey: ["accounts", "list"] });
    expect(invalidateSpy).not.toHaveBeenCalledWith({ queryKey: ["accounts", "trends"] });
    expect(invalidateSpy).not.toHaveBeenCalledWith({ queryKey: ["accounts", "usage-reset-credits"] });
    expect(invalidateSpy).not.toHaveBeenCalledWith({ queryKey: ["dashboard", "overview"] });
    expect(invalidateSpy).not.toHaveBeenCalledWith({ queryKey: ["dashboard", "projections"] });
  });

  it("reuses the dashboard usage reset redemption id after a failed attempt", async () => {
    const queryClient = createTestQueryClient();
    const usageResetBodies: unknown[] = [];
    server.use(
      http.post("/api/accounts/:accountId/usage-reset-credits/consume", async ({ params, request }) => {
        const accountId = String(params.accountId);
        usageResetBodies.push(await request.json());
        if (usageResetBodies.length === 1) {
          return HttpResponse.json(
            {
              error: {
                code: "upstream_timeout",
                message: "Upstream response was lost",
              },
            },
            { status: 504 },
          );
        }
        return HttpResponse.json({
          status: "already_redeemed",
          accountId,
          code: "already_redeemed",
          windowsReset: 1,
          usageWritten: true,
          primaryUsedPercentBefore: 99,
          primaryUsedPercentAfter: 1,
          secondaryUsedPercentBefore: 80,
          secondaryUsedPercentAfter: 1,
          accountStatusBefore: "rate_limited",
          accountStatusAfter: "active",
        });
      }),
    );
    const { result } = renderHook(() => useAccounts(), {
      wrapper: createWrapper(queryClient),
    });

    await waitFor(() => expect(result.current.accountsQuery.isSuccess).toBe(true));

    await expect(
      result.current.usageResetMutation.mutateAsync({ accountId: "acc_primary" }),
    ).rejects.toThrow("Upstream response was lost");
    await result.current.usageResetMutation.mutateAsync({ accountId: "acc_primary" });

    expect(usageResetBodies).toHaveLength(2);
    expect(usageResetBodies[0]).toEqual({
      redeemRequestId: expect.any(String),
    });
    expect(usageResetBodies[1]).toEqual(usageResetBodies[0]);
  });

  it("does not reuse a failed dashboard usage reset redemption id for another account", async () => {
    const queryClient = createTestQueryClient();
    const usageResetBodies: unknown[] = [];
    server.use(
      http.post("/api/accounts/:accountId/usage-reset-credits/consume", async ({ request }) => {
        usageResetBodies.push(await request.json());
        return HttpResponse.json(
          {
            error: {
              code: "upstream_timeout",
              message: "Upstream response was lost",
            },
          },
          { status: 504 },
        );
      }),
    );
    const { result } = renderHook(() => useAccounts(), {
      wrapper: createWrapper(queryClient),
    });

    await waitFor(() => expect(result.current.accountsQuery.isSuccess).toBe(true));

    await expect(
      result.current.usageResetMutation.mutateAsync({ accountId: "acc_primary" }),
    ).rejects.toThrow("Upstream response was lost");
    await expect(
      result.current.usageResetMutation.mutateAsync({ accountId: "acc_secondary" }),
    ).rejects.toThrow("Upstream response was lost");

    expect(usageResetBodies).toHaveLength(2);
    expect(usageResetBodies[0]).toEqual({
      redeemRequestId: expect.any(String),
    });
    expect(usageResetBodies[1]).toEqual({
      redeemRequestId: expect.any(String),
    });
    expect(usageResetBodies[1]).not.toEqual(usageResetBodies[0]);
  });

  it("does not permanently poll usage reset credits", async () => {
    const queryClient = createTestQueryClient();
    const accountId = "acc_primary";

    const { result } = renderHook(() => useAccountUsageResetCredits(accountId), {
      wrapper: createWrapper(queryClient),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const query = queryClient.getQueryCache().find({
      queryKey: ["accounts", "usage-reset-credits", accountId],
    });
    const refetchInterval = (query?.options as { refetchInterval?: unknown } | undefined)
      ?.refetchInterval;
    expect(refetchInterval).toBeUndefined();
  });
});
