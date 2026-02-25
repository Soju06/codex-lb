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

export type ImportDialogProps = {
  open: boolean;
  busy: boolean;
  error: string | null;
  onOpenChange: (open: boolean) => void;
  onImportAuthJson: (file: File) => Promise<void>;
  onImportCredentials: (credentialsText: string) => Promise<void>;
};

export function ImportDialog({
  open,
  busy,
  error,
  onOpenChange,
  onImportAuthJson,
  onImportCredentials,
}: ImportDialogProps) {
  const [mode, setMode] = useState<"authJson" | "credentials">("authJson");
  const [file, setFile] = useState<File | null>(null);
  const [credentialsText, setCredentialsText] = useState("");

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (mode === "authJson") {
      if (!file) {
        return;
      }
      await onImportAuthJson(file);
    } else {
      const trimmed = credentialsText.trim();
      if (!trimmed) {
        return;
      }
      await onImportCredentials(trimmed);
    }
    onOpenChange(false);
    setMode("authJson");
    setFile(null);
    setCredentialsText("");
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Import accounts</DialogTitle>
          <DialogDescription>Upload auth.json or paste credentials in email:password:2fa_secret format.</DialogDescription>
        </DialogHeader>

        <form className="space-y-4" onSubmit={handleSubmit}>
          <div className="grid grid-cols-2 gap-2">
            <Button
              type="button"
              variant={mode === "authJson" ? "default" : "outline"}
              onClick={() => setMode("authJson")}
            >
              auth.json
            </Button>
            <Button
              type="button"
              variant={mode === "credentials" ? "default" : "outline"}
              onClick={() => setMode("credentials")}
            >
              Credentials
            </Button>
          </div>

          {mode === "authJson" ? (
            <div className="space-y-2">
              <Label htmlFor="auth-json-file">File</Label>
              <Input
                id="auth-json-file"
                type="file"
                accept="application/json,.json"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
            </div>
          ) : (
            <div className="space-y-2">
              <Label htmlFor="credentials-text">Credentials</Label>
              <textarea
                id="credentials-text"
                value={credentialsText}
                onChange={(event) => setCredentialsText(event.target.value)}
                placeholder="email:password:2fa_secret"
                className="border-input bg-background min-h-36 w-full rounded-md border px-3 py-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]"
              />
            </div>
          )}

          {error ? (
            <p className="rounded-md border border-destructive/30 bg-destructive/10 px-2 py-1 text-xs text-destructive">
              {error}
            </p>
          ) : null}

          <DialogFooter>
            <Button
              type="submit"
              disabled={
                busy ||
                (mode === "authJson" ? !file : !credentialsText.trim())
              }
            >
              Import
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
