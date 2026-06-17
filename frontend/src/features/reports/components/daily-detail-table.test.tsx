import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DailyDetailTable } from "./daily-detail-table";

describe("DailyDetailTable", () => {
  it("fills missing days with zero rows and keeps the body scrollable", () => {
    render(
      <DailyDetailTable
        startDate="2026-06-05"
        endDate="2026-06-12"
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

    const filledRow = screen.getByTestId("daily-breakdown-row-2026-06-05");
    const zeroRow = screen.getByTestId("daily-breakdown-row-2026-06-06");

    expect(within(zeroRow).getByText("2026-06-06")).toBeInTheDocument();
    expect(within(zeroRow).getByText("$0.00")).toBeInTheDocument();
    expect(zeroRow.className).toBe(filledRow.className);
    expect(screen.getByTestId("daily-breakdown-scroll-body")).toHaveClass(
      "overflow-y-auto",
    );
  });

  it("renders existing rows when a date bound is cleared", () => {
    render(
      <DailyDetailTable
        startDate=""
        endDate="2026-06-12"
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

    expect(screen.getByTestId("daily-breakdown-row-2026-06-05")).toBeInTheDocument();
    expect(
      screen.queryByTestId("daily-breakdown-row-2026-06-06"),
    ).not.toBeInTheDocument();
  });
});
