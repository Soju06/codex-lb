import type { ReactNode } from "react";
import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { QueueWaitChart } from "./queue-wait-chart";

let capturedProps: { margin?: unknown; data?: unknown } | null = null;

vi.mock("@/components/lazy-recharts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/lazy-recharts")>();

  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactNode }) => <div>{children}</div>,
    AreaChart: (props: { children: ReactNode; margin?: unknown; data?: unknown }) => {
      capturedProps = props;
      return <div data-testid="queue-area-chart" />;
    },
    Area: () => null,
    XAxis: () => null,
    YAxis: () => null,
    CartesianGrid: () => null,
    Tooltip: () => null,
  };
});

const BASE_ROW = {
  requests: 10,
  conversations: 0,
  inputTokens: 1_000,
  outputTokens: 100,
  cachedInputTokens: 0,
  costUsd: 0.5,
  activeAccounts: 1,
  errorCount: 0,
  medianTtftMs: 200,
  medianTps: 25,
};

describe("QueueWaitChart", () => {
  beforeEach(() => {
    capturedProps = null;
  });

  it("plots the daily median queue wait", () => {
    render(
      <QueueWaitChart
        startDate="2026-07-14"
        endDate="2026-07-15"
        data={[
          { ...BASE_ROW, date: "2026-07-14", medianQueueMs: 420 },
          { ...BASE_ROW, date: "2026-07-15", medianQueueMs: 60 },
        ]}
      />,
    );

    expect(capturedProps?.data).toEqual([
      { date: "07-14", queue: 420 },
      { date: "07-15", queue: 60 },
    ]);
  });

  it("fills missing selected days with zero queue wait", () => {
    render(
      <QueueWaitChart
        startDate="2026-07-14"
        endDate="2026-07-16"
        data={[{ ...BASE_ROW, date: "2026-07-15", medianQueueMs: 90 }]}
      />,
    );

    expect(capturedProps?.data).toEqual([
      { date: "07-14", queue: 0 },
      { date: "07-15", queue: 90 },
      { date: "07-16", queue: 0 },
    ]);
  });
});
