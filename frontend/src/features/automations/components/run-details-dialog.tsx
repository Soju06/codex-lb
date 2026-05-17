import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { SpinnerBlock } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  resolveAccountDisplay,
  type AccountDisplayEntry,
} from "@/features/automations/account-display";
import {
  accountStateBadgeVariant,
  formatRunStatusLabel,
  runStatusVariant,
} from "@/features/automations/components/run-status-utils";
import type {
  AutomationRunDetails,
  AutomationRunStatus,
} from "@/features/automations/schemas";
import { formatTimeLong } from "@/utils/formatters";

type RunDetailsStateBreakdown = {
  total: number;
  success: number;
  failed: number;
  partial: number;
  running: number;
  pending: number;
  completed: number;
};

export type RunDetailsDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  isLoading: boolean;
  data: AutomationRunDetails | undefined;
  blurred: boolean;
  accountDisplayIndex: Map<string, AccountDisplayEntry>;
  accountBlurIndex: Map<string, { primary: boolean; secondary: boolean; any: boolean }>;
};

function buildRunDetailsStateBreakdown(
  accounts: Array<{ status: "pending" | AutomationRunStatus }>,
): RunDetailsStateBreakdown {
  const base: RunDetailsStateBreakdown = {
    total: accounts.length,
    success: 0,
    failed: 0,
    partial: 0,
    running: 0,
    pending: 0,
    completed: 0,
  };
  for (const account of accounts) {
    switch (account.status) {
      case "success":
        base.success += 1;
        base.completed += 1;
        break;
      case "failed":
        base.failed += 1;
        base.completed += 1;
        break;
      case "partial":
        base.partial += 1;
        base.completed += 1;
        break;
      case "running":
        base.running += 1;
        break;
      case "pending":
        base.pending += 1;
        break;
    }
  }
  return base;
}

export function RunDetailsDialog({
  open,
  onOpenChange,
  isLoading,
  data,
  blurred,
  accountDisplayIndex,
  accountBlurIndex,
}: RunDetailsDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-hidden sm:max-w-5xl">
        <DialogHeader>
          <DialogTitle>Run details</DialogTitle>
          <DialogDescription>
            Inspect per-account execution state for this automation run cycle.
          </DialogDescription>
        </DialogHeader>
        <div className="min-h-0 space-y-4 overflow-y-auto">
          {isLoading ? (
            <SpinnerBlock />
          ) : data ? (
            <>
              <div className="rounded-md border bg-muted/10 p-3">
                {(() => {
                  const effectiveStatus = (
                    data.run.effectiveStatus ?? data.run.status
                  ) as AutomationRunStatus;
                  const breakdown = buildRunDetailsStateBreakdown(data.accounts);
                  const failedTotal = breakdown.failed + breakdown.partial;
                  const pendingTotal = breakdown.pending + breakdown.running;
                  return (
                    <div className="grid gap-3 sm:grid-cols-[minmax(0,1.25fr)_repeat(4,minmax(0,1fr))]">
                      <div className="rounded-md border bg-background/60 p-3">
                        <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">
                          Run status
                        </div>
                        <div className="mt-2">
                          <Badge variant={runStatusVariant(effectiveStatus)}>
                            {formatRunStatusLabel(
                              effectiveStatus,
                              data.pendingAccounts,
                            )}
                          </Badge>
                        </div>
                        <div className="mt-2 text-xs text-muted-foreground">
                          Completed {breakdown.completed} of {breakdown.total}
                        </div>
                      </div>

                      <div className="rounded-md border bg-background/40 p-3">
                        <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">
                          Total
                        </div>
                        <div className="mt-1 text-2xl font-semibold tabular-nums">
                          {breakdown.total}
                        </div>
                      </div>

                      <div className="rounded-md border bg-background/40 p-3">
                        <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">
                          Success
                        </div>
                        <div className="mt-1 text-2xl font-semibold tabular-nums">
                          {breakdown.success}
                        </div>
                      </div>

                      <div className="rounded-md border bg-background/40 p-3">
                        <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">
                          Failed
                        </div>
                        <div className="mt-1 text-2xl font-semibold tabular-nums">
                          {failedTotal}
                        </div>
                        {breakdown.partial > 0 ? (
                          <div className="text-xs text-muted-foreground">
                            Includes {breakdown.partial} partial
                          </div>
                        ) : null}
                      </div>

                      <div className="rounded-md border bg-background/40 p-3">
                        <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">
                          Pending
                        </div>
                        <div className="mt-1 text-2xl font-semibold tabular-nums">
                          {pendingTotal}
                        </div>
                        {breakdown.running > 0 ? (
                          <div className="text-xs text-muted-foreground">
                            {breakdown.running} running
                          </div>
                        ) : null}
                      </div>
                    </div>
                  );
                })()}
              </div>

              <div className="rounded-md border">
                <div className="max-h-[56vh] overflow-auto">
                  <Table className="min-w-[980px] table-fixed">
                    <TableHeader>
                      <TableRow className="hover:bg-transparent [&>th]:sticky [&>th]:top-0 [&>th]:z-10 [&>th]:bg-card">
                        <TableHead className="w-52 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">
                          Account
                        </TableHead>
                        <TableHead className="w-28 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">
                          Status
                        </TableHead>
                        <TableHead
                          className="w-32 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80"
                          title="Cycle trigger time for this grouped run"
                        >
                          Cycle start
                        </TableHead>
                        <TableHead
                          className="w-32 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80"
                          title="Planned per-account dispatch time (threshold offset)"
                        >
                          Dispatch at
                        </TableHead>
                        <TableHead className="w-32 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">
                          Finished
                        </TableHead>
                        <TableHead className="w-56 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">
                          Error
                        </TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {data.accounts.map((entry) => {
                        const accountDisplay = resolveAccountDisplay(
                          entry.accountId,
                          accountDisplayIndex,
                        );
                        const detailAccountBlur = accountBlurIndex.get(
                          entry.accountId,
                        );
                        const shouldBlurPrimary =
                          blurred && !!detailAccountBlur?.primary;
                        const shouldBlurSecondary =
                          blurred && !!detailAccountBlur?.secondary;
                        const cycleStart = formatTimeLong(data.run.startedAt);
                        const dispatchAt = entry.scheduledFor
                          ? formatTimeLong(entry.scheduledFor)
                          : null;
                        const finishedAt = entry.finishedAt
                          ? formatTimeLong(entry.finishedAt)
                          : null;
                        const state = entry.status as
                          | "pending"
                          | AutomationRunStatus;
                        return (
                          <TableRow
                            key={`${entry.accountId}:${entry.runId ?? "pending"}`}
                          >
                            <TableCell className="align-middle text-xs">
                              <div className="space-y-0.5 leading-tight">
                                <div
                                  className="truncate text-foreground/95"
                                  title={accountDisplay.title}
                                >
                                  {shouldBlurPrimary ? (
                                    <span className="privacy-blur">
                                      {accountDisplay.primary}
                                    </span>
                                  ) : (
                                    accountDisplay.primary
                                  )}
                                </div>
                                {accountDisplay.secondary ? (
                                  <div
                                    className="truncate text-muted-foreground"
                                    title={accountDisplay.title}
                                  >
                                    {shouldBlurSecondary ? (
                                      <span className="privacy-blur">
                                        {accountDisplay.secondary}
                                      </span>
                                    ) : (
                                      accountDisplay.secondary
                                    )}
                                  </div>
                                ) : null}
                              </div>
                            </TableCell>
                            <TableCell className="align-middle text-xs">
                              <Badge variant={accountStateBadgeVariant(state)}>
                                {state === "pending"
                                  ? "pending"
                                  : formatRunStatusLabel(state)}
                              </Badge>
                            </TableCell>
                            <TableCell className="align-middle text-xs">
                              <div className="space-y-0.5 leading-tight">
                                <div className="font-mono tabular-nums text-foreground/95">
                                  {cycleStart.time}
                                </div>
                                <div className="text-muted-foreground">
                                  {cycleStart.date}
                                </div>
                              </div>
                            </TableCell>
                            <TableCell className="align-middle text-xs">
                              {dispatchAt ? (
                                <div className="space-y-0.5 leading-tight">
                                  <div className="font-mono tabular-nums text-foreground/95">
                                    {dispatchAt.time}
                                  </div>
                                  <div className="text-muted-foreground">
                                    {dispatchAt.date}
                                  </div>
                                </div>
                              ) : (
                                <span className="text-muted-foreground">-</span>
                              )}
                            </TableCell>
                            <TableCell className="align-middle text-xs">
                              {finishedAt ? (
                                <div className="space-y-0.5 leading-tight">
                                  <div className="font-mono tabular-nums text-foreground/95">
                                    {finishedAt.time}
                                  </div>
                                  <div className="text-muted-foreground">
                                    {finishedAt.date}
                                  </div>
                                </div>
                              ) : (
                                <span className="text-muted-foreground">-</span>
                              )}
                            </TableCell>
                            <TableCell className="align-middle text-xs">
                              {entry.errorCode || entry.errorMessage ? (
                                <div className="space-y-0.5 leading-tight">
                                  <div
                                    className="truncate font-medium text-destructive/90"
                                    title={`${entry.errorCode ?? "error"}${
                                      entry.errorMessage
                                        ? `: ${entry.errorMessage}`
                                        : ""
                                    }`}
                                  >
                                    {entry.errorCode ?? "error"}
                                  </div>
                                  {entry.errorMessage ? (
                                    <div
                                      className="line-clamp-2 break-words text-muted-foreground"
                                      title={entry.errorMessage}
                                    >
                                      {entry.errorMessage}
                                    </div>
                                  ) : null}
                                </div>
                              ) : (
                                <span className="text-muted-foreground">
                                  No error
                                </span>
                              )}
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </div>
              </div>
            </>
          ) : (
            <p className="text-sm text-muted-foreground">
              Run details unavailable.
            </p>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

