import { Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { DailyReportRow } from "../schemas";
import { formatReportBucketDate } from "../date";

export type DailyDetailTableProps = {
  startDate: string;
  endDate: string;
  data: DailyReportRow[];
};

const DAILY_BREAKDOWN_SCROLL_HEIGHT_CLASS = "max-h-[17.5rem]";

function formatTokens(v: number): string {
  if (v >= 1_000_000_000) return `${(v / 1_000_000_000).toFixed(1)}B`;
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(0)}K`;
  return String(v);
}

export function DailyDetailTable({ startDate, endDate, data }: DailyDetailTableProps) {
  const rows = buildContinuousRows(startDate, endDate, data);

  return (
    <div className="rounded-xl border bg-card p-5">
      <div className="mb-3 flex items-center justify-between">
        <div className="text-sm font-semibold text-foreground">Daily Breakdown</div>
        <Button
          variant="outline"
          size="sm"
          className="h-7 gap-1 text-xs"
          onClick={() => exportCSV(rows)}
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
              <th className="pb-2 pr-4 font-medium">Day</th>
              <th className="pb-2 pr-4 text-right font-medium">Reqs</th>
              <th className="pb-2 pr-4 text-right font-medium">Input Tokens</th>
              <th className="pb-2 pr-4 text-right font-medium">Output Tokens</th>
              <th className="pb-2 pr-4 text-right font-medium">Cost</th>
              <th className="pb-2 text-right font-medium">Accounts</th>
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
                    {formatTokens(row.inputTokens)}
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

function buildContinuousRows(
  startDate: string,
  endDate: string,
  rows: DailyReportRow[],
): DailyReportRow[] {
  if (!isISODate(startDate) || !isISODate(endDate) || startDate > endDate) {
    return rows;
  }

  const rowsByDate = new Map(rows.map((row) => [row.date, row]));
  const continuousRows: DailyReportRow[] = [];

  for (let current = startDate; current <= endDate; current = nextISODate(current)) {
    continuousRows.push(rowsByDate.get(current) ?? createZeroRow(current));
  }

  return continuousRows;
}

function nextISODate(date: string): string {
  const nextDate = new Date(`${date}T00:00:00Z`);
  nextDate.setUTCDate(nextDate.getUTCDate() + 1);
  return nextDate.toISOString().slice(0, 10);
}

function isISODate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return false;
  }

  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

function createZeroRow(date: string): DailyReportRow {
  return {
    date,
    requests: 0,
    inputTokens: 0,
    outputTokens: 0,
    cachedInputTokens: 0,
    costUsd: 0,
    activeAccounts: 0,
    errorCount: 0,
  };
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
