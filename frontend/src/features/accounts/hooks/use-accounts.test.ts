import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { createElement, type PropsWithChildren } from "react";
import { describe, expect, it, vi } from "vitest";

import {
  useAccounts,
  useAccountMutations,
  useAccountUsageResetCredits,
} from "@/features/accounts/hooks/use-accounts";
import type { AccountSummary } from "@/features/accounts/schemas";
import type { DashboardOverview } from "@/features/dashboard/schemas";
import {
  createAccountSummary,
  createDashboardOverview,
} from "@/test/mocks/factories";
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
  it.each(["accounts", "dashboard"] as const)(
    "does not let an older inactive %s read revert an acknowledged usage policy",
    async (source) => {
      const queryClient = createTestQueryClient();
      queryClient.setDefaultOptions({ queries: { retry: false, gcTime: Infinity } });
      const key = source === "accounts" ? ["accounts", "list"] : ["dashboard", "overview", "7d"];
      const account = createAccountSummary({
        accountId: "acc_primary", usageLimitEnabled: false, usageLimitPercent: null, usageLimitState: "disabled",
      });
      const oldData = source === "accounts" ? { accounts: [account] } : createDashboardOverview({ accounts: [account] });
      queryClient.setQueryData(key, oldData);
      let resolveOldRead!: (value: typeof oldData) => void;
      const oldRead = new Promise<typeof oldData>((resolve) => { resolveOldRead = resolve; });
      const read = vi.fn(() => oldRead);
      const observer = renderHook(() => useQuery({ queryKey: key, queryFn: read }), {
        wrapper: createWrapper(queryClient),
      });
      await waitFor(() => expect(read).toHaveBeenCalledOnce());
      observer.unmount();
      expect(queryClient.getQueryCache().find({ queryKey: key })?.isActive()).toBe(false);

      const mutation = renderHook(() => useAccountMutations(), { wrapper: createWrapper(queryClient) });
      await act(async () => {
        await mutation.result.current.usageLimitMutation.mutateAsync({
          accountId: "acc_primary", update: { enabled: true, percent: 10 },
        });
      });
      expect(queryClient.getQueryData<{ accounts: AccountSummary[] }>(key)?.accounts[0]).toMatchObject({
        usageLimitEnabled: true, usageLimitPercent: 10,
      });
      await act(async () => {
        resolveOldRead(oldData);
        await oldRead;
      });
      expect(queryClient.getQueryData<{ accounts: AccountSummary[] }>(key)?.accounts[0]).toMatchObject({
        usageLimitEnabled: true, usageLimitPercent: 10,
      });
      mutation.unmount();
      queryClient.clear();
    },
  );

  it("serializes overlapping policy edits across controls and retains the last saved percentage", async () => {
    const queryClient = createTestQueryClient();
    queryClient.setQueryDefaults(["accounts"], { gcTime: Infinity });
    queryClient.setQueryData(["accounts", "list"], { accounts: [createAccountSummary({ accountId: "acc_primary" })] });
    let releaseFirst!: () => void;
    const firstReply = new Promise<void>((resolve) => { releaseFirst = resolve; });
    const requests: { enabled: boolean; percent?: number | null }[] = [];
    let percent: number | null = null;
    server.use(http.put("/api/accounts/:accountId/usage-limit", async ({ request, params }) => {
      const update = await request.json() as { enabled: boolean; percent?: number | null };
      requests.push(update);
      if (update.percent === 10) await firstReply;
      if (update.percent !== undefined) percent = update.percent;
      return HttpResponse.json({ accountId: String(params.accountId), enabled: update.enabled, percent });
    }));
    const first = renderHook(() => useAccountMutations(), { wrapper: createWrapper(queryClient) });
    const second = renderHook(() => useAccountMutations(), { wrapper: createWrapper(queryClient) });
    const enable = first.result.current.usageLimitMutation.mutateAsync({
      accountId: "acc_primary", update: { enabled: true, percent: 10 },
    });
    await waitFor(() => expect(requests).toHaveLength(1));
    const change = second.result.current.usageLimitMutation.mutateAsync({
      accountId: "acc_primary", update: { enabled: true, percent: 20 },
    });
    const disable = second.result.current.usageLimitMutation.mutateAsync({
      accountId: "acc_primary", update: { enabled: false },
    });
    await waitFor(() => expect(queryClient.getMutationCache().getAll()).toHaveLength(3));
    const paused = queryClient.getMutationCache().getAll().filter((mutation) => mutation.state.isPaused).length;
    await act(async () => {
      releaseFirst();
      await Promise.all([enable, change, disable]);
    });
    expect(paused).toBe(2);
    expect(requests).toEqual([{ enabled: true, percent: 10 }, { enabled: true, percent: 20 }, { enabled: false }]);
    expect(queryClient.getQueryData<{ accounts: AccountSummary[] }>(["accounts", "list"])?.accounts[0]).toMatchObject({
      usageLimitEnabled: false, usageLimitPercent: 20, usageLimitState: "disabled",
    });
    first.unmount();
    second.unmount();
    queryClient.clear();
  });

  it("reconciles usage-limit caches and waits only for the account list invalidation", async () => {
    const queryClient = createTestQueryClient();
    queryClient.setQueryDefaults(["dashboard"], { gcTime: Infinity });
    let releaseInvalidation!: () => void;
    const invalidation = new Promise<void>((resolve) => {
      releaseInvalidation = () => resolve();
    });
    const neverSettles = new Promise<void>(() => {});
    const invalidateSpy = vi
      .spyOn(queryClient, "invalidateQueries")
      .mockImplementation((filters) =>
        filters?.queryKey?.[0] === "accounts" ? invalidation : neverSettles,
      );
    const { result } = renderHook(() => useAccounts(), {
      wrapper: createWrapper(queryClient),
    });

    await waitFor(() => expect(result.current.accountsQuery.isSuccess).toBe(true));
    queryClient.setQueryData(
      ["dashboard", "overview", "7d"],
      createDashboardOverview({
        accounts: [
          createAccountSummary({
            accountId: "acc_primary",
            usageLimitEnabled: false,
            usageLimitPercent: null,
            usageLimitState: "disabled",
          }),
        ],
      }),
    );

    const mutationPromise = result.current.usageLimitMutation.mutateAsync({
      accountId: "acc_primary",
      update: { enabled: true, percent: 10 },
    });

    await waitFor(() => expect(invalidateSpy).toHaveBeenCalled());
    const accountsCache = queryClient.getQueryData<{ accounts: AccountSummary[] }>([
      "accounts",
      "list",
    ]);
    const dashboardCache = queryClient.getQueryData<DashboardOverview>([
      "dashboard",
      "overview",
      "7d",
    ]);
    expect(accountsCache?.accounts[0]).toMatchObject({
      accountId: "acc_primary",
      usageLimitEnabled: true,
      usageLimitPercent: 10,
      usageLimitState: "data_unavailable",
    });
    expect(dashboardCache?.accounts[0]).toMatchObject({
      accountId: "acc_primary",
      usageLimitEnabled: true,
      usageLimitPercent: 10,
      usageLimitState: "data_unavailable",
    });
    expect(result.current.usageLimitMutation.isPending).toBe(true);

    releaseInvalidation();
    await mutationPromise;
    await waitFor(() => expect(result.current.usageLimitMutation.isPending).toBe(false));
  });

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
    const usageLimitResult = await result.current.usageLimitMutation.mutateAsync({
      accountId: firstAccountId as string,
      update: { enabled: true, percent: 10 },
    });
    expect(usageLimitResult).toEqual({
      accountId: firstAccountId,
      enabled: true,
      percent: 10,
    });

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
