import type { ReactNode } from "react";
import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CostPerDayChart } from "./cost-per-day-chart";

let capturedProps: { margin?: unknown; data?: unknown } | null = null;

vi.mock("recharts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("recharts")>();

  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactNode }) => <div>{children}</div>,
    AreaChart: (props: { children: ReactNode; margin?: unknown; data?: unknown }) => {
      capturedProps = props;
      return <div data-testid="cost-area-chart" />;
    },
    Area: () => null,
    XAxis: () => null,
    YAxis: () => null,
    CartesianGrid: () => null,
    Tooltip: () => null,
  };
});

describe("CostPerDayChart", () => {
  beforeEach(() => {
    capturedProps = null;
  });

  it("uses equal left and right chart margins", () => {
    render(
      <CostPerDayChart
        startDate="2026-06-05"
        endDate="2026-06-05"
        data={[
          {
            date: "2026-06-05",
            requests: 150,
            inputTokens: 5_400_000,
            outputTokens: 59_000,
            cachedInputTokens: 0,
            costUsd: 3.77,
            activeAccounts: 2,
            errorCount: 0,
          },
        ]}
      />,
    );

    expect(capturedProps?.margin).toEqual({ top: 5, right: 10, left: 10, bottom: 0 });
  });

  it("fills missing selected days with zero-value rows", () => {
    render(
      <CostPerDayChart
        startDate="2026-06-05"
        endDate="2026-06-07"
        data={[
          {
            date: "2026-06-05",
            requests: 150,
            inputTokens: 5_400_000,
            outputTokens: 59_000,
            cachedInputTokens: 0,
            costUsd: 3.77,
            activeAccounts: 2,
            errorCount: 0,
          },
          {
            date: "2026-06-07",
            requests: 179,
            inputTokens: 6_800_000,
            outputTokens: 73_000,
            cachedInputTokens: 0,
            costUsd: 4.54,
            activeAccounts: 2,
            errorCount: 0,
          },
        ]}
      />,
    );

    expect(capturedProps?.data).toEqual([
      { date: "06-05", cost: 3.77 },
      { date: "06-06", cost: 0 },
      { date: "06-07", cost: 4.54 },
    ]);
  });
});
