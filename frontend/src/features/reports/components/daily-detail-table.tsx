import { Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { DailyReportRow } from "../schemas";
import { formatReportBucketDate } from "../date";

export type DailyDetailTableProps = {
  data: DailyReportRow[];
};

function formatTokens(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(0)}K`;
  return String(v);
}

export function DailyDetailTable({ data }: DailyDetailTableProps) {
  return (
    <div className="rounded-xl border bg-card p-5">
      <div className="mb-3 flex items-center justify-between">
        <div className="text-sm font-semibold text-foreground">Daily Breakdown</div>
        <Button variant="outline" size="sm" className="h-7 gap-1 text-xs" onClick={() => exportCSV(data)}>
          <Download className="h-3 w-3" />
          CSV
        </Button>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
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
          <tbody>
            {data.map((row) => (
              <tr key={row.date} className="border-b border-border/50 last:border-0">
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
  );
}

function formatDate(iso: string): string {
  return formatReportBucketDate(iso);
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
