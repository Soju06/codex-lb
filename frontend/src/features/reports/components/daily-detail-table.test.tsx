import userEvent from "@testing-library/user-event";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DailyDetailTable } from "./daily-detail-table";

afterEach(() => {
  vi.restoreAllMocks();
});

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

  it("exports csv rows in chronological order regardless of visible sort", async () => {
    const user = userEvent.setup();
    const blobText = vi.fn(async () => "");
    const createObjectURL = vi.spyOn(URL, "createObjectURL").mockImplementation((blob) => {
      if (!(blob instanceof Blob)) {
        throw new TypeError("expected Blob export payload");
      }
      blobText.mockImplementation(() => blob.text());
      return "blob:daily-breakdown";
    });
    const revokeObjectURL = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

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
            cachedInputTokens: 1,
            costUsd: 1,
            activeAccounts: 3,
            errorCount: 0,
          },
          {
            date: "2026-06-06",
            requests: 2,
            inputTokens: 200,
            outputTokens: 30,
            cachedInputTokens: 2,
            costUsd: 2,
            activeAccounts: 1,
            errorCount: 0,
          },
          {
            date: "2026-06-07",
            requests: 5,
            inputTokens: 300,
            outputTokens: 40,
            cachedInputTokens: 3,
            costUsd: 3,
            activeAccounts: 2,
            errorCount: 0,
          },
        ]}
      />,
    );

    await user.click(screen.getByRole("button", { name: /reqs/i }));
    await user.click(screen.getByRole("button", { name: /csv/i }));

    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(clickSpy).toHaveBeenCalledOnce();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:daily-breakdown");
    await expect(blobText()).resolves.toBe(
      [
        "Date,Requests,Input Tokens,Output Tokens,Cached Tokens,Cost USD,Active Accounts,Errors",
        "2026-06-05,8,100,20,1,1.0000,3,0",
        "2026-06-06,2,200,30,2,2.0000,1,0",
        "2026-06-07,5,300,40,3,3.0000,2,0",
      ].join("\n"),
    );
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

  it("shows visible sort icons for active and inactive sortable headers", async () => {
    const user = userEvent.setup();

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
            requests: 3,
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

    const dayHeader = screen.getByRole("columnheader", { name: /day/i });
    const reqsHeader = screen.getByRole("columnheader", { name: /reqs/i });
    const dayButton = screen.getByRole("button", { name: /day/i });
    const reqsButton = screen.getByRole("button", { name: /reqs/i });
    const dayIcon = dayButton.querySelector('[data-testid="sort-icon-desc"]');
    const reqsIcon = reqsButton.querySelector('[data-testid="sort-icon-none"]');

    expect(dayIcon).toBeTruthy();
    expect(dayIcon).toHaveAttribute("data-sort-icon", "down");
    expect(dayIcon).toHaveClass("text-foreground");
    expect(reqsIcon).toBeTruthy();
    expect(reqsIcon).toHaveAttribute("data-sort-icon", "up-down");
    expect(reqsIcon).toHaveClass("text-muted-foreground/60");

    expect(dayHeader).toHaveAttribute("aria-sort", "descending");
    expect(reqsHeader).toHaveAttribute("aria-sort", "none");
    expect(dayButton).not.toHaveAttribute("aria-sort");
    expect(reqsButton).not.toHaveAttribute("aria-sort");

    await user.click(reqsButton);

    const activeReqsIcon = reqsButton.querySelector('[data-testid="sort-icon-asc"]');
    const inactiveDayIcon = dayButton.querySelector('[data-testid="sort-icon-none"]');

    expect(dayHeader).toHaveAttribute("aria-sort", "none");
    expect(reqsHeader).toHaveAttribute("aria-sort", "ascending");
    expect(dayButton).not.toHaveAttribute("aria-sort");
    expect(reqsButton).not.toHaveAttribute("aria-sort");
    expect(activeReqsIcon).toBeTruthy();
    expect(activeReqsIcon).toHaveAttribute("data-sort-icon", "up");
    expect(activeReqsIcon).toHaveClass("text-foreground");
    expect(inactiveDayIcon).toBeTruthy();
    expect(inactiveDayIcon).toHaveAttribute("data-sort-icon", "up-down");
    expect(inactiveDayIcon).toHaveClass("text-muted-foreground/60");
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
