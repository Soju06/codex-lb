import type { ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TokensPerSecondChart } from "./tokens-per-second-chart";

let chartData: unknown;
vi.mock("@/components/lazy-recharts", () => ({
  ResponsiveContainer: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  AreaChart: ({ data }: { data: unknown }) => { chartData = data; return <div />; },
  Area: () => null,
  XAxis: () => null,
  YAxis: () => null,
  CartesianGrid: () => null,
  Tooltip: () => null,
}));

const row = {
  date: "2026-09-15", requests: 5, conversations: 0, inputTokens: 1000,
  outputTokens: 200, reasoningTokens: 40, cachedInputTokens: 0, costUsd: 0,
  activeAccounts: 1, cancelledCount: 0, errorCount: 0,
};

describe("TokensPerSecondChart", () => {
  beforeEach(() => { chartData = undefined; });

  it("shows qualified sample coverage and keeps missing days as gaps", () => {
    render(<TokensPerSecondChart startDate="2026-09-14" endDate="2026-09-16"
      data={[{ ...row, medianTps: 200, tpsSampleCount: 2 }]} />);
    expect(chartData).toEqual([
      { date: "09-14", tps: null }, { date: "09-15", tps: 200 }, { date: "09-16", tps: null },
    ]);
    expect(screen.getByText(/2 qualified samples/)).toBeInTheDocument();
  });

  it("does not plot a measured zero when all speed samples are unavailable", () => {
    render(<TokensPerSecondChart startDate="2026-09-15" endDate="2026-09-15"
      data={[{ ...row, medianTps: null, tpsSampleCount: 0 }]} />);
    expect(chartData).toBeUndefined();
    expect(screen.getByText(/No qualified output-speed samples/)).toBeInTheDocument();
  });
});
