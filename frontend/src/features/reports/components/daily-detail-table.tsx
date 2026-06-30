import { useState } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown, Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import { buildContinuousDailyRows } from "../daily-series";
import type { DailyReportRow } from "../schemas";
import { formatReportBucketDate } from "../date";

export type DailyDetailTableProps = {
  startDate: string;
  endDate: string;
  data: DailyReportRow[];
};

const DAILY_BREAKDOWN_SCROLL_HEIGHT_CLASS = "max-h-[17.5rem]";

type SortKey = "date" | "requests" | "inputTokens" | "outputTokens" | "costUsd" | "activeAccounts";
type SortDirection = "asc" | "desc";

function formatTokens(v: number): string {
  if (v >= 1_000_000_000) return `${(v / 1_000_000_000).toFixed(1)}B`;
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(0)}K`;
  return String(v);
}

export function DailyDetailTable({ startDate, endDate, data }: DailyDetailTableProps) {
  const [sort, setSort] = useState<{ key: SortKey; direction: SortDirection }>({
    key: "date",
    direction: "desc",
  });
  const rows = sortRows(buildContinuousDailyRows(startDate, endDate, data), sort);
  const csvRows = sortRows(rows, { key: "date", direction: "asc" });

  const toggleSort = (key: SortKey) => {
    setSort((current) =>
      current.key === key
        ? { key, direction: current.direction === "asc" ? "desc" : "asc" }
        : { key, direction: "asc" },
    );
  };

  return (
    <div className="rounded-xl border bg-card p-5">
      <div className="mb-3 flex items-center justify-between">
        <div className="text-sm font-semibold text-foreground">Daily Breakdown</div>
        <Button
          variant="outline"
          size="sm"
          className="h-7 gap-1 text-xs"
          onClick={() => exportCSV(csvRows)}
        >
          <Download className="h-3 w-3" />
          CSV
        </Button>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full table-fixed text-xs">
          <ColumnGroup />
          <thead>
            <tr className="border-b text-left text-muted-foreground">
              <SortableHeader
                align="left"
                label="Day"
                isActive={sort.key === "date"}
                direction={sort.direction}
                onClick={() => toggleSort("date")}
              />
              <SortableHeader
                label="Reqs"
                isActive={sort.key === "requests"}
                direction={sort.direction}
                onClick={() => toggleSort("requests")}
              />
              <SortableHeader
                label="Input Tokens"
                isActive={sort.key === "inputTokens"}
                direction={sort.direction}
                onClick={() => toggleSort("inputTokens")}
              />
              <SortableHeader
                label="Output Tokens"
                isActive={sort.key === "outputTokens"}
                direction={sort.direction}
                onClick={() => toggleSort("outputTokens")}
              />
              <SortableHeader
                label="Cost"
                isActive={sort.key === "costUsd"}
                direction={sort.direction}
                onClick={() => toggleSort("costUsd")}
              />
              <SortableHeader
                label="Accounts"
                isActive={sort.key === "activeAccounts"}
                direction={sort.direction}
                onClick={() => toggleSort("activeAccounts")}
              />
            </tr>
          </thead>
        </table>
        <div
          data-testid="daily-breakdown-scroll-body"
          className={`${DAILY_BREAKDOWN_SCROLL_HEIGHT_CLASS} overflow-y-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden`}
        >
          <table className="w-full table-fixed text-xs">
            <ColumnGroup />
            <tbody>
              {rows.map((row) => (
                <tr
                  key={row.date}
                  data-testid={`daily-breakdown-row-${row.date}`}
                  className="border-b border-border/50 last:border-0"
                >
                  <td className="py-2.5 pr-4 font-medium text-foreground">
                    {formatDate(row.date)}
                  </td>
                  <td className="py-2.5 pr-4 text-right text-foreground">
                    {row.requests}
                  </td>
                  <td className="py-2.5 pr-4 text-right text-foreground">
                    <span>{formatTokens(row.inputTokens)}</span>{" "}
                    <span className="text-[11px] text-muted-foreground">
                      ({formatTokens(row.cachedInputTokens)})
                    </span>
                  </td>
                  <td className="py-2.5 pr-4 text-right text-foreground">
                    {formatTokens(row.outputTokens)}
                  </td>
                  <td className="py-2.5 pr-4 text-right font-medium text-emerald-600 dark:text-emerald-400">
                    ${row.costUsd.toFixed(2)}
                  </td>
                  <td className="py-2.5 text-right text-muted-foreground">
                    {row.activeAccounts}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

type SortableHeaderProps = {
  align?: "left" | "right";
  label: string;
  isActive: boolean;
  direction: SortDirection;
  onClick: () => void;
};

function SortableHeader({
  align = "right",
  label,
  isActive,
  direction,
  onClick,
}: SortableHeaderProps) {
  const Icon = isActive ? (direction === "asc" ? ArrowUp : ArrowDown) : ArrowUpDown;
  const iconTestId = isActive ? (direction === "asc" ? "sort-icon-asc" : "sort-icon-desc") : "sort-icon-none";
  const iconVariant = isActive ? (direction === "asc" ? "up" : "down") : "up-down";
  const ariaSort = isActive ? (direction === "asc" ? "ascending" : "descending") : "none";

  return (
    <th
      className={`pb-2 ${align === "left" ? "pr-4" : align === "right" ? "pr-4 text-right" : ""} font-medium`}
      aria-sort={ariaSort}
    >
      <button
        type="button"
        className={`flex w-full items-center gap-1 ${align === "left" ? "justify-start text-left" : "justify-end text-right"} font-medium text-inherit`}
        onClick={onClick}
      >
        <span>{label}</span>
        <Icon
          aria-hidden="true"
          data-testid={iconTestId}
          data-sort-icon={iconVariant}
          className={`h-3 w-3 shrink-0 ${isActive ? "text-foreground" : "text-muted-foreground/60"}`}
        />
      </button>
    </th>
  );
}

function ColumnGroup() {
  return (
    <colgroup>
      <col style={{ width: "18%" }} />
      <col style={{ width: "14%" }} />
      <col style={{ width: "20%" }} />
      <col style={{ width: "20%" }} />
      <col style={{ width: "14%" }} />
      <col style={{ width: "14%" }} />
    </colgroup>
  );
}

function formatDate(iso: string): string {
  return formatReportBucketDate(iso);
}

function sortRows(
  rows: DailyReportRow[],
  sort: { key: SortKey; direction: SortDirection },
): DailyReportRow[] {
  const sorted = [...rows].sort((left, right) => {
    const leftValue = left[sort.key];
    const rightValue = right[sort.key];

    if (leftValue < rightValue) {
      return sort.direction === "asc" ? -1 : 1;
    }
    if (leftValue > rightValue) {
      return sort.direction === "asc" ? 1 : -1;
    }
    return 0;
  });

  return sorted;
}

function exportCSV(rows: DailyReportRow[]) {
  const headers = ["Date", "Requests", "Input Tokens", "Output Tokens", "Cached Tokens", "Cost USD", "Active Accounts", "Errors"];
  const lines = rows.map((r) =>
    [r.date, r.requests, r.inputTokens, r.outputTokens, r.cachedInputTokens, r.costUsd.toFixed(4), r.activeAccounts, r.errorCount].join(","),
  );
  const csv = [headers.join(","), ...lines].join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `reports-${rows[0]?.date ?? "data"}-${rows[rows.length - 1]?.date ?? "data"}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}
