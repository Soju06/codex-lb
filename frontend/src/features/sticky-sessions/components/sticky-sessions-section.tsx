import { useEffect, useMemo, useState } from "react";
import { Pin } from "lucide-react";

import { AlertMessage } from "@/components/alert-message";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { SpinnerBlock } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PaginationControls } from "@/features/dashboard/components/filters/pagination-controls";
import { useStickySessions } from "@/features/sticky-sessions/hooks/use-sticky-sessions";
import type { StickySessionEntry, StickySessionIdentifier, StickySessionKind } from "@/features/sticky-sessions/schemas";
import { useDialogState } from "@/hooks/use-dialog-state";
import { getErrorMessageOrNull } from "@/utils/errors";
import { formatTimeLong } from "@/utils/formatters";

function kindLabel(kind: StickySessionKind): string {
  switch (kind) {
    case "codex_session":
      return "Codex session";
    case "sticky_thread":
      return "Sticky thread";
    case "prompt_cache":
      return "Prompt cache";
  }
}

function stickySessionRowId(entry: StickySessionIdentifier): string {
  return `${entry.kind}:${entry.key}`;
}

const EMPTY_STICKY_SESSION_ENTRIES: StickySessionEntry[] = [];

export function StickySessionsSection() {
  const { params, setLimit, setOffset, stickySessionsQuery, deleteMutation, purgeMutation } = useStickySessions();
  const deleteDialog = useDialogState<StickySessionIdentifier>();
  const deleteSelectedDialog = useDialogState<StickySessionIdentifier[]>();
  const purgeDialog = useDialogState();
  const [selectedRowIds, setSelectedRowIds] = useState<string[]>([]);

  const mutationError = useMemo(
    () =>
      getErrorMessageOrNull(stickySessionsQuery.error) ||
      getErrorMessageOrNull(deleteMutation.error) ||
      getErrorMessageOrNull(purgeMutation.error),
    [stickySessionsQuery.error, deleteMutation.error, purgeMutation.error],
  );

  const entries = stickySessionsQuery.data?.entries ?? EMPTY_STICKY_SESSION_ENTRIES;
  const staleCount = stickySessionsQuery.data?.stalePromptCacheCount ?? 0;
  const total = stickySessionsQuery.data?.total ?? 0;
  const hasMore = stickySessionsQuery.data?.hasMore ?? false;
  const busy = deleteMutation.isPending || purgeMutation.isPending;
  const hasEntries = entries.length > 0;
  const hasAnyRows = total > 0;
  const visibleRowIdSet = useMemo(() => new Set(entries.map((entry) => stickySessionRowId(entry))), [entries]);
  const selectedRowIdSet = useMemo(() => new Set(selectedRowIds), [selectedRowIds]);
  const selectedEntries = useMemo(
    () =>
      entries
        .filter((entry) => selectedRowIdSet.has(stickySessionRowId(entry)))
        .map(({ key, kind }) => ({ key, kind })),
    [entries, selectedRowIdSet],
  );
  const selectedCount = selectedEntries.length;
  const allVisibleSelected = hasEntries && selectedCount === entries.length;
  const someVisibleSelected = selectedCount > 0 && !allVisibleSelected;
  const selectedDeleteTargets = deleteSelectedDialog.data ?? [];
  const selectedDeleteCount = selectedDeleteTargets.length;

  useEffect(() => {
    if (!stickySessionsQuery.isLoading && total > 0 && entries.length === 0 && params.offset > 0) {
      const lastValidOffset = Math.max(0, Math.floor((total - 1) / params.limit) * params.limit);
      if (lastValidOffset !== params.offset) {
        setOffset(lastValidOffset);
      }
    }
  }, [entries.length, params.limit, params.offset, setOffset, stickySessionsQuery.isLoading, total]);

  const setSelected = (target: StickySessionIdentifier, checked: boolean) => {
    const rowId = stickySessionRowId(target);
    setSelectedRowIds((current) => {
      if (checked) {
        return current.includes(rowId) ? current : [...current, rowId];
      }
      return current.filter((value) => value !== rowId);
    });
  };

  const setAllVisibleSelected = (checked: boolean) => {
    setSelectedRowIds((current) => {
      const remaining = current.filter((rowId) => !visibleRowIdSet.has(rowId));
      return checked ? [...remaining, ...entries.map((entry) => stickySessionRowId(entry))] : remaining;
    });
  };

  return (
    <section className="space-y-3 rounded-xl border bg-card p-5">
      <div className="flex items-center gap-2.5">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
          <Pin className="h-4 w-4 text-primary" aria-hidden="true" />
        </div>
        <div>
          <h3 className="text-sm font-semibold">Sticky sessions</h3>
          <p className="text-xs text-muted-foreground">
            Inspect durable mappings and purge stale prompt-cache affinity rows.
          </p>
        </div>
      </div>

      {mutationError ? <AlertMessage variant="error">{mutationError}</AlertMessage> : null}

      <div className="flex flex-col gap-3 rounded-lg border px-3 py-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-1.5">
            <span className="text-xs text-muted-foreground">Visible rows</span>
            <span className="text-sm font-medium tabular-nums">{total}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-xs text-muted-foreground">Stale prompt-cache</span>
            <span className="text-sm font-medium tabular-nums">{staleCount}</span>
          </div>
          {selectedCount > 0 ? (
            <div className="flex items-center gap-1.5">
              <span className="text-xs text-muted-foreground">Selected</span>
              <span className="text-sm font-medium tabular-nums">{selectedCount}</span>
            </div>
          ) : null}
        </div>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <Button
            type="button"
            size="sm"
            variant="destructive"
            className="h-8 text-xs"
            disabled={busy || selectedCount === 0}
            onClick={() => deleteSelectedDialog.show(selectedEntries)}
          >
            Delete Sessions
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-8 text-xs"
            disabled={busy || staleCount === 0}
            onClick={() => purgeDialog.show()}
          >
            Purge stale
          </Button>
        </div>
      </div>

      {stickySessionsQuery.isLoading && !stickySessionsQuery.data ? (
        <div className="py-8">
          <SpinnerBlock />
        </div>
      ) : !hasAnyRows ? (
        <EmptyState
          icon={Pin}
          title="No sticky sessions"
          description="Sticky mappings appear here after routed requests create them."
        />
      ) : (
        <>
          {hasEntries ? (
            <div className="overflow-x-auto rounded-xl border">
              <Table className="table-fixed">
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[5%] min-w-[3rem] pl-4 text-[11px] uppercase tracking-wider text-muted-foreground/80">
                      <Checkbox
                        aria-label="Select all sticky sessions on current page"
                        checked={allVisibleSelected ? true : someVisibleSelected ? "indeterminate" : false}
                        disabled={busy || !hasEntries}
                        onCheckedChange={(checked) => setAllVisibleSelected(checked === true)}
                      />
                    </TableHead>
                    <TableHead className="w-[25%] min-w-[14rem] text-[11px] uppercase tracking-wider text-muted-foreground/80">
                      Key
                    </TableHead>
                    <TableHead className="w-[14%] min-w-[8rem] text-[11px] uppercase tracking-wider text-muted-foreground/80">
                      Kind
                    </TableHead>
                    <TableHead className="w-[18%] min-w-[9rem] text-[11px] uppercase tracking-wider text-muted-foreground/80">
                      Account
                    </TableHead>
                    <TableHead className="w-[16%] min-w-[9rem] text-[11px] uppercase tracking-wider text-muted-foreground/80">
                      Updated
                    </TableHead>
                    <TableHead className="w-[16%] min-w-[9rem] text-[11px] uppercase tracking-wider text-muted-foreground/80">
                      Expiry
                    </TableHead>
                    <TableHead className="w-[6%] min-w-[4.5rem] pr-4 text-right align-middle text-[11px] uppercase tracking-wider text-muted-foreground/80">
                      Actions
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {entries.map((entry) => {
                    const updated = formatTimeLong(entry.updatedAt);
                    const expires = entry.expiresAt ? formatTimeLong(entry.expiresAt) : null;
                    const selected = selectedRowIdSet.has(stickySessionRowId(entry));
                    return (
                      <TableRow key={`${entry.kind}:${entry.key}`} data-state={selected ? "selected" : undefined}>
                        <TableCell className="pl-4">
                          <Checkbox
                            aria-label={`Select sticky session ${entry.key}`}
                            checked={selected}
                            disabled={busy}
                            onCheckedChange={(checked) => setSelected(entry, checked === true)}
                          />
                        </TableCell>
                        <TableCell className="max-w-[18rem] truncate font-mono text-xs" title={entry.key}>
                          {entry.key}
                        </TableCell>
                        <TableCell>
                          <Badge variant="outline">{kindLabel(entry.kind)}</Badge>
                        </TableCell>
                        <TableCell className="truncate text-xs">{entry.displayName}</TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {updated.date} {updated.time}
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {entry.isStale ? (
                            <Badge variant="secondary">Stale</Badge>
                          ) : expires ? (
                            `${expires.date} ${expires.time}`
                          ) : (
                            "Durable"
                          )}
                        </TableCell>
                        <TableCell className="pr-4 text-right">
                          <Button
                            type="button"
                            size="sm"
                            variant="ghost"
                            className="text-destructive hover:text-destructive"
                            disabled={busy}
                            onClick={() => deleteDialog.show({ key: entry.key, kind: entry.kind })}
                          >
                            Remove
                          </Button>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          ) : (
            <EmptyState
              icon={Pin}
              title="No sticky sessions on this page"
              description="The current page is empty. Use pagination to navigate to another page."
            />
          )}
          <div className="flex justify-end pt-3">
            <PaginationControls
              total={total}
              limit={params.limit}
              offset={params.offset}
              hasMore={hasMore}
              onLimitChange={setLimit}
              onOffsetChange={setOffset}
            />
          </div>
        </>
      )}

      <ConfirmDialog
        open={deleteDialog.open}
        title="Remove sticky session"
        description={
          deleteDialog.data
            ? `${kindLabel(deleteDialog.data.kind)} mapping ${deleteDialog.data.key} will stop pinning future requests.`
            : ""
        }
        confirmLabel="Delete"
        onOpenChange={deleteDialog.onOpenChange}
        onConfirm={() => {
          if (!deleteDialog.data) {
            return;
          }
          void deleteMutation.mutateAsync([deleteDialog.data]).finally(() => {
            deleteDialog.hide();
          });
        }}
      />

      <ConfirmDialog
        open={deleteSelectedDialog.open}
        title="Delete selected sticky sessions"
        description={
          selectedDeleteCount === 1
            ? "Delete the selected sticky session? Failed deletions will be reported."
            : `Delete ${selectedDeleteCount} selected sticky sessions? Failed deletions will be reported.`
        }
        confirmLabel="Delete Sessions"
        onOpenChange={deleteSelectedDialog.onOpenChange}
        onConfirm={() => {
          if (selectedDeleteTargets.length === 0) {
            return;
          }
          void deleteMutation.mutateAsync(selectedDeleteTargets).then((response) => {
            setSelectedRowIds(response.failed.map((entry) => stickySessionRowId(entry)));
          }).finally(() => {
            deleteSelectedDialog.hide();
          });
        }}
      />

      <ConfirmDialog
        open={purgeDialog.open}
        title="Purge stale prompt-cache mappings"
        description="Only expired prompt-cache entries will be deleted. Durable session and sticky-thread mappings stay intact."
        confirmLabel="Purge"
        onOpenChange={purgeDialog.onOpenChange}
        onConfirm={() => {
          void purgeMutation.mutateAsync(true).finally(() => {
            purgeDialog.hide();
          });
        }}
      />
    </section>
  );
}
