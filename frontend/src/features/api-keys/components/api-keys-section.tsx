import { lazy, useMemo } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import { useDialogState } from "@/hooks/use-dialog-state";
import { ApiKeyAuthToggle } from "@/features/api-keys/components/api-key-auth-toggle";
import { ApiKeyCreatedDialog } from "@/features/api-keys/components/api-key-created-dialog";
import { ApiKeyTable } from "@/features/api-keys/components/api-key-table";
import { useApiKeys } from "@/features/api-keys/hooks/use-api-keys";
import type { ApiKey, ApiKeyCreateRequest, ApiKeyUpdateRequest } from "@/features/api-keys/schemas";
import { getErrorMessageOrNull } from "@/utils/errors";

const ApiKeyCreateDialog = lazy(() =>
  import("@/features/api-keys/components/api-key-create-dialog").then((m) => ({ default: m.ApiKeyCreateDialog })),
);
const ApiKeyEditDialog = lazy(() =>
  import("@/features/api-keys/components/api-key-edit-dialog").then((m) => ({ default: m.ApiKeyEditDialog })),
);

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

  const createDialog = useDialogState();
  const editDialog = useDialogState<ApiKey>();
  const deleteDialog = useDialogState<ApiKey>();
  const createdDialog = useDialogState<string>();

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
      getErrorMessageOrNull(createMutation.error) ||
      getErrorMessageOrNull(updateMutation.error) ||
      getErrorMessageOrNull(deleteMutation.error) ||
      getErrorMessageOrNull(regenerateMutation.error),
    [createMutation.error, deleteMutation.error, regenerateMutation.error, updateMutation.error],
  );

  const handleCreate = async (payload: ApiKeyCreateRequest) => {
    const created = await createMutation.mutateAsync(payload);
    createdDialog.show(created.key);
  };

  const handleUpdate = async (payload: ApiKeyUpdateRequest) => {
    if (!editDialog.data) {
      return;
    }
    await updateMutation.mutateAsync({ keyId: editDialog.data.id, payload });
  };

  return (
    <section className="space-y-3 rounded-xl border bg-card p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">API Keys</h3>
          <p className="text-xs text-muted-foreground">Create and manage API keys for clients.</p>
        </div>
        <Button type="button" size="sm" onClick={() => createDialog.show()} disabled={busy}>
          Create key
        </Button>
      </div>

      <ApiKeyAuthToggle
        enabled={apiKeyAuthEnabled}
        disabled={busy}
        onChange={onApiKeyAuthEnabledChange}
      />

      {mutationError ? <AlertMessage variant="error">{mutationError}</AlertMessage> : null}

      <ApiKeyTable
        keys={keys}
        busy={busy}
        onEdit={(apiKey) => editDialog.show(apiKey)}
        onDelete={(apiKey) => deleteDialog.show(apiKey)}
        onRegenerate={(apiKey) => {
          void regenerateMutation.mutateAsync(apiKey.id).then((result) => {
            createdDialog.show(result.key);
          });
        }}
      />

      <ApiKeyCreateDialog
        open={createDialog.open}
        busy={createMutation.isPending}
        onOpenChange={createDialog.onOpenChange}
        onSubmit={handleCreate}
      />

      <ApiKeyEditDialog
        open={editDialog.open}
        busy={updateMutation.isPending}
        apiKey={editDialog.data}
        onOpenChange={editDialog.onOpenChange}
        onSubmit={handleUpdate}
      />

      <ApiKeyCreatedDialog
        open={createdDialog.open}
        apiKey={createdDialog.data}
        onOpenChange={createdDialog.onOpenChange}
      />

      <ConfirmDialog
        open={deleteDialog.open}
        title="Delete API key"
        description="This key will stop working immediately."
        confirmLabel="Delete"
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
