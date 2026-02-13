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
import type { ApiKeyCreateRequest } from "@/features/api-keys/schemas";

function toModels(value: string): string[] | undefined {
  const values = value
    .split(",")
    .map((entry) => entry.trim())
    .filter(Boolean);
  return values.length > 0 ? values : undefined;
}

function toIsoDateTime(value: string): string | undefined {
  if (!value) {
    return undefined;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return undefined;
  }
  return date.toISOString();
}

export type ApiKeyCreateDialogProps = {
  open: boolean;
  busy: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (payload: ApiKeyCreateRequest) => Promise<void>;
};

export function ApiKeyCreateDialog({ open, busy, onOpenChange, onSubmit }: ApiKeyCreateDialogProps) {
  const [name, setName] = useState("");
  const [models, setModels] = useState("");
  const [weeklyLimit, setWeeklyLimit] = useState("");
  const [expiresAt, setExpiresAt] = useState("");

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const payload: ApiKeyCreateRequest = {
      name,
      allowedModels: toModels(models),
      weeklyTokenLimit: weeklyLimit ? Number(weeklyLimit) : undefined,
      expiresAt: toIsoDateTime(expiresAt),
    };
    await onSubmit(payload);
    setName("");
    setModels("");
    setWeeklyLimit("");
    setExpiresAt("");
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create API key</DialogTitle>
          <DialogDescription>Set restrictions and expiration for this key.</DialogDescription>
        </DialogHeader>

        <form className="space-y-3" onSubmit={handleSubmit}>
          <div className="space-y-1">
            <Label htmlFor="api-key-name">Name</Label>
            <Input id="api-key-name" value={name} onChange={(event) => setName(event.target.value)} required />
          </div>

          <div className="space-y-1">
            <Label htmlFor="api-key-models">Allowed models (comma-separated)</Label>
            <Input
              id="api-key-models"
              value={models}
              onChange={(event) => setModels(event.target.value)}
              placeholder="gpt-5.1, gpt-4o-mini"
            />
          </div>

          <div className="space-y-1">
            <Label htmlFor="api-key-limit">Weekly token limit</Label>
            <Input
              id="api-key-limit"
              type="number"
              min={1}
              value={weeklyLimit}
              onChange={(event) => setWeeklyLimit(event.target.value)}
            />
          </div>

          <div className="space-y-1">
            <Label htmlFor="api-key-expiry">Expiry</Label>
            <Input
              id="api-key-expiry"
              type="datetime-local"
              value={expiresAt}
              onChange={(event) => setExpiresAt(event.target.value)}
            />
          </div>

          <DialogFooter>
            <Button type="submit" disabled={busy || !name.trim()}>
              Create
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
