import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { AccountSummary, RequestLog } from "@/features/dashboard/schemas";
import {
  formatCompactNumber,
  formatCurrency,
  formatModelLabel,
  formatTimeLong,
} from "@/utils/formatters";

const STATUS_CLASS_MAP: Record<string, string> = {
  ok: "bg-emerald-500 text-white hover:bg-emerald-500/90",
  rate_limit: "bg-orange-500 text-white hover:bg-orange-500/90",
  quota: "bg-red-500 text-white hover:bg-red-500/90",
  error: "bg-zinc-700 text-white hover:bg-zinc-700/90",
};

const STATUS_LABEL_MAP: Record<string, string> = {
  ok: "OK",
  rate_limit: "Rate limit",
  quota: "Quota",
  error: "Error",
};

function truncate(value: string, max = 72): string {
  if (value.length <= max) {
    return value;
  }
  return `${value.slice(0, max - 3)}...`;
}

export type RecentRequestsTableProps = {
  requests: RequestLog[];
  accounts: AccountSummary[];
};

export function RecentRequestsTable({ requests, accounts }: RecentRequestsTableProps) {
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

  const accountLabelMap = useMemo(() => {
    const index = new Map<string, string>();
    for (const account of accounts) {
      index.set(account.accountId, account.displayName || account.email || account.accountId);
    }
    return index;
  }, [accounts]);

  const toggleExpanded = (requestId: string) => {
    setExpandedIds((previous) => {
      const next = new Set(previous);
      if (next.has(requestId)) {
        next.delete(requestId);
      } else {
        next.add(requestId);
      }
      return next;
    });
  };

  if (requests.length === 0) {
    return (
      <div className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">
        No request logs match the current filters.
      </div>
    );
  }

  return (
    <div className="rounded-xl border bg-card p-4">
      <h3 className="mb-3 text-sm font-semibold">Recent Requests</h3>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Time</TableHead>
            <TableHead>Account</TableHead>
            <TableHead>Model</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="text-right">Tokens</TableHead>
            <TableHead className="text-right">Cost</TableHead>
            <TableHead>Error</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {requests.map((request) => {
            const time = formatTimeLong(request.requestedAt);
            const accountLabel = accountLabelMap.get(request.accountId) ?? request.accountId;
            const isExpanded = expandedIds.has(request.requestId);
            const errorMessage = request.errorMessage || request.errorCode || "-";

            return (
              <TableRow key={request.requestId}>
                <TableCell className="align-top">
                  <div className="leading-tight">
                    <div className="font-medium">{time.time}</div>
                    <div className="text-xs text-muted-foreground">{time.date}</div>
                  </div>
                </TableCell>
                <TableCell className="max-w-[12rem] truncate align-top">{accountLabel}</TableCell>
                <TableCell className="max-w-[10rem] truncate align-top">
                  {formatModelLabel(request.model, request.reasoningEffort)}
                </TableCell>
                <TableCell className="align-top">
                  <Badge className={STATUS_CLASS_MAP[request.status] ?? STATUS_CLASS_MAP.error}>
                    {STATUS_LABEL_MAP[request.status] ?? request.status}
                  </Badge>
                </TableCell>
                <TableCell className="text-right tabular-nums align-top">
                  {formatCompactNumber(request.tokens)}
                </TableCell>
                <TableCell className="text-right tabular-nums align-top">
                  {formatCurrency(request.costUsd)}
                </TableCell>
                <TableCell className="max-w-[22rem] align-top">
                  <div className="space-y-1">
                    <p className="text-xs text-muted-foreground">
                      {isExpanded ? errorMessage : truncate(errorMessage)}
                    </p>
                    {errorMessage !== "-" && errorMessage.length > 72 ? (
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="h-6 px-2 text-xs"
                        onClick={() => toggleExpanded(request.requestId)}
                      >
                        {isExpanded ? "Collapse" : "Expand"}
                      </Button>
                    ) : null}
                  </div>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
