import { KeySquare } from "lucide-react";
import { lazy, useMemo, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
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
  ownerOptions?: ReadonlyArray<{
    id: string;
    username: string;
  }>;
  showApiKeyAuthToggle?: boolean;
  onApiKeyAuthEnabledChange: (enabled: boolean) => void;
};

export function ApiKeysSection({
  apiKeyAuthEnabled,
  disabled = false,
  ownerOptions = [],
  showApiKeyAuthToggle = true,
  onApiKeyAuthEnabledChange,
}: ApiKeysSectionProps) {
  const [ownerUserId, setOwnerUserId] = useState<string | undefined>(undefined);
  const {
    apiKeysQuery,
    createMutation,
    updateMutation,
    deleteMutation,
    regenerateMutation,
  } = useApiKeys(ownerUserId);

  const createDialog = useDialogState();
  const editDialog = useDialogState<ApiKey>();
  const deleteDialog = useDialogState<ApiKey>();
  const createdDialog = useDialogState<string>();
  const showOwnerFilter = ownerOptions.length > 0;
  const ownerLabels = useMemo(
    () =>
      Object.fromEntries(ownerOptions.map((item) => [item.id, item.username])) as Record<string, string>,
    [ownerOptions],
  );

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
    const created = await createMutation.mutateAsync({
      ...payload,
      ...(ownerUserId ? { ownerUserId } : {}),
    });
    createdDialog.show(created.key);
  };

  const handleUpdate = async (payload: ApiKeyUpdateRequest) => {
    if (!editDialog.data) {
      return;
    }
    await updateMutation.mutateAsync({ keyId: editDialog.data.id, payload });
  };

  return (
    <section className="space-y-3 rounded-xl border bg-card p-5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
            <KeySquare className="h-4 w-4 text-primary" aria-hidden="true" />
          </div>
          <div>
            <h3 className="text-sm font-semibold">API Keys</h3>
            <p className="text-xs text-muted-foreground">Create and manage API keys for clients.</p>
          </div>
        </div>
        <Button type="button" size="sm" className="h-8 text-xs" onClick={() => createDialog.show()} disabled={busy}>
          Create key
        </Button>
      </div>

      {showApiKeyAuthToggle ? (
        <ApiKeyAuthToggle
          enabled={apiKeyAuthEnabled}
          disabled={busy}
          onChange={onApiKeyAuthEnabledChange}
        />
      ) : null}

      {showOwnerFilter ? (
        <div className="flex items-center justify-between rounded-lg border p-3">
          <div className="space-y-1">
            <p className="text-sm font-medium">Owner scope</p>
            <p className="text-xs text-muted-foreground">Filter keys by user and create keys for selected owner.</p>
          </div>
          <Select value={ownerUserId ?? "all"} onValueChange={(value) => setOwnerUserId(value === "all" ? undefined : value)}>
            <SelectTrigger size="sm" className="w-56">
              <SelectValue placeholder="All users" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All users</SelectItem>
              {ownerOptions.map((user) => (
                <SelectItem key={user.id} value={user.id}>
                  {user.username}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      ) : null}

      {mutationError ? <AlertMessage variant="error">{mutationError}</AlertMessage> : null}

      <ApiKeyTable
        keys={keys}
        busy={busy}
        showOwner={showOwnerFilter}
        ownerLabels={ownerLabels}
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
