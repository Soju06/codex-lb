import type { ReactElement, ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CostPerDayChart } from "./cost-per-day-chart";

type ChartProps = { children: ReactNode; margin?: unknown; data?: unknown };

let capturedProps: ChartProps | null = null;
let capturedYAxisProps: { tickFormatter?: (value: number) => string } | null = null;

function findTooltipContent(node: ReactNode): ReactElement<{ formatValue?: (value: number) => string }> | null {
  if (!node || typeof node !== "object") return null;
  if (Array.isArray(node)) {
    for (const child of node) {
      const found = findTooltipContent(child);
      if (found) return found;
    }
    return null;
  }

  const element = node as ReactElement<{ content?: ReactElement<{ formatValue?: (value: number) => string }>; children?: ReactNode }>;
  if (element.props.content) return element.props.content;
  return findTooltipContent(element.props.children);
}

vi.mock("@/components/lazy-recharts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/lazy-recharts")>();

  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactNode }) => <div>{children}</div>,
    AreaChart: (props: { children: ReactNode; margin?: unknown; data?: unknown }) => {
      capturedProps = props;
      return <div data-testid="cost-area-chart">{props.children}</div>;
    },
    Area: () => null,
    XAxis: () => null,
    YAxis: (props: { tickFormatter?: (value: number) => string }) => {
      capturedYAxisProps = props;
      return null;
    },
    CartesianGrid: () => null,
    Tooltip: () => null,
  };
});

describe("CostPerDayChart", () => {
  beforeEach(() => {
    capturedProps = null;
    capturedYAxisProps = null;
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
            conversations: 0,
            inputTokens: 5_400_000,
            outputTokens: 59_000,
            reasoningTokens: 0,
            cachedInputTokens: 0,
            costUsd: 3.77,
            activeAccounts: 2,
            cancelledCount: 0,
            errorCount: 0,
          },
        ]}
      />,
    );

    expect(capturedProps?.margin).toEqual({ top: 5, right: 10, left: 10, bottom: 0 });
  });

  it("formats full-value Cost axis and tooltip amounts with grouping separators", () => {
    render(
      <CostPerDayChart
        startDate="2026-06-05"
        endDate="2026-06-05"
        data={[
          {
            date: "2026-06-05",
            requests: 1,
            conversations: 0,
            inputTokens: 0,
            outputTokens: 0,
            reasoningTokens: 0,
            cachedInputTokens: 0,
            costUsd: 1400,
            activeAccounts: 1,
            cancelledCount: 0,
            errorCount: 0,
          },
        ]}
      />,
    );

    const tooltip = findTooltipContent(capturedProps?.children);
    expect(capturedYAxisProps?.tickFormatter?.(1400)).toBe("$1,400.00");
    expect(tooltip?.props.formatValue?.(1400)).toBe("$1,400.00");
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
            conversations: 0,
            inputTokens: 5_400_000,
            outputTokens: 59_000,
            reasoningTokens: 0,
            cachedInputTokens: 0,
            costUsd: 3.77,
            activeAccounts: 2,
            cancelledCount: 0,
            errorCount: 0,
          },
          {
            date: "2026-06-07",
            requests: 179,
            conversations: 0,
            inputTokens: 6_800_000,
            outputTokens: 73_000,
            reasoningTokens: 0,
            cachedInputTokens: 0,
            costUsd: 4.54,
            activeAccounts: 2,
            cancelledCount: 0,
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

  it("shows no-data instead of a zero-filled series when daily rows are absent", () => {
    render(<CostPerDayChart startDate="2026-06-05" endDate="2026-06-07" data={[]} />);

    expect(screen.getByText("No data")).toBeInTheDocument();
    expect(screen.getByText("No usage recorded for the selected range.")).toBeInTheDocument();
    expect(capturedProps).toBeNull();
    expect(screen.queryByTestId("cost-area-chart")).not.toBeInTheDocument();
  });
});
