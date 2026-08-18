import userEvent from "@testing-library/user-event";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useDateDisplayFormatStore } from "@/hooks/use-date-format";
import { formatReportBucketDate } from "../date";
import { buildContinuousDailyRows } from "../daily-series";
import type { DailyReportRow } from "../schemas";

import {
  DailyDetailTable as DailyDetailTableImpl,
  type DailyDetailTableProps,
} from "./daily-detail-table";

type DailyDetailTableFixtureRow = Omit<
  DailyReportRow,
  "cancelledCount" | "reasoningTokens"
> & {
  cancelledCount?: number;
  reasoningTokens?: number | null;
};

function DailyDetailTable({
  data,
  ...props
}: Omit<DailyDetailTableProps, "data"> & { data: DailyDetailTableFixtureRow[] }) {
  return (
    <DailyDetailTableImpl
      {...props}
      data={data.map((row) => ({ cancelledCount: 0, reasoningTokens: 0, ...row }))}
    />
  );
}

beforeEach(() => {
  useDateDisplayFormatStore.setState({ dateDisplayFormat: "default" });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("DailyDetailTable", () => {
  it("updates the Day column when the date display format changes", () => {
    const date = "2026-06-05";
    render(
      <DailyDetailTable
        startDate={date}
        endDate={date}
        data={[
          {
            date,
            requests: 1,
            conversations: 0,
            inputTokens: 100,
            outputTokens: 20,
            cachedInputTokens: 0,
            costUsd: 1,
            activeAccounts: 1,
            errorCount: 0,
          },
        ]}
      />,
    );

    const row = screen.getByTestId(`daily-breakdown-row-${date}`);
    expect(within(row).getByText(formatReportBucketDate(date, "default"))).toBeInTheDocument();

    act(() => {
      useDateDisplayFormatStore.setState({ dateDisplayFormat: "iso8601" });
    });

    expect(within(row).getByText(formatReportBucketDate(date, "iso8601"))).toBeInTheDocument();

    act(() => {
      useDateDisplayFormatStore.setState({ dateDisplayFormat: "default" });
    });

    expect(within(row).getByText(formatReportBucketDate(date, "default"))).toBeInTheDocument();
  });

  it("fills missing days with zero rows and keeps the body scrollable", () => {
    render(
      <DailyDetailTable
        startDate="2026-06-05"
        endDate="2026-06-12"
        data={[
          {
            date: "2026-06-05",
            requests: 150,
            conversations: 0,
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
            conversations: 0,
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

    expect(
      within(zeroRow).getByText(formatReportBucketDate("2026-06-06", "default")),
    ).toBeInTheDocument();
    expect(within(zeroRow).getByText("$0.00")).toBeInTheDocument();
    expect(zeroRow.className).toBe(filledRow.className);
    expect(screen.getByTestId("daily-breakdown-scroll-body")).toHaveClass(
      "overflow-y-auto",
    );
  });

  it("renders grouped currency in full-value Cost cells", () => {
    render(
      <DailyDetailTable
        startDate="2026-06-05"
        endDate="2026-06-05"
        data={[
          {
            date: "2026-06-05",
            requests: 1,
            conversations: 0,
            inputTokens: 0,
            outputTokens: 0,
            cachedInputTokens: 0,
            costUsd: 1400,
            activeAccounts: 1,
            errorCount: 0,
          },
        ]}
      />,
    );

    expect(within(screen.getByTestId("daily-breakdown-row-2026-06-05")).getByText("$1,400.00")).toBeInTheDocument();
  });

  it("zero-fills cancelled counts for dates missing from the response", () => {
    const rows = buildContinuousDailyRows("2026-06-05", "2026-06-06", [
      {
        date: "2026-06-05",
        requests: 4,
        conversations: 0,
        inputTokens: 100,
        outputTokens: 20,
        reasoningTokens: 12,
        cachedInputTokens: 0,
        costUsd: 1,
        activeAccounts: 1,
        errorCount: 1,
        cancelledCount: 2,
      },
    ]);

    expect(Reflect.get(rows[0] ?? {}, "cancelledCount")).toBe(2);
    expect(Reflect.get(rows[1] ?? {}, "cancelledCount")).toBe(0);
    expect(rows[0]?.reasoningTokens).toBe(12);
    expect(rows[1]?.reasoningTokens).toBe(0);
    expect(rows[0]?.requests).toBe(4);
    expect(rows[0]?.errorCount).toBe(1);
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
            conversations: 0,
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
            conversations: 0,
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
            conversations: 0,
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
            conversations: 0,
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
            conversations: 0,
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
            conversations: 0,
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

  it("renders requests, cancelled, and errors as distinct daily values", () => {
    render(
      <DailyDetailTable
        startDate="2026-06-05"
        endDate="2026-06-05"
        data={[
          {
            date: "2026-06-05",
            requests: 4,
            conversations: 0,
            inputTokens: 100,
            outputTokens: 20,
            cachedInputTokens: 0,
            costUsd: 1,
            activeAccounts: 1,
            errorCount: 1,
            cancelledCount: 2,
          },
        ]}
      />,
    );

    expect.soft(screen.queryByRole("columnheader", { name: "Reqs" })).toBeInTheDocument();
    expect.soft(screen.queryByRole("columnheader", { name: "Cancelled" })).toBeInTheDocument();
    expect.soft(screen.queryByRole("columnheader", { name: "Errors" })).toBeInTheDocument();

    const row = screen.getByTestId("daily-breakdown-row-2026-06-05");
    const cells = Array.from(row.querySelectorAll("td"), (cell) => cell.textContent?.trim());
    expect.soft(cells).toContain("4");
    expect.soft(cells).toContain("2");
    expect.soft(cells).toContain("1");
  });

  it("exports localized cancellation values and preserves requests and errors", async () => {
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
        endDate="2026-06-06"
        data={[
          {
            date: "2026-06-05",
            requests: 4,
            conversations: 0,
            inputTokens: 100,
            outputTokens: 20,
            reasoningTokens: 12,
            cachedInputTokens: 1,
            costUsd: 1,
            activeAccounts: 3,
            errorCount: 1,
            cancelledCount: 2,
          },
        ]}
      />,
    );

    await user.click(screen.getByRole("button", { name: /csv/i }));

    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(clickSpy).toHaveBeenCalledOnce();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:daily-breakdown");
    await expect(blobText()).resolves.toBe(
      [
        "Date,Requests,Conversations,Input Tokens,Output Tokens,Reported Reasoning Tokens,Cached Tokens,Cost USD,Active Accounts,Cancelled,Errors",
        "2026-06-05,4,0,100,20,12,1,1.0000,3,2,1",
        "2026-06-06,0,0,0,0,0,0,0.0000,0,0,0",
      ].join("\n"),
    );
  });

  it("renders and exports unknown reasoning separately from known zero and sorts unknown last", async () => {
    const user = userEvent.setup();
    const blobText = vi.fn(async () => "");
    vi.spyOn(URL, "createObjectURL").mockImplementation((blob) => {
      if (!(blob instanceof Blob)) {
        throw new TypeError("expected Blob export payload");
      }
      blobText.mockImplementation(() => blob.text());
      return "blob:nullable-reasoning";
    });
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    render(
      <DailyDetailTable
        startDate="2026-06-05"
        endDate="2026-06-07"
        data={[
          {
            date: "2026-06-05",
            requests: 1,
            conversations: 0,
            inputTokens: 100,
            outputTokens: 20,
            reasoningTokens: null,
            cachedInputTokens: 0,
            costUsd: 1,
            activeAccounts: 1,
            errorCount: 0,
          },
          {
            date: "2026-06-06",
            requests: 2,
            conversations: 0,
            inputTokens: 200,
            outputTokens: 30,
            reasoningTokens: 0,
            cachedInputTokens: 0,
            costUsd: 2,
            activeAccounts: 1,
            errorCount: 0,
          },
          {
            date: "2026-06-07",
            requests: 3,
            conversations: 0,
            inputTokens: 300,
            outputTokens: 40,
            reasoningTokens: 5,
            cachedInputTokens: 0,
            costUsd: 3,
            activeAccounts: 1,
            errorCount: 0,
          },
        ]}
      />,
    );

    const unknownCells = screen
      .getByTestId("daily-breakdown-row-2026-06-05")
      .querySelectorAll("td");
    const zeroCells = screen
      .getByTestId("daily-breakdown-row-2026-06-06")
      .querySelectorAll("td");
    expect(unknownCells[5]?.textContent?.trim()).toBe("—");
    expect(zeroCells[5]?.textContent?.trim()).toBe("0");

    await user.click(screen.getByRole("button", { name: "Reported Reasoning Tokens" }));
    expect(
      screen.getAllByTestId(/daily-breakdown-row-/).map((row) => row.dataset.testid),
    ).toEqual([
      "daily-breakdown-row-2026-06-06",
      "daily-breakdown-row-2026-06-07",
      "daily-breakdown-row-2026-06-05",
    ]);

    await user.click(screen.getByRole("button", { name: /csv/i }));
    const csvLines = (await blobText()).split("\n");
    expect(csvLines[1]?.split(",")[5]).toBe("");
    expect(csvLines[2]?.split(",")[5]).toBe("0");
    expect(csvLines[3]?.split(",")[5]).toBe("5");
  });

  it.each([
    ["Day", "daily-breakdown-row-2026-06-05"],
    ["Reqs", "daily-breakdown-row-2026-06-06"],
    ["Input Tokens", "daily-breakdown-row-2026-06-05"],
    ["Output Tokens", "daily-breakdown-row-2026-06-05"],
    ["Reported Reasoning Tokens", "daily-breakdown-row-2026-06-06"],
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
            conversations: 0,
            inputTokens: 100,
            outputTokens: 20,
            reasoningTokens: 5,
            cachedInputTokens: 0,
            costUsd: 1,
            activeAccounts: 3,
            errorCount: 0,
          },
          {
            date: "2026-06-06",
            requests: 2,
            conversations: 0,
            inputTokens: 200,
            outputTokens: 30,
            reasoningTokens: 1,
            cachedInputTokens: 0,
            costUsd: 2,
            activeAccounts: 1,
            errorCount: 0,
          },
          {
            date: "2026-06-07",
            requests: 5,
            conversations: 0,
            inputTokens: 300,
            outputTokens: 40,
            reasoningTokens: 3,
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
            conversations: 0,
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
            conversations: 0,
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
            conversations: 0,
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
            conversations: 0,
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
            conversations: 0,
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
    expect(within(row).getByText("(0)")).toBeInTheDocument();
  });

  it("renders conversations between Reqs and Input Tokens with sorting and CSV", async () => {
    const user = userEvent.setup();
    const blobText = vi.fn(async () => "");
    vi.spyOn(URL, "createObjectURL").mockImplementation((blob) => {
      if (!(blob instanceof Blob)) throw new TypeError("expected Blob");
      blobText.mockImplementation(() => blob.text());
      return "blob:daily-conv";
    });
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    render(
      <DailyDetailTable
        startDate="2026-06-05"
        endDate="2026-06-07"
        data={[
          { date: "2026-06-05", requests: 8, conversations: 1, inputTokens: 100, outputTokens: 20, cachedInputTokens: 0, costUsd: 1, activeAccounts: 1, cancelledCount: 0, errorCount: 0 },
          { date: "2026-06-06", requests: 2, conversations: 5, inputTokens: 200, outputTokens: 30, cachedInputTokens: 1, costUsd: 2, activeAccounts: 1, cancelledCount: 0, errorCount: 0 },
          { date: "2026-06-07", requests: 5, conversations: 3, inputTokens: 300, outputTokens: 40, cachedInputTokens: 2, costUsd: 3, activeAccounts: 1, cancelledCount: 0, errorCount: 0 },
        ]}
      />,
    );

    // Sort by conversations ascending
    await user.click(screen.getByRole("button", { name: /conversations/i }));
    const rows = screen.getAllByTestId(/daily-breakdown-row-/);
    expect(rows.map((r) => r.getAttribute("data-testid"))).toEqual([
      "daily-breakdown-row-2026-06-05",
      "daily-breakdown-row-2026-06-07",
      "daily-breakdown-row-2026-06-06",
    ]);

    // Verify conversations column between Reqs and Input Tokens in header
    const headerRow = screen.getAllByRole("row")[0];
    const headerCells = Array.from(headerRow?.querySelectorAll("th") ?? []);
    const labels = headerCells.map((c) => c.textContent?.trim() ?? "");
    expect(labels).toEqual(["Day", "Reqs", "Conversations", "Input Tokens", "Output Tokens", "Reported Reasoning Tokens", "Cost", "Accounts", "Cancelled", "Errors"]);

    // CSV: full header + first data row with Conversations between Requests and Input Tokens
    await user.click(screen.getByRole("button", { name: /csv/i }));
    const csv = await blobText();
    const csvLines = csv.split("\n");
    expect(csvLines[0]).toBe("Date,Requests,Conversations,Input Tokens,Output Tokens,Reported Reasoning Tokens,Cached Tokens,Cost USD,Active Accounts,Cancelled,Errors");
    // First data row in CSV (chronological: 06-05 first, conversations=1)
    expect(csvLines[1]).toMatch(/2026-06-05,8,1,100,20,0,0,1\.0000,1,0,0/);
  });

  it("zero-filled gap rows have conversations=0 in column 2", () => {
    render(
      <DailyDetailTable
        startDate="2026-06-05"
        endDate="2026-06-07"
        data={[
          { date: "2026-06-05", requests: 99, conversations: 3, inputTokens: 100, outputTokens: 20, cachedInputTokens: 0, costUsd: 1, activeAccounts: 1, errorCount: 0 },
        ]}
      />,
    );

    const gapRow = screen.getByTestId("daily-breakdown-row-2026-06-06");
    // Column 2 (0-indexed) = Conversations
    const gapCells = gapRow.querySelectorAll("td");
    expect(gapCells.length).toBeGreaterThanOrEqual(3);
    expect(gapCells[2]?.textContent?.trim()).toBe("0");

    const dataRow = screen.getByTestId("daily-breakdown-row-2026-06-05");
    const dataCells = dataRow.querySelectorAll("td");
    expect(dataCells[2]?.textContent?.trim()).toBe("3");
  });

  it("keeps headers and rows in one horizontally scrollable table", () => {
    render(
      <DailyDetailTable
        startDate="2026-06-05"
        endDate="2026-06-05"
        data={[{ date: "2026-06-05", requests: 1, conversations: 0, inputTokens: 100, outputTokens: 20, cachedInputTokens: 0, costUsd: 1, activeAccounts: 1, errorCount: 0 }]}
      />,
    );

    const scrollContainer = screen.getByTestId("daily-breakdown-scroll-body");
    const tables = scrollContainer.querySelectorAll("table.min-w-\\[1000px\\]");
    expect(scrollContainer).toHaveClass("overflow-x-auto", "overflow-y-auto");
    expect(tables).toHaveLength(1);
    expect(tables[0]?.querySelector("thead")).toBeInTheDocument();
    expect(tables[0]?.querySelector("tbody")).toBeInTheDocument();
  });
});
