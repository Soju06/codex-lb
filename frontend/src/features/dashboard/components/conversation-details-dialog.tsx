import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { AlertMessage } from "@/components/alert-message";
import { CopyButton } from "@/components/copy-button";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
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
import { useConversationDetails } from "@/features/dashboard/hooks/use-conversation-details";
import type { ConversationModelStat } from "@/features/dashboard/schemas";
import { formatCompactNumber, formatCurrency, formatDateTimeInline, formatElapsed, formatModelLabel } from "@/utils/formatters";

type DetailSortKey =
  | "modelEffort"
  | "reqs"
  | "totalElapsedTime"
  | "totalInputTokens"
  | "totalOutputTokens"
  | "totalCostUsd";

type SortState = { key: DetailSortKey; direction: "asc" | "desc" };

export type ConversationDetailsDialogProps = {
  open: boolean;
  conversationId: string | null;
  onOpenChange: (open: boolean) => void;
};

const DEFAULT_SORT: SortState = { key: "reqs", direction: "desc" };

export function ConversationDetailsDialog({
  open,
  conversationId,
  onOpenChange,
}: ConversationDetailsDialogProps) {
  const { t } = useTranslation();
  const detailsQuery = useConversationDetails(conversationId, open);
  const [sortState, setSortState] = useState<{ conversationId: string | null; sort: SortState }>({
    conversationId: null,
    sort: DEFAULT_SORT,
  });
  const sort = sortState.conversationId === conversationId ? sortState.sort : DEFAULT_SORT;

  const sortedStats = useMemo(() => {
    const stats = detailsQuery.data?.modelStats ?? [];
    return [...stats].sort((left, right) => {
      const leftValue = sortValue(left, sort.key);
      const rightValue = sortValue(right, sort.key);
      const result = typeof leftValue === "number" && typeof rightValue === "number"
        ? leftValue - rightValue
        : String(leftValue).localeCompare(String(rightValue));
      if (result !== 0) {
        return sort.direction === "asc" ? result : -result;
      }
      return 0;
    });
  }, [detailsQuery.data?.modelStats, sort]);

  const setSortKey = (key: DetailSortKey) => {
    setSortState((current) => {
      const currentSort = current.conversationId === conversationId ? current.sort : DEFAULT_SORT;
      return {
        conversationId,
        sort: {
          key,
          direction: currentSort.key === key && currentSort.direction === "desc" ? "asc" : "desc",
        },
      };
    });
  };

  const errorMessage = detailsQuery.error instanceof Error
    ? detailsQuery.error.message
    : t("dashboard.conversations.errorDescription");

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[88vh] sm:max-w-5xl">
        <DialogHeader>
          <DialogTitle>{t("dashboard.conversations.detailsTitle")}</DialogTitle>
          <DialogDescription>{t("dashboard.conversations.detailsDescription")}</DialogDescription>
        </DialogHeader>

        {detailsQuery.isPending ? (
          <div className="rounded-md border bg-muted/30 py-8">
            <SpinnerBlock />
          </div>
        ) : detailsQuery.error ? (
          <div className="space-y-3 rounded-md border bg-muted/30 p-4">
            <div role="alert">
              <AlertMessage variant="error">{errorMessage}</AlertMessage>
            </div>
            <Button type="button" variant="outline" size="sm" onClick={() => void detailsQuery.refetch()} disabled={detailsQuery.isFetching}>
              {t("common.actions.retry")}
            </Button>
          </div>
        ) : detailsQuery.data ? (
          <div className="grid gap-4 overflow-y-auto">
            <div data-testid="conversation-details-information" className="space-y-4 rounded-md border bg-muted/30 p-4">
              <div className="grid gap-3 sm:grid-cols-3">
                <DetailField
                  label={t("dashboard.conversations.details.conversationId")}
                  value={detailsQuery.data.conversationId || "—"}
                  mono
                  translateNo
                  copyValue={detailsQuery.data.conversationId}
                  copyLabel={t("dashboard.requestDetails.copyConversationId")}
                />
                <DetailField label={t("dashboard.conversations.details.start")} value={formatDateTimeInline(detailsQuery.data.start)} />
                <DetailField label={t("dashboard.conversations.details.latest")} value={formatDateTimeInline(detailsQuery.data.latest)} />
              </div>
              <div className="grid gap-3 sm:grid-cols-3">
                <DetailField label={t("dashboard.conversations.details.accountCount")} value={formatCompactNumber(detailsQuery.data.accountCount)} />
                <DetailField label={t("dashboard.conversations.details.totalElapsed")} value={formatElapsed(detailsQuery.data.totalElapsedTime)} />
                <DetailField label={t("dashboard.conversations.details.dominantUseragent")} value={detailsQuery.data.dominantUseragentGroup || "—"} />
              </div>
              <div className="rounded-md border">
                <div className="relative overflow-x-auto">
                  <Table className="min-w-[760px]">
                    <TableHeader>
                      <TableRow className="hover:bg-transparent">
                        <SortableHead label={t("dashboard.conversations.details.columns.modelEffort")} sortKey="modelEffort" sort={sort} onSort={setSortKey} />
                        <SortableHead label={t("dashboard.conversations.details.columns.reqs")} sortKey="reqs" sort={sort} onSort={setSortKey} />
                        <SortableHead label={t("dashboard.conversations.details.columns.totalElapsed")} sortKey="totalElapsedTime" sort={sort} onSort={setSortKey} />
                        <SortableHead label={t("dashboard.conversations.details.columns.totalInput")} sortKey="totalInputTokens" sort={sort} onSort={setSortKey} />
                        <SortableHead label={t("dashboard.conversations.details.columns.totalOutput")} sortKey="totalOutputTokens" sort={sort} onSort={setSortKey} />
                        <SortableHead label={t("dashboard.conversations.details.columns.totalCost")} sortKey="totalCostUsd" sort={sort} onSort={setSortKey} />
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {sortedStats.map((stat) => (
                        <TableRow key={conversationStatKey(stat)}>
                          <TableCell className="font-mono text-xs"><span translate="no">{modelEffortLabel(stat)}</span></TableCell>
                          <TableCell className="font-mono text-xs tabular-nums">{formatCompactNumber(stat.reqs)}</TableCell>
                          <TableCell className="font-mono text-xs tabular-nums">{formatElapsed(stat.totalElapsedTime)}</TableCell>
                          <TableCell className="font-mono text-xs tabular-nums">
                            <div>{formatCompactNumber(stat.totalInputTokens)}</div>
                            <div className="mt-1 text-[11px] font-sans text-muted-foreground">
                              ({t("dashboard.conversations.details.cache", { count: formatCachedTokenCount(stat.cachedInputTokens) })})
                            </div>
                          </TableCell>
                          <TableCell className="font-mono text-xs tabular-nums">{formatCompactNumber(stat.totalOutputTokens)}</TableCell>
                          <TableCell className="font-mono text-xs tabular-nums">{formatCurrency(stat.totalCostUsd)}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </div>
            </div>
          </div>
        ) : null}

        <DialogFooter showCloseButton />
      </DialogContent>
    </Dialog>
  );
}

function modelEffortLabel(stat: ConversationModelStat): string {
  return formatModelLabel(stat.modelEffort.model, stat.modelEffort.reasoningEffort).replace(/^--$/, "—");
}

function conversationStatKey(stat: ConversationModelStat): string {
  return JSON.stringify([stat.modelEffort.model, stat.modelEffort.reasoningEffort ?? null]);
}

function formatCachedTokenCount(value: number | null | undefined): string {
  return value == null ? "—" : formatCompactNumber(value);
}

function sortValue(stat: ConversationModelStat, key: DetailSortKey): number | string {
  if (key === "modelEffort") {
    return modelEffortLabel(stat);
  }
  return stat[key];
}

function SortableHead({
  label,
  sortKey,
  sort,
  onSort,
}: {
  label: string;
  sortKey: DetailSortKey;
  sort: SortState;
  onSort: (key: DetailSortKey) => void;
}) {
  const active = sort.key === sortKey;
  return (
    <TableHead aria-sort={active ? (sort.direction === "desc" ? "descending" : "ascending") : "none"}>
      <button
        type="button"
        className="inline-flex items-center gap-1 rounded-sm py-1 text-left text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80 hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        onClick={() => onSort(sortKey)}
        aria-label={label}
      >
        {label}
        {active ? <span aria-hidden="true">{sort.direction === "desc" ? "↓" : "↑"}</span> : null}
      </button>
    </TableHead>
  );
}

function DetailField({
  label,
  value,
  mono = false,
  translateNo = false,
  copyValue,
  copyLabel,
}: {
  label: string;
  value: string;
  mono?: boolean;
  translateNo?: boolean;
  copyValue?: string;
  copyLabel?: string;
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">
        <span>{label}</span>
        {copyValue ? <CopyButton value={copyValue} label={copyLabel} iconOnly /> : null}
      </div>
      <p className={`break-all text-sm leading-relaxed ${mono ? "font-mono" : ""}`} translate={translateNo ? "no" : undefined}>{value}</p>
    </div>
  );
}
