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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

export type ImportProvider = "openai" | "anthropic";

export type ImportDialogProps = {
  open: boolean;
  busy: boolean;
  error: string | null;
  onOpenChange: (open: boolean) => void;
  onImport: (provider: ImportProvider, file: File, email: string | null) => Promise<void>;
};

export function ImportDialog({
  open,
  busy,
  error,
  onOpenChange,
  onImport,
}: ImportDialogProps) {
  const [file, setFile] = useState<File | null>(null);
  const [provider, setProvider] = useState<ImportProvider>("openai");
  const [anthropicEmail, setAnthropicEmail] = useState("");

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!file) {
      return;
    }
    const email = provider === "anthropic" ? anthropicEmail.trim() : null;
    if (provider === "anthropic" && !email) {
      return;
    }
    await onImport(provider, file, email);
    onOpenChange(false);
    setFile(null);
    setProvider("openai");
    setAnthropicEmail("");
  };

  const title = provider === "anthropic" ? "Import Claude credentials" : "Import auth.json";
  const description =
    provider === "anthropic"
      ? "Upload Claude credentials and provide the account email to display in dashboard."
      : "Upload an exported account auth.json file.";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>

        <form className="space-y-4" onSubmit={handleSubmit}>
          <div className="space-y-2">
            <Label htmlFor="import-provider">Provider</Label>
            <Select value={provider} onValueChange={(value) => setProvider(value as ImportProvider)}>
              <SelectTrigger id="import-provider">
                <SelectValue placeholder="Select provider" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="openai">OpenAI (auth.json)</SelectItem>
                <SelectItem value="anthropic">Claude (credentials JSON)</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label htmlFor="auth-json-file">File</Label>
            <Input
              id="auth-json-file"
              type="file"
              accept="application/json,.json"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
          </div>

          {provider === "anthropic" ? (
            <div className="space-y-2">
              <Label htmlFor="anthropic-email">Account email</Label>
              <Input
                id="anthropic-email"
                type="email"
                placeholder="you@example.com"
                value={anthropicEmail}
                onChange={(event) => setAnthropicEmail(event.target.value)}
                required
              />
            </div>
          ) : null}

          {error ? (
            <p className="rounded-md border border-destructive/30 bg-destructive/10 px-2 py-1 text-xs text-destructive">
              {error}
            </p>
          ) : null}

          <DialogFooter>
            <Button
              type="submit"
              disabled={busy || !file || (provider === "anthropic" && !anthropicEmail.trim())}
            >
              Import
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
