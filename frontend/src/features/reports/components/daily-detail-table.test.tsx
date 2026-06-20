import userEvent from "@testing-library/user-event";
import { cleanup, render, screen, within } from "@testing-library/react";
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

  it("sorts by day descending by default", () => {
    render(
      <DailyDetailTable
        startDate="2026-06-05"
        endDate="2026-06-07"
        data={[
          {
            date: "2026-06-05",
            requests: 1,
            inputTokens: 100,
            outputTokens: 20,
            cachedInputTokens: 0,
            costUsd: 1,
            activeAccounts: 1,
            errorCount: 0,
          },
          {
            date: "2026-06-07",
            requests: 3,
            inputTokens: 300,
            outputTokens: 40,
            cachedInputTokens: 50,
            costUsd: 2,
            activeAccounts: 2,
            errorCount: 0,
          },
        ]}
      />,
    );

    const rows = screen.getAllByTestId(/daily-breakdown-row-/);

    expect(rows.map((row) => row.getAttribute("data-testid"))).toEqual([
      "daily-breakdown-row-2026-06-07",
      "daily-breakdown-row-2026-06-06",
      "daily-breakdown-row-2026-06-05",
    ]);
  });

  it("toggles sorting when a header is clicked", async () => {
    const user = userEvent.setup();

    render(
      <DailyDetailTable
        startDate="2026-06-05"
        endDate="2026-06-07"
        data={[
          {
            date: "2026-06-05",
            requests: 8,
            inputTokens: 100,
            outputTokens: 20,
            cachedInputTokens: 0,
            costUsd: 1,
            activeAccounts: 1,
            errorCount: 0,
          },
          {
            date: "2026-06-06",
            requests: 2,
            inputTokens: 200,
            outputTokens: 30,
            cachedInputTokens: 0,
            costUsd: 2,
            activeAccounts: 1,
            errorCount: 0,
          },
          {
            date: "2026-06-07",
            requests: 5,
            inputTokens: 300,
            outputTokens: 40,
            cachedInputTokens: 0,
            costUsd: 3,
            activeAccounts: 1,
            errorCount: 0,
          },
        ]}
      />,
    );

    await user.click(screen.getByRole("button", { name: /reqs/i }));

    let rows = screen.getAllByTestId(/daily-breakdown-row-/);
    expect(rows.map((row) => row.getAttribute("data-testid"))).toEqual([
      "daily-breakdown-row-2026-06-06",
      "daily-breakdown-row-2026-06-07",
      "daily-breakdown-row-2026-06-05",
    ]);

    await user.click(screen.getByRole("button", { name: /reqs/i }));

    rows = screen.getAllByTestId(/daily-breakdown-row-/);
    expect(rows.map((row) => row.getAttribute("data-testid"))).toEqual([
      "daily-breakdown-row-2026-06-05",
      "daily-breakdown-row-2026-06-07",
      "daily-breakdown-row-2026-06-06",
    ]);
  });

  it.each([
    ["Day", "daily-breakdown-row-2026-06-05"],
    ["Reqs", "daily-breakdown-row-2026-06-06"],
    ["Input Tokens", "daily-breakdown-row-2026-06-05"],
    ["Output Tokens", "daily-breakdown-row-2026-06-05"],
    ["Cost", "daily-breakdown-row-2026-06-05"],
    ["Accounts", "daily-breakdown-row-2026-06-06"],
  ])("sorts by %s when its header is clicked", async (headerLabel, expectedFirstRow) => {
    cleanup();
    const user = userEvent.setup();

    render(
      <DailyDetailTable
        startDate="2026-06-05"
        endDate="2026-06-07"
        data={[
          {
            date: "2026-06-05",
            requests: 8,
            inputTokens: 100,
            outputTokens: 20,
            cachedInputTokens: 0,
            costUsd: 1,
            activeAccounts: 3,
            errorCount: 0,
          },
          {
            date: "2026-06-06",
            requests: 2,
            inputTokens: 200,
            outputTokens: 30,
            cachedInputTokens: 0,
            costUsd: 2,
            activeAccounts: 1,
            errorCount: 0,
          },
          {
            date: "2026-06-07",
            requests: 5,
            inputTokens: 300,
            outputTokens: 40,
            cachedInputTokens: 0,
            costUsd: 3,
            activeAccounts: 2,
            errorCount: 0,
          },
        ]}
      />,
    );

    await user.click(screen.getByRole("button", { name: headerLabel }));

    expect(screen.getAllByTestId(/daily-breakdown-row-/)[0]).toHaveAttribute(
      "data-testid",
      expectedFirstRow,
    );
  });

  it("renders cached tokens inline inside the input tokens cell", () => {
    render(
      <DailyDetailTable
        startDate="2026-06-05"
        endDate="2026-06-05"
        data={[
          {
            date: "2026-06-05",
            requests: 1,
            inputTokens: 1_200_000,
            outputTokens: 20,
            cachedInputTokens: 960_000,
            costUsd: 1,
            activeAccounts: 1,
            errorCount: 0,
          },
        ]}
      />,
    );

    const row = screen.getByTestId("daily-breakdown-row-2026-06-05");
    expect(within(row).getByText("1.2M")).toBeInTheDocument();
    expect(within(row).getByText("(960K)")).toBeInTheDocument();
  });

  it("renders zero cached tokens explicitly when both token values are zero", () => {
    render(
      <DailyDetailTable
        startDate="2026-06-05"
        endDate="2026-06-05"
        data={[
          {
            date: "2026-06-05",
            requests: 1,
            inputTokens: 0,
            outputTokens: 20,
            cachedInputTokens: 0,
            costUsd: 1,
            activeAccounts: 1,
            errorCount: 0,
          },
        ]}
      />,
    );

    const row = screen.getByTestId("daily-breakdown-row-2026-06-05");
    expect(within(row).getByText("0")).toBeInTheDocument();
    expect(within(row).getByText("(0)")).toBeInTheDocument();
  });
});
