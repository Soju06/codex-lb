import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as automationsApi from "@/features/automations/api";
import { useAutomationListing } from "@/features/automations/hooks/use-automation-listing";

vi.mock("@/features/automations/api", () => ({
  listAutomations: vi.fn(async () => ({ items: [], total: 0, hasMore: false })),
  listAutomationRunsPage: vi.fn(async () => ({ items: [], total: 0, hasMore: false })),
  getAutomationJobOptions: vi.fn(async () => ({
    accountIds: [],
    models: [],
    statuses: [],
    scheduleTypes: [],
  })),
  getAutomationRunOptions: vi.fn(async () => ({
    accountIds: [],
    models: [],
    statuses: [],
    triggers: [],
  })),
}));

function createWrapper(initialEntry = "/automations") {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  function Wrapper({ children }: PropsWithChildren) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[initialEntry]}>{children}</MemoryRouter>
      </QueryClientProvider>
    );
  }
  return { queryClient, Wrapper };
}

describe("useAutomationListing", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("uses defaults and fetches jobs/runs/options", async () => {
    const { Wrapper } = createWrapper();
    const { result } = renderHook(() => useAutomationListing(), { wrapper: Wrapper });

    await waitFor(() => expect(result.current.jobsQuery.isSuccess).toBe(true));
    await waitFor(() => expect(result.current.runsQuery.isSuccess).toBe(true));
    await waitFor(() => expect(result.current.jobOptionsQuery.isSuccess).toBe(true));
    await waitFor(() => expect(result.current.runOptionsQuery.isSuccess).toBe(true));

    expect(result.current.jobsFilters).toEqual({
      search: "",
      accountIds: [],
      models: [],
      statuses: [],
      scheduleTypes: [],
      limit: 25,
      offset: 0,
    });
    expect(result.current.runsFilters).toEqual({
      search: "",
      accountIds: [],
      models: [],
      statuses: [],
      triggers: [],
      limit: 25,
      offset: 0,
    });
    expect(vi.mocked(automationsApi.listAutomations)).toHaveBeenCalledWith({
      limit: 25,
      offset: 0,
      search: undefined,
      accountIds: [],
      models: [],
      statuses: [],
      scheduleTypes: [],
    });
    expect(vi.mocked(automationsApi.listAutomationRunsPage)).toHaveBeenCalledWith({
      limit: 25,
      offset: 0,
      search: undefined,
      accountIds: [],
      models: [],
      statuses: [],
      triggers: [],
    });
  });

  it("parses url filters, updates jobs/runs filters, and resets both", async () => {
    const { Wrapper } = createWrapper(
      "/automations?jobsSearch=alpha&jobsAccountId=acc-1&jobsModel=gpt-5&jobsStatus=running&jobsScheduleType=daily&jobsLimit=10&jobsOffset=5&runsSearch=beta&runsAccountId=acc-2&runsModel=gpt-4&runsStatus=partial&runsTrigger=manual&runsLimit=15&runsOffset=9",
    );
    const { result } = renderHook(() => useAutomationListing(), { wrapper: Wrapper });

    await waitFor(() => expect(result.current.jobsQuery.isSuccess).toBe(true));

    expect(result.current.jobsFilters.search).toBe("alpha");
    expect(result.current.runsFilters.search).toBe("beta");
    expect(result.current.jobsFilters.limit).toBe(10);
    expect(result.current.runsFilters.limit).toBe(15);

    act(() => {
      result.current.updateJobsFilters({
        search: "gamma",
        accountIds: ["acc-9"],
        models: ["gpt-5-mini"],
        statuses: ["success"],
        scheduleTypes: ["daily"],
        limit: 8,
        offset: 16,
      });
    });
    await waitFor(() => expect(result.current.jobsFilters.search).toBe("gamma"));
    expect(result.current.jobsFilters.accountIds).toEqual(["acc-9"]);
    expect(result.current.jobsFilters.offset).toBe(16);

    act(() => {
      result.current.updateRunsFilters({
        search: "delta",
        accountIds: ["acc-7"],
        models: ["gpt-5.4-mini"],
        statuses: ["failed"],
        triggers: ["scheduled"],
        limit: 6,
        offset: 12,
      });
    });
    await waitFor(() => expect(result.current.runsFilters.search).toBe("delta"));
    expect(result.current.runsFilters.accountIds).toEqual(["acc-7"]);
    expect(result.current.runsFilters.offset).toBe(12);

    act(() => {
      result.current.resetJobsFilters();
    });
    await waitFor(() => expect(result.current.jobsFilters.search).toBe(""));
    expect(result.current.jobsFilters.limit).toBe(25);

    act(() => {
      result.current.resetRunsFilters();
    });
    await waitFor(() => expect(result.current.runsFilters.search).toBe(""));
    expect(result.current.runsFilters.search).toBe("");
    expect(result.current.runsFilters.limit).toBe(25);
  });

  it("normalizes invalid numeric params to safe defaults", async () => {
    const { Wrapper } = createWrapper(
      "/automations?jobsLimit=0&jobsOffset=-4&runsLimit=nan&runsOffset=oops",
    );
    const { result } = renderHook(() => useAutomationListing(), { wrapper: Wrapper });

    await waitFor(() => expect(result.current.jobsQuery.isSuccess).toBe(true));

    expect(result.current.jobsFilters.limit).toBe(1);
    expect(result.current.jobsFilters.offset).toBe(0);
    expect(result.current.runsFilters.limit).toBe(25);
    expect(result.current.runsFilters.offset).toBe(0);
  });
});
