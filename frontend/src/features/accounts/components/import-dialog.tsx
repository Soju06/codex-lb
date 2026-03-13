import { useState } from "react";
import type { FormEvent } from "react";

import { AlertMessage } from "@/components/alert-message";
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
import type { AccountImportBatchResponse } from "@/features/accounts/schemas";

export type ImportDialogProps = {
  open: boolean;
  busy: boolean;
  error: string | null;
  result: AccountImportBatchResponse | null;
  onOpenChange: (open: boolean) => void;
  onImport: (files: File[]) => Promise<AccountImportBatchResponse>;
};

export function ImportDialog({
  open,
  busy,
  error,
  result,
  onOpenChange,
  onImport,
}: ImportDialogProps) {
  const [files, setFiles] = useState<File[]>([]);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (files.length === 0) {
      return;
    }
    const importResult = await onImport(files);
    setFiles([]);
    if (importResult.failed.length === 0) {
      onOpenChange(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Import auth.json files</DialogTitle>
          <DialogDescription>Upload one or more exported account auth.json files in a single batch.</DialogDescription>
        </DialogHeader>

        <form className="space-y-4" onSubmit={handleSubmit}>
          <div className="space-y-2">
            <Label htmlFor="auth-json-file">Files</Label>
            <Input
              id="auth-json-file"
              type="file"
              accept="application/json,.json"
              multiple
              onChange={(event) => setFiles(Array.from(event.target.files ?? []))}
            />
            <p className="text-xs text-muted-foreground">
              {files.length === 0
                ? "Select one or more auth.json files."
                : `${files.length} file${files.length === 1 ? "" : "s"} selected`}
            </p>
            {files.length > 0 ? (
              <ul className="space-y-1 rounded-md border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
                {files.map((file) => (
                  <li key={`${file.name}-${file.size}`}>{file.name}</li>
                ))}
              </ul>
            ) : null}
          </div>

          {error ? (
            <p className="rounded-md border border-destructive/30 bg-destructive/10 px-2 py-1 text-xs text-destructive">
              {error}
            </p>
          ) : null}

          {result ? (
            <div className="space-y-2">
              <AlertMessage variant={result.failed.length > 0 ? "error" : "success"}>
                {result.failed.length > 0
                  ? `Imported ${result.imported.length} file${result.imported.length === 1 ? "" : "s"}, ${result.failed.length} failed.`
                  : `Imported ${result.imported.length} file${result.imported.length === 1 ? "" : "s"} successfully.`}
              </AlertMessage>
              {result.failed.length > 0 ? (
                <ul className="space-y-1 rounded-md border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs text-destructive">
                  {result.failed.map((failure) => (
                    <li key={`${failure.filename ?? "unknown"}-${failure.code}`}>
                      <span className="font-medium">{failure.filename ?? "Unknown file"}:</span> {failure.message}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}

          <DialogFooter>
            <Button type="submit" disabled={busy || files.length === 0}>
              Import
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
