import { useState } from "react";
import type { FormEvent } from "react";
import { useTranslation } from "react-i18next";

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
  onImport: (file: File) => Promise<void>;
};

export function ImportDialog({
  open,
  busy,
  error,
  onOpenChange,
  onImport,
}: ImportDialogProps) {
  const { t } = useTranslation();
  const [files, setFiles] = useState<File[]>([]);
  const [inputKey, setInputKey] = useState(0);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (files.length === 0) {
      return;
    }

    setSubmitting(true);
    try {
      for (const [index, file] of files.entries()) {
        try {
          await onImport(file);
        } catch {
          setFiles(files.slice(index));
          setInputKey((currentKey) => currentKey + 1);
          return;
        }
        setFiles(files.slice(index + 1));
      }

      onOpenChange(false);
      setFiles([]);
      setInputKey((currentKey) => currentKey + 1);
    } finally {
      setSubmitting(false);
    }
  };

  const importBusy = busy || submitting;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("accounts.importDialog.title")}</DialogTitle>
          <DialogDescription>{t("accounts.importDialog.description")}</DialogDescription>
        </DialogHeader>

        <form className="space-y-4" aria-busy={importBusy} onSubmit={handleSubmit}>
          <div className="space-y-2">
            <Label htmlFor="auth-json-file">{t("accounts.importDialog.fileLabel")}</Label>
            <Input
              key={inputKey}
              id="auth-json-file"
              type="file"
              accept="application/json,.json"
              multiple
              disabled={importBusy}
              onChange={(event) => setFiles(Array.from(event.currentTarget.files ?? []))}
            />
          </div>

          {files.length > 0 ? (
            <div className="space-y-1 text-xs text-muted-foreground">
              <p>{t("accounts.importDialog.selectedFiles")}</p>
              <ul className="max-h-28 space-y-1 overflow-y-auto rounded-md border px-2 py-1">
                {files.map((file, index) => (
                  <li
                    key={`${file.name}-${file.size}-${file.lastModified}-${index}`}
                    className="truncate"
                    title={file.name}
                  >
                    {file.name}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {error ? (
            <p className="rounded-md border border-destructive/30 bg-destructive/10 px-2 py-1 text-xs text-destructive">
              {error}
            </p>
          ) : null}

          <DialogFooter>
            <Button type="submit" disabled={importBusy || files.length === 0}>
              {t("common.actions.import")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
