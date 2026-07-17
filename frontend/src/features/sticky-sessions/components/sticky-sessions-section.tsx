import { useEffect, useMemo, useState } from "react";
import { Pin } from "lucide-react";
import { useTranslation } from "react-i18next";

import { AlertMessage } from "@/components/alert-message";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
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
import type {
  StickySessionEntry,
  StickySessionIdentifier,
  StickySessionKind,
  StickySessionSortBy,
  StickySessionSortDir,
} from "@/features/sticky-sessions/schemas";
import { useDialogState } from "@/hooks/use-dialog-state";
import { getErrorMessageOrNull } from "@/utils/errors";
import { formatTimeLong } from "@/utils/formatters";

function kindLabel(kind: StickySessionKind, t: ReturnType<typeof useTranslation>["t"]): string {
  switch (kind) {
    case "codex_session":
      return t("stickySessions.kinds.codexSession");
    case "sticky_thread":
      return t("stickySessions.kinds.stickyThread");
    case "prompt_cache":
      return t("stickySessions.kinds.promptCache");
  }
}

function stickySessionRowId(entry: StickySessionIdentifier): string {
  return `${entry.kind}:${entry.key}`;
}

const EMPTY_STICKY_SESSION_ENTRIES: StickySessionEntry[] = [];

function nextSortDirection(currentSortBy: StickySessionSortBy, currentSortDir: StickySessionSortDir, target: StickySessionSortBy) {
  if (currentSortBy != target) {
    return target === "updated_at" ? "desc" : "asc";
  }
  return currentSortDir === "asc" ? "desc" : "asc";
}

function sortIndicator(currentSortBy: StickySessionSortBy, currentSortDir: StickySessionSortDir, target: StickySessionSortBy) {
  if (currentSortBy !== target) {
    return null;
  }
  return currentSortDir === "asc" ? " ↑" : " ↓";
}

export type StickySessionsSectionProps = {
  disabled?: boolean;
};

export function StickySessionsSection({ disabled = false }: StickySessionsSectionProps) {
  const { t } = useTranslation();
  const {
    params,
    setAccountQuery,
    setKeyQuery,
    setSort,
    setLimit,
    setOffset,
    stickySessionsQuery,
    deleteMutation,
    deleteFilteredMutation,
    purgeMutation,
  } = useStickySessions();
  const deleteDialog = useDialogState<StickySessionIdentifier>();
  const deleteSelectedDialog = useDialogState<StickySessionIdentifier[]>();
  const deleteFilteredDialog = useDialogState<number>();
  const purgeDialog = useDialogState();
  const [selectedRowIds, setSelectedRowIds] = useState<string[]>([]);

  const mutationError = useMemo(
    () =>
      getErrorMessageOrNull(stickySessionsQuery.error) ||
      getErrorMessageOrNull(deleteMutation.error) ||
      getErrorMessageOrNull(deleteFilteredMutation.error) ||
      getErrorMessageOrNull(purgeMutation.error),
    [stickySessionsQuery.error, deleteMutation.error, deleteFilteredMutation.error, purgeMutation.error],
  );

  const entries = stickySessionsQuery.data?.entries ?? EMPTY_STICKY_SESSION_ENTRIES;
  const staleCount = stickySessionsQuery.data?.stalePromptCacheCount ?? 0;
  const total = stickySessionsQuery.data?.total ?? 0;
  const hasMore = stickySessionsQuery.data?.hasMore ?? false;
  const busy = disabled || deleteMutation.isPending || deleteFilteredMutation.isPending || purgeMutation.isPending;
  const hasEntries = entries.length > 0;
  const hasAnyRows = total > 0;
  const hasActiveTextFilter = params.accountQuery.trim().length > 0 || params.keyQuery.trim().length > 0;
  const visibleRowIdSet = useMemo(() => new Set(entries.map((entry) => stickySessionRowId(entry))), [entries]);
  const selectedRowIdSet = useMemo(() => new Set(selectedRowIds), [selectedRowIds]);
  const selectedEntries = useMemo(
    () =>
      entries.reduce<StickySessionIdentifier[]>((selected, entry) => {
        if (selectedRowIdSet.has(stickySessionRowId(entry))) {
          selected.push({ key: entry.key, kind: entry.kind });
        }
        return selected;
      }, []),
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
          <h3 className="text-sm font-semibold">{t("stickySessions.title")}</h3>
          <p className="text-xs text-muted-foreground">
            {t("stickySessions.description")}
          </p>
        </div>
      </div>

      {mutationError ? <AlertMessage variant="error">{mutationError}</AlertMessage> : null}

      <div className="grid gap-2 sm:grid-cols-2">
	        <Input
	          aria-label={t("stickySessions.filters.accountAria")}
	          placeholder={t("stickySessions.filters.accountPlaceholder")}
          value={params.accountQuery}
          onChange={(event) => setAccountQuery(event.target.value)}
        />
	        <Input
	          aria-label={t("stickySessions.filters.keyAria")}
	          placeholder={t("stickySessions.filters.keyPlaceholder")}
          value={params.keyQuery}
          onChange={(event) => setKeyQuery(event.target.value)}
        />
      </div>

      <div className="flex flex-col gap-3 rounded-lg border px-3 py-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-1.5">
	            <span className="text-xs text-muted-foreground">{t("stickySessions.summary.visibleRows")}</span>
            <span className="text-sm font-medium tabular-nums">{total}</span>
          </div>
          <div className="flex items-center gap-1.5">
	            <span className="text-xs text-muted-foreground">{t("stickySessions.summary.stalePromptCache")}</span>
            <span className="text-sm font-medium tabular-nums">{staleCount}</span>
          </div>
          {selectedCount > 0 ? (
            <div className="flex items-center gap-1.5">
	              <span className="text-xs text-muted-foreground">{t("stickySessions.summary.selected")}</span>
              <span className="text-sm font-medium tabular-nums">{selectedCount}</span>
            </div>
          ) : null}
        </div>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-8 text-xs"
            disabled={busy || !hasActiveTextFilter || total === 0}
            onClick={() => deleteFilteredDialog.show(total)}
          >
	            {t("stickySessions.actions.deleteFiltered")}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="destructive"
            className="h-8 text-xs"
            disabled={busy || selectedCount === 0}
            onClick={() => deleteSelectedDialog.show(selectedEntries)}
          >
	            {t("stickySessions.actions.deleteSessions")}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-8 text-xs"
            disabled={busy || staleCount === 0}
            onClick={() => purgeDialog.show()}
          >
	            {t("stickySessions.actions.purgeStale")}
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
	          title={t("stickySessions.empty.title")}
	          description={t("stickySessions.empty.description")}
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
	                        aria-label={t("stickySessions.table.selectAllAria")}
                        checked={allVisibleSelected ? true : someVisibleSelected ? "indeterminate" : false}
                        disabled={busy || !hasEntries}
                        onCheckedChange={(checked) => setAllVisibleSelected(checked === true)}
                      />
                    </TableHead>
                    <TableHead className="w-[25%] min-w-[14rem] text-[11px] uppercase tracking-wider text-muted-foreground/80">
                      <button
                        type="button"
                        className="cursor-pointer text-left transition-colors hover:text-foreground"
                        onClick={() => setSort("key", nextSortDirection(params.sortBy, params.sortDir, "key"))}
                      >
	                        {`${t("stickySessions.table.key")}${sortIndicator(params.sortBy, params.sortDir, "key") ?? ""}`}
                      </button>
                    </TableHead>
                    <TableHead className="w-[14%] min-w-[8rem] text-[11px] uppercase tracking-wider text-muted-foreground/80">
	                      {t("stickySessions.table.kind")}
                    </TableHead>
                    <TableHead className="w-[18%] min-w-[9rem] text-[11px] uppercase tracking-wider text-muted-foreground/80">
                      <button
                        type="button"
                        className="cursor-pointer text-left transition-colors hover:text-foreground"
                        onClick={() => setSort("account", nextSortDirection(params.sortBy, params.sortDir, "account"))}
                      >
	                        {`${t("dashboard.requests.columns.account")}${sortIndicator(params.sortBy, params.sortDir, "account") ?? ""}`}
                      </button>
                    </TableHead>
                    <TableHead className="w-[16%] min-w-[9rem] text-[11px] uppercase tracking-wider text-muted-foreground/80">
                      <button
                        type="button"
                        className="cursor-pointer text-left transition-colors hover:text-foreground"
                        onClick={() =>
                          setSort("updated_at", nextSortDirection(params.sortBy, params.sortDir, "updated_at"))
                        }
                      >
	                        {`${t("stickySessions.table.updated")}${sortIndicator(params.sortBy, params.sortDir, "updated_at") ?? ""}`}
                      </button>
                    </TableHead>
                    <TableHead className="w-[16%] min-w-[9rem] text-[11px] uppercase tracking-wider text-muted-foreground/80">
	                      {t("apiKeys.table.expiry")}
                    </TableHead>
                    <TableHead className="w-[6%] min-w-[4.5rem] pr-4 text-right align-middle text-[11px] uppercase tracking-wider text-muted-foreground/80">
	                      {t("apiKeys.table.actions")}
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
	                            aria-label={t("stickySessions.table.selectRowAria", { key: entry.key })}
                            checked={selected}
                            disabled={busy}
                            onCheckedChange={(checked) => setSelected(entry, checked === true)}
                          />
                        </TableCell>
                        <TableCell className="max-w-[18rem] truncate font-mono text-xs" title={entry.key}>
                          {entry.key}
                        </TableCell>
                        <TableCell>
	                          <Badge variant="outline">{kindLabel(entry.kind, t)}</Badge>
                        </TableCell>
                        <TableCell className="truncate text-xs">{entry.displayName}</TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {updated.date} {updated.time}
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {entry.isStale ? (
	                            <Badge variant="secondary">{t("stickySessions.states.stale")}</Badge>
                          ) : expires ? (
                            `${expires.date} ${expires.time}`
                          ) : (
	                            t("stickySessions.states.durable")
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
	                            {t("common.actions.remove")}
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
	              title={t("stickySessions.emptyPage.title")}
	              description={t("stickySessions.emptyPage.description")}
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
	        title={t("stickySessions.removeDialog.title")}
	        description={
	          deleteDialog.data
	            ? t("stickySessions.removeDialog.description", {
	                kind: kindLabel(deleteDialog.data.kind, t),
	                key: deleteDialog.data.key,
	              })
	            : ""
	        }
	        confirmLabel={t("common.actions.delete")}
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
	        title={t("stickySessions.deleteSelectedDialog.title")}
	        description={
	          selectedDeleteCount === 1
	            ? t("stickySessions.deleteSelectedDialog.descriptionOne")
	            : t("stickySessions.deleteSelectedDialog.descriptionMany", { count: selectedDeleteCount })
	        }
	        confirmLabel={t("stickySessions.actions.deleteSessions")}
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
        open={deleteFilteredDialog.open}
	        title={t("stickySessions.deleteFilteredDialog.title")}
	        description={t("stickySessions.deleteFilteredDialog.description", { count: deleteFilteredDialog.data ?? 0 })}
	        confirmLabel={t("stickySessions.actions.deleteFiltered")}
        onOpenChange={deleteFilteredDialog.onOpenChange}
        onConfirm={() => {
          void deleteFilteredMutation.mutateAsync().then(() => {
            setSelectedRowIds([]);
          }).finally(() => {
            deleteFilteredDialog.hide();
          });
        }}
      />

      <ConfirmDialog
        open={purgeDialog.open}
	        title={t("stickySessions.purgeDialog.title")}
	        description={t("stickySessions.purgeDialog.description")}
	        confirmLabel={t("stickySessions.actions.purge")}
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
