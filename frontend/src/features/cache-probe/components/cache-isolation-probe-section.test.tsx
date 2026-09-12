import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CacheIsolationProbeSection } from "@/features/cache-probe/components/cache-isolation-probe-section";
import { useCacheIsolationProbe } from "@/features/cache-probe/hooks/use-cache-isolation-probe";
import type { CacheProbePlan, CacheProbeRun } from "@/features/cache-probe/schemas";

vi.mock("@/features/cache-probe/hooks/use-cache-isolation-probe", () => ({
  useCacheIsolationProbe: vi.fn(),
}));

const useCacheIsolationProbeMock = useCacheIsolationProbe as unknown as ReturnType<typeof vi.fn>;

function plan(overrides: Partial<CacheProbePlan> = {}): CacheProbePlan {
  return {
    model: "gpt-probe",
    seedAccount: { accountId: "a-0", label: "Seed" },
    availableOtherAccounts: [
      { accountId: "a-1", label: "Two" },
      { accountId: "a-2", label: "Three" },
      { accountId: "a-3", label: "Four" },
      { accountId: "a-4", label: "Five" },
    ],
    seedRepetitions: 3,
    totalCalls: 7,
    estimatedInputTokensPerCall: 28_000,
    estimatedTotalInputTokens: 196_000,
    maxSeedRepetitions: 5,
    maxOtherAccounts: 5,
    pressure: {
      underPressure: false,
      reason: null,
      detail: null,
      selectableAccountCount: 5,
      eligibleAccountCount: 5,
      pressuredAccountCount: 0,
    },
    ...overrides,
  };
}

function run(overrides: Partial<CacheProbeRun> = {}): CacheProbeRun {
  return {
    runId: "run-1",
    model: "gpt-probe",
    startedAt: "2026-09-11T00:00:00Z",
    completedAt: "2026-09-11T00:01:00Z",
    seedAccount: { accountId: "a-0", label: "Seed" },
    seedRepetitions: 1,
    calls: [
      {
        sequence: 1,
        accountId: "a-0",
        accountLabel: "Seed",
        role: "seed",
        status: "miss",
        cacheHit: false,
        inputTokens: 28_168,
        cachedTokens: 0,
        latencyMs: 1100,
        errorCode: null,
      },
      {
        sequence: 2,
        accountId: "a-1",
        accountLabel: "Two",
        role: "other",
        status: "hit",
        cacheHit: true,
        inputTokens: 28_168,
        cachedTokens: 28_032,
        latencyMs: 900,
        errorCode: null,
      },
    ],
    seedHitCount: 0,
    seedCallCount: 1,
    otherHitCount: 1,
    otherCallCount: 1,
    crossAccountHit: true,
    verdict: "cross_account_sharing",
    ...overrides,
  };
}

function mockHook(overrides: Record<string, unknown> = {}) {
  const mutate = vi.fn();
  useCacheIsolationProbeMock.mockReturnValue({
    planQuery: { data: plan(), error: null, isLoading: false },
    runMutation: { mutate, isPending: false, error: null },
    result: null,
    seedRepetitions: 3,
    otherAccountCount: 4,
    setSeedRepetitions: vi.fn(),
    setOtherAccountCount: vi.fn(),
    ...overrides,
  });
  return mutate;
}

describe("CacheIsolationProbeSection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows the estimated token cost before anything is spent", () => {
    mockHook();

    render(<CacheIsolationProbeSection />);

    expect(screen.getByTestId("cache-isolation-probe-cost")).toHaveTextContent("7 calls");
    expect(screen.getByTestId("cache-isolation-probe-cost")).toHaveTextContent("196,000");
    expect(screen.queryByTestId("cache-isolation-probe-result")).not.toBeInTheDocument();
  });

  it("does not run until the operator confirms in the dialog", async () => {
    const user = userEvent.setup();
    const mutate = mockHook();

    render(<CacheIsolationProbeSection />);
    await user.click(screen.getByRole("button", { name: "Run probe" }));

    expect(mutate).not.toHaveBeenCalled();
    expect(screen.getByText("Run the cache isolation probe?")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Spend quota and run" }));

    expect(mutate).toHaveBeenCalledTimes(1);
  });

  it("refuses to offer the run while the pool is under pressure", () => {
    mockHook({
      planQuery: {
        data: plan({
          pressure: {
            underPressure: true,
            reason: "pool_under_pressure",
            detail: "2 of 8 routable accounts are rate-limited or quota-exceeded.",
            selectableAccountCount: 8,
            eligibleAccountCount: 6,
            pressuredAccountCount: 2,
          },
        }),
        error: null,
        isLoading: false,
      },
    });

    render(<CacheIsolationProbeSection />);

    expect(
      screen.getByText("2 of 8 routable accounts are rate-limited or quota-exceeded."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run probe" })).toBeDisabled();
  });

  it("tells the operator that a seed miss is expected, not a failure", () => {
    mockHook();

    render(<CacheIsolationProbeSection />);

    expect(
      screen.getByText(/a miss on the seed account is expected and is not a failure of the probe/i),
    ).toBeInTheDocument();
  });

  it("renders one row per call and the cross-account verdict", () => {
    mockHook({ result: run() });

    render(<CacheIsolationProbeSection />);

    const table = screen.getByTestId("cache-isolation-probe-result");
    expect(table).toHaveTextContent("Shared across accounts");
    expect(screen.getAllByRole("row")).toHaveLength(3);
    expect(table).toHaveTextContent("28,032 / 28,168");
    expect(table).toHaveTextContent("Hit");
    expect(table).toHaveTextContent("Miss");
  });

  it("labels an inconclusive run as inconclusive rather than isolated", () => {
    mockHook({
      result: run({
        crossAccountHit: false,
        otherHitCount: 0,
        verdict: "inconclusive",
        calls: run().calls.map((call) => ({ ...call, status: "miss", cacheHit: false, cachedTokens: 0 })),
      }),
    });

    render(<CacheIsolationProbeSection />);

    expect(screen.getByTestId("cache-isolation-probe-result")).toHaveTextContent("Inconclusive");
    expect(screen.getByTestId("cache-isolation-probe-result")).not.toHaveTextContent("Shared across accounts");
  });
});
