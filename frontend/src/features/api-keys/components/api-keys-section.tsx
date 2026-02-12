import { useMemo, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { Button } from "@/components/ui/button";
import { ApiKeyAuthToggle } from "@/features/api-keys/components/api-key-auth-toggle";
import { ApiKeyCreateDialog } from "@/features/api-keys/components/api-key-create-dialog";
import { ApiKeyCreatedDialog } from "@/features/api-keys/components/api-key-created-dialog";
import { ApiKeyEditDialog } from "@/features/api-keys/components/api-key-edit-dialog";
import { ApiKeyTable } from "@/features/api-keys/components/api-key-table";
import { useApiKeys } from "@/features/api-keys/hooks/use-api-keys";
import type { ApiKey, ApiKeyCreateRequest, ApiKeyUpdateRequest } from "@/features/api-keys/schemas";

function errorMessage(error: unknown): string | null {
  if (!error) {
    return null;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "API key request failed";
}

export type ApiKeysSectionProps = {
  apiKeyAuthEnabled: boolean;
  disabled?: boolean;
  onApiKeyAuthEnabledChange: (enabled: boolean) => void;
};

export function ApiKeysSection({
  apiKeyAuthEnabled,
  disabled = false,
  onApiKeyAuthEnabledChange,
}: ApiKeysSectionProps) {
  const {
    apiKeysQuery,
    createMutation,
    updateMutation,
    deleteMutation,
    regenerateMutation,
  } = useApiKeys();

  const [createOpen, setCreateOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<ApiKey | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ApiKey | null>(null);
  const [createdOpen, setCreatedOpen] = useState(false);
  const [createdKey, setCreatedKey] = useState<string | null>(null);

  const keys = apiKeysQuery.data ?? [];
  const busy =
    disabled ||
    apiKeysQuery.isFetching ||
    createMutation.isPending ||
    updateMutation.isPending ||
    deleteMutation.isPending ||
    regenerateMutation.isPending;

  const mutationError = useMemo(
    () =>
      errorMessage(createMutation.error) ||
      errorMessage(updateMutation.error) ||
      errorMessage(deleteMutation.error) ||
      errorMessage(regenerateMutation.error),
    [createMutation.error, deleteMutation.error, regenerateMutation.error, updateMutation.error],
  );

  const handleCreate = async (payload: ApiKeyCreateRequest) => {
    const created = await createMutation.mutateAsync(payload);
    setCreatedKey(created.key);
    setCreatedOpen(true);
  };

  const handleUpdate = async (payload: ApiKeyUpdateRequest) => {
    if (!editTarget) {
      return;
    }
    await updateMutation.mutateAsync({ keyId: editTarget.id, payload });
  };

  return (
    <section className="space-y-3 rounded-xl border p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">API Keys</h3>
          <p className="text-xs text-muted-foreground">Create and manage API keys for clients.</p>
        </div>
        <Button type="button" size="sm" onClick={() => setCreateOpen(true)} disabled={busy}>
          Create key
        </Button>
      </div>

      <ApiKeyAuthToggle
        enabled={apiKeyAuthEnabled}
        disabled={busy}
        onChange={onApiKeyAuthEnabledChange}
      />

      {mutationError ? (
        <p className="rounded-md border border-destructive/30 bg-destructive/10 px-2 py-1 text-xs text-destructive">
          {mutationError}
        </p>
      ) : null}

      <ApiKeyTable
        keys={keys}
        busy={busy}
        onEdit={(apiKey) => {
          setEditTarget(apiKey);
          setEditOpen(true);
        }}
        onDelete={(apiKey) => setDeleteTarget(apiKey)}
        onRegenerate={(apiKey) => {
          void regenerateMutation.mutateAsync(apiKey.id).then((result) => {
            setCreatedKey(result.key);
            setCreatedOpen(true);
          });
        }}
      />

      <ApiKeyCreateDialog
        open={createOpen}
        busy={createMutation.isPending}
        onOpenChange={setCreateOpen}
        onSubmit={handleCreate}
      />

      <ApiKeyEditDialog
        open={editOpen}
        busy={updateMutation.isPending}
        apiKey={editTarget}
        onOpenChange={(open) => {
          setEditOpen(open);
          if (!open) {
            setEditTarget(null);
          }
        }}
        onSubmit={handleUpdate}
      />

      <ApiKeyCreatedDialog open={createdOpen} apiKey={createdKey} onOpenChange={setCreatedOpen} />

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete API key"
        description="This key will stop working immediately."
        confirmLabel="Delete"
        onOpenChange={(open) => {
          if (!open) {
            setDeleteTarget(null);
          }
        }}
        onConfirm={() => {
          if (!deleteTarget) {
            return;
          }
          void deleteMutation.mutateAsync(deleteTarget.id).finally(() => {
            setDeleteTarget(null);
          });
        }}
      />
    </section>
  );
}
