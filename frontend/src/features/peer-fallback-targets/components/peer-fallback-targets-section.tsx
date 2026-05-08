import { useMemo, useState } from "react";
import { Check, Pencil, Plus, ServerCog, Trash2, X } from "lucide-react";

import { AlertMessage } from "@/components/alert-message";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SpinnerBlock } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { PeerFallbackTarget } from "@/features/peer-fallback-targets/schemas";
import { usePeerFallbackTargets } from "@/features/peer-fallback-targets/hooks/use-peer-fallback-targets";
import { useDialogState } from "@/hooks/use-dialog-state";
import { getErrorMessageOrNull } from "@/utils/errors";
import { formatTimeLong } from "@/utils/formatters";

export function PeerFallbackTargetsSection() {
  const [baseUrl, setBaseUrl] = useState("");
  const [editingTargetId, setEditingTargetId] = useState<string | null>(null);
  const [editingBaseUrl, setEditingBaseUrl] = useState("");
  const { targetsQuery, createMutation, updateMutation, deleteMutation } = usePeerFallbackTargets();
  const deleteDialog = useDialogState<PeerFallbackTarget>();

  const mutationError = useMemo(
    () =>
      getErrorMessageOrNull(targetsQuery.error) ||
      getErrorMessageOrNull(createMutation.error) ||
      getErrorMessageOrNull(updateMutation.error) ||
      getErrorMessageOrNull(deleteMutation.error),
    [targetsQuery.error, createMutation.error, updateMutation.error, deleteMutation.error],
  );

  const targets = targetsQuery.data?.targets ?? [];
  const enabledCount = targets.filter((target) => target.enabled).length;
  const busy = createMutation.isPending || updateMutation.isPending || deleteMutation.isPending;

  const handleAdd = async () => {
    const normalized = baseUrl.trim();
    if (!normalized) {
      return;
    }
    await createMutation.mutateAsync(normalized);
    setBaseUrl("");
  };

  const startEditing = (target: PeerFallbackTarget) => {
    setEditingTargetId(target.id);
    setEditingBaseUrl(target.baseUrl);
  };

  const cancelEditing = () => {
    setEditingTargetId(null);
    setEditingBaseUrl("");
  };

  const saveEditing = async (target: PeerFallbackTarget) => {
    const nextBaseUrl = editingBaseUrl.trim();
    if (!nextBaseUrl || nextBaseUrl === target.baseUrl) {
      cancelEditing();
      return;
    }
    await updateMutation.mutateAsync({
      targetId: target.id,
      payload: { baseUrl: nextBaseUrl },
    });
    cancelEditing();
  };

  return (
    <section className="flex flex-col gap-3 rounded-xl border bg-card p-5">
      <div className="flex items-center gap-2.5">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
          <ServerCog className="h-4 w-4 text-primary" aria-hidden="true" />
        </div>
        <div>
          <h3 className="text-sm font-semibold">Peer fallback targets</h3>
          <p className="text-xs text-muted-foreground">Register peer codex-lb instances for pre-output failures.</p>
        </div>
      </div>

      {mutationError ? <AlertMessage variant="error">{mutationError}</AlertMessage> : null}

      <div className="flex items-center gap-3 rounded-lg border px-3 py-2">
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-muted-foreground">Targets</span>
          <span className="text-sm font-medium tabular-nums">{targets.length}</span>
        </div>
        <div className="h-4 w-px bg-border" />
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-muted-foreground">Enabled</span>
          <Badge variant={enabledCount > 0 ? "default" : "outline"}>{enabledCount}</Badge>
        </div>
      </div>

      <div className="flex flex-col gap-2 sm:flex-row">
        <Input
          value={baseUrl}
          onChange={(event) => setBaseUrl(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              void handleAdd();
            }
          }}
          placeholder="https://peer.example.com"
          className="h-8 text-xs"
          disabled={busy}
        />
        <Button
          type="button"
          size="sm"
          className="h-8 text-xs"
          onClick={() => void handleAdd()}
          disabled={busy || !baseUrl.trim()}
        >
          <Plus aria-hidden="true" />
          Add target
        </Button>
      </div>

      {targetsQuery.isLoading && !targetsQuery.data ? (
        <div className="py-8">
          <SpinnerBlock />
        </div>
      ) : targets.length === 0 ? (
        <EmptyState
          icon={ServerCog}
          title="No peer targets registered"
          description="Peer fallback URLs are configured directly on API keys."
        />
      ) : (
        <div className="overflow-x-auto rounded-xl border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Base URL</TableHead>
                <TableHead>Updated</TableHead>
                <TableHead className="w-[96px]">Enabled</TableHead>
                <TableHead className="w-[80px] text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {targets.map((target) => {
                const updated = formatTimeLong(target.updatedAt);
                const editing = editingTargetId === target.id;
                return (
                  <TableRow key={target.id}>
                    <TableCell className="min-w-[260px]">
                      {editing ? (
                        <Input
                          value={editingBaseUrl}
                          onChange={(event) => setEditingBaseUrl(event.target.value)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter") {
                              void saveEditing(target);
                            }
                            if (event.key === "Escape") {
                              cancelEditing();
                            }
                          }}
                          className="h-8 font-mono text-xs"
                          disabled={busy}
                        />
                      ) : (
                        <span className="font-mono text-xs">{target.baseUrl}</span>
                      )}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {updated.date} {updated.time}
                    </TableCell>
                    <TableCell>
                      <Switch
                        checked={target.enabled}
                        disabled={busy}
                        onCheckedChange={(enabled) =>
                          updateMutation.mutate({
                            targetId: target.id,
                            payload: { enabled },
                          })
                        }
                      />
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1">
                        {editing ? (
                          <>
                            <Button
                              type="button"
                              size="icon-sm"
                              variant="ghost"
                              disabled={busy}
                              aria-label={`Save ${target.baseUrl}`}
                              onClick={() => void saveEditing(target)}
                            >
                              <Check aria-hidden="true" />
                            </Button>
                            <Button
                              type="button"
                              size="icon-sm"
                              variant="ghost"
                              disabled={busy}
                              aria-label="Cancel edit"
                              onClick={cancelEditing}
                            >
                              <X aria-hidden="true" />
                            </Button>
                          </>
                        ) : (
                          <>
                            <Button
                              type="button"
                              size="icon-sm"
                              variant="ghost"
                              disabled={busy}
                              aria-label={`Edit ${target.baseUrl}`}
                              onClick={() => startEditing(target)}
                            >
                              <Pencil aria-hidden="true" />
                            </Button>
                            <Button
                              type="button"
                              size="icon-sm"
                              variant="ghost"
                              className="text-destructive hover:text-destructive"
                              disabled={busy}
                              aria-label={`Remove ${target.baseUrl}`}
                              onClick={() => deleteDialog.show(target)}
                            >
                              <Trash2 aria-hidden="true" />
                            </Button>
                          </>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}

      <ConfirmDialog
        open={deleteDialog.open}
        title="Remove peer fallback target"
        description={`${deleteDialog.data?.baseUrl ?? ""} will no longer receive fallback requests.`}
        confirmLabel="Remove"
        onOpenChange={deleteDialog.onOpenChange}
        onConfirm={() => {
          if (!deleteDialog.data) {
            return;
          }
          void deleteMutation.mutateAsync(deleteDialog.data.id).finally(() => {
            deleteDialog.hide();
          });
        }}
      />
    </section>
  );
}
