import { useState } from "react";
import type { FormEvent } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import type { ApiKey, ApiKeyUpdateRequest } from "@/features/api-keys/schemas";

function toModels(value: string): string[] | null | undefined {
  const values = value
    .split(",")
    .map((entry) => entry.trim())
    .filter(Boolean);
  return values.length > 0 ? values : null;
}

function toIsoDateTime(value: string): string | null | undefined {
  if (!value) {
    return null;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return null;
  }
  return date.toISOString();
}

function toLocalDateTime(value: string | null): string {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  const offset = date.getTimezoneOffset();
  const adjusted = new Date(date.getTime() - offset * 60_000);
  return adjusted.toISOString().slice(0, 16);
}

export type ApiKeyEditDialogProps = {
  open: boolean;
  busy: boolean;
  apiKey: ApiKey | null;
  onOpenChange: (open: boolean) => void;
  onSubmit: (payload: ApiKeyUpdateRequest) => Promise<void>;
};

type ApiKeyEditFormProps = {
  apiKey: ApiKey;
  busy: boolean;
  onSubmit: (payload: ApiKeyUpdateRequest) => Promise<void>;
  onClose: () => void;
};

function ApiKeyEditForm({ apiKey, busy, onSubmit, onClose }: ApiKeyEditFormProps) {
  const [name, setName] = useState(() => apiKey.name);
  const [models, setModels] = useState(() => (apiKey.allowedModels || []).join(", "));
  const [weeklyLimit, setWeeklyLimit] = useState(() =>
    apiKey.weeklyTokenLimit ? String(apiKey.weeklyTokenLimit) : "",
  );
  const [expiresAt, setExpiresAt] = useState(() => toLocalDateTime(apiKey.expiresAt));
  const [isActive, setIsActive] = useState(() => apiKey.isActive);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const payload: ApiKeyUpdateRequest = {
      name,
      allowedModels: toModels(models),
      weeklyTokenLimit: weeklyLimit ? Number(weeklyLimit) : null,
      expiresAt: toIsoDateTime(expiresAt),
      isActive,
    };
    await onSubmit(payload);
    onClose();
  };

  return (
    <form className="space-y-3" onSubmit={handleSubmit}>
      <div className="space-y-1">
        <Label htmlFor="api-key-edit-name">Name</Label>
        <Input
          id="api-key-edit-name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          required
        />
      </div>

      <div className="space-y-1">
        <Label htmlFor="api-key-edit-models">Allowed models (comma-separated)</Label>
        <Input
          id="api-key-edit-models"
          value={models}
          onChange={(event) => setModels(event.target.value)}
        />
      </div>

      <div className="space-y-1">
        <Label htmlFor="api-key-edit-limit">Weekly token limit</Label>
        <Input
          id="api-key-edit-limit"
          type="number"
          min={1}
          value={weeklyLimit}
          onChange={(event) => setWeeklyLimit(event.target.value)}
        />
      </div>

      <div className="space-y-1">
        <Label htmlFor="api-key-edit-expiry">Expiry</Label>
        <Input
          id="api-key-edit-expiry"
          type="datetime-local"
          value={expiresAt}
          onChange={(event) => setExpiresAt(event.target.value)}
        />
      </div>

      <div className="flex items-center justify-between rounded-md border p-2">
        <span className="text-sm">Active</span>
        <Switch checked={isActive} onCheckedChange={setIsActive} />
      </div>

      <DialogFooter>
        <Button type="submit" disabled={busy || !name.trim()}>
          Save
        </Button>
      </DialogFooter>
    </form>
  );
}

export function ApiKeyEditDialog({ open, busy, apiKey, onOpenChange, onSubmit }: ApiKeyEditDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit API key</DialogTitle>
          <DialogDescription>Update restrictions and lifecycle settings.</DialogDescription>
        </DialogHeader>

        {apiKey ? (
          <ApiKeyEditForm
            key={`${apiKey.id}:${open ? "open" : "closed"}`}
            apiKey={apiKey}
            busy={busy}
            onSubmit={onSubmit}
            onClose={() => onOpenChange(false)}
          />
        ) : (
          <p className="text-sm text-muted-foreground">Select an API key to edit.</p>
        )}
      </DialogContent>
    </Dialog>
  );
}
