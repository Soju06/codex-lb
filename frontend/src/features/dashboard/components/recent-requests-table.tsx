import { Inbox } from "lucide-react";
import { useMemo, useState } from "react";

import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PaginationControls } from "@/features/dashboard/components/filters/pagination-controls";
import type { AccountSummary, RequestLog } from "@/features/dashboard/schemas";
import { REQUEST_STATUS_LABELS } from "@/utils/constants";
import {
  formatCompactNumber,
  formatCurrency,
  formatModelLabel,
  formatTimeLong,
} from "@/utils/formatters";

const STATUS_CLASS_MAP: Record<string, string> = {
  ok: "bg-emerald-500/15 text-emerald-700 border-emerald-500/20 hover:bg-emerald-500/20 dark:text-emerald-400",
  rate_limit: "bg-orange-500/15 text-orange-700 border-orange-500/20 hover:bg-orange-500/20 dark:text-orange-400",
  quota: "bg-red-500/15 text-red-700 border-red-500/20 hover:bg-red-500/20 dark:text-red-400",
  error: "bg-zinc-500/15 text-zinc-700 border-zinc-500/20 hover:bg-zinc-500/20 dark:text-zinc-400",
};

export type RecentRequestsTableProps = {
  requests: RequestLog[];
  accounts: AccountSummary[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
  onLimitChange: (limit: number) => void;
  onOffsetChange: (offset: number) => void;
};

export function RecentRequestsTable({
  requests,
  accounts,
  total,
  limit,
  offset,
  hasMore,
  onLimitChange,
  onOffsetChange,
}: RecentRequestsTableProps) {
  const [viewingError, setViewingError] = useState<string | null>(null);

  const accountLabelMap = useMemo(() => {
    const index = new Map<string, string>();
    for (const account of accounts) {
      index.set(account.accountId, account.displayName || account.email || account.accountId);
    }
    return index;
  }, [accounts]);

  if (requests.length === 0) {
    return (
      <EmptyState
        icon={Inbox}
        title="No request logs"
        description="No request logs match the current filters."
      />
    );
  }

  return (
    <div className="space-y-3">
    <div className="rounded-xl border bg-card">
      <div className="relative overflow-x-auto">
        <Table className="min-w-[800px] table-fixed">
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="w-28 pl-4 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">Time</TableHead>
              <TableHead className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">Account</TableHead>
              <TableHead className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">Model</TableHead>
              <TableHead className="w-24 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">Status</TableHead>
              <TableHead className="w-24 text-right text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">Tokens</TableHead>
              <TableHead className="w-16 text-right text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">Cost</TableHead>
              <TableHead className="w-28 pr-4 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">Error</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {requests.map((request) => {
              const time = formatTimeLong(request.requestedAt);
              const accountLabel = accountLabelMap.get(request.accountId) ?? request.accountId;
              const errorMessage = request.errorMessage || request.errorCode || "-";
              const hasLongError = errorMessage !== "-" && errorMessage.length > 72;

              return (
                <TableRow key={request.requestId}>
                  <TableCell className="pl-4 align-top">
                    <div className="leading-tight">
                      <div className="text-sm font-medium">{time.time}</div>
                      <div className="text-xs text-muted-foreground">{time.date}</div>
                    </div>
                  </TableCell>
                  <TableCell className="truncate align-top text-sm">
                    {accountLabel}
                  </TableCell>
                  <TableCell className="truncate align-top">
                    <span className="font-mono text-xs">
                      {formatModelLabel(request.model, request.reasoningEffort)}
                    </span>
                  </TableCell>
                  <TableCell className="align-top">
                    <Badge
                      variant="outline"
                      className={STATUS_CLASS_MAP[request.status] ?? STATUS_CLASS_MAP.error}
                    >
                      {REQUEST_STATUS_LABELS[request.status] ?? request.status}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-right align-top font-mono text-xs tabular-nums">
                    <div className="leading-tight">
                      <div>{formatCompactNumber(request.tokens)}</div>
                      {request.cachedInputTokens != null && request.cachedInputTokens > 0 && (
                        <div className="text-[11px] text-muted-foreground">
                          {formatCompactNumber(request.cachedInputTokens)} Cached
                        </div>
                      )}
                    </div>
                  </TableCell>
                  <TableCell className="text-right align-top font-mono text-xs tabular-nums">
                    {formatCurrency(request.costUsd)}
                  </TableCell>
                  <TableCell className="overflow-hidden pr-4 align-top">
                    <div className="flex items-center gap-1.5">
                      <p className="min-w-0 truncate text-xs text-muted-foreground">
                        {errorMessage}
                      </p>
                      {hasLongError ? (
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="h-5 shrink-0 px-1.5 text-[11px]"
                          onClick={() => setViewingError(errorMessage)}
                        >
                          View
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
    </div>

      <div className="flex justify-end">
        <PaginationControls
          total={total}
          limit={limit}
          offset={offset}
          hasMore={hasMore}
          onLimitChange={onLimitChange}
          onOffsetChange={onOffsetChange}
        />
      </div>

      <Dialog open={viewingError !== null} onOpenChange={(open) => { if (!open) setViewingError(null); }}>
        <DialogContent className="max-h-[80vh] sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Error Detail</DialogTitle>
            <DialogDescription>Full error message from the request.</DialogDescription>
          </DialogHeader>
          <div className="max-h-[50vh] overflow-y-auto rounded-md bg-muted/50 p-3">
            <p className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed">
              {viewingError}
            </p>
          </div>
          <DialogFooter showCloseButton />
        </DialogContent>
      </Dialog>
    </div>
  );
}
